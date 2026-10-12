"""Tests for per-bar quality scoring and the as-played vs recomposed decision."""

from __future__ import annotations

import pytest

from services.arrangement.grid import BarGrid
from services.arrangement.quality import (
    QualityThresholds,
    assess_bar,
    assess_bars,
)
from services.audio_analysis.polyphony import NoteEvent


def grid_at(tempo: float = 120.0, duration: float = 8.0) -> BarGrid:
    """2 s bars in 4/4, no phase offset, so beat = 0.5 s and 16th = 0.125 s."""
    return BarGrid(tempo_bpm=tempo, beats_per_bar=4, phase_seconds=0.0, duration=duration)


def note(start: float, duration: float, midi: int, amplitude: float = 0.6) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def test_a_clean_bar_scores_well() -> None:
    grid = grid_at()
    right = [note(i * 0.5, 0.4, 76) for i in range(8)]
    left = [note(i * 0.5, 0.4, 48 + (i % 4) * 4) for i in range(8)]
    quality = assess_bar(right, left, grid, 0)
    assert quality.score >= 90
    assert quality.flags == ()
    assert not quality.failed


def test_same_pitch_overlap_is_flagged() -> None:
    """One hand cannot hold the same key twice: always a transcription artefact."""
    grid = grid_at()
    left = [note(0.0, 1.0, 48), note(0.5, 1.0, 48)]
    quality = assess_bar([note(0.0, 0.4, 76)], left, grid, 0)
    assert "overlap" in quality.flags
    assert quality.overlaps == 1
    assert quality.hard_fault
    assert quality.score < 80


def test_a_hard_fault_outranks_a_good_score() -> None:
    """The bar would otherwise score well on density and grid, but cannot be played.

    Regression: a 30-point penalty alone left the bar at 78, above the 55
    recompose threshold, so an impossible overlap would have been kept.
    """
    grid = grid_at()
    right = [note(i * 0.5, 0.4, 76) for i in range(7)]
    left = [note(i * 0.5, 0.4, 48 + (i % 4) * 4) for i in range(7)]
    left += [note(0.0, 1.0, 48), note(0.5, 1.0, 48)]
    quality = assess_bar(right, left, grid, 0)
    assert quality.hard_fault
    buckets = {0: (right, left), 1: (right, left)}
    report = assess_bars(buckets, grid)
    assert set(report.recomposed_bars) == {0, 1}


def test_overlap_only_counts_overlapping_notes_of_the_same_pitch() -> None:
    grid = grid_at()
    # Same pitch but sequential, plus different pitches at once: neither is an overlap.
    left = [note(0.0, 0.4, 48), note(0.5, 0.4, 48), note(0.0, 0.4, 52)]
    quality = assess_bar([note(0.0, 0.4, 76)], left, grid, 0)
    assert quality.overlaps == 0
    assert "overlap" not in quality.flags


def test_left_hand_above_the_right_hand_is_flagged() -> None:
    grid = grid_at()
    right = [note(0.0, 1.0, 60)]
    left = [note(0.0, 1.0, 72)]
    quality = assess_bar(right, left, grid, 0)
    assert "crossing" in quality.flags
    assert quality.crossings == 1


def test_too_many_notes_in_a_bar_is_flagged() -> None:
    grid = grid_at()
    notes = [note(i * 0.05, 0.05, 48 + (i % 12)) for i in range(30)]
    quality = assess_bar(notes[:15], notes[15:], grid, 0)
    assert "density" in quality.flags


def test_an_almost_empty_bar_is_flagged() -> None:
    grid = grid_at()
    quality = assess_bar([note(0.0, 0.2, 76)], [note(0.0, 0.2, 48)], grid, 0)
    assert quality.notes_per_beat == 0.5
    assert "density" not in quality.flags


def test_off_grid_onsets_are_flagged() -> None:
    grid = grid_at()
    # At 120 BPM a 16th is 125 ms, so 62.5 ms and 437.5 ms are the midpoints
    # between grid positions: 62.5 ms off, just past the 60 ms tolerance.
    right = [note(0.0625, 0.2, 76), note(0.4375, 0.2, 78)]
    left = [note(i * 0.5, 0.4, 48) for i in range(4)]
    quality = assess_bar(right, left, grid, 0)
    assert "grid" in quality.flags
    assert quality.grid_fit == pytest.approx(4 / 6)


