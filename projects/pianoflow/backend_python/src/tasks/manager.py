"""In-memory task registry plus a fan-out broker for Server-Sent Events.

Design constraints that shaped this module:

* yt-dlp progress hooks fire on a worker thread, therefore updates coming from
  outside the event loop must be marshalled with ``call_soon_threadsafe``.
* A late SSE subscriber must immediately receive the current snapshot, then
  every subsequent change, then exactly one terminal event.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from core.errors import NotFoundError

from .models import TaskRecord, TaskStage

logger = logging.getLogger(__name__)

#: Percentages reserved for each phase so the progress bar never jumps back.
STAGE_FLOORS: dict[TaskStage, float] = {
    TaskStage.QUEUED: 0.0,
    TaskStage.RESOLVING: 2.0,
    TaskStage.DOWNLOADING: 8.0,
    TaskStage.TRANSCODING: 72.0,
    TaskStage.DONE: 100.0,
}


class TaskManager:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        history_limit: int = 200,
    ) -> None:
        self._clock = clock
        self._history_limit = history_limit
        self._tasks: dict[str, TaskRecord] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._global_subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sequence = 0

    # ----------------------------------------------------------- lifecycle
    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def _ensure_loop(self) -> None:
        """Bind the running loop lazily.

        ``create`` is always called from the event loop thread, so this makes
        the manager usable without an explicit bootstrap call (tests, scripts)
        while keeping worker-thread updates safe.
        """
        if self._loop is not None:
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - called outside a loop
            logger.debug("no running event loop available for task manager")

    def create(self, kind: str, meta: dict[str, Any] | None = None) -> TaskRecord:
        self._ensure_loop()
        self._sequence += 1
        record = TaskRecord(
            id=uuid.uuid4().hex,
            kind=kind,
            stage=TaskStage.QUEUED,
            percent=0.0,
            message="queued",
            created_at=self._clock(),
            updated_at=self._clock(),
            meta=dict(meta or {}),
            sequence=self._sequence,
        )
        self._tasks[record.id] = record
        self._prune_history()
        self._publish(record)
        return record

    def update(
        self,
        task_id: str,
        *,
        stage: TaskStage | None = None,
        percent: float | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> TaskRecord:
        record = self.get(task_id)
        if record.stage.is_terminal:
            logger.debug("ignoring update for finished task %s", task_id)
            return record

        if stage is not None:
            # Accept a plain string as well: TaskStage is a str Enum, and
            # snapshot() assumes the field is a real enum member.
            record.stage = stage if isinstance(stage, TaskStage) else TaskStage(stage)
            floor = STAGE_FLOORS.get(record.stage, 0.0)
            record.percent = max(record.percent, floor)
        if percent is not None:
            record.percent = _clamp(percent)
        if message is not None:
            record.message = message
        if result is not None:
            record.result = result
        if error is not None:
            record.error = error
        record.updated_at = self._clock()

        if record.stage is TaskStage.DONE:
            record.percent = 100.0
        self._publish(record)
        return record

    def update_from_thread(self, task_id: str, **kwargs: Any) -> None:
        """Thread-safe entry point for yt-dlp worker-thread callbacks."""
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("no event loop bound, dropping progress update")
            return
        loop.call_soon_threadsafe(lambda: self.update(task_id, **kwargs))

    def fail(self, task_id: str, error: dict[str, Any], message: str = "") -> TaskRecord:
        return self.update(
            task_id,
            stage=TaskStage.FAILED,
            message=message or error.get("message", "failed"),
            error=error,
        )

    # ---------------------------------------------------------------- reads
    def get(self, task_id: str) -> TaskRecord:
        record = self._tasks.get(task_id)
        if record is None:
            raise NotFoundError(f"task {task_id} not found")
        return record

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        records = sorted(
            self._tasks.values(), key=lambda r: (r.created_at, r.sequence), reverse=True
        )
        return [record.snapshot() for record in records[: max(1, limit)]]

    # ------------------------------------------------------------ subscribe
    def subscribe(self, task_id: str) -> asyncio.Queue:
        record = self.get(task_id)
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, set()).add(queue)
        queue.put_nowait(record.snapshot())
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        listeners = self._subscribers.get(task_id)
        if not listeners:
            return
        listeners.discard(queue)
        if not listeners:
            self._subscribers.pop(task_id, None)

    def subscriber_count(self, task_id: str) -> int:
        return len(self._subscribers.get(task_id, ()))

    def global_subscriber_count(self) -> int:
        return len(self._global_subscribers)

    def subscribe_all(self) -> asyncio.Queue:
        """Subscribe to *every* task update through one queue.

        A browser only gets ~6 concurrent connections per origin under HTTP/1.1,
        so one multiplexed stream is the only safe shape for a UI that watches
        many tasks at once.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._global_subscribers.add(queue)
        return queue

    def unsubscribe_all(self, queue: asyncio.Queue) -> None:
        self._global_subscribers.discard(queue)

    # ------------------------------------------------------------ internals
    def _publish(self, record: TaskRecord) -> None:
        snapshot = record.snapshot()
        listeners = self._subscribers.get(record.id)
        if listeners:
            for queue in list(listeners):
                queue.put_nowait(snapshot)
            if record.stage.is_terminal:
                for queue in list(listeners):
                    self.unsubscribe(record.id, queue)

        for queue in list(self._global_subscribers):
            queue.put_nowait(snapshot)

    def _prune_history(self) -> None:
        if len(self._tasks) <= self._history_limit:
            return
        terminal = sorted(
            (r for r in self._tasks.values() if r.stage.is_terminal),
            key=lambda r: r.updated_at,
        )
        overflow = len(self._tasks) - self._history_limit
        for record in terminal[:overflow]:
            self._tasks.pop(record.id, None)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


__all__ = ["STAGE_FLOORS", "TaskManager"]
