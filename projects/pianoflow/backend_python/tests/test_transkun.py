"""Tests for the Transkun bridge.

None of these need torch. The transcriber is a subprocess boundary, and the two things that
can go wrong at that boundary are a wrong argument vector and a wrong answer from the
worker - both are testable with the subprocess stubbed. The genuine torch inference was
verified once on the reference recording and its numbers are recorded in ``transkun.py``.

The argument-vector bug these tests exist to prevent is not hypothetical: the worker was
first called with ``main(sys.argv)`` instead of ``main(sys.argv[1:])``, which shifted every
positional by one and made argparse report the real output path as unrecognised. Nothing
inside either file would have caught it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core.errors import ValidationError
from services.audio_analysis import transkun as T
from services.audio_analysis.polyphony import NoteEvent

AUDIO = Path(r"D:\Program Files\pianoflow\cache\audio\cde3a16bbb536a15661e1f25efc98ca059dbde2c.wav")


def _payload(notes: list[dict], *, pseudo: int = 0, raw: int | None = None) -> str:
    return json.dumps(
        {
            "notes": notes,
            "pseudoEvents": pseudo,
            "meta": {
                "audioSeconds": 12.0,
                "parameters": 13610000,
                "rawEvents": raw if raw is not None else len(notes) + pseudo,
                "sampleRate": 44100,
            },
        }
    )


class _FakeRun:
    """Stand-in for ``subprocess.run`` that writes the payload the worker would write."""

    def __init__(
        self, payload: str | None = None, *, returncode: int = 0, stderr: str = ""
    ) -> None:
        self.payload = payload
        self.returncode = returncode
        self.stderr = stderr
        self.command: list[str] | None = None

    def __call__(self, command, **kwargs):
        self.command = command
        out = Path(command[3])
        if self.payload is not None and self.returncode == 0:
            out.write_text(self.payload, encoding="utf-8")
        return subprocess.CompletedProcess(
            command, self.returncode, stdout="ok", stderr=self.stderr
        )


def _facade(tmp_path: Path, **kwargs) -> tuple[T.TranskunTranscriber, Path]:
    """A transcriber with a stub interpreter, plus the clip file it will be handed."""
    interpreter = tmp_path / "python.exe"
    interpreter.write_text("", encoding="utf-8")
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"")
    return T.TranskunTranscriber(str(interpreter), **kwargs), clip


class TestDropPseudoEvents:
    """The rule, and the evidence it rests on.

    Over the reference recording the Semi-CRF emitted 101 non-notes of 1315 events, with
    only two distinct pitches (-64 and -67) at velocity 127. Both are negative, so no MIDI
    instrument produces them. Filtering on the piano's own 21..108 is safe rather than
    convenient: the lowest kept note there was 33 and no kept note was at or below 24, so
    the rule falls in an empty gap.
    """

    def test_negative_pitches_are_not_notes(self):
        notes, artifacts = T.drop_pseudo_events(
            [
                {"pitch": -64, "start": 0.1, "end": 2.5, "velocity": 127},
                {"pitch": -67, "start": 2.6, "end": 5.0, "velocity": 127},
            ]
        )
        assert notes == []
        assert len(artifacts) == 2

    def test_the_boundary_of_the_piano_is_kept(self):
        notes, _ = T.drop_pseudo_events(
            [{"pitch": 21, "start": 0, "end": 1}, {"pitch": 108, "start": 1, "end": 2}]
        )
        assert [n["pitch"] for n in notes] == [21, 108]

    def test_one_step_outside_is_dropped(self):
        notes, artifacts = T.drop_pseudo_events(
            [{"pitch": 20, "start": 0, "end": 1}, {"pitch": 109, "start": 1, "end": 2}]
        )
        assert notes == []
        assert len(artifacts) == 2

    def test_a_loud_legitimate_note_is_kept(self):
        # Velocity is recorded for auditing, deliberately not part of the rule: a
        # fortissimo note has velocity 127 too.
        notes, _ = T.drop_pseudo_events([{"pitch": 60, "start": 0, "end": 1, "velocity": 127}])
        assert len(notes) == 1

    def test_mixed_input_is_split_not_reordered(self):
        records = [
            {"pitch": 60, "start": 2.0, "end": 3.0},
            {"pitch": -64, "start": 0.0, "end": 1.0},
            {"pitch": 62, "start": 4.0, "end": 5.0},
        ]
        notes, artifacts = T.drop_pseudo_events(records)
        assert [n["start"] for n in notes] == [2.0, 4.0]
        assert len(artifacts) == 1


class TestWorkerResolution:
    def test_an_explicit_path_that_exists_is_used(self, tmp_path):
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        assert T._worker_python(str(interpreter)) == interpreter

    def test_an_explicit_path_that_does_not_exist_is_an_error(self, tmp_path):
        with pytest.raises(ValidationError, match="does not exist"):
            T._worker_python(str(tmp_path / "absent.exe"))

    def test_a_missing_environment_variable_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIANOFLOW_TRANSKUN_PYTHON", str(tmp_path / "absent.exe"))
        with pytest.raises(ValidationError, match="points at nothing"):
            T._worker_python(None)

    def test_the_environment_variable_is_honoured(self, monkeypatch, tmp_path):
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        monkeypatch.setenv("PIANOFLOW_TRANSKUN_PYTHON", str(interpreter))
        assert T._worker_python(None) == interpreter

    def test_nothing_installed_says_how_to_install_it(self, monkeypatch):
        monkeypatch.delenv("PIANOFLOW_TRANSKUN_PYTHON", raising=False)
        monkeypatch.setattr(T, "DEFAULT_PYTHON_CANDIDATES", ())
        with pytest.raises(ValidationError) as caught:
            T._worker_python(None)
        assert "PIANOFLOW_TRANSKUN_PYTHON" in caught.value.details["hint"]

    def test_availability_is_a_question_not_an_exception(self, monkeypatch):
        monkeypatch.setattr(T, "DEFAULT_PYTHON_CANDIDATES", ())
        monkeypatch.delenv("PIANOFLOW_TRANSKUN_PYTHON", raising=False)
        assert T.TranskunTranscriber.is_available() is False


class TestWorkerLocation:
    def test_the_worker_script_ships_with_the_repository(self):
        assert T.WORKER.is_file()
        assert T.WORKER.name == "transkun_worker.py"

    def test_the_worker_does_not_import_this_package(self):
        # It runs under a different interpreter with its own torch, so importing from
        # backend_python/src would fail there even though it resolves here.
        source = T.WORKER.read_text(encoding="utf-8")
        assert "services.audio_analysis" not in source
        assert "from core" not in source

    def test_the_worker_does_not_pass_its_own_path_to_argparse(self):
        # The bug this whole file exists to prevent. sys.argv[0] is the script path; giving
        # it to parse_args makes every positional shift by one.
        source = T.WORKER.read_text(encoding="utf-8")
        assert "main(sys.argv)" not in source
        assert "main(sys.argv[1:])" in source


class TestTranscription:
    def test_events_become_the_same_note_type_as_basic_pitch(self, monkeypatch, tmp_path):
        fake = _FakeRun(_payload([{"start": 1.0, "end": 1.5, "pitch": 60, "velocity": 100}]))
        monkeypatch.setattr(T.subprocess, "run", fake)
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"")
        notes = T.TranskunTranscriber(str(interpreter))._run(clip)
        assert len(notes) == 1
        assert isinstance(notes[0], NoteEvent)
        assert notes[0].start == 1.0
        assert notes[0].duration == pytest.approx(0.5)
        assert notes[0].midi == 60
        assert notes[0].amplitude == pytest.approx(100 / 127)

    def test_the_argument_vector_puts_the_flags_after_the_positionals(self, monkeypatch, tmp_path):
        fake = _FakeRun(_payload([]))
        monkeypatch.setattr(T.subprocess, "run", fake)
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"")
        T.TranskunTranscriber(str(interpreter), device="cpu")._run(clip)
        assert fake.command is not None
        assert fake.command[1].endswith("transkun_worker.py")
        assert fake.command[2].endswith("clip.wav")
        assert fake.command[3].endswith(".json")
        assert fake.command[4] == "--device"

    def test_a_missing_audio_file_is_rejected_before_any_process_starts(self, tmp_path):
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        with pytest.raises(ValidationError, match="not found"):
            T.TranskunTranscriber(str(interpreter))._run(tmp_path / "absent.wav")

    def test_audio_too_short_to_transcribe_is_rejected(self, tmp_path):
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        transcriber = T.TranskunTranscriber(str(interpreter))
        with pytest.raises(ValidationError, match="too short"):
            transcriber.transcribe_array(np.zeros(100, dtype=np.float32), 44100)

    def test_a_worker_failure_carries_the_reason(self, monkeypatch, tmp_path):
        fake = _FakeRun(returncode=2, stderr="ModuleNotFoundError: No module named 'ncls'")
        monkeypatch.setattr(T.subprocess, "run", fake)
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"")
        with pytest.raises(ValidationError) as caught:
            T.TranskunTranscriber(str(interpreter))._run(clip)
        assert "ncls" in caught.value.details["reason"]

    def test_silence_and_no_output_file_is_still_an_error(self, monkeypatch, tmp_path):
        # returncode 0 but nothing written means the worker died in a way that swallowed
        # itself; that must not read as an empty transcription.
        fake = _FakeRun(returncode=0, stderr="")
        monkeypatch.setattr(T.subprocess, "run", fake)
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"")
        with pytest.raises(ValidationError, match="no output"):
            T.TranskunTranscriber(str(interpreter))._run(clip)

    def test_a_note_with_no_length_still_gets_one_frame(self, monkeypatch, tmp_path):
        # A zero-length note would render as an instant click; a 10 ms floor keeps the
        # event real without pretending to know a duration the model did not give.
        fake = _FakeRun(_payload([{"start": 2.0, "end": 2.0, "pitch": 60, "velocity": 64}]))
        monkeypatch.setattr(T.subprocess, "run", fake)
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"")
        notes = T.TranskunTranscriber(str(interpreter))._run(clip)
        assert notes[0].duration == pytest.approx(0.01)

    def test_velocity_is_clamped_into_the_midi_range(self, monkeypatch, tmp_path):
        fake = _FakeRun(
            _payload(
                [
                    {"start": 0.0, "end": 1.0, "pitch": 60, "velocity": 0},
                    {"start": 1.0, "end": 2.0, "pitch": 60, "velocity": 300},
                ]
            )
        )
        monkeypatch.setattr(T.subprocess, "run", fake)
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"")
        notes = T.TranskunTranscriber(str(interpreter))._run(clip)
        assert [n.amplitude for n in notes] == [0.0, 1.0]


@pytest.mark.skipif(not AUDIO.is_file(), reason="the reference recording is not present")
def test_the_reference_recording_is_reachable_for_manual_runs() -> None:
    """A guard, not a test: this file is the one input every number in transkun.py quotes."""
    assert AUDIO.suffix == ".wav"
    assert AUDIO.stat().st_size > 0
