"""SQLite storage: WAL-mode connection, idempotent schema, transactional writes.

Tables:
    sessions  - one row per chat session (the session's headline text)
    messages  - one row per message, FK to its session
    history   - flat history of past session texts
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Role = Literal["user", "system", "assistant", "tool"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'system', 'assistant', 'tool')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""


def data_dir() -> Path:
    """Return the default data directory ($GAME_COMPANION_HOME, else ~/.game-companion)."""
    return Path(os.environ.get("GAME_COMPANION_HOME", str(Path.home() / ".game-companion")))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a WAL-mode connection and ensure the schema exists.

    Schema creation is idempotent (``IF NOT EXISTS``), so calling this
    repeatedly against the same file is safe.
    """
    target = Path(path) if path is not None else data_dir() / "data" / "companion.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    logger.debug("connected to sqlite at %s", target)
    return conn


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def append_message(
    conn: sqlite3.Connection,
    *,
    session_text: str,
    role: Role,
    content: str,
    when: str | None = None,
) -> tuple[int, int]:
    """Create a session row and its first message row in one transaction.

    Returns:
        ``(session_id, message_id)``. If either write fails, both roll back.
    """
    ts = when if when is not None else utc_now()
    with conn:
        session_cur = conn.execute(
            "INSERT INTO sessions (text, created_at) VALUES (?, ?)",
            (session_text, ts),
        )
        message_cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_cur.lastrowid, role, content, ts),
        )
    return int(session_cur.lastrowid), int(message_cur.lastrowid)
