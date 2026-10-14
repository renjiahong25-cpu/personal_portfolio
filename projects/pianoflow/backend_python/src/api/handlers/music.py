"""Music endpoints: search, download submission, SSE progress, task snapshot."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from core.errors import AppError, ValidationError
from services.download.audio_service import DownloadRequest
from tasks.models import TaskStage

from .base import BaseHandler, api_endpoint

logger = logging.getLogger(__name__)

SSE_KEEPALIVE_SECONDS = 15.0


class SearchHandler(BaseHandler):
    """POST /api/music/search"""

    @api_endpoint
    async def post(self) -> None:
        payload = self.json_body()
        keyword = self.require_str(payload, "keyword")
        settings = self.services.settings
        platform = str(payload.get("platform") or "").strip().lower()
        limit = self.optional_int(
            payload,
            "limit",
            settings.search_default_limit,
            low=1,
            high=settings.search_max_limit,
        )

        waited = await self.services.search_throttle.wait()
        logger.info(
            "search platform=%s limit=%d throttled=%.1fs", platform or "default", limit, waited
        )

        registry = self.services.extractors
        extractor = registry.get(platform) if platform else registry.all()[0]
        started = time.monotonic()
        tracks = await extractor.search(keyword, limit)
        elapsed = time.monotonic() - started
        logger.info(
            "search done platform=%s keyword=%s results=%d elapsed=%.1fs",
            extractor.name,
            keyword,
            len(tracks),
            elapsed,
        )
        self.write_json(
            {
                "keyword": keyword,
                "platform": extractor.name,
                "count": len(tracks),
                "elapsedSeconds": round(elapsed, 2),
                "list": [track.to_dict() for track in tracks],
            }
        )


class LyricsHandler(BaseHandler):
    """GET /api/music/lyrics?source=netease&sourceId=... -> lyrics payload.

    The response always carries ``lrc`` (plain LRC text, possibly empty) plus
    whatever per-word data the source had: ``klyric`` (NetEase karaoke JSON)
    and/or ``krc`` (KuGou karaoke text). The immersive fullscreen player uses
    the word level when present and falls back to even-splitting the LRC.
    Sidecar-backed sources resolve lyrics locally (NetEase without login,
    KuGou with the account cookie); a source with no lyrics at all answers an
    empty string, not an error, so the player can show "no lyrics" instead of
    treating the absence as a failure.
    """

    @api_endpoint
    async def get(self) -> None:
        source = self.get_query_argument("source", "").strip().lower()
        source_id = self.get_query_argument("sourceId", "").strip()
        if not source or not source_id:
            raise ValidationError("query parameters 'source' and 'sourceId' are required")
        extractor = self.services.extractors.get(source)
        payload = await extractor.lyrics(source_id) or {}
        body: dict[str, Any] = {"source": source, "sourceId": source_id, "lrc": payload.get("lrc") or ""}
        for key in ("klyric", "krc"):
            if payload.get(key):
                body[key] = payload[key]
        self.write_json(body)


class DownloadHandler(BaseHandler):
    """POST /api/music/download-audio -> 202 Accepted with a task id."""

    @api_endpoint
    async def post(self) -> None:
        payload = self.json_body()
        source = self.require_str(payload, "source")
        source_id = self.require_str(payload, "sourceId")
        audio_format = str(payload.get("format") or "wav").strip().lower()
        title = payload.get("title")
        artist = payload.get("artist")
        thumbnail = payload.get("thumbnail")
        duration_raw = payload.get("duration")
        try:
            duration = float(duration_raw) if duration_raw is not None else None
        except (TypeError, ValueError):
            duration = None
        request = DownloadRequest(
            source=source,
            source_id=source_id,
            audio_format=audio_format,
            title=title if isinstance(title, str) else None,
            artist=artist if isinstance(artist, str) else None,
            thumbnail=thumbnail if isinstance(thumbnail, str) and thumbnail else None,
            duration=duration if duration and duration > 0 else None,
        )
        snapshot = await self.services.downloads.submit(request)
        self.write_json(snapshot, status=202)


class QueueHandler(BaseHandler):
    """GET /api/music/download-audio/queue -> what is running / waiting."""

    @api_endpoint
    async def get(self) -> None:
        self.write_json(self.services.downloads.queue_snapshot())


class DownloadTaskHandler(BaseHandler):
    """GET /api/music/download-audio/<task_id> -> polling fallback for SSE."""

    @api_endpoint
    async def get(self, task_id: str) -> None:
        self.write_json(self.services.downloads.get_task(task_id))


class EventsHandler(BaseHandler):
    """GET /api/events -> one SSE stream carrying every task update.

    The UI used to open one stream per download. Browsers cap concurrent
    connections per origin (6 under HTTP/1.1), so a handful of downloads locked
    out every other request and the page appeared frozen. One multiplexed
    stream removes that failure mode entirely.
    """

    MAX_CLIENTS = 4
    KEEPALIVE_SECONDS = 15.0

    async def get(self) -> None:
        tasks = self.services.tasks
        if tasks.global_subscriber_count() >= self.MAX_CLIENTS:
            self._fail(
                AppError(
                    "事件流连接数已达上限，请刷新页面重试",
                    code="too_many_streams",
                    status=503,
                )
            )
            return

        queue = tasks.subscribe_all()
        self.set_status(200)
        self.set_header("Content-Type", "text/event-stream; charset=UTF-8")
        self.set_header("Cache-Control", "no-cache, no-transform")
        self.set_header("Connection", "keep-alive")
        self.set_header("X-Accel-Buffering", "no")
        connection = self.request.connection
        if connection is not None and hasattr(connection, "set_nodelay"):
            connection.set_nodelay(True)

        try:
            while True:
                try:
                    snapshot = await asyncio.wait_for(queue.get(), timeout=self.KEEPALIVE_SECONDS)
                except TimeoutError:
                    self.write(": keepalive\n\n")
                    await self.flush()
                    continue
                stage = snapshot["stage"]
                self.write(f"event: {stage}\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n")
                await self.flush()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - client disconnects land here
            logger.info("event stream closed: %s", exc)
        finally:
            tasks.unsubscribe_all(queue)

    def _fail(self, exc: AppError) -> None:
        self.set_status(exc.status)
        self.write_json(exc.to_dict())


class DownloadStreamHandler(BaseHandler):
    """GET /api/music/download-audio/stream?task_id=... -> Server-Sent Events."""

    async def get(self) -> None:
        task_id = self.get_argument("task_id", "").strip()
        if not task_id:
            self._fail(ValidationError("query parameter 'task_id' is required"))
            return

        try:
            queue = self.services.tasks.subscribe(task_id)
        except AppError as exc:
            self._fail(exc)
            return

        self.set_status(200)
        self.set_header("Content-Type", "text/event-stream; charset=UTF-8")
        self.set_header("Cache-Control", "no-cache, no-transform")
        self.set_header("Connection", "keep-alive")
        self.set_header("X-Accel-Buffering", "no")
        # Disable Nagle so each SSE frame leaves immediately.
        connection = self.request.connection
        if connection is not None and hasattr(connection, "set_nodelay"):
            connection.set_nodelay(True)

        tasks = self.services.tasks
        try:
            while True:
                try:
                    snapshot = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SECONDS)
                except TimeoutError:
                    self.write(": keepalive\n\n")
                    await self.flush()
                    continue

                stage = snapshot["stage"]
                payload = json.dumps(snapshot, ensure_ascii=False)
                self.write(f"event: {stage}\ndata: {payload}\n\n")
                await self.flush()
                if stage in _TERMINAL_VALUES:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - client disconnects land here
            logger.info("SSE stream for task %s closed: %s", task_id, exc)
        finally:
            tasks.unsubscribe(task_id, queue)

    def _fail(self, exc: AppError) -> None:
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            self.set_header("Retry-After", str(int(retry_after) + 1))
        self.set_status(exc.status)
        self.write_json(exc.to_dict())


_TERMINAL_VALUES = frozenset(stage.value for stage in TaskStage if stage.is_terminal)


__all__ = [
    "DownloadHandler",
    "DownloadStreamHandler",
    "DownloadTaskHandler",
    "EventsHandler",
    "LyricsHandler",
    "QueueHandler",
    "SearchHandler",
]
