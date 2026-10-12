"""Regression tests for the phase-1 code review fixes."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path

import app as app_module
from tornado.testing import AsyncHTTPTestCase

from api.routes import make_app
from container import build_services
from core.config import Settings
from services.download.audio_service import MAX_QUEUE_DEPTH, AudioDownloadService, DownloadRequest
from services.music_source.base import Extractor, Track
from services.music_source.registry import ExtractorRegistry

# --- H1/H2: entry point ----------------------------------------------------


class TestResolveSettings:
    """H1/H2: CLI overrides must not rebuild the dataclass by hand."""

    def test_cli_overrides_win(self, monkeypatch):
        monkeypatch.setenv("PIANOFLOW_PORT", "7000")
        settings = app_module.resolve_settings(["--port", "8123", "--host", "0.0.0.0"])
        assert settings.port == 8123
        assert settings.host == "0.0.0.0"

    def test_env_is_used_without_cli_flags(self, monkeypatch):
        monkeypatch.setenv("PIANOFLOW_PORT", "7000")
        monkeypatch.setenv("PIANOFLOW_HOST", "127.0.0.1")
        settings = app_module.resolve_settings([])
        assert settings.port == 7000

    def test_replacement_keeps_every_field(self, monkeypatch):
        """dataclasses.replace must not drop any field (the old bug)."""
        monkeypatch.delenv("PIANOFLOW_PORT", raising=False)
        base = Settings.from_env()
        replaced = dataclasses.replace(base, port=9999)
        for field in base.__dataclass_fields__:
            if field == "port":
                assert getattr(replaced, field) == 9999
            else:
                assert getattr(replaced, field) == getattr(base, field)

    def test_entry_point_does_not_call_deprecated_get_event_loop(self):
        """H1: asyncio.get_event_loop() is deprecated and later removed.

        Checked on the AST rather than the raw text so that mentioning the API
        in a comment does not fail the test.
        """
        import ast

        tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
        called = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "get_event_loop" not in called
        assert not any(
            isinstance(node, ast.Import) and any(a.name == "asyncio" for a in node.names)
            for node in ast.walk(tree)
        )


# --- M1/M3: download queue -------------------------------------------------


class FakeExtractor(Extractor):
    name = "fake"
    display_name = "Fake"
    supports_download = True

    def canonical_url(self, source_id: str) -> str:
        return f"https://example.invalid/{source_id}"

    async def search(self, keyword: str, limit: int) -> list[Track]:
        return []


class TestQueueAccounting:
    def _service(self, tmp_path: Path) -> tuple[AudioDownloadService, ExtractorRegistry]:
        settings = dataclasses.replace(
            Settings.from_env(),
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            download_min_interval_seconds=0.0,
        )
        services = build_services(settings)
        registry = ExtractorRegistry()
        registry.register(FakeExtractor())
        services.downloads = AudioDownloadService(
            settings, services.cache, services.tasks, registry
        )
        return services.downloads, registry

    def test_submit_enqueues_synchronously(self, tmp_path):
        """M1: the depth check must not be separated from the append."""
        import asyncio

        service, _ = self._service(tmp_path)

        async def scenario():
            first = await service.submit(DownloadRequest("fake", "A"))
            second = await service.submit(DownloadRequest("fake", "B"))
            return first, second, service.queue_snapshot()

        first, second, snapshot = asyncio.run(scenario())
        assert first["taskId"] in snapshot["queue"]
        assert second["taskId"] in snapshot["queue"]
        assert snapshot["queued"] == 1

    def test_queue_depth_is_enforced(self, tmp_path):
        import asyncio

        service, _ = self._service(tmp_path)

        async def scenario():
            accepted = 0
            rejected = 0
            for index in range(MAX_QUEUE_DEPTH + 3):
                try:
                    await service.submit(DownloadRequest("fake", f"BV{index}"))
                    accepted += 1
                except Exception as exc:  # noqa: BLE001 - checking the cap
                    assert getattr(exc, "status", None) == 429, exc
                    rejected += 1
            return accepted, rejected

        accepted, rejected = asyncio.run(scenario())
        assert accepted == MAX_QUEUE_DEPTH
        assert rejected == 3

    def test_active_flag_reflects_a_running_download(self, tmp_path):
        """M3: the old expression was inverted and always reported False."""
        import asyncio

        service, _ = self._service(tmp_path)

        async def scenario():
            before = service.queue_snapshot()["active"]

            # Build a real task record the way submit() does, so _run can update
            # it when cancelled.
            record = service._tasks.create("download_audio", {"sourceId": "A"})
            service._running_uids.add("fake:A")
            service._queue.append(record.id)

            task = asyncio.create_task(
                service._run(record.id, DownloadRequest("fake", "A"), FakeExtractor())
            )
            await asyncio.sleep(0)
            during = service.queue_snapshot()["active"]
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            service._queue.clear()
            service._running_uids.clear()
            return before, during

        before, during = asyncio.run(scenario())
        assert before is False
        assert during is True


# --- M2: unknown API endpoints must not return the SPA --------------------


class TestUnknownApiEndpoint(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pianoflow-api404-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.settings = dataclasses.replace(
            Settings.from_env(),
            cache_dir=self._tmp / "cache",
            log_dir=self._tmp / "logs",
        )
        self.services = build_services(self.settings)
        super().setUp()

    def get_app(self):
        return make_app(self.settings, self.services)

    def test_unknown_api_path_is_404_json(self):
        response = self.fetch("/api/does/not/exist", raise_error=False)
        self.assertEqual(response.code, 404)
        self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
        self.assertEqual(json.loads(response.body)["error"]["code"], "not_found")

    def test_typo_in_a_real_endpoint_is_not_html(self):
        response = self.fetch("/api/music/searchh", raise_error=False)
        self.assertEqual(response.code, 404)
        self.assertNotIn(b"<!DOCTYPE html>", response.body[:64].upper())

    def test_spa_routes_still_get_html(self):
        response = self.fetch("/some/client/route", raise_error=False)
        self.assertEqual(response.code, 200)
        self.assertIn(b"PianoFlow", response.body)
