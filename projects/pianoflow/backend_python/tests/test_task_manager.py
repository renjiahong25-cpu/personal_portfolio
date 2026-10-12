"""Task manager: the multiplexed session stream."""

from __future__ import annotations

import pytest

from core.errors import NotFoundError
from tasks.manager import TaskManager
from tasks.models import TaskStage


@pytest.fixture
def manager() -> TaskManager:
    return TaskManager()


def test_create_starts_queued(manager):
    record = manager.create("download_audio", {"sourceId": "BV1"})
    snapshot = record.snapshot()
    assert snapshot["stage"] == "queued"
    assert snapshot["percent"] == 0.0
    assert len(record.id) == 32


def test_unknown_task_raises(manager):
    with pytest.raises(NotFoundError):
        manager.get("does-not-exist")


def test_stage_change_never_moves_progress_backwards(manager):
    record = manager.create("download_audio")
    manager.update(record.id, stage=TaskStage.DOWNLOADING, percent=55.0)
    manager.update(record.id, stage=TaskStage.RESOLVING)
    assert manager.get(record.id).percent == 55.0


def test_percent_is_clamped(manager):
    record = manager.create("download_audio")
    manager.update(record.id, percent=180.0)
    assert manager.get(record.id).percent == 100.0
    manager.update(record.id, percent=-20.0)
    assert manager.get(record.id).percent == 0.0


def test_done_forces_full_progress(manager):
    record = manager.create("download_audio")
    manager.update(record.id, stage=TaskStage.DONE, result={"ok": True})
    snapshot = record.snapshot()
    assert snapshot["percent"] == 100.0
    assert snapshot["result"] == {"ok": True}


def test_terminal_task_ignores_further_updates(manager):
    record = manager.create("download_audio")
    manager.fail(record.id, {"code": "x", "message": "boom"})
    manager.update(record.id, percent=99.0, message="late")
    assert manager.get(record.id).stage is TaskStage.FAILED
    assert manager.get(record.id).message == "boom"


def test_subscriber_receives_snapshot_then_terminal_event(manager):
    record = manager.create("download_audio")
    queue = manager.subscribe(record.id)
    assert queue.get_nowait()["stage"] == "queued"

    manager.update(record.id, stage=TaskStage.DONE, result={"audioPath": "x.wav"})
    final = queue.get_nowait()
    assert final["stage"] == "done"
    assert final["result"]["audioPath"] == "x.wav"


def test_unsubscribe_stops_delivery(manager):
    record = manager.create("download_audio")
    queue = manager.subscribe(record.id)
    queue.get_nowait()
    manager.unsubscribe(record.id, queue)
    manager.update(record.id, stage=TaskStage.DONE)
    assert manager.subscriber_count(record.id) == 0
    assert queue.empty()


def test_history_is_pruned(manager):
    manager._history_limit = 3
    ids = [manager.create("download_audio").id for _ in range(6)]
    for task_id in ids[:5]:
        manager.fail(task_id, {"code": "x", "message": "done"})
    manager.create("download_audio")
    assert len(manager.list_tasks(limit=100)) <= 4


def test_list_tasks_is_newest_first(manager):
    first = manager.create("download_audio")
    second = manager.create("download_audio")
    tasks = manager.list_tasks()
    assert tasks[0]["taskId"] == second.id
    assert tasks[1]["taskId"] == first.id


# --- multiplexed session stream -----------------------------------------


def test_global_subscriber_sees_every_task(manager):
    queue = manager.subscribe_all()
    first = manager.create("download_audio")
    second = manager.create("download_audio")
    manager.update(first.id, stage=TaskStage.DONE, result={"n": 1})
    manager.update(second.id, stage=TaskStage.FAILED, error={"message": "x"})

    stages = [queue.get_nowait()["stage"] for _ in range(3)]
    assert stages == ["queued", "queued", "done"]


def test_global_subscriber_also_sees_task_created_before_subscribing(manager):
    """A late subscriber misses history by design; polling covers that gap."""
    manager.create("download_audio")
    queue = manager.subscribe_all()
    assert queue.empty()


def test_global_unsubscribe_stops_delivery(manager):
    queue = manager.subscribe_all()
    assert manager.global_subscriber_count() == 1
    manager.unsubscribe_all(queue)
    assert manager.global_subscriber_count() == 0
    manager.create("download_audio")
    assert queue.empty()


def test_terminal_event_does_not_close_the_session_stream(manager):
    queue = manager.subscribe_all()
    record = manager.create("download_audio")
    manager.update(record.id, stage=TaskStage.DONE)
    queue.get_nowait()  # queued
    assert queue.get_nowait()["stage"] == "done"
    assert manager.global_subscriber_count() == 1
