"""Tests for app.storage.db: idempotent schema, WAL, transactional appends."""

from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import append_message, connect


def test_connect_creates_schema_idempotently(tmp_path: pytest.TempPath) -> None:
    path = tmp_path / "companion.db"
    c1 = connect(path)
    c1.close()
    c2 = connect(path)
    try:
        tables = {
            row[0] for row in c2.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        c2.close()
    assert {"sessions", "messages", "history"} <= tables


def test_connect_uses_wal(tmp_path: pytest.TempPath) -> None:
    conn = connect(tmp_path / "wal.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        conn.close()


def test_append_message_writes_both_rows(tmp_path: pytest.TempPath) -> None:
    conn = connect(tmp_path / "a.db")
    try:
        session_id, message_id = append_message(
            conn, session_text="s1", role="user", content="hello"
        )
        row = conn.execute(
            "SELECT s.text AS s_text, m.role AS m_role, m.content AS m_content "
            "FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        assert row is not None
        assert row["s_text"] == "s1"
        assert row["m_role"] == "user"
        assert row["m_content"] == "hello"
        assert session_id is not None
        assert message_id is not None
    finally:
        conn.close()


def test_append_message_rolls_back_on_error(tmp_path: pytest.TempPath) -> None:
    conn = connect(tmp_path / "rb.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            append_message(conn, session_text="boom", role="bogus-role", content="x")
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        append_message(conn, session_text="ok", role="assistant", content="hi")
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        conn.close()
