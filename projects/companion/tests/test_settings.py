"""Tests for app.config.settings: round-trip, atomicity, v0 migration."""

from __future__ import annotations

import json
import os

import pytest

from app.config.settings import SCHEMA_VERSION, Settings, migrate


def test_roundtrip(tmp_path: pytest.TempPath) -> None:
    path = tmp_path / "config.json"
    original = Settings(
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet",
        llm_temperature=0.4,
        max_steps=5,
    )
    original.save(path)
    loaded = Settings.load(path)
    assert loaded == original
    assert loaded.schema_version == SCHEMA_VERSION
    assert path.exists()


def test_load_missing_file_returns_defaults(tmp_path: pytest.TempPath) -> None:
    loaded = Settings.load(tmp_path / "absent.json")
    assert loaded == Settings()


def test_save_crash_preserves_old_bytes(
    tmp_path: pytest.TempPath, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    Settings(app_name="old version").save(path)
    old_bytes = path.read_bytes()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash while replacing target")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        Settings(app_name="new version").save(path)

    assert path.read_bytes() == old_bytes
    assert not (tmp_path / "config.json.tmp").exists()


def test_migrate_v0_adds_schema_version() -> None:
    migrated = migrate({"llm_model": "gpt-4o", "tts_enabled": False})
    assert migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["llm_model"] == "gpt-4o"


def test_migrate_v1_passes_through() -> None:
    raw = {"schema_version": SCHEMA_VERSION, "llm_model": "x"}
    assert migrate(raw) is raw


def test_v0_file_loads_with_full_fields(tmp_path: pytest.TempPath) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"llm_model": "gpt-4o", "tts_enabled": False}),
        encoding="utf-8",
    )
    loaded = Settings.load(path)
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.llm_model == "gpt-4o"
    assert loaded.tts_enabled is False
    assert loaded.app_name == Settings().app_name
    assert loaded.max_steps == Settings().max_steps
    assert loaded.stt_enabled is True
