"""Direct-URL download leg + the AudioDownloadService direct pipeline."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import httpx
import pytest

from core.config import PROJECT_ROOT, Settings
from core.errors import UpstreamError
from services.download.audio_service import AudioDownloadService, DownloadRequest
from services.download.direct import fetch_audio_url, resolve_ffmpeg, transcode_to_wav
from services.music_source.base import Extractor
from services.music_source.registry import ExtractorRegistry
from tasks.manager import TaskManager
from tasks.models import TaskStage
from services.cache.manager import CacheManager


@pytest.mark.asyncio
async def test_fetch_streams_and_reports_progress(tmp_path):
    payload = b"x" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=payload, headers={"content-length": str(len(payload))}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    target = tmp_path / "a.mp3"
    progress: list[float] = []
    written = await fetch_audio_url(client, "http://cdn/a.mp3", target, on_progress=progress.append)
    assert written == 1000
    assert target.read_bytes() == payload
    assert progress and progress[-1] == 1.0


@pytest.mark.asyncio
async def test_fetch_404_raises(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"err": 1}))
    )
    with pytest.raises(UpstreamError) as excinfo:
        await fetch_audio_url(client, "http://cdn/404.mp3", tmp_path / "x.mp3")
    assert "404" in excinfo.value.message


@pytest.mark.asyncio
async def test_fetch_empty_body_raises_and_cleans_up(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b""))
    )
    target = tmp_path / "empty.mp3"
    with pytest.raises(UpstreamError):
        await fetch_audio_url(client, "http://cdn/empty.mp3", target)
    assert not target.exists()


def test_resolve_ffmpeg_prefers_configured_dir(tmp_path):
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"MZ")
    assert resolve_ffmpeg(str(tmp_path)) == fake


def test_transcode_roundtrip_with_real_ffmpeg(tmp_path):
    """Synthesize a wav, encode mp3 with the real ffmpeg, transcode back."""
    ffmpeg_dir = Settings.from_env().ffmpeg_location
    if resolve_ffmpeg(ffmpeg_dir) is None:
        pytest.skip("ffmpeg not available")
    import soundfile as sf
    import numpy as np

    import subprocess

    sr = 22050
    samples = (0.3 * np.sin(2 * np.pi * 440 * np.arange(sr // 10) / sr)).astype("float32")
    wav_in = tmp_path / "in.wav"
    sf.write(wav_in, samples, sr, subtype="PCM_16")
    mp3 = tmp_path / "in.mp3"
    proc = subprocess.run(
        [str(resolve_ffmpeg(ffmpeg_dir)), "-y", "-i", str(wav_in), str(mp3)],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip("ffmpeg mp3 encoder unavailable")
    wav_out = tmp_path / "out.wav"
    transcode_to_wav(ffmpeg_dir, mp3, wav_out)
    assert wav_out.is_file() and wav_out.stat().st_size > 100


class _DirectExtractor(Extractor):
    """Stands in for the NetEase/KuGou sidecar: search is unused, audio_url is fixed."""

    name = "netease"
    display_name = "网易云音乐"
    supports_download = True

    def __init__(self, audio_url: str, payload: bytes) -> None:
        self._audio_url = audio_url
        self._payload = payload

    def canonical_url(self, source_id: str) -> str:
        return f"https://music.163.com/#/song?id={source_id}"

    async def search(self, keyword: str, limit: int) -> list:
        raise NotImplementedError

    async def audio_url(self, source_id: str) -> str:
        return self._audio_url


@pytest.mark.asyncio
async def test_direct_pipeline_downloads_and_registers_cache(tmp_path):
    payload = b"ID3" + b"\x00" * 500
    audio_url = "http://cdn.example/track.mp3"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/track"):
            return httpx.Response(
                200, content=payload, headers={"content-length": str(len(payload))}
            )
        raise AssertionError(f"unexpected request: {request.url}")

    settings = dataclasses.replace(
        Settings.from_env(),
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        netease_sidecar_url="http://unused",
        netease_cookie_file=str(tmp_path / "netease.txt"),
    )
    settings.ensure_dirs()
    cache = CacheManager(
        settings.cache_dir,
        max_bytes=settings.cache_max_bytes,
        ttl_days=settings.cache_ttl_days,
    )
    tasks = TaskManager()
    extractors = ExtractorRegistry()
    extractors.register(_DirectExtractor(audio_url, payload))
    service = AudioDownloadService(
        settings, cache, tasks, extractors, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    snapshot = await service.submit(
        DownloadRequest(
            source="netease",
            source_id="347230",
            audio_format="mp3",  # mp3 needs no ffmpeg: keeps the test hermetic
            title="晴天",
            artist="周杰伦",
            duration=278.9,
        )
    )

    for _ in range(200):
        if snapshot["stage"] in (TaskStage.DONE.value, TaskStage.FAILED.value):
            break
        await asyncio.sleep(0.01)
        snapshot = tasks.get(snapshot["taskId"]).snapshot()

    assert snapshot["stage"] == TaskStage.DONE.value, snapshot["message"]
    assert snapshot["result"]["fromCache"] is False
    assert snapshot["result"]["title"] == "晴天"
    assert snapshot["result"]["artist"] == "周杰伦"
    # audioUrl is a route URL; the actual file sits under the cache key.
    key = cache.make_key("netease", "347230", "mp3")
    assert (cache.root / f"{key}.mp3").is_file()
    assert cache.lookup("netease", "347230", "mp3") is not None
