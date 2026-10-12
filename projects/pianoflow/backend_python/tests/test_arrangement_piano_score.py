"""Tests for the two-staff piano score model."""

from __future__ import annotations

import pytest

from services.arrangement.arranger import arrange
from services.arrangement.grid import BarGrid
from services.arrangement.piano_score import (
    DIVISIONS,
    PianoScore,
    build_score,
    choose_clef,
    fifths_for,
)
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent


def grid_at(duration: float = 8.0, tempo: float = 120.0) -> BarGrid:
    return BarGrid(tempo_bpm=tempo, beats_per_bar=4, phase_seconds=0.0, duration=duration)


def note(start: float, duration: float, midi: int, amplitude: float = 0.6) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def buckets_from(
    grid: BarGrid,
    right: list[NoteEvent],
    left: list[NoteEvent],
) -> dict[int, tuple[list[NoteEvent], list[NoteEvent]]]:
    out: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start, end = grid.bar_start(index), grid.bar_end(index)
        out[index] = (
            [n for n in right if start <= n.start < end],
            [n for n in left if start <= n.start < end],
        )
    return out


def chords(grid: BarGrid, root: int = 9) -> list[ChordEvent]:
    return [ChordEvent(start=i * 2.0, duration=2.0, root=root, quality="minor") for i in range(8)]


def test_a_score_has_one_measure_per_bar() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], [note(0.0, 0.4, 48)]), grid)
    score = build_score(result, grid)
    assert len(score.measures) == grid.bar_count()
    assert all(m.index == i for i, m in enumerate(score.measures))


def test_notes_land_on_the_bar_they_start_in() -> None:
    grid = grid_at()
    result = arrange(
        buckets_from(grid, [note(0.0, 0.4, 76), note(2.0, 0.4, 77)], [note(1.0, 0.4, 48)]),
        grid,
    )
    score = build_score(result, grid)
    assert [n.midi for n in score.measures[0].right] == [76]
    assert [n.midi for n in score.measures[1].right] == [77]
    assert [n.midi for n in score.measures[0].left] == [48]


def test_no_measure_overflows_its_bar() -> None:
    """The invariant that a hand-rolled piano writer breaks first."""
    grid = grid_at()
    result = arrange(
        buckets_from(
            grid,
            [note(0.0, 0.4, 76), note(1.9, 1.6, 79)],
            [note(0.0, 3.9, 40)],
        ),
        grid,
    )
    score = build_score(result, grid)
    for measure in score.measures:
        for staff in (measure.right, measure.left):
            for n in staff:
                assert n.beat >= 0.0
                assert n.end_beat <= score.beats_per_bar + 1e-6


def test_a_note_crossing_a_barline_is_split_and_tied() -> None:
    grid = grid_at()
    # At 120 BPM a beat is 0.5 s. Starting 0.2 s in and ringing 2.5 s means
    # 5 beats from beat 0.5, which runs 1.5 beats past the 4-beat barline.
    result = arrange(buckets_from(grid, [note(0.2, 2.5, 79)], [note(0.0, 0.4, 48)]), grid)
    score = build_score(result, grid)
    pieces = [n for m in score.measures for n in m.right if n.midi == 79]
    assert len(pieces) == 2
    assert pieces[0].tied_to_next
    assert not pieces[1].tied_to_next
    assert pieces[0].bar == 0 and pieces[1].bar == 1
    assert pieces[0].tie_start is False and pieces[1].tie_start is True
    assert pieces[0].end_beat == pytest.approx(4.0)
    assert pieces[1].beat == 0.0
    assert score.tied_count == 1


def test_a_split_note_preserves_its_total_duration() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.2, 3.6, 79)], []), grid)
    score = build_score(result, grid)
    pieces = [n for m in score.measures for n in m.right if n.midi == 79]
    assert len(pieces) == 2
    assert sum(p.duration_beats for p in pieces) == pytest.approx(7.25)
    assert all(p.tied_to_next for p in pieces[:-1])


def test_durations_snap_to_the_grid() -> None:
    grid = grid_at()
    # 0.13 s at 120 BPM is 0.26 beats, which must not become a 0.25-beat note.
    result = arrange(buckets_from(grid, [note(0.0, 0.13, 76)], []), grid)
    score = build_score(result, grid)
    duration = score.measures[0].right[0].duration_beats
    assert duration == pytest.approx(round(duration * 4) / 4)


def test_sixteenths_are_representable() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.0, 0.125, 76)], []), grid)
    score = build_score(result, grid)
    assert score.divisions == DIVISIONS
    assert score.measures[0].right[0].duration_ticks == 1


