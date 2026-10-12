"""Analysis core tests, driven by synthesised audio so nothing hits a network.

The fixtures are intentionally simple (sine tones, arpeggios, a vibrato line)
because the point is to prove the DSP wiring works, not to benchmark accuracy.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
import soundfile as sf

from core.errors import NotFoundError, ValidationError
from services.audio_analysis.chords import chord_progression, detect_chords
from services.audio_analysis.models import Note, midi_to_freq, midi_to_pitch
from services.audio_analysis.ornaments import (
    build_contour,
    contour_summary,
    detect_ornaments,
    leap_statistics,
)
from services.audio_analysis.pipeline import analyze_array, load_audio, summarise
from services.audio_analysis.pitch import PitchOptions, detect_notes, estimate_key, estimate_tempo

SR = 22050


def tone(freq: float, seconds: float, *, sr: int = SR, harmonics: int = 1) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    wave = np.zeros_like(t)
    for index in range(1, harmonics + 1):
        wave += np.sin(2 * np.pi * freq * index * t) / index
    return wave * 0.4


def silence(seconds: float, *, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr))


def concat(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts)


def chord(*pitches: int, seconds: float = 1.0, harmonics: int = 2) -> np.ndarray:
    """A chord: all pitches sound *at the same time*, so sum the tones.

    Concatenating them would produce a melody, not a chord.
    """
    total = np.zeros(int(seconds * SR))
    for midi in pitches:
        total += tone(midi_to_freq(midi), seconds, harmonics=harmonics)
    return total * 0.3


def write_wav(path, y: np.ndarray, sr: int = SR) -> None:
    sf.write(path, y, sr)


# --- value objects --------------------------------------------------------


def test_midi_to_pitch_and_freq():
    assert midi_to_pitch(60) == "C4"
    assert midi_to_pitch(61) == "C#4"
    assert midi_to_pitch(69) == "A4"
    assert midi_to_freq(69) == pytest.approx(440.0)
    assert midi_to_freq(60) == pytest.approx(261.6256, rel=1e-3)


def test_note_end_and_serialisation():
    note = Note(start=1.0, duration=0.5, midi=64)
    assert note.end == 1.5
    assert note.pitch_name == "E4"
    assert note.to_dict()["pitch"] == "E4"


# --- pitch ----------------------------------------------------------------


def test_detects_a_single_a4():
    y = concat(tone(440.0, 1.2), silence(0.2))
    notes = detect_notes(y, SR)[0]
    assert notes, "expected at least one note"
    assert notes[0].midi == 69
    assert notes[0].duration == pytest.approx(1.2, abs=0.15)


def test_detects_a_three_note_phrase():
    y = concat(
        tone(midi_to_freq(60), 0.5),
        tone(midi_to_freq(62), 0.5),
        tone(midi_to_freq(64), 0.5),
    )
    notes, _, _ = detect_notes(y, SR)
    pitches = [note.midi for note in notes]
    assert set(pitches) >= {60, 62, 64}


def test_silence_produces_no_notes():
    notes, _, _ = detect_notes(silence(1.5), SR)
    assert notes == []


def test_short_blips_are_dropped():
    y = concat(tone(440.0, 1.0), tone(300.0, 0.02), tone(440.0, 1.0))
    notes, _, _ = detect_notes(y, SR)
    assert all(note.duration > 0.05 for note in notes)


def test_octave_is_not_split_into_two_notes():
    y = tone(midi_to_freq(57), 1.5)  # A3
    notes, _, _ = detect_notes(y, SR)
    assert len(notes) == 1


# --- tempo & key ----------------------------------------------------------


def test_tempo_of_a_click_track():
    bpm = 120.0
    click = np.zeros(int(SR * 60 / bpm))
    click[0] = 1.0
    track = np.tile(click, 8)
    assert estimate_tempo(track, SR) == pytest.approx(bpm, abs=6)


def test_tempo_is_none_for_silence():
    assert estimate_tempo(silence(2.0), SR) is None


def test_key_of_a_c_major_arpeggio():
    y = concat(
        tone(midi_to_freq(60), 0.4),  # C
        tone(midi_to_freq(64), 0.4),  # E
        tone(midi_to_freq(67), 0.4),  # G
        tone(midi_to_freq(72), 0.4),  # C
    )
    root, quality, score = estimate_key(y, SR)
    assert root == 0
    assert quality == "major"
    assert score > 0


# --- chords ---------------------------------------------------------------


def test_detects_a_c_major_chord():
    events = detect_chords(chord(60, 64, 67), SR)
    assert events
    assert events[0].name == "C"
    assert events[0].confidence > 0.5


def test_detects_a_minor_chord():
    events = detect_chords(chord(57, 60, 64), SR)  # A C E
    assert events
    assert events[0].name == "Am"


def test_chord_confidence_is_a_bounded_cosine():
    events = detect_chords(chord(60, 64, 67), SR)
    assert all(0.0 <= event.confidence <= 1.0 for event in events)


def test_chord_progression_collapses_only_consecutive_repeats():
    y = concat(
        chord(60, 64, 67, seconds=0.8),
        chord(57, 60, 64, seconds=0.8),
        chord(53, 57, 60, seconds=0.8),
        chord(60, 64, 67, seconds=0.8),
    )
    progression = chord_progression(detect_chords(y, SR))
    assert len(progression) >= 2
    # no two identical chords back to back
    assert all(a != b for a, b in itertools.pairwise(progression))
    # revisiting a chord is legitimate, so C may appear twice
    assert progression.count("C") <= 2


def test_no_chords_for_silence():
    assert detect_chords(silence(1.0), SR) == []


# --- ornaments & contour --------------------------------------------------


def test_grace_note_before_a_long_note():
    notes = [
        Note(start=0.00, duration=0.60, midi=60),
        Note(start=0.62, duration=0.08, midi=62),  # short lead-in
        Note(start=0.71, duration=0.60, midi=64),  # resolves
    ]
    graces = [item for item in detect_ornaments(notes) if item.kind == "grace"]
    assert graces
    assert graces[0].pitch == 62


def test_long_note_is_not_a_grace_note():
    notes = [
        Note(start=0.0, duration=0.6, midi=60),
        Note(start=0.7, duration=0.6, midi=62),
    ]
    assert [item for item in detect_ornaments(notes) if item.kind == "grace"] == []


def test_trill_needs_real_alternation():
    slow = [Note(start=i * 0.4, duration=0.35, midi=60 if i % 2 == 0 else 62) for i in range(6)]
    assert [item for item in detect_ornaments(slow) if item.kind == "trill"] == []

    fast = [Note(start=i * 0.07, duration=0.06, midi=60 if i % 2 == 0 else 62) for i in range(12)]
    assert [item for item in detect_ornaments(fast) if item.kind == "trill"]


def test_contour_reports_direction():
    notes = [
        Note(start=0.0, duration=0.5, midi=60),
        Note(start=0.6, duration=0.5, midi=64),
        Note(start=1.2, duration=0.5, midi=67),
        Note(start=1.8, duration=0.5, midi=64),
    ]
    contour = build_contour(notes, bucket_seconds=1.5)
    assert contour
    directions = {segment.direction for segment in contour}
    assert "up" in directions
    assert contour_summary(contour)


def test_contour_of_one_note_is_empty():
    assert build_contour([Note(start=0, duration=1, midi=60)]) == []


def test_leap_statistics():
    stepwise = [Note(start=i * 0.3, duration=0.25, midi=60 + i) for i in range(6)]
    stats = leap_statistics(stepwise)
    assert stats["range"] == 5
    assert stats["leaps"] == 0

    leaping = [
        Note(start=0.0, duration=0.4, midi=48),
        Note(start=0.5, duration=0.4, midi=60),
        Note(start=1.0, duration=0.4, midi=48),
    ]
    assert leap_statistics(leaping)["leaps"] == 2


# --- pipeline -------------------------------------------------------------


def test_pipeline_end_to_end_on_a_scale():
    pitches = [60, 62, 64, 65, 67]
    y = concat(*(tone(midi_to_freq(p), 0.4, harmonics=2) for p in pitches))
    result = analyze_array(y, SR)

    assert result.sample_rate == SR
    assert result.duration == pytest.approx(2.0, abs=0.05)
    assert result.note_count >= 4
    assert result.key_name is not None
    assert result.contour
    assert "warnings" in result.to_dict()

    summary = summarise(result)
    assert summary["noteCount"] == result.note_count
    assert isinstance(summary["chordProgression"], list)
    assert summary["leaps"]["range"] >= 0


def test_pipeline_warns_when_there_is_no_melody():
    result = analyze_array(silence(2.0), SR)
    assert result.note_count == 0
    assert any("旋律" in warning for warning in result.warnings)


def test_options_are_honoured():
    y = tone(midi_to_freq(60), 2.0)
    strict = PitchOptions(min_note_seconds=0.5, min_consistency=0.6)
    result = analyze_array(y, SR, options=strict, with_slides=False)
    assert result.note_count == 1


def test_load_audio_rejects_missing_and_unsupported(tmp_path):
    with pytest.raises(NotFoundError):
        load_audio(tmp_path / "nope.wav")

    bad = tmp_path / "clip.txt"
    bad.write_text("not audio", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_audio(bad)


def test_load_audio_reads_a_real_file(tmp_path):
    path = tmp_path / "clip.wav"
    write_wav(path, tone(440.0, 0.5))
    y, sr = load_audio(path)
    assert sr == 22050
    assert y.size > 0