def test_onsets_near_the_tolerance_still_pass() -> None:
    """Real transcriptions land within 60 ms of the grid, so this must not fire."""
    grid = grid_at()
    right = [note(0.19, 0.2, 76), note(0.58, 0.2, 78)]
    left = [note(i * 0.5, 0.4, 48) for i in range(4)]
    quality = assess_bar(right, left, grid, 0)
    assert "grid" not in quality.flags
    assert quality.grid_fit == 1.0


def test_a_silent_left_hand_under_a_melody_is_flagged() -> None:
    grid = grid_at()
    quality = assess_bar([note(i * 0.5, 0.4, 76) for i in range(4)], [], grid, 0)
    assert "hole" in quality.flags
    assert quality.left_hand_holes == 4


def test_no_hole_flag_when_the_left_hand_plays_every_beat() -> None:
    grid = grid_at()
    left = [note(i * 0.5, 0.4, 48) for i in range(4)]
    quality = assess_bar([note(i * 0.5, 0.4, 76) for i in range(4)], left, grid, 0)
    assert quality.left_hand_holes == 0
    assert "hole" not in quality.flags


def test_an_empty_bar_is_not_penalised_for_a_missing_left_hand() -> None:
    """Silence is not a defect; only a melody with no accompaniment is."""
    grid = grid_at()
    quality = assess_bar([], [], grid, 0)
    assert "hole" not in quality.flags


def test_bad_bars_are_recomposed_only_after_two_in_a_row() -> None:
    """Hysteresis keeps one weak bar between good ones as played."""
    grid = grid_at(duration=16.0)
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        good_right = [note(index * 2.0 + i * 0.5, 0.4, 76) for i in range(4)]
        good_left = [note(index * 2.0 + i * 0.5, 0.4, 48) for i in range(4)]
        if index in (3, 6):
            # Same pitch twice in the left hand: an unplayable artefact.
            bad_left = [
                note(index * 2.0, 1.0, 48),
                note(index * 2.0 + 0.5, 1.0, 48),
            ]
            buckets[index] = (good_right, bad_left)
        else:
            buckets[index] = (good_right, good_left)
    report = assess_bars(buckets, grid)
    assert 3 not in report.recomposed_bars  # isolated: hysteresis holds it
    assert 6 not in report.recomposed_bars


def test_two_consecutive_bad_bars_are_recomposed() -> None:
    grid = grid_at(duration=16.0)
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        right = [note(index * 2.0 + i * 0.5, 0.4, 76) for i in range(4)]
        if index in (3, 4):
            left = [note(index * 2.0, 1.0, 48), note(index * 2.0 + 0.5, 1.0, 48)]
        else:
            left = [note(index * 2.0 + i * 0.5, 0.4, 48) for i in range(4)]
        buckets[index] = (right, left)
    report = assess_bars(buckets, grid)
    assert set(report.recomposed_bars) == {3, 4}


def test_a_run_ending_at_the_last_bar_still_counts() -> None:
    """The hysteresis loop must not drop a failing run that reaches the end."""
    grid = grid_at(duration=12.0)
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        right = [note(index * 2.0 + i * 0.5, 0.4, 76) for i in range(4)]
        if index >= grid.bar_count() - 2:
            left = [note(index * 2.0, 1.0, 48), note(index * 2.0 + 0.5, 1.0, 48)]
        else:
            left = [note(index * 2.0 + i * 0.5, 0.4, 48) for i in range(4)]
        buckets[index] = (right, left)
    report = assess_bars(buckets, grid)
    assert len(report.recomposed_bars) == 2


def test_thresholds_are_configurable() -> None:
    grid = grid_at()
    right = [note(i * 0.5, 0.4, 76) for i in range(8)]
    left = [note(i * 0.5, 0.4, 48) for i in range(8)]
    strict = QualityThresholds(recompose_score=99.0)
    report = assess_bars({0: (right, left)}, grid, thresholds=strict)
    # With an unreachable score threshold, hysteresis still needs two bars, so
    # a single bar is never recomposed.
    assert report.recomposed_bars == ()


def test_report_serialises_for_the_api() -> None:
    grid = grid_at()
    buckets = {
        0: ([note(0.0, 0.4, 76)], [note(0.0, 0.4, 48)]),
        1: ([note(2.0, 0.4, 77)], [note(2.0, 0.4, 50)]),
    }
    payload = assess_bars(buckets, grid).to_dict()
    assert payload["barCount"] == 4
    assert len(payload["bars"]) == 4
    assert isinstance(payload["recomposedBars"], list)