def test_never_zero_duration() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.0, 0.01, 76)], []), grid)
    score = build_score(result, grid)
    assert score.measures[0].right[0].duration_ticks >= 1


def test_clef_follows_the_register_of_each_hand() -> None:
    grid = grid_at()
    high = arrange(buckets_from(grid, [note(0.0, 0.4, 88)], [note(0.0, 0.4, 36)]), grid)
    assert build_score(high, grid).left_clef == "bass"
    assert build_score(high, grid).right_clef == "treble"


def test_an_empty_hand_falls_back_to_its_default_clef() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], []), grid)
    score = build_score(result, grid)
    assert score.left_clef == "bass"
    assert score.measures[0].left == ()


def test_choose_clef_uses_the_mean_pitch() -> None:
    from services.arrangement.piano_score import ScoredNote

    low = [ScoredNote(0, 0.0, 1.0, 40), ScoredNote(0, 1.0, 1.0, 52)]
    high = [ScoredNote(0, 0.0, 1.0, 72), ScoredNote(0, 1.0, 1.0, 84)]
    assert choose_clef(low, "treble") == "bass"
    assert choose_clef(high, "bass") == "treble"
    assert choose_clef([], "bass") == "bass"


def test_key_signature_maps_to_the_circle_of_fifths() -> None:
    assert fifths_for(0, "major") == 0  # C
    assert fifths_for(7, "major") == 1  # G
    assert fifths_for(9, "minor") == 0  # A minor, no accidentals
    assert fifths_for(2, "major") == 2  # D


def test_a_black_key_minor_key_is_respelled_into_seven_sharps() -> None:
    """G# minor would otherwise demand 8 sharps."""
    assert fifths_for(8, "minor") == 5  # B major / B minor spelling
    assert abs(fifths_for(8, "minor")) <= 7


def test_an_unknown_key_is_no_accidentals() -> None:
    assert fifths_for(None, None) == 0
    assert fifths_for(4, None) == 4  # E major by default


def test_tempo_and_key_come_from_the_caller_when_given() -> None:
    grid = grid_at(tempo=140.0)
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], []), grid)
    score = build_score(result, grid, tempo_bpm=90.0, key_tonic=2, key_mode="major")
    assert score.tempo_bpm == 90.0
    assert score.key_fifths == 2


def test_the_tempo_falls_back_to_the_grid() -> None:
    grid = grid_at(tempo=140.0)
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], []), grid)
    assert build_score(result, grid).tempo_bpm == 140.0


def test_the_phase_places_bar_one_in_the_audio() -> None:
    """Regression: every notated time was shifted by the phase.

    The grid finds bar 1 by sliding the beat grid until onset energy lines up,
    and on real material that offset is not zero. Without carrying it, the cursor
    led or trailed the audio by up to a second from the very first bar.
    """
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=1.25, duration=8.0)
    result = arrange(buckets_from(grid, [note(1.25, 0.4, 76)], [note(1.25, 0.4, 48)]), grid)
    score = build_score(result, grid)
    assert score.phase_seconds == 1.25

    notes = score.note_timeline()
    assert notes, "no notes in the timeline"
    # The note sounds at 1.25 s in the audio, so that is where it must be placed.
    assert min(n["time"] for n in notes) == pytest.approx(1.25, abs=0.02)


def test_a_zero_phase_still_puts_bar_one_at_zero() -> None:
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=8.0)
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], []), grid)
    score = build_score(result, grid)
    assert score.note_timeline()[0]["time"] == pytest.approx(0.0, abs=0.02)


def test_the_bar_length_is_exposed_for_the_ui() -> None:
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=8.0)
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], []), grid)
    score = build_score(result, grid)
    assert score.bar_seconds == pytest.approx(2.0)
    assert score.to_dict()["barSeconds"] == pytest.approx(2.0)


def test_a_score_serialises_for_the_api() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], [note(0.0, 0.4, 48)]), grid)
    payload = build_score(result, grid, title="Test").to_dict()
    assert payload["measureCount"] == grid.bar_count()
    assert payload["noteCount"] == 2
    assert payload["divisions"] == DIVISIONS
    assert len(payload["measures"]) == grid.bar_count()


def test_note_serialises() -> None:
    grid = grid_at()
    result = arrange(buckets_from(grid, [note(0.0, 0.4, 76)], []), grid)
    payload = build_score(result, grid).measures[0].right[0].to_dict()
    assert payload["midi"] == 76
    assert payload["tiedToNext"] is False


def test_an_empty_arrangement_still_produces_measures() -> None:
    grid = grid_at()
    result = arrange({}, grid)
    score = build_score(result, grid)
    assert isinstance(score, PianoScore)
    assert score.note_count == 0
    assert len(score.measures) == grid.bar_count()
