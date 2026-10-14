"""HTTP contract tests for the phase-1 API.

Network access is replaced by in-test doubles: the real extractor and the real
yt-dlp calls never run here.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from tornado import gen
from tornado.testing import AsyncHTTPTestCase

from api.routes import make_app
from container import build_services
from core.config import Settings
from core.errors import UpstreamBlockedError, UpstreamError
from services.download import audio_service as audio_module
from services.music_source.base import Extractor, Track
from services.music_source.registry import ExtractorRegistry

FAKE_TRACKS = [
    Track(
        source="fake",
        source_id="BV1",
        title="茉莉花",
        artist="几朵儿歌",
        duration=1204.6,
        webpage_url="https://www.bilibili.com/video/BV1",
    ),
    Track(source="fake", source_id="BV2", title="茉莉花（钢琴版）", duration=44.0),
]


class FakeExtractor(Extractor):
    name = "fake"
    display_name = "Fake"
    supports_download = True

    def __init__(self, *, downloadable: bool = True) -> None:
        self.supports_download = downloadable  # type: ignore[misc]
        self.search_calls: list[tuple[str, int]] = []

    def canonical_url(self, source_id: str) -> str:
        return f"https://example.invalid/video/{source_id}"

    async def search(self, keyword: str, limit: int) -> list[Track]:
        self.search_calls.append((keyword, limit))
        return FAKE_TRACKS[:limit]


def fake_resolve_metadata(url: str, **_: Any) -> dict[str, Any]:
    return {"title": "茉莉花", "duration": 125.0, "uploader": "几朵儿歌"}


#: ``DOWNLOAD_GATE`` is open by default so ordinary tests complete; the
#: rate-limit test closes it to hold a download in flight.
#: ``DOWNLOAD_STARTED`` signals that the fake download is actually running.
DOWNLOAD_GATE = threading.Event()
DOWNLOAD_STARTED = threading.Event()


def fake_run_download(
    url: str,
    *,
    target_dir: Path,
    output_stem: str,
    audio_format: str,
    progress_hook=None,
    postprocessor_hook=None,
    **_: Any,
) -> dict[str, Any]:
    DOWNLOAD_STARTED.set()
    DOWNLOAD_GATE.wait(timeout=10)
    if progress_hook is not None:
        progress_hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000})
    if postprocessor_hook is not None:
        postprocessor_hook({"status": "started", "name": "ExtractAudio"})
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{output_stem}.{audio_format}").write_bytes(b"RIFFfake-wave-data")
    return {"title": "茉莉花"}


class ApiTestCase(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pianoflow-test-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.settings = dataclasses.replace(
            Settings.from_env(),
            cache_dir=self._tmp / "cache",
            log_dir=self._tmp / "logs",
            download_min_interval_seconds=0.0,
            cache_max_bytes=5 * 1024 * 1024,
            cache_ttl_days=7,
        )
        self.services = build_services(self.settings)
        self.extractor = FakeExtractor()
        registry = ExtractorRegistry()
        registry.register(self.extractor)
        self.services.extractors = registry
        self.services.downloads._extractors = registry

        self._saved = (audio_module.resolve_metadata, audio_module.run_download)
        audio_module.resolve_metadata = fake_resolve_metadata
        audio_module.run_download = fake_run_download
        DOWNLOAD_GATE.set()
        DOWNLOAD_STARTED.clear()
        super().setUp()

    def tearDown(self) -> None:
        DOWNLOAD_GATE.set()
        audio_module.resolve_metadata, audio_module.run_download = self._saved
        super().tearDown()

    def get_app(self):
        return make_app(self.settings, self.services)

    # ------------------------------------------------------------- helpers
    def post_json(self, path: str, payload: dict[str, Any]):
        return self.fetch(
            path,
            method="POST",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )

    def poll_task(self, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
        async def _poll() -> dict[str, Any]:
            deadline = time.monotonic() + timeout
            url = self.get_url(f"/api/music/download-audio/{task_id}")
            while time.monotonic() < deadline:
                response = await self.http_client.fetch(url, raise_error=False)
                payload = json.loads(response.body)
                if payload.get("stage") in {"done", "failed"}:
                    return payload
                await gen.sleep(0.05)
            raise AssertionError(f"task {task_id} never finished")

        return self.io_loop.run_sync(_poll)

    def wait_until_download_started(self, timeout: float = 5.0) -> None:
        async def _wait() -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if DOWNLOAD_STARTED.is_set():
                    return
                await gen.sleep(0.02)
            raise AssertionError("fake download never started")

        self.io_loop.run_sync(_wait)

    # --------------------------------------------------------------- tests
    def test_health_reports_sources(self):
        response = self.fetch("/api/health")
        body = json.loads(response.body)

        self.assertEqual(response.code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual([s["name"] for s in body["sources"]], ["fake"])
        self.assertTrue(body["sources"][0]["available"])

    def test_search_returns_tracks(self):
        response = self.post_json(
            "/api/music/search", {"keyword": "茉莉花", "platform": "fake", "limit": 2}
        )
        body = json.loads(response.body)

        self.assertEqual(response.code, 200)
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["list"][0]["uid"], "fake:BV1")
        self.assertEqual(self.extractor.search_calls, [("茉莉花", 2)])

    def test_search_requires_keyword(self):
        response = self.post_json("/api/music/search", {"platform": "fake"})
        body = json.loads(response.body)

        self.assertEqual(response.code, 400)
        self.assertEqual(body["error"]["code"], "validation_error")

    def test_search_rejects_unknown_platform(self):
        response = self.post_json("/api/music/search", {"keyword": "茉莉花", "platform": "napster"})
        self.assertEqual(response.code, 503)

    def test_search_surfaces_platform_risk_control(self):
        async def blocked(keyword: str, limit: int):
            raise UpstreamBlockedError(
                "平台风控拦截了本次请求（HTTP 412）。", details={"retryAfterSeconds": 90}
            )

        self.extractor.search = blocked  # type: ignore[assignment]
        response = self.post_json("/api/music/search", {"keyword": "星辰大海", "platform": "fake"})
        body = json.loads(response.body)

        self.assertEqual(response.code, 503)
        self.assertEqual(body["error"]["code"], "upstream_blocked")
        self.assertEqual(response.headers["Retry-After"], "91")

    def test_search_defaults_to_configured_page_size(self):
        self.post_json("/api/music/search", {"keyword": "星辰大海", "platform": "fake"})
        keyword, limit = self.extractor.search_calls[-1]
        self.assertEqual(keyword, "星辰大海")
        self.assertEqual(limit, self.settings.search_default_limit)

    def test_search_limit_is_capped_by_settings(self):
        response = self.post_json(
            "/api/music/search",
            {"keyword": "星辰大海", "platform": "fake", "limit": 999},
        )
        self.assertEqual(response.code, 400)

    def test_download_flow_completes_and_caches(self):
        response = self.post_json(
            "/api/music/download-audio",
            {"source": "fake", "sourceId": "BV1", "format": "wav", "title": "茉莉花"},
        )
        body = json.loads(response.body)
        self.assertEqual(response.code, 202)
        self.assertEqual(body["stage"], "queued")

        final = self.poll_task(body["taskId"])
        self.assertEqual(final["stage"], "done")
        self.assertEqual(final["percent"], 100.0)
        self.assertFalse(final["result"]["fromCache"])
        self.assertTrue(Path(final["result"]["audioPath"]).is_file())

        second = self.post_json(
            "/api/music/download-audio",
            {"source": "fake", "sourceId": "BV1", "format": "wav"},
        )
        cached = self.poll_task(json.loads(second.body)["taskId"])
        self.assertEqual(cached["stage"], "done")
        self.assertTrue(cached["result"]["fromCache"])

    def test_download_rejects_unknown_format(self):
        response = self.post_json(
            "/api/music/download-audio",
            {"source": "fake", "sourceId": "BV1", "format": "flac"},
        )
        body = json.loads(response.body)
        self.assertEqual(response.code, 400)
        self.assertEqual(body["error"]["code"], "validation_error")

    def test_download_rejects_source_without_download_support(self):
        registry = ExtractorRegistry()
        registry.register(FakeExtractor(downloadable=False))
        self.services.downloads._extractors = registry

        response = self.post_json(
            "/api/music/download-audio", {"source": "fake", "sourceId": "BV9"}
        )
        self.assertEqual(response.code, 503)

    def test_download_rejects_empty_source_id(self):
        response = self.post_json("/api/music/download-audio", {"source": "fake", "sourceId": "  "})
        self.assertEqual(response.code, 400)

    def test_duplicate_download_is_rate_limited(self):
        DOWNLOAD_GATE.clear()
        DOWNLOAD_STARTED.clear()
        self.addCleanup(DOWNLOAD_GATE.set)

        first = self.post_json("/api/music/download-audio", {"source": "fake", "sourceId": "BV7"})
        self.assertEqual(first.code, 202)
        self.wait_until_download_started()

        second = self.post_json("/api/music/download-audio", {"source": "fake", "sourceId": "BV7"})
        self.assertEqual(second.code, 429)
        self.assertEqual(second.headers["Retry-After"], "3")

    def test_failed_download_reports_upstream_error(self):
        def exploding(*_: Any, **__: Any) -> dict[str, Any]:
            raise UpstreamError("yt-dlp download failed: boom")

        audio_module.run_download = exploding
        try:
            response = self.post_json(
                "/api/music/download-audio", {"source": "fake", "sourceId": "BV8"}
            )
            final = self.poll_task(json.loads(response.body)["taskId"])
        finally:
            audio_module.run_download = fake_run_download

        self.assertEqual(final["stage"], "failed")
        self.assertEqual(final["error"]["code"], "upstream_error")

    def test_unknown_task_snapshot_is_404(self):
        response = self.fetch("/api/music/download-audio/" + "0" * 32, raise_error=False)
        self.assertEqual(response.code, 404)
        self.assertEqual(json.loads(response.body)["error"]["code"], "not_found")

    def test_stream_requires_task_id(self):
        response = self.fetch("/api/music/download-audio/stream", raise_error=False)
        body = json.loads(response.body)
        self.assertEqual(response.code, 400)
        self.assertEqual(body["error"]["code"], "validation_error")

    def test_stream_emits_events_until_terminal(self):
        response = self.post_json(
            "/api/music/download-audio", {"source": "fake", "sourceId": "BV3"}
        )
        task_id = json.loads(response.body)["taskId"]

        async def _read() -> str:
            url = self.get_url(f"/api/music/download-audio/stream?task_id={task_id}")
            self._buffer = bytearray()
            await self.http_client.fetch(url, streaming_callback=self._on_chunk, request_timeout=20)
            return self._buffer.decode("utf-8", "replace")

        raw = self.io_loop.run_sync(_read)
        self.assertIn("event: done", raw)
        self.assertIn('"percent": 100.0', raw)

    def _on_chunk(self, chunk: bytes) -> None:
        self._buffer = getattr(self, "_buffer", bytearray()) + chunk

    def test_cache_status_and_clean(self):
        self.post_json("/api/music/download-audio", {"source": "fake", "sourceId": "BV4"})
        status = json.loads(self.fetch("/api/cache/status").body)
        self.assertGreaterEqual(status["entryCount"], 1)

        bad = self.post_json("/api/cache/clean", {"mode": "nuke"})
        self.assertEqual(bad.code, 400)

        cleaned = self.post_json("/api/cache/clean", {"mode": "cleanup"})
        self.assertEqual(cleaned.code, 200)
        self.assertIn("deletedCount", json.loads(cleaned.body))

        cleared = json.loads(self.post_json("/api/cache/clean", {"mode": "clear"}).body)
        self.assertEqual(cleared["remainingEntries"], 0)

    def test_lyrics_endpoint_passes_the_word_level_through(self):
        async def fake_lyrics(source_id: str):
            return {"lrc": "[00:12] 从前从前", "klyric": "[[0,260,4]]"}

        self.extractor.lyrics = fake_lyrics
        response = self.fetch("/api/music/lyrics?source=fake&sourceId=BV1")
        self.assertEqual(response.code, 200)
        body = json.loads(response.body)
        self.assertEqual(body["source"], "fake")
        self.assertEqual(body["lrc"], "[00:12] 从前从前")
        self.assertEqual(body["klyric"], "[[0,260,4]]")

    def test_lyrics_endpoint_empty_when_the_source_has_none(self):
        response = self.fetch("/api/music/lyrics?source=fake&sourceId=BV1")
        self.assertEqual(response.code, 200)
        body = json.loads(response.body)
        self.assertEqual(body["lrc"], "")
        self.assertNotIn("klyric", body)
        self.assertNotIn("krc", body)

    def test_lyrics_endpoint_requires_both_parameters(self):
        response = self.fetch("/api/music/lyrics?source=fake", raise_error=False)
        self.assertEqual(response.code, 400)

    def test_malformed_json_body(self):
        response = self.fetch(
            "/api/music/search",
            method="POST",
            body=b"{not json",
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )
        self.assertEqual(response.code, 400)

    def test_cors_preflight_for_vite_dev_server(self):
        response = self.fetch(
            "/api/music/search",
            method="OPTIONS",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:5173")

    def test_frontend_build_is_served(self):
        response = self.fetch("/some/spa/route", raise_error=False)
        self.assertEqual(response.code, 200)
        self.assertIn(b"PianoFlow", response.body)
