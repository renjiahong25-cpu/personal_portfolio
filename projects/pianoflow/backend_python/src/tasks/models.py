"""Task state machine shared by the download pipeline and the SSE endpoint."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStage(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    TRANSCODING = "transcoding"
    ANALYZING = "analyzing"
    SCORING = "scoring"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STAGES


_TERMINAL_STAGES = frozenset({TaskStage.DONE, TaskStage.FAILED, TaskStage.CANCELLED})


@dataclass
class TaskRecord:
    id: str
    kind: str
    stage: TaskStage = TaskStage.QUEUED
    percent: float = 0.0
    message: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    #: Monotonic creation counter. ``time.time()`` alone cannot order two tasks
    #: created inside the same clock tick, which would make ``list_tasks``
    #: non-deterministic.
    sequence: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "taskId": self.id,
            "kind": self.kind,
            "stage": self.stage.value,
            "percent": round(self.percent, 2),
            "message": self.message,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "meta": self.meta,
            "result": self.result,
            "error": self.error,
        }


__all__ = ["TaskRecord", "TaskStage"]
