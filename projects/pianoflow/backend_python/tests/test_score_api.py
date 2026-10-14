"""HTTP contract tests for the score module endpoints.

The real analysis pipeline is replaced: what is under test here is the HTTP
contract - routing, validation, error mapping and the shape of the response - not
the transcription, which ``test_score_service.py`` covers against real audio.

The contract is two modules, not one endpoint: the piano-transcription module
(faithful fidelities) and the pop-to-piano module (arranging fidelities). The
interesting cases are the seams: each module must refuse the other module's
options with a pointer to the right endpoint, not default them away.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import numpy as np
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
    # Mirrors Arrangement.to_dict(): the density level the run actually used.
    arrangement = {
        "fidelity": request.fidelity,
        "difficulty": request.difficulty,
        "accompaniment": request.accompaniment,
    }
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
        for suffix, path_attr, info_attr in (
            ("piano", "piano_path", "piano"),
            ("melody", "melody_path", "melody"),
            ("left", "left_path", "left"),
        ):
            track_path = output_dir / f"{audio_path.stem}.{suffix}.wav"
            track_path.write_bytes(_sine_wav())
            setattr(result, path_attr, track_path)
            setattr(result, info_attr, {
                "available": True,
                "noteCount": 2,
                "sampleRate": 22050,
                "source": "generated-from-score",
            })
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
    for path_attr, info_attr in (
        ("piano_path", "piano"),
        ("melody_path", "melody"),
        ("left_path", "left"),
    ):
        setattr(result, path_attr, None)
        setattr(result, info_attr, {"available": False, "reason": "the synth or SoundFont is not installed"})
    if output_dir is not None:
        for stale in Path(output_dir).glob(f"{audio_path.stem}.*.wav"):
            stale.unlink()
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


class TestModuleOptions(ScoreApiTestCase):
    def test_transcribe_options_carry_only_faithful_modes(self) -> None:
        payload = json.loads(self.fetch("/api/score/transcribe/options").body)
        self.assertEqual(payload["fidelities"], ["transcribed", "as-played"])
        self.assertIn("transcribed", payload["fidelityHints"])
        self.assertNotIn("patterns", payload)
        self.assertNotIn("textures", payload)
        # Density is an arranging option; the faithful module may not offer it.
        self.assertNotIn("accompaniments", payload)
        self.assertIn("basic_pitch", payload["transcribers"])

    def test_arrange_options_carry_only_arranging_modes(self) -> None:
        payload = json.loads(self.fetch("/api/score/arrange/options").body)
        self.assertEqual(payload["fidelities"], ["hybrid", "arranged"])
        self.assertIn("hybrid", payload["fidelityHints"])
        self.assertIn("broken_chord", payload["patterns"])
        self.assertTrue(payload["textures"])
        self.assertEqual(payload["accompaniments"], ["light", "standard", "full"])
        self.assertEqual(payload["defaultAccompaniment"], "light")
        for level in payload["accompaniments"]:
            self.assertTrue(payload["accompanimentHints"][level].strip())
        self.assertIn("basic_pitch", payload["transcribers"])

    def test_every_difficulty_carries_an_explanation(self) -> None:
        for path in ("/api/score/transcribe/options", "/api/score/arrange/options"):
            payload = json.loads(self.fetch(path).body)
            self.assertEqual(set(payload["difficulties"]), {"1", "2", "3", "4"})
            for hint in payload["difficulties"].values():
                self.assertTrue(hint.strip())
            self.assertGreater(payload["maxPreviewSeconds"], 0)


class TestScoreTranscribe(ScoreApiTestCase):
    """The piano module: a cached piano recording, faithful fidelities only."""

    def test_a_cached_piano_recording_can_be_transcribed(self) -> None:
        key = self.register_audio()
        response = self.post_json("/api/score/transcribe", {"cacheKey": key})
        self.assertEqual(response.code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["cacheKey"], key)
        for section in ("grid", "handSplit", "quality", "arrangement", "key", "chords"):
            self.assertIn(section, payload)

    def test_the_default_fidelity_is_the_faithful_original(self) -> None:
        key = self.register_audio()
        payload = json.loads(self.post_json("/api/score/transcribe", {"cacheKey": key}).body)
        self.assertEqual(payload["request"]["fidelity"], "transcribed")

    def test_the_cached_title_becomes_the_score_title(self) -> None:
        key = self.register_audio()
        payload = json.loads(self.post_json("/api/score/transcribe", {"cacheKey": key}).body)
        self.assertEqual(payload["request"]["title"], "测试曲目")

    def test_options_are_passed_through(self) -> None:
        key = self.register_audio()
        payload = json.loads(
            self.post_json(
                "/api/score/transcribe",
                {"cacheKey": key, "fidelity": "as-played", "difficulty": 2, "fillChords": True},
            ).body
        )
        self.assertEqual(payload["request"]["fidelity"], "as-played")
        self.assertEqual(payload["request"]["difficulty"], 2)
        self.assertTrue(payload["request"]["fillChords"])

    def test_a_missing_cache_key_is_refused(self) -> None:
        response = self.post_json("/api/score/transcribe", {})
        self.assertEqual(response.code, 400)
        self.assertEqual(json.loads(response.body)["error"]["code"], "validation_error")

    def test_an_unknown_cache_key_is_a_404(self) -> None:
        response = self.post_json("/api/score/transcribe", {"cacheKey": "a" * 40})
        self.assertEqual(response.code, 404)
        self.assertEqual(json.loads(response.body)["error"]["code"], "not_found")

    def test_an_unsupported_fidelity_is_refused(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/transcribe", {"cacheKey": key, "fidelity": "perfect"}
        )
        self.assertEqual(response.code, 400)
        self.assertIn("supported", json.loads(response.body)["error"]["details"])

    def test_an_unsupported_difficulty_is_refused(self) -> None:
        key = self.register_audio()
        response = self.post_json("/api/score/transcribe", {"cacheKey": key, "difficulty": 9})
        self.assertEqual(response.code, 400)

    def test_an_arranging_fidelity_is_refused_with_a_pointer(self) -> None:
        """The piano module must not run the arranging pipeline under a lie."""
        key = self.register_audio()
        response = self.post_json(
            "/api/score/transcribe", {"cacheKey": key, "fidelity": "arranged"}
        )
        self.assertEqual(response.code, 400)
        self.assertIn("arrange", json.loads(response.body)["error"]["details"]["hint"])

    def test_arranging_options_are_refused_with_a_pointer(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/transcribe", {"cacheKey": key, "pattern": "broken_chord"}
        )
        self.assertEqual(response.code, 400)
        error = json.loads(response.body)["error"]
        self.assertIn("pattern", error["message"])
        self.assertIn("arrange", error["details"]["hint"])

    def test_the_density_option_is_refused_with_a_pointer(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/transcribe", {"cacheKey": key, "accompaniment": "light"}
        )
        self.assertEqual(response.code, 400)
        error = json.loads(response.body)["error"]
        self.assertIn("accompaniment", error["message"])
        self.assertIn("arrange", error["details"]["hint"])

    def test_a_domain_failure_is_reported_as_json_not_a_500(self) -> None:
        """A pipeline error must reach the user as a 400, not an internal error."""
        score_module.generate_score = exploding_score
        key = self.register_audio()
        response = self.post_json("/api/score/transcribe", {"cacheKey": key})
        self.assertEqual(response.code, 400)
        body = json.loads(response.body)
        self.assertEqual(body["error"]["code"], "validation_error")
        self.assertIn("no notes", body["error"]["message"])


class TestScoreArrange(ScoreApiTestCase):
    """The pop-to-piano module: a cached song, arranging fidelities only."""

    def test_a_cached_song_can_be_arranged(self) -> None:
        key = self.register_audio()
        response = self.post_json("/api/score/arrange", {"cacheKey": key})
        self.assertEqual(response.code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["cacheKey"], key)
        self.assertEqual(payload["request"]["fidelity"], "arranged")
        for section in ("grid", "handSplit", "quality", "arrangement", "key", "chords"):
            self.assertIn(section, payload)

    def test_options_are_passed_through(self) -> None:
        key = self.register_audio()
        payload = json.loads(
            self.post_json(
                "/api/score/arrange",
                {
                    "cacheKey": key,
                    "fidelity": "hybrid",
                    "difficulty": 2,
                    "pattern": "alberti_bass",
                },
            ).body
        )
        self.assertEqual(payload["request"]["fidelity"], "hybrid")
        self.assertEqual(payload["request"]["difficulty"], 2)
        self.assertEqual(payload["request"]["pattern"], "alberti_bass")

    def test_a_filling_option_is_refused_with_a_pointer(self) -> None:
        """fillChords only acts in the transcribed mode; accept nothing here."""
        key = self.register_audio()
        response = self.post_json(
            "/api/score/arrange", {"cacheKey": key, "fillChords": True}
        )
        self.assertEqual(response.code, 400)
        self.assertIn("transcribe", json.loads(response.body)["error"]["details"]["hint"])

    def test_the_density_option_is_passed_through(self) -> None:
        key = self.register_audio()
        payload = json.loads(
            self.post_json(
                "/api/score/arrange",
                {"cacheKey": key, "accompaniment": "full"},
            ).body
        )
        self.assertEqual(payload["request"]["accompaniment"], "full")
        # The arrangement itself reports the level it ran with.
        self.assertEqual(payload["arrangement"]["accompaniment"], "full")

    def test_the_default_density_is_light(self) -> None:
        key = self.register_audio()
        payload = json.loads(self.post_json("/api/score/arrange", {"cacheKey": key}).body)
        self.assertEqual(payload["request"]["accompaniment"], "light")

    def test_an_unknown_density_is_refused_with_the_supported_list(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/arrange", {"cacheKey": key, "accompaniment": "loud"}
        )
        self.assertEqual(response.code, 400)
        self.assertIn("supported", json.loads(response.body)["error"]["details"])


class TestScoreArrangeSections(ScoreApiTestCase):
    """GET /api/score/arrange/sections: the detected sections, mocked detection."""

    def _fake_analysis(self, audio_path, *, settings=None, transcriber="basic_pitch"):
        from services.arrangement.sections import Section, SectionAnalysis, VocalEvidence

        sections = (
            Section(0, 8, "intro", 0.9, high_motif=False, evidence={"part": "A"}),
            Section(8, 16, "verse", 0.7, evidence={"part": "B"}),
            Section(16, 24, "chorus", 0.85, high_motif=True, evidence={"part": "C"}),
        )
        return SectionAnalysis(
            sections=sections,
            vocal=VocalEvidence(coverage=(0.0, 0.5, 0.6), available=True, language="en"),
            duration_seconds=48.0,
            transcriber=transcriber,
        )

    def setUp(self) -> None:
        super().setUp()
        self._saved_sections = score_module.get_section_analysis
        score_module.get_section_analysis = self._fake_analysis

    def tearDown(self) -> None:
        score_module.get_section_analysis = self._saved_sections
        super().tearDown()

    def test_sections_endpoint_returns_the_labels_and_evidence(self) -> None:
        key = self.register_audio()
        response = self.fetch(f"/api/score/arrange/sections?cacheKey={key}", raise_error=False)
        self.assertEqual(response.code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["cacheKey"], key)
        self.assertEqual([s["label"] for s in payload["sections"]], ["intro", "verse", "chorus"])
        self.assertEqual(payload["sections"][0]["startBar"], 0)
        self.assertEqual(payload["sections"][0]["endBar"], 8)
        self.assertTrue(payload["sections"][2]["highMotif"])
        self.assertIn("part", payload["sections"][1]["evidence"])
        self.assertTrue(payload["vocal"]["available"])
        self.assertEqual(payload["vocal"]["language"], "en")

    def test_sections_endpoint_requires_a_cache_key(self) -> None:
        response = self.fetch("/api/score/arrange/sections", raise_error=False)
        self.assertEqual(response.code, 400)

    def test_sections_endpoint_unknown_key_is_a_404(self) -> None:
        response = self.fetch(
            f"/api/score/arrange/sections?cacheKey={'a' * 40}", raise_error=False
        )
        self.assertEqual(response.code, 404)

    def test_the_arrange_options_offer_the_section_labels(self) -> None:
        payload = json.loads(self.fetch("/api/score/arrange/options").body)
        self.assertEqual(
            payload["sectionLabels"], ["intro", "verse", "chorus", "interlude", "bridge", "outro"]
        )
        self.assertIn("chorus", payload["sectionTexturesDefaults"])
        self.assertIn("arpfull", payload["sectionTexturesDefaults"]["chorus"])

    def test_section_textures_are_passed_through(self) -> None:
        key = self.register_audio()
        payload = json.loads(
            self.post_json(
                "/api/score/arrange",
                {
                    "cacheKey": key,
                    "sectionTextures": {"verse": "block", "chorus": "arpfull"},
                },
            ).body
        )
        self.assertEqual(
            payload["request"]["sectionTextures"], {"verse": "block", "chorus": "arpfull"}
        )

    def test_an_unknown_section_label_is_refused(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/arrange", {"cacheKey": key, "sectionTextures": {"solo": "block"}}
        )
        self.assertEqual(response.code, 400)
        self.assertIn("supported", json.loads(response.body)["error"]["details"])

    def test_an_unknown_texture_in_section_textures_is_refused(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/arrange", {"cacheKey": key, "sectionTextures": {"verse": "disco"}}
        )
        self.assertEqual(response.code, 400)

    def test_section_textures_clash_with_a_manual_texture(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/arrange",
            {
                "cacheKey": key,
                "texture": "block",
                "sectionTextures": {"verse": "half"},
            },
        )
        self.assertEqual(response.code, 400)

    def test_the_transcribe_module_refuses_section_textures(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/transcribe",
            {"cacheKey": key, "sectionTextures": {"verse": "half"}},
        )
        self.assertEqual(response.code, 400)
        error = json.loads(response.body)["error"]
        self.assertIn("sectionTextures", error["message"])
        self.assertIn("arrange", error["details"]["hint"])

    def test_a_faithful_fidelity_is_refused_with_a_pointer(self) -> None:
        key = self.register_audio()
        response = self.post_json(
            "/api/score/arrange", {"cacheKey": key, "fidelity": "transcribed"}
        )
        self.assertEqual(response.code, 400)
        self.assertIn("transcribe", json.loads(response.body)["error"]["details"]["hint"])


class TestScoreTranscribeUpload(ScoreApiTestCase):
    def test_an_upload_without_a_file_is_refused(self) -> None:
        response = self.post_multipart("/api/score/transcribe/upload", {}, "", b"")
        self.assertEqual(response.code, 400)
        self.assertIn("multipart", json.loads(response.body)["error"]["message"])

    def test_an_unsupported_extension_is_refused(self) -> None:
        response = self.post_multipart(
            "/api/score/transcribe/upload", {"analyse": "0"}, "notes.txt", b"hello"
        )
        self.assertEqual(response.code, 400)
        self.assertIn("supported", json.loads(response.body)["error"]["details"])

    def test_an_empty_upload_is_refused(self) -> None:
        response = self.post_multipart(
            "/api/score/transcribe/upload", {"analyse": "0"}, "take.wav", b""
        )
        self.assertEqual(response.code, 400)

    def test_uploading_a_real_wav_stores_and_transcribes_it(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        response = self.post_multipart(
            "/api/score/transcribe/upload",
            {"fidelity": "as-played", "difficulty": "L2", "fillChords": "true", "analyse": "1"},
            "我的钢琴曲.wav",
            audio.read_bytes(),
        )
        self.assertEqual(response.code, 200, response.body)
        payload = json.loads(response.body)
        self.assertIn("upload", payload)
        self.assertEqual(payload["request"]["fidelity"], "as-played")
        self.assertEqual(payload["request"]["difficulty"], 2)
        self.assertTrue(payload["request"]["fillChords"])
        self.assertEqual(payload["upload"]["originalName"], "我的钢琴曲.wav")
        self.assertEqual(len(payload["upload"]["key"]), 64)

    def test_a_false_fill_chords_flag_stays_false(self) -> None:
        """The form field is a string; "false" must not read as truthy."""
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        payload = json.loads(
            self.post_multipart(
                "/api/score/transcribe/upload",
                {"fillChords": "false", "analyse": "1"},
                "take.wav",
                audio.read_bytes(),
            ).body
        )
        self.assertFalse(payload["request"]["fillChords"])

    def test_the_upload_can_be_stored_without_analysis(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        response = self.post_multipart(
            "/api/score/transcribe/upload", {"analyse": "0"}, "take.wav", audio.read_bytes()
        )
        self.assertEqual(response.code, 200, response.body)
        payload = json.loads(response.body)
        self.assertIn("upload", payload)
        self.assertNotIn("score", payload)


class TestScoreArrangeUpload(ScoreApiTestCase):
    def test_uploading_a_real_wav_stores_and_arranges_it(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        response = self.post_multipart(
            "/api/score/arrange/upload",
            {"fidelity": "hybrid", "difficulty": "L2", "pattern": "alberti_bass", "analyse": "1"},
            "流行歌.wav",
            audio.read_bytes(),
        )
        self.assertEqual(response.code, 200, response.body)
        payload = json.loads(response.body)
        self.assertIn("upload", payload)
        self.assertEqual(payload["request"]["fidelity"], "hybrid")
        self.assertEqual(payload["request"]["pattern"], "alberti_bass")
        self.assertEqual(payload["upload"]["originalName"], "流行歌.wav")

    def test_the_filename_becomes_the_default_title(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        payload = json.loads(
            self.post_multipart(
                "/api/score/arrange/upload", {"analyse": "1"}, "茉莉花.wav", audio.read_bytes()
            ).body
        )
        self.assertEqual(payload["request"]["title"], "茉莉花")

    def test_the_uploaded_key_is_playable_through_the_cache_route(self) -> None:
        audio = write_wav(self._tmp / "source.wav", seconds=1.0)
        payload = json.loads(
            self.post_multipart(
                "/api/score/arrange/upload", {"analyse": "0"}, "take.wav", audio.read_bytes()
            ).body
        )
        key = payload["upload"]["key"]
        response = self.fetch(f"/api/cache/audio/{key}", raise_error=False)
        self.assertIn(response.code, (200, 404))


class TestPianoTrack(ScoreApiTestCase):
    """The generated piano track is what follow-along plays by default."""

    def _generate_one(self, path: str = "/api/score/arrange") -> dict[str, object]:
        key = self.register_audio()
        return json.loads(self.post_json(path, {"cacheKey": key}).body)

    def test_the_response_advertises_the_piano_track(self) -> None:
        body = self._generate_one()
        # Versioned, so the frontend reloads instead of replaying the last score.
        self.assertTrue(body["pianoTrackUrl"].startswith("/api/score/piano?v="))
        self.assertTrue(body["piano"]["available"])
        # The UI must be able to say where the audio came from.
        self.assertEqual(body["piano"]["source"], "generated-from-score")

    def test_the_transcribe_module_serves_a_track_too(self) -> None:
        body = self._generate_one("/api/score/transcribe")
        self.assertTrue(body["piano"]["available"])

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
        self.assertIsNone(body["melodyTrackUrl"])
        self.assertIsNone(body["leftHandTrackUrl"])
        # Everything else still works.
        self.assertEqual(self.fetch("/api/score/musicxml").code, 200)
        self.assertEqual(self.fetch("/api/score/midi").code, 200)
        self.assertEqual(self.fetch("/api/score/piano", raise_error=False).code, 404)

    def test_the_response_advertises_the_per_hand_previews(self) -> None:
        body = self._generate_one()
        self.assertTrue(body["melodyTrackUrl"].startswith("/api/score/melody?v="))
        self.assertTrue(body["leftHandTrackUrl"].startswith("/api/score/left?v="))
        self.assertTrue(body["melody"]["available"])
        self.assertTrue(body["leftHand"]["available"])

    def test_the_melody_track_is_served_as_playable_audio(self) -> None:
        self._generate_one()
        response = self.fetch("/api/score/melody")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.headers["Content-Type"], "audio/wav")
        self.assertEqual(response.body[:4], b"RIFF")

    def test_the_left_hand_track_is_served_as_playable_audio(self) -> None:
        self._generate_one()
        response = self.fetch("/api/score/left")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.headers["Content-Type"], "audio/wav")
        self.assertEqual(response.body[:4], b"RIFF")

    def test_the_per_hand_tracks_404_before_any_generation(self) -> None:
        self.assertEqual(self.fetch("/api/score/melody", raise_error=False).code, 404)
        self.assertEqual(self.fetch("/api/score/left", raise_error=False).code, 404)


class TestTrackRange(ScoreApiTestCase):
    """A player seeks by asking for a byte window; the tracks must answer.

    Without Range support the only answer is "here is the whole file", and the
    route used to build that whole file in memory - tens of megabytes per client
    before the first byte of audio was written. The semantics under test are
    RFC 9110's, because they are what the browser sends and what it will retry
    against: 206 for a satisfiable window, 416 for one that starts past the end,
    and a plain 200 when the header cannot be parsed at all.
    """

    def _generate_one(self, name: str = "take.wav") -> str:
        key = self.register_audio(name=name)
        self.post_json("/api/score/arrange", {"cacheKey": key})
        return key

    def _size_of(self, path: str) -> int:
        response = self.fetch(path)
        self.assertEqual(response.code, 200)
        return int(response.headers["Content-Length"])

    def test_a_window_is_served_as_206_with_a_content_range(self) -> None:
        self._generate_one()
        size = self._size_of("/api/score/piano")
        response = self.fetch("/api/score/piano", headers={"Range": "bytes=0-1023"})
        self.assertEqual(response.code, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes 0-1023/{size}")
        self.assertEqual(len(response.body), 1024)

    def test_a_seek_mid_file_returns_exactly_that_window(self) -> None:
        self._generate_one()
        size = self._size_of("/api/score/piano")
        start = size // 2
        response = self.fetch("/api/score/piano", headers={"Range": f"bytes={start}-{start + 511}"})
        self.assertEqual(response.code, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes {start}-{start + 511}/{size}")
        self.assertEqual(len(response.body), 512)

    def test_an_open_ended_range_runs_to_the_last_byte(self) -> None:
        self._generate_one()
        size = self._size_of("/api/score/piano")
        start = size - 100
        response = self.fetch("/api/score/piano", headers={"Range": f"bytes={start}-"})
        self.assertEqual(response.code, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes {start}-{size - 1}/{size}")
        self.assertEqual(len(response.body), 100)

    def test_a_suffix_range_returns_the_last_bytes(self) -> None:
        self._generate_one()
        size = self._size_of("/api/score/piano")
        response = self.fetch("/api/score/piano", headers={"Range": "bytes=-512"})
        self.assertEqual(response.code, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes {size - 512}-{size - 1}/{size}")
        self.assertEqual(len(response.body), 512)

    def test_a_range_past_the_end_is_416(self) -> None:
        self._generate_one()
        size = self._size_of("/api/score/piano")
        response = self.fetch("/api/score/piano", headers={"Range": f"bytes={size + 10}-"})
        self.assertEqual(response.code, 416)
        self.assertEqual(response.headers["Content-Range"], f"bytes */{size}")

    def test_an_unparsable_range_is_ignored_not_refused(self) -> None:
        """RFC 9110: a Range the server cannot read is served in full."""
        self._generate_one()
        size = self._size_of("/api/score/piano")
        response = self.fetch("/api/score/piano", headers={"Range": "bytes=abc"})
        self.assertEqual(response.code, 200)
        self.assertEqual(int(response.headers["Content-Length"]), size)
        self.assertEqual(len(response.body), size)

    def test_every_track_route_is_range_aware(self) -> None:
        self._generate_one()
        for route in ("/api/score/piano", "/api/score/melody", "/api/score/left"):
            with self.subTest(route=route):
                response = self.fetch(route, headers={"Range": "bytes=0-99"})
                self.assertEqual(response.code, 206)
                self.assertEqual(len(response.body), 100)

    def test_the_midi_download_can_be_resumed(self) -> None:
        """An interrupted download must continue, not restart from zero."""
        self._generate_one()
        size = self._size_of("/api/score/midi")
        response = self.fetch("/api/score/midi", headers={"Range": "bytes=0-9"})
        self.assertEqual(response.code, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes 0-9/{size}")
        # bytes=0-9 is ten bytes: the MThd header plus its four-byte length, then
        # the first two bytes of the division field.
        self.assertEqual(response.body, b"MThd\x00\x00\x00\x06\x00\x01")
        self.assertIn("attachment", response.headers["Content-Disposition"])


class TestTrackAddressing(ScoreApiTestCase):
    """A track URL must name one song's file, not whichever file is newest.

    The audio, the notation and the cursor all come from one generation. If the
    routes answer "whichever file is newest", a second generation anywhere makes
    the first song's player play the second song's audio over the first song's
    notation - which is the one thing a follow-along view cannot get wrong.
    """

    def _generate_one(self, name: str) -> str:
        key = self.register_audio(name=name)
        self.post_json("/api/score/arrange", {"cacheKey": key})
        return key

    def test_the_track_url_carries_the_cache_key(self) -> None:
        key = self._generate_one("take.wav")
        body = json.loads(self.post_json("/api/score/arrange", {"cacheKey": key}).body)
        for field in ("pianoTrackUrl", "melodyTrackUrl", "leftHandTrackUrl", "midiUrl"):
            with self.subTest(field=field):
                self.assertIn(f"cacheKey={key}", body[field])

    def test_an_addressed_url_serves_its_own_song_not_the_newest(self) -> None:
        first = self._generate_one("first.wav")
        second = self._generate_one("second.wav")
        scores = self._tmp / "scores"
        # Make the older song identifiable, then ask for both by key.
        first_file = scores / f"{first}.piano.wav"
        second_file = scores / f"{second}.piano.wav"
        first_file.write_bytes(b"RIFF-first-song")
        second_file.write_bytes(b"RIFF-second-song")
        # Pin the mtimes: back-to-back writes can land on the same filesystem
        # timestamp tick (probed: ~1 ms clock quantization on this machine),
        # and "newest" then has no clock basis to stand on.
        now = time.time()
        os.utime(first_file, (now - 60, now - 60))
        os.utime(second_file, (now, now))
        addressed_first = self.fetch(f"/api/score/piano?cacheKey={first}")
        addressed_second = self.fetch(f"/api/score/piano?cacheKey={second}")
        self.assertEqual(addressed_first.body, b"RIFF-first-song")
        self.assertEqual(addressed_second.body, b"RIFF-second-song")
        # Without a key the old behaviour is unchanged: the newest file.
        self.assertEqual(self.fetch("/api/score/piano").body, b"RIFF-second-song")

    def test_a_range_request_is_answered_on_an_addressed_url(self) -> None:
        key = self._generate_one("take.wav")
        size = int(self.fetch(f"/api/score/piano?cacheKey={key}").headers["Content-Length"])
        response = self.fetch(f"/api/score/piano?cacheKey={key}", headers={"Range": "bytes=0-15"})
        self.assertEqual(response.code, 206)
        self.assertEqual(response.headers["Content-Range"], f"bytes 0-15/{size}")
        self.assertEqual(len(response.body), 16)

    def test_an_unknown_cache_key_is_a_404_not_the_newest_file(self) -> None:
        self._generate_one("take.wav")
        missing = "0" * 40
        self.assertEqual(
            self.fetch(f"/api/score/piano?cacheKey={missing}", raise_error=False).code, 404
        )

    def test_a_malformed_cache_key_is_refused(self) -> None:
        self._generate_one("take.wav")
        response = self.fetch("/api/score/piano?cacheKey=../secrets", raise_error=False)
        self.assertEqual(response.code, 400)

    def test_the_notation_is_addressed_the_same_way(self) -> None:
        key = self._generate_one("take.wav")
        response = self.fetch(f"/api/score/musicxml?cacheKey={key}")
        self.assertEqual(response.code, 200)
        self.assertIn("score-partwise", response.body.decode("utf-8"))


class TestScoreOutputs(ScoreApiTestCase):
    def _generate_one(self) -> None:
        key = self.register_audio()
        self.post_json("/api/score/arrange", {"cacheKey": key})

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
