"""Cached audio streaming: Range handling and path-traversal refusal."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from pathlib import Path

from tornado.testing import AsyncHTTPTestCase

from api.routes import make_app
from container import build_services
from core.config import Settings
from services.cache.manager import CacheManager

WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 32


class CacheAudioTestCase(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pianoflow-audio-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.settings = dataclasses.replace(
            Settings.from_env(),
            cache_dir=self._tmp / "cache",
            log_dir=self._tmp / "logs",
        )
        self.services = build_services(self.settings)
        self.cache: CacheManager = self.services.cache

        self.entry = self._register("bilibili", "BV1", "星辰大海")
        super().setUp()

    def get_app(self):
        return make_app(self.settings, self.services)

    def _register(self, source: str, source_id: str, title: str):
        self.cache.root.mkdir(parents=True, exist_ok=True)
        produced = self.cache.root / f"incoming-{source_id}.wav"
        produced.write_bytes(WAV)
        return self.cache.register(
            source=source,
            source_id=source_id,
            audio_format="wav",
            title=title,
            artist="知己音乐",
            duration=209.0,
            audio_file=produced,
            webpage_url=f"https://www.bilibili.com/video/{source_id}",
        )

    # --------------------------------------------------------------- tests
    def test_full_body_is_served(self):
        response = self.fetch(f"/api/cache/audio/{self.entry.key}")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, WAV)
        self.assertEqual(response.headers["Content-Type"], "audio/wav")
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        self.assertEqual(response.headers["Content-Length"], str(len(WAV)))

    def test_range_request_returns_partial_content(self):
        response = self.fetch(
            f"/api/cache/audio/{self.entry.key}",
            headers={"Range": "bytes=4-11"},
        )
        self.assertEqual(response.code, 206)
        self.assertEqual(response.body, WAV[4:12])
        self.assertEqual(response.headers["Content-Range"], f"bytes 4-11/{len(WAV)}")
        self.assertEqual(response.headers["Content-Length"], "8")

    def test_suffix_range_returns_the_tail(self):
        response = self.fetch(
            f"/api/cache/audio/{self.entry.key}",
            headers={"Range": "bytes=-8"},
        )
        self.assertEqual(response.code, 206)
        self.assertEqual(response.body, WAV[-8:])

    def test_unsatisfiable_range_is_416(self):
        response = self.fetch(
            f"/api/cache/audio/{self.entry.key}",
            headers={"Range": f"bytes={len(WAV) + 100}-"},
            raise_error=False,
        )
        self.assertEqual(response.code, 416)
        self.assertEqual(response.headers["Content-Range"], f"bytes */{len(WAV)}")

    def test_bogus_range_is_ignored(self):
        response = self.fetch(
            f"/api/cache/audio/{self.entry.key}",
            headers={"Range": "kilobytes=1-2"},
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, WAV)

    def test_unknown_key_is_404(self):
        response = self.fetch("/api/cache/audio/" + "0" * 40, raise_error=False)
        self.assertEqual(response.code, 404)

    def test_non_hex_key_falls_through_to_spa(self):
        """No route match -> SPA handler, which must not serve audio."""
        response = self.fetch("/api/cache/audio/../../secret", raise_error=False)
        self.assertNotEqual(response.headers.get("Content-Type", ""), "audio/wav")

    def test_traversal_filename_is_refused(self):
        entry = self.entry
        meta = self.cache.meta_path(entry.key)
        payload = meta.read_text(encoding="utf-8").replace(entry.filename, "../escaped.wav")
        meta.write_text(payload, encoding="utf-8")

        response = self.fetch(f"/api/cache/audio/{entry.key}", raise_error=False)
        self.assertEqual(response.code, 404)

    def test_metadata_exposes_source_page(self):
        stats = self.cache.stats()
        self.assertEqual(
            stats["entries"][0]["webpageUrl"],
            "https://www.bilibili.com/video/BV1",
        )

    def test_webpage_url_survives_a_round_trip(self):
        reloaded = self.cache.get_entry(self.entry.key)
        self.assertEqual(reloaded.webpage_url, "https://www.bilibili.com/video/BV1")

    def test_old_metadata_without_webpage_url_still_loads(self):
        """Cache files written before webpage_url existed must stay readable."""
        import json

        entry = self.entry
        meta = self.cache.meta_path(entry.key)
        payload = json.loads(meta.read_text(encoding="utf-8"))
        payload.pop("webpage_url", None)
        meta.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        self.assertIsNone(self.cache.get_entry(entry.key).webpage_url)
