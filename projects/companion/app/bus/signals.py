"""Process-wide Qt signal bus.

Defines signals only; nothing here touches widgets or event loops, so the
bus can be instantiated (and emitted on) in headless tests. A single
process-level instance is expected; producers (agent, providers, UI
actions) and consumers (UI, notify) connect to the same object.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class SignalsBus(QObject):
    """Signal definitions for cross-module communication.

    Signals:
        chat_delta (str): incremental LLM chat text to stream into the UI.
        notice (str): short user-facing notice (toasts, status lines).
        state_changed (str, str): (key, value) agent/app state updates.
        asr_state (str): speech recognition state transitions.
        tts_state (str): speech synthesis state transitions.
    """

    chat_delta = Signal(str)
    notice = Signal(str)
    state_changed = Signal(str, str)
    asr_state = Signal(str)
    tts_state = Signal(str)
