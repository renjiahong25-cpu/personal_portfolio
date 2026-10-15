"""User settings: typed pydantic model, versioned JSON, atomic persistence.

Config lives in ``$GAME_COMPANION_HOME/config.json`` (default
``~/.game-companion/config.json``). Files without a ``schema_version`` key
are treated as v0 and migrated to v1 before validation.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def config_path() -> Path:
    """Return the default settings file location."""
    base = Path(os.environ.get("GAME_COMPANION_HOME", str(Path.home() / ".game-companion")))
    return base / "config.json"


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Run the config migration chain and return a version-normalized dict.

    v0 (no ``schema_version`` key) -> v1: stamp the version; model defaults
    fill any remaining missing fields during validation. Future steps
    (v1 -> v2, ...) chain here.
    """
    if "schema_version" not in raw:
        logger.debug("migrating config v0 -> v1")
        raw = dict(raw)
        raw["schema_version"] = SCHEMA_VERSION
    return raw


def _atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically: temp file then ``os.replace``.

    On any failure the temp file is removed and the exception re-raised,
    so a crash mid-write never corrupts the existing config.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Settings(BaseModel):
    """Typed application settings persisted as versioned JSON."""

    model_config = ConfigDict(extra="ignore")

    app_name: str = "Game Companion"
    schema_version: int = Field(default=SCHEMA_VERSION)

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    tts_enabled: bool = True
    stt_enabled: bool = True
    voice_provider: str = "edge"

    max_steps: int = Field(default=8, ge=1, le=64)
    overlay_opacity: float = Field(default=0.9, ge=0.1, le=1.0)
    notifications_enabled: bool = True
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: Path | str | None = None) -> Settings:
        """Load settings from ``path`` (default: :func:`config_path`).

        A missing file yields defaults; v0 payloads are migrated first.
        """
        target = Path(path) if path is not None else config_path()
        if not target.exists():
            logger.info("no settings file at %s, using defaults", target)
            return cls()
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"settings file {target} must contain a JSON object")
        return cls.model_validate(migrate(raw))

    def save(self, path: Path | str | None = None) -> None:
        """Persist settings atomically to ``path`` (default: :func:`config_path`)."""
        target = Path(path) if path is not None else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.model_dump(), indent=2, ensure_ascii=False)
        _atomic_write(target, payload)
        logger.debug("saved settings to %s", target)
