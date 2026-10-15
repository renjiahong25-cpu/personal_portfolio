"""Tests for app.bus.signals: headless instantiation and emit/receive."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication

from app.bus.signals import SignalsBus


@pytest.fixture(scope="session")
def qapp() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


def test_bus_emits_chat_delta(qapp: QCoreApplication) -> None:
    bus = SignalsBus()
    received: list[str] = []
    bus.chat_delta.connect(received.append)
    bus.chat_delta.emit("hello world")
    assert received == ["hello world"]


def test_bus_state_changed_carries_two_arguments(qapp: QCoreApplication) -> None:
    bus = SignalsBus()
    received: list[tuple[str, str]] = []

    def on_state(key: str, value: str) -> None:
        received.append((key, value))

    bus.state_changed.connect(on_state)
    bus.state_changed.emit("mode", "active")
    assert received == [("mode", "active")]


def test_bus_notice_asr_tts(qapp: QCoreApplication) -> None:
    bus = SignalsBus()
    notes: list[str] = []
    asr_states: list[str] = []
    tts_states: list[str] = []
    bus.notice.connect(notes.append)
    bus.asr_state.connect(asr_states.append)
    bus.tts_state.connect(tts_states.append)
    bus.notice.emit("hi")
    bus.asr_state.emit("listening")
    bus.tts_state.emit("speaking")
    assert notes == ["hi"]
    assert asr_states == ["listening"]
    assert tts_states == ["speaking"]
