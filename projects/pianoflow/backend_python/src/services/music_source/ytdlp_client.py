"""Thin, blocking-safe wrapper around yt-dlp.

yt-dlp is synchronous and CPU/IO heavy, so every call here is meant to be
executed through ``asyncio.to_thread`` (see
:class:`~src.services.music_source.ytdlp_client.YtdlpClient`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from core.errors import AppError, UpstreamBlockedError, UpstreamError

ProgressHook = Callable[[dict[str, Any]], None]


def base_ydl_options(
    *,
    ffmpeg_location: str | None,
    socket_timeout: float,
    retries: int,
    cookie_file: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "retries": retries,
        "socket_timeout": socket_timeout,
        "noplaylist": True,
    }
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location
    if cookie_file and Path(cookie_file).is_file():
        options["cookiefile"] = cookie_file
    return options


def _translate(exc: Exception, action: str) -> AppError:
    """Map yt-dlp failures onto typed, actionable API errors."""
    message = str(exc)
    # Bilibili (and other CN platforms) answer abuse with HTTP 412.
    if "412" in message or "Precondition Failed" in message:
        return UpstreamBlockedError(
            "平台风控拦截了本次请求（HTTP 412）。请等待 1-2 分钟再试，"
            "或配置 PIANOFLOW_YTDLP_COOKIE_FILE 降低被拦概率。",
            retry_after=90.0,
        )
    if "Private video" in message or "members-only" in message:
        return UpstreamError("该视频是会员/私有内容，无法使用")
    if "Video unavailable" in message or "not available" in message:
        return UpstreamError("该视频已不可用，请换一个结果")
    return UpstreamError(f"yt-dlp {action} 失败: {message}")


def run_search(
    query: str,
    *,
    ffmpeg_location: str | None,
    socket_timeout: float,
    retries: int,
    cookie_file: str | None = None,
) -> list[dict[str, Any]]:
    """Run a yt-dlp search and return fully resolved entries.

    ``extract_flat`` must NOT be used here: for ``bilisearch`` it yields bare
    ids with empty ``title``/``duration``/``thumbnails``, which is useless for
    preview cards. Full resolution costs roughly one second per entry, which is
    why the caller keeps the page size small and throttles requests.
    """
    options = base_ydl_options(
        ffmpeg_location=ffmpeg_location,
        socket_timeout=socket_timeout,
        retries=retries,
        cookie_file=cookie_file,
    )
    options.update({"skip_download": True})
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(query, download=False)
    except DownloadError as exc:
        raise _translate(exc, "搜索") from exc
    except Exception as exc:
        raise _translate(exc, "搜索") from exc

    if not info:
        return []
    entries = info.get("entries") or []
    return [entry for entry in entries if entry]


def resolve_metadata(
    url: str,
    *,
    ffmpeg_location: str | None,
    socket_timeout: float,
    retries: int,
    cookie_file: str | None = None,
) -> dict[str, Any]:
    """Fetch a single track's metadata without downloading any bytes.

    Used to learn the real duration (and therefore enforce the compliance
    duration cap) *before* a download starts.
    """
    options = base_ydl_options(
        ffmpeg_location=ffmpeg_location,
        socket_timeout=socket_timeout,
        retries=retries,
        cookie_file=cookie_file,
    )
    options.update({"skip_download": True, "noplaylist": True})
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise _translate(exc, "解析") from exc
    except Exception as exc:
        raise _translate(exc, "解析") from exc
    if not info:
        raise UpstreamError(f"yt-dlp 没有返回 {url} 的元数据")
    return info


def run_download(
    url: str,
    *,
    target_dir: Path,
    output_stem: str,
    audio_format: str,
    ffmpeg_location: str | None,
    socket_timeout: float,
    retries: int,
    cookie_file: str | None = None,
    progress_hook: ProgressHook | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Download one track, transcode it, and return yt-dlp's info dict."""
    options = base_ydl_options(
        ffmpeg_location=ffmpeg_location,
        socket_timeout=socket_timeout,
        retries=retries,
        cookie_file=cookie_file,
    )
    options.update(
        {
            "format": "bestaudio/best",
            "outtmpl": str(target_dir / f"{output_stem}.%(ext)s"),
            "restrictfilenames": True,
            "overwrites": True,
            "noplaylist": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "0",
                }
            ],
            "progress_hooks": [progress_hook] if progress_hook else [],
            "postprocessor_hooks": [postprocessor_hook] if postprocessor_hook else [],
        }
    )
    try:
        with YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=True) or {}
    except DownloadError as exc:
        raise _translate(exc, "下载") from exc
    except Exception as exc:
        raise _translate(exc, "下载") from exc


__all__ = [
    "base_ydl_options",
    "resolve_metadata",
    "run_download",
    "run_search",
]
