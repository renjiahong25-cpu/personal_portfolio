"""Tests for the score service: request validation and the full pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.errors import ValidationError
from services.score.service import (
    DIFFICULTY_HINTS,
    MAX_PREVIEW_SECONDS,
    PATTERN_NAMES,
    ScoreRequest,
    generate_score,
    validate_request,
)


def write_tone_wav(path: Path, *, seconds: float = 4.0, sr: int = 22050) -> Path:
    """A short chord so the pipeline has something it can actually transcribe."""
    import soundfile as sf

    t = np.arange(int(sr * seconds)) / sr
    # C-E-G arpeggio plus a held top note: a bass plus a melody line.
    signal = np.zeros_like(t)
    for index, (freq, start) in enumerate(
        [(261.63, 0.0), (329.63, 1.0), (392.0, 2.0), (523.25, 0.0)]
    ):
        offset = int(start * sr)
        end = min(offset + sr, len(signal))
        segment = np.arange(end - offset) / sr
        signal[offset:end] += 0.4 * np.sin(2 * np.pi * freq * segment)
        del index
    peak = float(np.max(np.abs(signal))) or 1.0
    sf.write(path, (signal / peak * 0.8).astype(np.float32), sr)
    return path


def test_the_defaults_are_a_reasonable_piano_arrangement() -> None:
    """Default is ``arranged``, not ``hybrid``.

    ``hybrid`` only rewrites the bars that failed quality, and on real material
    that was 2 bars out of 34. The other 32 kept the raw transcription, whose right
    hand is the vocal melody: 100% single notes. So the default produced something
    that was neither a faithful transcription worth reading nor a piano
    arrangement worth playing. ``arranged`` rebuilds every bar.
    """
    request = validate_request({})
    assert request.fidelity == "arranged"
    assert request.difficulty == 3
    assert request.pattern == ""
    assert request.preview_seconds == 0.0


def test_a_difficulty_may_be_given_as_l3() -> None:
    """The UI shows L1..L4, so the API has to accept that spelling."""
    assert validate_request({"difficulty": "L3"}).difficulty == 3
    assert validate_request({"difficulty": "l1"}).difficulty == 1


def test_every_supported_fidelity_is_accepted() -> None:
    for fidelity in ("as-played", "hybrid", "arranged"):
        assert validate_request({"fidelity": fidelity}).fidelity == fidelity


def test_every_supported_pattern_is_accepted() -> None:
    for pattern in PATTERN_NAMES:
        assert validate_request({"pattern": pattern}).pattern == pattern


def test_an_unknown_fidelity_is_refused_with_the_supported_list() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_request({"fidelity": "perfect"})
    assert "supported" in excinfo.value.details


def test_an_unknown_pattern_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_request({"pattern": "disco"})


def test_an_out_of_range_difficulty_is_refused() -> None:
    for value in (0, 5, -1):
        with pytest.raises(ValidationError):
            validate_request({"difficulty": value})


def test_a_boolean_difficulty_is_refused() -> None:
    """True is an int in Python; accepting it would silently mean difficulty 1."""
    with pytest.raises(ValidationError):
        validate_request({"difficulty": True})


def test_a_non_numeric_difficulty_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_request({"difficulty": "hard"})


def test_a_preview_beyond_the_limit_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_request({"previewSeconds": MAX_PREVIEW_SECONDS + 1})


def test_a_negative_preview_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_request({"previewSeconds": -5})


def test_a_non_numeric_preview_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_request({"previewSeconds": "soon"})


def test_a_non_string_title_is_refused() -> None:
    with pytest.raises(ValidationError):
        validate_request({"title": 42})


def test_the_default_title_fills_an_absent_one() -> None:
    request = validate_request({}, default_title="星辰大海")
    assert request.title == "星辰大海"
    assert validate_request({"title": "given"}, default_title="other").title == "given"


def test_a_very_long_title_is_truncated() -> None:
    request = validate_request({"title": "x" * 500})
    assert len(request.title) == 200


def test_every_difficulty_has_a_hint_for_the_ui() -> None:
    """A difficulty the UI cannot explain is a difficulty nobody picks on purpose."""
    assert set(DIFFICULTY_HINTS) == {1, 2, 3, 4}
    assert all(hint.strip() for hint in DIFFICULTY_HINTS.values())


def test_every_note_is_returned_with_its_exact_timing(tmp_path: Path) -> None:
    """Follow-along mode lights a piano key per note, so times must be real.

    The per-measure note counts the response used to carry cannot say *when* a
    note sounds, so the cursor could only guess.
    """
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=6.0)
    result = generate_score(audio, ScoreRequest())
    notes = result.score.note_timeline()
    assert len(notes) == result.score.note_count

    beat = 60.0 / result.score.tempo_bpm
    for note in notes:
        assert note["time"] >= 0
        assert note["duration"] > 0
        assert note["hand"] in ("right", "left")
        assert note["staff"] == (1 if note["hand"] == "right" else 2)
        assert 0 < note["midi"] < 128
        assert note["pitch"]
    assert [n["time"] for n in notes] == sorted(n["time"] for n in notes)

    # Onsets must sit on the notated 16th grid, measured from where bar 1 actually
    # falls in the audio. Without the phase this measurement is meaningless, which
    # is precisely the bug this test now guards.
    sixteenth = beat / 4
    phase = result.score.phase_seconds
    off_grid = [
        n
        for n in notes
        if abs((n["time"] - phase) / sixteenth - round((n["time"] - phase) / sixteenth)) > 0.02
    ]
    assert not off_grid, f"{len(off_grid)} notes are off the 16th grid"


def test_the_timeline_reaches_the_response(tmp_path: Path) -> None:
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=4.0)
    payload = generate_score(audio, ScoreRequest()).to_dict()
    score = payload["score"]
    assert len(score["notes"]) == score["noteCount"]
    assert len(score["pitchRange"]) == 2
    low, high = score["pitchRange"]
    assert low <= high


def test_pitch_range_follows_the_actual_notes(tmp_path: Path) -> None:
    from services.arrangement.piano_score import Measure, PianoScore, ScoredNote

    empty = PianoScore(measures=(), tempo_bpm=120.0, beats_per_bar=4)
    # A silent score still needs a sensible keyboard range to draw.
    assert empty.pitch_range() == (48, 84)

    score = PianoScore(
        measures=(Measure(index=0, right=(ScoredNote(0, 0.0, 1.0, 72),)),),
        tempo_bpm=120.0,
        beats_per_bar=4,
    )
    assert score.pitch_range() == (72, 72)


def test_a_request_serialises() -> None:
    payload = validate_request({"fidelity": "arranged", "difficulty": 2}).to_dict()
    assert payload["fidelity"] == "arranged"
    assert payload["difficulty"] == 2


def test_the_pipeline_produces_a_two_staff_score(tmp_path: Path) -> None:
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=4.0)
    result = generate_score(audio, ScoreRequest(preview_seconds=0), output_dir=tmp_path / "out")
    assert result.score.note_count > 0
    assert len(result.score.measures) > 0
    assert result.score.right_clef == "treble"
    assert "<staves>2</staves>" in result.musicxml
    assert result.musicxml_path is not None and result.musicxml_path.is_file()
    assert result.midi_path is not None and result.midi_path.is_file()


def test_the_response_carries_every_stage_the_ui_shows(tmp_path: Path) -> None:
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=4.0)
    result = generate_score(audio, ScoreRequest())
    payload = result.to_dict()
    for key in ("grid", "handSplit", "quality", "arrangement", "key", "chords", "score"):
        assert key in payload, key
    assert payload["score"]["measureCount"] == len(result.score.measures)


def test_a_preview_truncates_and_says_so(tmp_path: Path) -> None:
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=8.0)
    full = generate_score(audio, ScoreRequest())
    preview = generate_score(audio, ScoreRequest(preview_seconds=3.0))
    assert not full.truncated
    assert preview.truncated
    assert preview.analysed_seconds == pytest.approx(3.0, abs=0.2)
    assert preview.analysed_seconds < full.analysed_seconds


def test_each_fidelity_produces_a_valid_score(tmp_path: Path) -> None:
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=4.0)
    for fidelity in ("as-played", "hybrid", "arranged"):
        result = generate_score(audio, ScoreRequest(fidelity=fidelity))
        assert result.score.note_count > 0, fidelity


def test_each_difficulty_produces_a_valid_score(tmp_path: Path) -> None:
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=4.0)
    counts = {}
    for difficulty in (1, 2, 3, 4):
        result = generate_score(audio, ScoreRequest(difficulty=difficulty))
        counts[difficulty] = result.score.note_count
        assert result.score.note_count > 0, difficulty
    assert counts[3] >= counts[1]


def test_an_empty_recording_is_refused(tmp_path: Path) -> None:
    import soundfile as sf

    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(22050, dtype=np.float32), 22050)
    with pytest.raises(ValidationError):
        generate_score(path, ScoreRequest())


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        generate_score(tmp_path / "nope.wav", ScoreRequest())


def test_no_measure_overflows_when_the_whole_pipeline_runs(tmp_path: Path) -> None:
    """The invariant the score writer exists to protect, checked end to end."""
    audio = write_tone_wav(tmp_path / "tone.wav", seconds=6.0)
    result = generate_score(audio, ScoreRequest(fidelity="arranged"))
    for measure in result.score.measures:
        for staff in (measure.right, measure.left):
            for note in staff:
                assert note.beat >= 0.0
                assert note.end_beat <= result.score.beats_per_bar + 1e-6
