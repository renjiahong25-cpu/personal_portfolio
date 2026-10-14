"""Direct-URL download leg for sidecar-backed sources.

yt-dlp earns its keep on Bilibili (signed playurl, risk control), but a
NetEase/KuGou CDN link is a plain mp3 URL: fetching it with httpx is faster,
avoids a second dependency layer, and keeps progress reporting in one place.
The file lands in the cache as ``<key>.mp3``; when the caller asked for WAV
the ffmpeg transcode follows the same target spec the pipeline expects
(22050 Hz mono, what the transcriber consumes).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

from core.errors import UpstreamError

logger = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024

#: The pipeline's analysis spec (see render.py): mono 22050 Hz.
_TARGET_SAMPLE_RATE = 22050
_TARGET_CHANNELS = 1


async def fetch_audio_url(
    client: httpx.AsyncClient,
    url: str,
    target: Path,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> int:
    """Stream ``url`` to ``target``. Returns the number of bytes written."""
    try:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise UpstreamError(
                    f"音频地址返回 HTTP {response.status_code}", details={"url": url}
                )
            total_raw = response.headers.get("content-length")
            total = int(total_raw) if total_raw and total_raw.isdigit() else None
            written = 0
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
                    handle.write(chunk)
                    written += len(chunk)
                    if on_progress is not None and total:
                        on_progress(min(1.0, written / total))
    except httpx.HTTPError as exc:
        target.unlink(missing_ok=True)
        raise UpstreamError(f"下载音频失败：{exc}", details={"url": url}) from exc
    if written == 0:
        target.unlink(missing_ok=True)
        raise UpstreamError("音频地址没有返回任何字节", details={"url": url})
    logger.info("direct download complete: %s (%d bytes)", target.name, written)
    return written


def resolve_ffmpeg(ffmpeg_dir: str | None) -> Path | None:
    """The ffmpeg binary: the configured directory first, then PATH."""
    if ffmpeg_dir:
        root = Path(ffmpeg_dir)
        for name in ("ffmpeg.exe", "ffmpeg"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def transcode_to_wav(ffmpeg_dir: str | None, source: Path, target: Path) -> None:
    """Blocking mp3→wav transcode; the caller runs it in a worker thread."""
    ffmpeg = resolve_ffmpeg(ffmpeg_dir)
    if ffmpeg is None:
        raise UpstreamError("ffmpeg 不可用，无法转成 WAV", details={"source": str(source)})
    command = [
        str(ffmpeg),
        "-y",
        "-i",
        str(source),
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(_TARGET_SAMPLE_RATE),
        "-ac",
        str(_TARGET_CHANNELS),
        str(target),
    ]
    proc = subprocess.run(command, capture_output=True, timeout=600)
    if proc.returncode != 0 or not target.is_file():
        stderr = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise UpstreamError("ffmpeg 转码失败", details={"stderr": stderr})


__all__ = ["fetch_audio_url", "resolve_ffmpeg", "transcode_to_wav"]
