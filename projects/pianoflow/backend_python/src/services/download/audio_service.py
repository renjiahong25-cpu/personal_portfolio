"""Download + transcode pipeline for a single track.

Every stage is reported through :class:`~src.tasks.manager.TaskManager` so the
HTTP layer can stream it over SSE. yt-dlp itself is blocking, so it always runs
on a worker thread (``asyncio.to_thread``) and reports progress back through
``TaskManager.update_from_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.config import Settings
from core.errors import (
    AppError,
    ComplianceError,
    ExtractorUnavailableError,
    RateLimitedError,
    ValidationError,
)
from services.cache.manager import CacheEntry, CacheManager
from services.download.direct import fetch_audio_url, transcode_to_wav
from services.music_source.base import Extractor
from services.music_source.registry import ExtractorRegistry
from services.music_source.ytdlp_client import resolve_metadata, run_download
from tasks.manager import TaskManager
from tasks.models import TaskStage

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = ("wav", "mp3")

#: How many downloads may wait behind the active one before we push back.
MAX_QUEUE_DEPTH = 5

#: How the 0-100 progress bar is divided between the phases.
_DOWNLOAD_SPAN = 62.0
_DOWNLOAD_FLOOR = 8.0


@dataclass(frozen=True)
class DownloadRequest:
    source: str
    source_id: str
    audio_format: str = "wav"
    title: str | None = None
    #: Metadata the search leg already knew. The direct-URL leg has no
    #: metadata step, so without these the cache entry would be bare.
    artist: str | None = None
    duration: float | None = None
    #: Cover image URL, persisted so the library wall can render art.
    thumbnail: str | None = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sourceId": self.source_id,
            "format": self.audio_format,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
        }


class AudioDownloadService:
    def __init__(
        self,
        settings: Settings,
        cache: CacheManager,
        tasks: TaskManager,
        extractors: ExtractorRegistry,
        http: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._tasks = tasks
        self._extractors = extractors
        self._http = http
        self._concurrency = asyncio.Semaphore(max(1, settings.download_max_concurrency))
        self._last_started_at: float | None = None
        self._running_uids: set[str] = set()
        self._queue: list[str] = []
        self._background_tasks: set[asyncio.Task] = set()

    def queue_snapshot(self) -> dict[str, Any]:
        """Current queue depth, exposed for the UI and for debugging."""
        depth = len(self._queue)
        return {
            # The semaphore is locked exactly while a task holds it.
            "active": self._concurrency.locked() and bool(self._running_uids),
            "queued": max(0, depth - 1) if depth else 0,
            "queue": list(self._queue),
            "runningUids": sorted(self._running_uids),
        }

    # ------------------------------------------------------------------ api
    def validate(self, request: DownloadRequest) -> None:
        if not request.source_id or not request.source_id.strip():
            raise ValidationError("sourceId must not be empty")
        if len(request.source_id) > 128:
            raise ValidationError("sourceId is too long")
        if request.audio_format not in SUPPORTED_FORMATS:
            raise ValidationError(
                f"unsupported format {request.audio_format!r}",
                details={"supported": list(SUPPORTED_FORMATS)},
            )

    async def submit(self, request: DownloadRequest) -> dict[str, Any]:
        """Register a task and start it in the background."""
        self.validate(request)
        extractor = self._extractors.get(request.source)
        if not extractor.supports_download:
            raise ExtractorUnavailableError(
                f"source {request.source!r} cannot provide audio downloads",
                details={"available": self._extractors.names()},
            )

        uid = f"{request.source}:{request.source_id}"
        if uid in self._running_uids:
            raise RateLimitedError("this track is already downloading", retry_after=2.0)

        record = self._tasks.create("download_audio", request.to_meta())
        # Enqueue synchronously: the depth check and the append must not be
        # separated by an await, otherwise two concurrent submits both see an
        # empty queue and the cap is exceeded.
        if len(self._queue) >= MAX_QUEUE_DEPTH:
            self._tasks.fail(
                record.id,
                RateLimitedError(
                    "下载队列已满，请等前面的任务完成",
                    retry_after=10.0,
                    details={"queueDepth": len(self._queue)},
                ).to_dict()["error"],
                message="queue full",
            )
            raise RateLimitedError(
                "下载队列已满，请等前面的任务完成",
                retry_after=10.0,
                details={"queueDepth": len(self._queue)},
            )

        self._running_uids.add(uid)
        self._queue.append(record.id)
        # Keep a strong reference: without it the event loop may garbage
        # collect a running task and the download silently stops.
        task = asyncio.create_task(
            self._run(record.id, request, extractor),
            name=f"download:{record.id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return record.snapshot()

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._tasks.get(task_id).snapshot()

    # -------------------------------------------------------------- workers
    async def _run(self, task_id: str, request: DownloadRequest, extractor: Extractor) -> None:
        # The task id was already appended to the queue by submit(); by the time
        # this coroutine runs, earlier entries may have finished.
        try:
            # Downloads are serialised on purpose (personal-use throttling), so
            # a second click must visibly *queue* instead of looking stuck.
            if self._concurrency.locked():
                position = self._queue.index(task_id)
                if position > 0:
                    self._tasks.update(
                        task_id,
                        message=f"排队中（队列第 {position} 位，前面还有 {position} 个任务）",
                    )
            async with self._concurrency:
                await self._throttle(task_id)
                result = await self._pipeline(task_id, request, extractor)
            self._tasks.update(
                task_id,
                stage=TaskStage.DONE,
                percent=100.0,
                message="done",
                result=result,
            )
        except AppError as exc:
            logger.info("task %s failed: %s", task_id, exc.message)
            self._tasks.fail(task_id, exc.to_dict()["error"], message=exc.message)
        except asyncio.CancelledError:
            self._tasks.update(
                task_id,
                stage=TaskStage.CANCELLED,
                message="cancelled",
                error={"code": "cancelled", "message": "task cancelled"},
            )
            raise
        except Exception as exc:
            logger.exception("task %s crashed", task_id)
            self._tasks.fail(
                task_id,
                {"code": "internal_error", "message": str(exc)},
                message="internal error",
            )
        finally:
            self._running_uids.discard(f"{request.source}:{request.source_id}")
            if task_id in self._queue:
                self._queue.remove(task_id)

    async def _pipeline(
        self, task_id: str, request: DownloadRequest, extractor: Extractor
    ) -> dict[str, Any]:
        cache = self._cache
        cached = cache.lookup(request.source, request.source_id, request.audio_format)
        if cached is not None:
            self._tasks.update(
                task_id,
                stage=TaskStage.RESOLVING,
                message="cache hit",
            )
            return self._result(cached, cache.root, from_cache=True)

        # Sidecar-backed sources (NetEase/KuGou) hand back a plain CDN URL:
        # fetch it directly instead of paying for a yt-dlp round trip. Bilibili
        # returns None (signed URL) and falls through to the yt-dlp path below.
        direct_url = await extractor.audio_url(request.source_id)
        if direct_url is not None:
            return await self._pipeline_direct(task_id, request, extractor, direct_url)
        return await self._pipeline_ytdlp(task_id, request, extractor)

    async def _pipeline_direct(
        self,
        task_id: str,
        request: DownloadRequest,
        extractor: Extractor,
        direct_url: str,
    ) -> dict[str, Any]:
        cache = self._cache
        key = cache.make_key(request.source, request.source_id, request.audio_format)
        self._tasks.update(
            task_id,
            stage=TaskStage.DOWNLOADING,
            percent=_DOWNLOAD_FLOOR,
            message="downloading audio",
        )

        def on_progress(ratio: float) -> None:
            self._tasks.update_from_thread(
                task_id,
                stage=TaskStage.DOWNLOADING,
                percent=_DOWNLOAD_FLOOR + _DOWNLOAD_SPAN * ratio,
                message="downloading audio",
            )

        native = cache.root / f"{key}.mp3"
        await fetch_audio_url(self._http, direct_url, native, on_progress=on_progress)

        if request.audio_format == "mp3":
            audio_file = native
        else:
            self._tasks.update(
                task_id,
                stage=TaskStage.TRANSCODING,
                percent=85.0,
                message=f"transcoding to {request.audio_format}",
            )
            converted = cache.root / f"{key}.{request.audio_format}"
            await asyncio.to_thread(
                transcode_to_wav, self._settings.ffmpeg_dir_or_none(), native, converted
            )
            native.unlink(missing_ok=True)
            audio_file = converted

        entry = cache.register(
            source=request.source,
            source_id=request.source_id,
            audio_format=request.audio_format,
            title=request.title or request.source_id,
            artist=request.artist,
            duration=request.duration,
            audio_file=audio_file,
            webpage_url=extractor.canonical_url(request.source_id),
            thumbnail=request.thumbnail,
        )
        return self._result(entry, cache.root, from_cache=False)

    async def _pipeline_ytdlp(
        self, task_id: str, request: DownloadRequest, extractor: Extractor
    ) -> dict[str, Any]:
        cache = self._cache
        url = extractor.canonical_url(request.source_id)
        # Prefer the platform's own cookie jar (real buvid3/buvid4) over nothing:
        # yt-dlp downloads hit the same risk control as search does.
        cookie_file = getattr(extractor, "cookie_file", lambda: None)() or None

        self._tasks.update(
            task_id,
            stage=TaskStage.RESOLVING,
            message="resolving track metadata",
        )
        info = await asyncio.to_thread(
            resolve_metadata,
            url,
            ffmpeg_location=self._settings.ffmpeg_dir_or_none(),
            socket_timeout=self._settings.ytdlp_socket_timeout_seconds,
            retries=self._settings.ytdlp_retries,
            cookie_file=cookie_file,
        )
        # The search leg already knew these; the platform's metadata wins when
        # it has its own reading of them.
        duration = _as_float(info.get("duration")) or request.duration
        title = str(info.get("title") or request.title or request.source_id)
        uploader = info.get("uploader") or info.get("channel") or request.artist

        if duration and duration > self._settings.max_audio_seconds:
            raise ComplianceError(
                "track is longer than the configured single-track limit",
                details={
                    "durationSeconds": duration,
                    "maxAudioSeconds": self._settings.max_audio_seconds,
                },
            )

        key = cache.make_key(request.source, request.source_id, request.audio_format)
        self._tasks.update(
            task_id,
            stage=TaskStage.DOWNLOADING,
            percent=_DOWNLOAD_FLOOR,
            message="downloading audio",
        )

        def progress_hook(status: dict[str, Any]) -> None:
            if status.get("status") != "downloading":
                return
            ratio = _progress_ratio(status)
            percent = _DOWNLOAD_FLOOR + _DOWNLOAD_SPAN * ratio
            speed = status.get("speed")
            message = "downloading audio"
            if speed:
                message = f"downloading audio ({speed / 1024 / 1024:.2f} MiB/s)"
            self._tasks.update_from_thread(
                task_id,
                stage=TaskStage.DOWNLOADING,
                percent=percent,
                message=message,
            )

        def postprocessor_hook(status: dict[str, Any]) -> None:
            if status.get("status") == "started":
                self._tasks.update_from_thread(
                    task_id,
                    stage=TaskStage.TRANSCODING,
                    percent=75.0,
                    message=f"transcoding to {request.audio_format}",
                )

        await asyncio.to_thread(
            run_download,
            url,
            target_dir=cache.root,
            output_stem=key,
            audio_format=request.audio_format,
            ffmpeg_location=self._settings.ffmpeg_dir_or_none(),
            socket_timeout=self._settings.ytdlp_socket_timeout_seconds,
            retries=self._settings.ytdlp_retries,
            cookie_file=cookie_file,
            progress_hook=progress_hook,
            postprocessor_hook=postprocessor_hook,
        )

        audio_file = self._locate_output(cache.root, key, request.audio_format)
        entry = cache.register(
            source=request.source,
            source_id=request.source_id,
            audio_format=request.audio_format,
            title=title,
            artist=str(uploader) if uploader else None,
            duration=duration,
            audio_file=audio_file,
            webpage_url=url,
            thumbnail=request.thumbnail,
        )
        return self._result(entry, cache.root, from_cache=False)

    async def _throttle(self, task_id: str) -> None:
        """Personal-use throttling: never start two downloads back to back."""
        interval = self._settings.download_min_interval_seconds
        if interval <= 0:
            self._last_started_at = time.monotonic()
            return
        now = time.monotonic()
        if self._last_started_at is not None:
            wait = interval - (now - self._last_started_at)
            if wait > 0:
                if wait > 60:
                    raise RateLimitedError(
                        "too many download requests, slow down",
                        retry_after=round(wait, 2),
                    )
                self._tasks.update(task_id, message=f"throttled, waiting {wait:.1f}s")
                await asyncio.sleep(wait)
        self._last_started_at = time.monotonic()

    @staticmethod
    def _locate_output(root: Path, key: str, audio_format: str) -> Path:
        expected = root / f"{key}.{audio_format}"
        if expected.is_file() and expected.stat().st_size > 0:
            return expected
        candidates = [
            path
            for path in root.glob(f"{key}.*")
            if path.suffix != ".json" and path.is_file() and path.stat().st_size > 0
        ]
        if not candidates:
            raise ValidationError("yt-dlp produced no audio file")
        return max(candidates, key=lambda path: path.stat().st_size)

    @staticmethod
    def _result(entry: CacheEntry, root: Path, *, from_cache: bool) -> dict[str, Any]:
        return {
            "audioPath": str(root / entry.filename),
            "audioUrl": f"/api/cache/audio/{entry.key}",
            "cacheKey": entry.key,
            "source": entry.source,
            "sourceId": entry.source_id,
            "title": entry.title,
            "artist": entry.artist,
            "duration": entry.duration,
            "format": entry.audio_format,
            "sizeBytes": entry.size_bytes,
            "webpageUrl": entry.webpage_url,
            "fromCache": from_cache,
        }


def _progress_ratio(status: dict[str, Any]) -> float:
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    downloaded = status.get("downloaded_bytes")
    if not total or downloaded is None:
        return 0.0
    return max(0.0, min(1.0, float(downloaded) / float(total)))


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


__all__ = ["SUPPORTED_FORMATS", "AudioDownloadService", "DownloadRequest"]
