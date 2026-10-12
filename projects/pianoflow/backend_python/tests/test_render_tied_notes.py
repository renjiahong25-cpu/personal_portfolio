"""Tests for the render path joining tied note halves into a single note-on.

The notation and the synth are built from two separate functions, so the tie join has to
hold in both. This covers the synth one; test_arrangement_piano_midi_ties.py covers the
notation one.
"""

from __future__ import annotations

import pytest

from services.score.render import _merge_tied_events


def event(time, duration, midi, *, velocity=90, staff=1, tie_start=False, tied=False):
    return (time, duration, midi, velocity, staff, tie_start, tied)


def test_a_tied_note_is_struck_once_and_held_through_both_halves() -> None:
    merged = _merge_tied_events(
        [event(3.0, 1.0, 60, tied=True), event(4.0, 2.0, 60, tie_start=True)]
    )
    assert merged == [(3.0, 3.0, 60, 90)]


def test_chord_notes_between_the_halves_do_not_split_the_tie() -> None:
    """The regression that put a click at every barline.

    A note tied over a barline is written underneath the chord that follows, so the chord
    notes sit between the halves in time order and the pair is never adjacent. Here the
    chord keeps sounding on past the tie, so only the pitch of the tied note is joined.
    """
    merged = _merge_tied_events(
        [
            event(3.0, 1.0, 60, tied=True),
            event(3.0, 1.0, 64),
            event(4.0, 2.0, 60, tie_start=True),
            event(4.0, 2.0, 64),
        ]
    )
    assert (3.0, 3.0, 60, 90) in merged
    assert [o for o in merged if o[2] == 60] == [(3.0, 3.0, 60, 90)]
    assert len(merged) == 3


def test_the_join_holds_the_strike_velocity_of_the_first_half() -> None:
    merged = _merge_tied_events(
        [
            event(3.0, 1.0, 60, velocity=110, tied=True),
            event(4.0, 2.0, 60, velocity=40, tie_start=True),
        ]
    )
    assert merged == [(3.0, 3.0, 60, 110)]


def test_the_same_pitch_in_the_other_hand_is_not_merged() -> None:
    merged = _merge_tied_events(
        [
            event(3.0, 1.0, 60, staff=1, tied=True),
            event(4.0, 2.0, 60, staff=2, tie_start=True),
        ]
    )
    assert len(merged) == 2


def test_different_pitches_are_never_merged() -> None:
    merged = _merge_tied_events(
        [event(3.0, 1.0, 60, tied=True), event(4.0, 2.0, 62, tie_start=True)]
    )
    assert len(merged) == 2


def test_a_tie_flag_without_continuity_does_not_join() -> None:
    merged = _merge_tied_events(
        [event(3.0, 1.0, 60, tied=True), event(4.5, 2.0, 60, tie_start=True)]
    )
    assert len(merged) == 2


def test_a_note_tied_across_three_halves_is_struck_once() -> None:
    merged = _merge_tied_events(
        [
            event(3.0, 1.0, 55, tied=True),
            event(4.0, 4.0, 55, tie_start=True, tied=True),
            event(8.0, 1.0, 55, tie_start=True),
        ]
    )
    assert merged == [(3.0, 6.0, 55, 90)]


def test_a_plain_note_does_not_cancel_another_notes_pending_tie() -> None:
    merged = _merge_tied_events(
        [
            event(0.0, 2.0, 60, tied=True),
            event(3.0, 1.0, 60),
            event(8.0, 1.0, 60, tie_start=True),
        ]
    )
    # The third note is not continuous with the first (2 != 8), so it stays its own note.
    assert len(merged) == 3


def test_rounding_in_the_timeline_does_not_break_a_join() -> None:
    """note_timeline rounds times to 4 decimals, so touching halves can differ slightly."""
    merged = _merge_tied_events(
        [event(3.0, 1.0, 60, tied=True), event(4.0001, 2.0, 60, tie_start=True)]
    )
    assert len(merged) == 1
    onset, duration, midi, velocity = merged[0]
    assert (onset, midi, velocity) == (3.0, 60, 90)
    assert duration == pytest.approx(3.0001)


def test_a_real_gap_is_still_not_joined() -> None:
    merged = _merge_tied_events(
        [event(3.0, 1.0, 60, tied=True), event(4.01, 2.0, 60, tie_start=True)]
    )
    assert len(merged) == 2
