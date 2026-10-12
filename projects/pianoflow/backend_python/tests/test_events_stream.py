"""Session event stream: one connection for all tasks, with a hard cap."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path

from tornado import gen
from tornado.testing import AsyncHTTPTestCase

from api.routes import make_app
from container import build_services
from core.config import Settings
from tasks.models import TaskStage


class EventsStreamTestCase(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pianoflow-events-"))
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

    # --------------------------------------------------------------- tests
    def test_stream_carries_updates_for_a_new_task(self):
        async def scenario() -> bytes:
            chunks = bytearray()
            seen = asyncio.Event()

            def on_chunk(chunk: bytes) -> None:
                chunks.extend(chunk)
                if b"event: queued" in bytes(chunks):
                    seen.set()

            future = self.http_client.fetch(
                self.get_url("/api/events"),
                streaming_callback=on_chunk,
                request_timeout=20,
            )
            try:
                for _ in range(200):
                    if self.services.tasks.global_subscriber_count() > 0:
                        break
                    await gen.sleep(0.02)
                self.assertEqual(
                    self.services.tasks.global_subscriber_count(),
                    1,
                    "stream never subscribed",
                )

                self.services.tasks.create("download_audio", {"sourceId": "BV1"})
                for _ in range(200):
                    if seen.is_set():
                        break
                    await gen.sleep(0.02)
                return bytes(chunks)
            finally:
                future.cancel()

        raw = self.io_loop.run_sync(scenario)
        self.assertIn(b"event: queued", raw)
        payload_line = [line for line in raw.decode().splitlines() if line.startswith("data: ")]
        self.assertTrue(payload_line)
        snapshot = json.loads(payload_line[0][len("data: ") :])
        self.assertEqual(snapshot["kind"], "download_audio")
        self.assertEqual(snapshot["stage"], "queued")

    def test_stream_is_refused_when_too_many_clients(self):
        for _ in range(4):  # EventsHandler.MAX_CLIENTS
            self.services.tasks.subscribe_all()

        response = self.fetch("/api/events", raise_error=False)
        body = json.loads(response.body)
        self.assertEqual(response.code, 503)
        self.assertEqual(body["error"]["code"], "too_many_streams")

    def test_per_task_stream_replays_current_state(self):
        """A late subscriber gets the current snapshot, then the terminal end."""
        record = self.services.tasks.create("download_audio", {"sourceId": "BV1"})

        async def read() -> bytes:
            chunks = bytearray()
            future = self.http_client.fetch(
                self.get_url(f"/api/music/download-audio/stream?task_id={record.id}"),
                streaming_callback=chunks.extend,
                request_timeout=10,
            )
            for _ in range(100):
                if self.services.tasks.subscriber_count(record.id) > 0:
                    break
                await gen.sleep(0.02)
            self.services.tasks.update(
                record.id, stage=TaskStage.DONE, percent=100.0, result={"ok": True}
            )
            try:
                for _ in range(200):
                    if b"event: done" in bytes(chunks):
                        break
                    await gen.sleep(0.02)
            finally:
                future.cancel()
            return bytes(chunks)

        raw = self.io_loop.run_sync(read)
        self.assertIn(b"event: queued", raw)  # replayed on subscribe
        self.assertIn(b"event: done", raw)  # then the terminal update
        self.assertEqual(self.services.tasks.subscriber_count(record.id), 0)
