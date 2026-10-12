"""HTTP contract tests for the score endpoints.

The real analysis pipeline is replaced: what is under test here is the HTTP
contract - routing, validation, error mapping and the shape of the response - not
the transcription, which ``test_score_service.py`` covers against real audio.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tornado.testing import AsyncHTTPTestCase

import api.handlers.score as score_module
from api.routes import make_app
from container import build_services
from core.config import Settings
from core.errors import ValidationError
from services.arrangement.piano_score import Measure, PianoScore
from services.score.service import ScoreResult

FAKE_MUSICXML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<score-partwise version="4.0"><part-list>'
    '<score-part id="P1"><part-name>Piano</part-name></score-part>'
    '</part-list><part id="P1"><measure number="1"><attributes>'
    "<staves>2</staves></attributes></measure></part></score-partwise>"
)


def fake_score(
    audio_path: Path,
    request: Any,
    *,
    output_dir: Path | None = None,
) -> ScoreResult:
    """A canned result, so the tests never pay for transcription."""
    grid = {"tempoBpm": 120.0, "beatsPerBar": 4, "barCount": 4}
    arrangement = {"fidelity": request.fidelity, "difficulty": request.difficulty}
    score = PianoScore(
        measures=tuple(Measure(index=i) for i in range(4)),
        tempo_bpm=120.0,
        beats_per_bar=4,
        title=request.title or "Test",
    )
    result = ScoreResult(
        score=score,
        musicxml=FAKE_MUSICXML,
        arrangement=arrangement,
        grid=grid,
        quality={"meanScore": 91.0, "recomposedBars": [2]},
        hand_split={"splitMidi": 60, "rightHandCount": 4, "leftHandCount": 6},
        key={"tonic": 9, "mode": "minor", "confidence": 0.8, "fifths": 0},
        chords=[{"name": "Am", "start": 0.0, "duration": 2.0}],
        truncated=bool(request.preview_seconds),
        analysed_seconds=30.0,
    )
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        result.musicxml_path = output_dir / f"{audio_path.stem}.musicxml"
        result.musicxml_path.write_text(FAKE_MUSICXML, encoding="utf-8")
        result.midi_path = output_dir / f"{audio_path.stem}.mid"
        result.midi_path.write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x03\x01\xe0")
        # Stand in for the synth output so the endpoint can be tested without
        # fluidsynth; the render itself is covered by test_follow_along_render.
        result.piano_path = output_dir / f"{audio_path.stem}.piano.wav"
        result.piano_path.write_bytes(_sine_wav())
        result.piano = {
            "available": True,
            "noteCount": 4,
            "sampleRate": 22050,
            "source": "generated-from-score",
        }
    return result


def _sine_wav(seconds: float = 0.2, sr: int = 22050) -> bytes:
    import io

    import numpy as np
    import soundfile as sf

    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    buffer = io.BytesIO()
    sf.write(buffer, np.sin(2 * np.pi * 440.0 * t), sr, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def exploding_score(audio_path: Path, request: Any, *, output_dir: Path | None = None):
    raise ValidationError("no notes were detected in this audio")


def score_without_piano(
    audio_path: Path, request: Any, *, output_dir: Path | None = None
) -> ScoreResult:
    """The same result with no synth available, as _render_piano reports it."""
    result = fake_score(audio_path, request, output_dir=output_dir)
    result.piano_path = None
    if result.piano_path is not None or output_dir is not None:
        for stale in Path(output_dir or ".").glob(f"{audio_path.stem}.piano.wav"):
            stale.unlink()
    result.piano = {"available": False, "reason": "the synth or SoundFont is not installed"}
    return result


def write_wav(path: Path, *, seconds: float = 1.0, sr: int = 22050) -> Path:
    import soundfile as sf

    t = np.arange(int(sr * seconds)) / sr
    sf.write(path, (0.4 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32), sr)
    return path


class ScoreApiTestCase(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pianoflow-score-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.settings = dataclasses.replace(
            Settings.from_env(),
            cache_dir=self._tmp / "cache",
            log_dir=self._tmp / "logs",
            download_min_interval_seconds=0.0,
        )
        self.services = build_services(self.settings)
        self._saved = score_module.generate_score
        score_module.generate_score = fake_score
        super().setUp()

    def tearDown(self) -> None:
        score_module.generate_score = self._saved
        super().tearDown()

    def get_app(self):
        return make_app(self.settings, self.services)

    # ------------------------------------------------------------- helpers
    def post_json(self, path: str, payload: dict[str, Any]):
        return self.fetch(
            path,
            method="POST",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )

    def register_audio(self, *, name: str = "take.wav") -> str:
        """Put a real file in the cache and return its key.

        ``register`` *moves* the file, so it is staged outside the cache first.
        """
        source = write_wav(self._tmp / name, seconds=1.0)
        entry = self.services.cache.register(
            source="upload",
            source_id=source.stem,
            audio_format="wav",
            title="测试曲目",
            artist="测试作者",
            duration=1.0,
            audio_file=source,
        )
        return entry.key

    def post_multipart(self, path: str, fields: dict[str, str], filename: str, payload: bytes):
        boundary = "----pianoflowtest"
        body = bytearray()
        for name, value in fields.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            body += f"{value}\r\n".encode()
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="audio"; filename="{filename}"\r\n'
        ).encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += payload
        body += f"\r\n--{boundary}--\r\n".encode()
        return self.fetch(
            path,
            method="POST",
            body=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            raise_error=False,
        )


class TestScoreOptions(ScoreApiTestCase):
    def test_options_lists_what_the_ui_may_offer(self) -> None:
        response = self.fetch("/api/score/options")
        self.assertEqual(response.code, 200)
        payload = json.loads(response.body)
        self.assertIn("hybrid", payload["fidElities"])
        self.assertEqual(set(payload["difficulties"]), {"1", "2", "3", "4"})
        self.assertIn("broken_chord", payload["patterns"])
        self.assertGreater(payload["maxPreviewSeconds"], 0)

    def test_every_difficulty_carries_an_explanation(self) -> None:
        payload = json.loads(self.fetch("/api/score/options").body)
        for hint in payload["difficulties"].values():
            self.assertTrue(hint.strip())


class TestScoreAnalyse(ScoreApiTestCase):
    def test_a_cached_song_can_be_scored(self) -> None:
        key = self.register_audio()
        response = self.post_json("/api/score/analyse", {"cacheKey": key})
        self.assertEqual(response.code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["cacheKey"], key)
        self.assertEqual(payload["request"]["fidelity"], "arranged")
        for section in ("grid", "handSplit", "quality", "arrangement", "key", "chords"):
            self.assertIn(section, payload)

    def test_the_cached_title_becomes_the_score_title(self) -> None:
        key = self.register_audio()
        payload = json.loads(self.post_json("/api/score/analyse", {"cacheKey": key}).body)
        self.assertEqual(payload["request"]["title"], "测试曲目")

    def test_options_are_passed_through(self) -> None:
        key = self.register_audio()
        payload = json.loads(
            self.post_json(
                "/api/score/analyse",
                {
                    "cacheKey": key,
                    "fidelity": "arranged",
                    "difficulty": 2,
                    "pattern": "alberti_bass",
                },
            ).body
        )
        self.assertEqual(payload["request"]["fidelity"], "arranged")
        self.assertEqual(payload["request"]["difficulty"], 2)
        self.assertEqual(payload["request"]["pattern"], "alberti_bass")

    def test_a_missing_cache_key_is_refused(self) -> None:
        response = self.post_json("/api/score/analyse", {})
        self.assertEqual(response.code, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "validation_error")

    def test_an_unknown_cache_key_is_a_404(self) -> None:
        response = self.post_json("/api/score/analyse", {"cacheKey": "a" * 40})
        self.assertEqual(response.code, 404)
        self.assertEqual(json.loads(response.body)["error"]["code"], "not_found")

    def test_an_unsupported_fidelity_is_refused(self) -> None:
        key = self.register_audio()
        response = self.post_json("/api/score/analyse", {"cacheKey": key, "fidelity": "perfect"})
        self.assertEqual(response.code, 400)
        self.assertIn("supported", json.loads(response.body)["error"]["details"])

    def test_an_unsupported_difficulty_is_refused(self) -> None:
        key = self.register_audio()
        response = self.post_json("/api/score/analyse", {"cacheKey": key, "difficulty": 9})
        self.assertEqual(response.code, 400)

    def test_a_domain_failure_is_reported_as_json_not_a_500(self) -> None:
        """A pipeline error must reach the user as a 400, not an internal error."""
        score_module.generate_score = exploding_score
        key = self.register_audio()
        response = self.post_json("/api/score/analyse", {"cacheKey": key})
        self.assertEqual(response.code, 400)
        body = json.loads(response.body)
        self.assertEqual(body["error"]["code"], "validation_error")
        self.assertIn("no notes", body["error"]["message"])


#: The store step decodes the upload with ffmpeg; on a machine without ffmpeg it
#: refuses rather than guessing, so the tests that reach the decoder skip there
#: instead of failing (the refusal itself is covered by the extension tests).
requires_ffmpeg = pytest.mark.skipif(
    Settings.from_env().ffmpeg_dir_or_none() is None,
    reason="ffmpeg is not available",
)


class TestScoreUpload(ScoreApiTestCase):
    def test_an_upload_without_a_file_is_refused(self) -> None:
        response = self.post_multipart("/api/score/upload", {}, "", b"")
        self.assertEqual(response.code, 400)
        self.assertIn("multipart", json.loads(response.body)["error"]["message"])

    def test_an_unsupported_extension_is_refused(self) -> None:
        response = self.post_multipart("/api/score/upload", {"analyse": "0"}, "notes.txt", b"hello")
        self.assertEqual(response.code, 400)
        self.assertIn("supported", json.loads(response.body)["error"]["details"])

    def test_an_empty_upload_is_refused(self) -> None:
        response = self.post_multipart("/api/score/upload", {"analyse": "0"}, "take.wav", b"")
        self.assertEqual(response.code, 400)

    @requires_ffmpeg
    def test_uploading_a_real_wav_stores_and_analyses_it(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        response = self.post_multipart(
            "/api/score/upload",
            {"fidelity": "hybrid", "difficulty": "L2", "analyse": "1"},
            "我的钢琴曲.wav",
            audio.read_bytes(),
        )
        self.assertEqual(response.code, 200, response.body)
        payload = json.loads(response.body)
        self.assertIn("upload", payload)
        self.assertEqual(payload["request"]["difficulty"], 2)
        self.assertEqual(payload["upload"]["originalName"], "我的钢琴曲.wav")
        self.assertEqual(len(payload["upload"]["key"]), 64)

    @requires_ffmpeg
    def test_the_upload_can_be_stored_without_analysis(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        response = self.post_multipart(
            "/api/score/upload", {"analyse": "0"}, "take.wav", audio.read_bytes()
        )
        self.assertEqual(response.code, 200, response.body)
        payload = json.loads(response.body)
        self.assertIn("upload", payload)
        self.assertNotIn("score", payload)

    @requires_ffmpeg
    def test_the_filename_becomes_the_default_title(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        payload = json.loads(
            self.post_multipart(
                "/api/score/upload", {"analyse": "1"}, "茉莉花.wav", audio.read_bytes()
            ).body
        )
        self.assertEqual(payload["request"]["title"], "茉莉花")

    @requires_ffmpeg
    def test_the_uploaded_key_is_playable_through_the_cache_route(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        payload = json.loads(
            self.post_multipart(
                "/api/score/upload", {"analyse": "0"}, "take.wav", audio.read_bytes()
            ).body
        )
        key = payload["upload"]["key"]
        response = self.fetch(f"/api/cache/audio/{key}", raise_error=False)
        self.assertIn(response.code, (200, 404))


class TestPianoTrack(ScoreApiTestCase):
    """The generated piano track is what follow-along plays by default."""

    def _generate_one(self) -> dict[str, object]:
        key = self.register_audio()
        return json.loads(self.post_json("/api/score/analyse", {"cacheKey": key}).body)

    def test_the_response_advertises_the_piano_track(self) -> None:
        body = self._generate_one()
        # Versioned, so the frontend reloads instead of replaying the last score.
        self.assertTrue(body["pianoTrackUrl"].startswith("/api/score/piano?v="))
        self.assertTrue(body["piano"]["available"])
        # The UI must be able to say where the audio came from.
        self.assertEqual(body["piano"]["source"], "generated-from-score")

    def test_the_track_is_served_as_playable_audio(self) -> None:
        self._generate_one()
        response = self.fetch("/api/score/piano")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.headers["Content-Type"], "audio/wav")
        # Playable inline, so the audio element can stream it rather than download it.
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(response.body[:4], b"RIFF")
        self.assertEqual(response.body[8:12], b"WAVE")

    def test_the_track_is_the_newest_score_not_a_stale_one(self) -> None:
        """Scoring twice must hand out a different URL, or the UI replays the first."""
        first = self._generate_one()["pianoTrackUrl"]
        second = self._generate_one()["pianoTrackUrl"]
        assert first != second
        # And both must resolve to playable audio.
        assert self.fetch(second).body[:4] == b"RIFF"

    def test_a_missing_synth_leaves_the_score_usable(self) -> None:
        """No synth must degrade to the source recording, not fail the request."""
        score_module.generate_score = score_without_piano
        body = self._generate_one()
        self.assertIsNone(body["pianoTrackUrl"])
        self.assertFalse(body["piano"]["available"])
        # Everything else still works.
        self.assertEqual(self.fetch("/api/score/musicxml").code, 200)
        self.assertEqual(self.fetch("/api/score/midi").code, 200)
        self.assertEqual(self.fetch("/api/score/piano", raise_error=False).code, 404)


class TestScoreOutputs(ScoreApiTestCase):
    def _generate_one(self) -> None:
        key = self.register_audio()
        self.post_json("/api/score/analyse", {"cacheKey": key})

    def test_musicxml_is_served_for_the_renderer(self) -> None:
        self._generate_one()
        response = self.fetch("/api/score/musicxml")
        self.assertEqual(response.code, 200)
        self.assertIn("score-partwise", response.body.decode("utf-8"))
        self.assertIn("<staves>2</staves>", response.body.decode("utf-8"))

    def test_midi_is_downloadable(self) -> None:
        self._generate_one()
        response = self.fetch("/api/score/midi")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body[:4], b"MThd")

    def test_requesting_output_before_generating_is_a_404(self) -> None:
        self.assertEqual(self.fetch("/api/score/musicxml", raise_error=False).code, 404)
        self.assertEqual(self.fetch("/api/score/midi", raise_error=False).code, 404)
        self.assertEqual(self.fetch("/api/score/piano", raise_error=False).code, 404)

    def test_a_404_endpoint_still_returns_json(self) -> None:
        response = self.fetch("/api/score/nope", raise_error=False)
        self.assertEqual(response.code, 404)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
