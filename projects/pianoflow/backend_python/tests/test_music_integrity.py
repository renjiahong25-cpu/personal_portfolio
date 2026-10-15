"""Regressions for audible errors: phase units, contour and intra-bar harmony."""

from __future__ import annotations

import numpy as np
import pytest

from core.errors import ValidationError
from services.arrangement.arranger import arrange, clean_melody_artifacts, fold_melody_to_range
from services.arrangement.grid import BarGrid, estimate_phase
from services.arrangement.hand_split import HandAssignment, hand_events_by_bar
from services.arrangement.piano_midi import write_midi
from services.arrangement.piano_score import build_score
from services.arrangement.pop_melody import extract_pop_melody
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent
from services.score.service import validate_arrange_request, validate_transcribe_request


@pytest.mark.parametrize("sr,hop", [(16000, 160), (22050, 512), (48000, 1024)])
def test_phase_uses_samples_per_frame(sr, hop):
    offset, period, duration = 0.19, 0.625, 18.0
    envelope = np.zeros(round(duration * sr / hop) + 1)
    envelope[np.rint(np.arange(offset, duration, period) * sr / hop).astype(int)] = 1
    phase = estimate_phase(envelope, period, duration, sr=sr, hop_length=hop)
    assert abs(phase - offset) < hop / sr + period / 200


def test_register_keeps_all_intervals_and_chromatic_notes():
    notes = [NoteEvent(i * 0.125, 0.1, p, 0.8) for i, p in enumerate([71, 72, 73, 74, 85, 84])]
    shifted = fold_melody_to_range(notes)
    assert np.diff([n.midi for n in shifted]).tolist() == np.diff([n.midi for n in notes]).tolist()
    assert [n.midi % 12 for n in shifted] == [n.midi % 12 for n in notes]


def test_cleanup_keeps_fast_real_run_and_removes_weak_spike():
    grid = BarGrid(120, 4, 0, 2)
    run = [NoteEvent(i * 0.05, 0.04, p, 0.7) for i, p in enumerate([60, 61, 62, 63])]
    assert clean_melody_artifacts(run, grid) == run
    spike = NoteEvent(0.075, 0.025, 90, 0.05)
    assert spike not in clean_melody_artifacts([*run, spike], grid)


def test_accompaniment_changes_on_midbar_chord_event():
    grid = BarGrid(120, 4, 0, 2)
    result = arrange(
        {0: ([NoteEvent(0, 1.8, 76, 0.8)], [])},
        grid,
        chords=[ChordEvent(0, 1, 0, "major"), ChordEvent(1, 1, 7, "major")],
        fidelity="arranged",
        pattern="root_only",
        difficulty=1,
    )
    left = result.bars[0].left
    assert any(n.start < 1 and n.midi % 12 == 0 for n in left)
    assert any(n.start >= 1 and n.midi % 12 == 7 for n in left)
    assert all(n.end <= 1 + 1e-6 for n in left if n.start < 1)


@pytest.mark.parametrize("fidelity", ["transcribed", "as-played"])
def test_faithful_midi_keeps_fast_onsets_pitches_and_lengths(tmp_path, fidelity):
    import mido

    grid = BarGrid(120, 4, 0.14, 2)
    notes = [
        NoteEvent(0.04, 0.055, 36, 0.7),
        NoteEvent(0.105, 0.06, 85, 0.8),
        NoteEvent(1.98, 0.07, 73, 0.8),
    ]
    buckets = hand_events_by_bar(HandAssignment(right_hand=notes), grid)
    result = arrange(buckets, grid, fidelity=fidelity, difficulty=1)
    score = build_score(result, grid)
    assert [(n["time"], n["duration"], n["midi"]) for n in score.note_timeline()] == [
        (n.start, n.duration, n.midi) for n in notes
    ]
    midi = mido.MidiFile(str(write_midi(score, tmp_path / "faithful.mid")))
    onsets = []
    for track in midi.tracks[1:]:
        ticks = 0
        for message in track:
            ticks += message.time
            if message.type == "note_on" and message.velocity:
                onsets.append((ticks / 960, message.note))
    assert [p for _, p in sorted(onsets)] == [36, 85, 73]
    assert [t for t, _ in sorted(onsets)] == pytest.approx([n.start for n in notes], abs=1 / 960)


def test_pop_default_requires_vocals_and_piano_refuses_pop_option():
    assert validate_arrange_request({}).melody_source == "vocal"
    assert validate_arrange_request({"melodySource": "mixed"}).melody_source == "mixed"
    with pytest.raises(ValidationError):
        validate_transcribe_request({"melodySource": "vocal"})


def test_missing_separator_does_not_silently_use_mix(monkeypatch):
    monkeypatch.delenv("PIANOFLOW_SEPARATOR_PYTHON", raising=False)
    with pytest.raises(ValidationError) as exc:
        extract_pop_melody(
            np.ones(22050), 22050, BarGrid(120, 4, 0, 1), source="vocal", tonic=0, mode="major"
        )
    assert exc.value.details["fallbackUsed"] is False


def test_pedal_releases_before_actual_midbar_harmony_change():
    from services.score.render import _pedal_segments

    grid = BarGrid(120, 4, 0, 2)
    result = arrange(
        {0: ([NoteEvent(0, 1.8, 76, 0.8)], [])},
        grid,
        chords=[ChordEvent(0, 1, 0, "major"), ChordEvent(1, 1, 7, "major")],
        fidelity="arranged",
        difficulty=3,
    )
    segments = _pedal_segments(build_score(result, grid))
    assert len(segments) == 2
    assert segments[0][1] < 1
    assert segments[1][0] == 1


def test_chord_filling_does_not_ring_across_next_harmony():
    from services.arrangement.harmony import harmonize_melody

    notes = harmonize_melody(
        [NoteEvent(0, 1.8, 76, 0.8)],
        [ChordEvent(0, 1, 0, "major"), ChordEvent(1, 1, 7, "major")],
        difficulty=4,
        beat_seconds=0.5,
        span_cap=12,
    )
    assert next(n for n in notes if n.midi == 76).duration == 1.8
    assert all(n.end <= 1 for n in notes if n.midi != 76)
