"""Tests for the multi-feature hand split."""

from __future__ import annotations

import numpy as np

from services.arrangement.grid import BarGrid
from services.arrangement.hand_split import (
    DEFAULT_SPLIT_MIDI,
    HandAssignment,
    HandSplitConfig,
    _fix_crossings,
    estimate_split_midi,
    hand_events_by_bar,
    split_hands,
)
from services.audio_analysis.polyphony import NoteEvent


def note(start: float, duration: float, midi: int, amplitude: float = 0.6) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def test_empty_input_yields_an_empty_assignment() -> None:
    assignment = split_hands([])
    assert assignment.right_hand == []
    assert assignment.left_hand == []


def test_adaptive_split_follows_the_actual_registers() -> None:
    """The boundary must separate the two registers, not sit inside one."""
    low = [40 + (i % 3) for i in range(12)]
    high = [72 + (i % 3) for i in range(4)]
    events = [note(i * 0.5, 0.4, midi) for i, midi in enumerate(low)]
    events += [note(i * 0.5, 0.4, midi) for i, midi in enumerate(high)]
    split = estimate_split_midi(events)
    assert max(low) <= split < min(high)


def test_a_single_held_note_cannot_drag_the_split_onto_itself() -> None:
    """Regression: duration weighting put the boundary on the melody note.

    Weighting the histogram by duration lets one long loud note own the
    threshold, which then classifies that same note as left hand.
    """
    events = [
        note(0.0, 0.2, 52, 0.4),
        note(0.25, 1.8, 67, 0.95),
        note(2.1, 0.2, 50, 0.4),
    ]
    assert estimate_split_midi(events) < 67


def test_adaptive_split_can_be_disabled() -> None:
    events = [note(0, 0.4, 45), note(0.5, 0.4, 78)]
    assert estimate_split_midi(events, fixed=60, adaptive=False) == 60


def test_flat_single_line_falls_back_to_the_middle() -> None:
    """Without two registers there is nothing to separate."""
    assert estimate_split_midi([note(0, 0.4, 60), note(0.5, 0.4, 61)]) == 60


def test_low_bass_goes_left_and_high_melody_goes_right() -> None:
    events = [
        note(0.0, 0.4, 45, 0.7),
        note(0.0, 0.4, 76, 0.8),
        note(0.5, 0.4, 45, 0.7),
        note(0.5, 0.4, 79, 0.8),
    ]
    assignment = split_hands(events)
    assert {n.midi for n in assignment.left_hand} == {45}
    assert {n.midi for n in assignment.right_hand} == {76, 79}


def test_top_note_of_a_chord_is_always_the_right_hand() -> None:
    """Even a soft top note is the melody; the register score cannot override it."""
    events = [note(0.0, 0.9, midi, 0.9) for midi in (48, 55, 60, 64, 69)]
    assignment = split_hands(events)
    assert 69 in {n.midi for n in assignment.right_hand}


def test_a_low_dyad_stays_in_the_left_hand() -> None:
    """Regression: B2+F#3 is left-hand writing, not a melody with a bass note.

    Treating the top of every group as the melody put bass notes in the right
    hand, which then scored as a crossing on the bar.
    """
    events = [
        note(0.0, 0.9, 47, 0.8),
        note(0.0, 0.9, 54, 0.8),
        note(1.0, 0.9, 47, 0.8),
        note(1.0, 0.9, 54, 0.8),
        note(2.0, 0.9, 76, 0.9),
    ]
    assignment = split_hands(events, config=HandSplitConfig(split_midi=61, adaptive_split=False))
    assert {n.midi for n in assignment.right_hand} == {76}
    assert {47, 54} <= {n.midi for n in assignment.left_hand}


def test_a_dense_chord_is_kept_whole_in_one_hand() -> None:
    """Regression: chords used to be torn apart, which flattened the right hand.

    This asserted that a six-note chord left only its top note in the right hand and
    sent the other five left. For a piano recording that is wrong in a way that is
    invisible in the note count and fatal to the arrangement: measured on 星辰大海,
    a pure piano cover, splitting note by note left the right hand with 206 notes in
    203 groups - 1.5 % chords, effectively a single line - while the left hand held
    55.7 %.

    The chord now goes to one hand whole, unless it is genuinely too wide to hold.
    """
    # Six notes spanning 11 semitones, inside the 12-semitone one-hand budget.
    events = [note(0.0, 0.9, midi, 0.5) for midi in (55, 59, 62, 64, 67, 66)]
    events = sorted(events, key=lambda n: n.midi)
    assignment = split_hands(events)

    right = {n.midi for n in assignment.right_hand}
    left = {n.midi for n in assignment.left_hand}
    # No note may be stranded in the other hand.
    assert not (right and left), f"the chord was split: right={sorted(right)} left={sorted(left)}"
    kept = right or left
    assert len(kept) == 6, f"the chord lost notes: {sorted(kept)}"


def test_a_chord_too_wide_for_one_hand_is_divided_at_its_widest_gap() -> None:
    """A chord no hand can hold is split, and cut where the notes are furthest apart.

    Cutting at the split point instead would leave a seventh in one hand and a lone
    root in the other; cutting at the widest gap keeps each side playable.
    """
    events = [note(0.0, 0.9, midi, 0.5) for midi in (40, 52, 53, 76)]
    assignment = split_hands(events)
    right = sorted(n.midi for n in assignment.right_hand)
    left = sorted(n.midi for n in assignment.left_hand)
    assert right and left, "a 36-semitone chord cannot be one hand's work"
    assert assignment.chords_divided >= 1
    # The widest gap in this chord is between 53 and 76, so the cut falls there and
    # the three lower notes stay together. Cutting at the split point instead would
    # have left a major seventh in the right hand and a bare octave in the left.
    assert 40 in left and 52 in left and 53 in left
    assert right == [76]


def test_a_chord_is_never_divided_when_one_hand_can_hold_it() -> None:
    """A chord inside the stretch budget must not be split at all."""
    events = [note(0.0, 0.9, midi, 0.5) for midi in (60, 64, 67, 71)]
    assignment = split_hands(events)
    right = {n.midi for n in assignment.right_hand}
    left = {n.midi for n in assignment.left_hand}
    assert not (right and left), f"a playable chord was divided: {sorted(right)} / {sorted(left)}"
    assert assignment.chords_divided == 0


def test_lone_held_loud_note_is_read_as_melody() -> None:
    """The loud long note goes right, and no chord is torn to achieve it.

    The rule that used to decide isolated notes by loudness and duration has been
    replaced: register decides, and the weighted score only breaks a tie within three
    semitones of the boundary. Loudness alone is not a reliable melody cue once
    chords are kept whole - a soft chord tone is still part of the tune's harmony.

    What is asserted here is the part that must not change: the prominent note is in
    the right hand, and every note lands in exactly one hand.
    """
    events = [
        note(0.0, 0.2, 52, 0.4),
        note(0.25, 1.8, 67, 0.95),
        note(2.1, 0.2, 50, 0.4),
    ]
    assignment = split_hands(events)
    assert 67 in {n.midi for n in assignment.right_hand}
    assert len(assignment.right_hand) + len(assignment.left_hand) == len(events)
    right = {n.midi for n in assignment.right_hand}
    left = {n.midi for n in assignment.left_hand}
    assert not (right & left), f"a note is in both hands: {sorted(right & left)}"


def test_one_pitch_never_lands_in_both_hands() -> None:
    """Same pitch sounding twice must resolve to a single hand."""
    events = [
        note(0.0, 1.0, 60, 0.9),
        note(0.5, 1.0, 60, 0.3),
        note(0.0, 1.0, 40, 0.5),
    ]
    assignment = split_hands(events)
    in_right = [n for n in assignment.right_hand if n.midi == 60]
    in_left = [n for n in assignment.left_hand if n.midi == 60]
    assert len(in_right) + len(in_left) == 2
    assert not (in_right and in_left)


def test_a_right_hand_note_far_below_the_left_hand_is_repaired() -> None:
    """White-box: the crossing guard is a safety net for the density rule.

    The register split plus the top-voice rule normally make an unplayable
    layout impossible, so this exercises the repair directly rather than
    pretending a natural input reaches it.
    """
    ordered = [
        note(0.0, 1.0, 72, 0.9),
        note(0.0, 1.0, 40, 0.5),
        note(0.0, 1.0, 45, 0.5),
    ]
    # Simulate the density rule having pushed the low melody note to the right.
    is_right = np.array([False, False, True])
    assignment = HandAssignment()
    _fix_crossings(ordered, is_right, assignment)
    assert assignment.crossings_fixed == 1
    assert not is_right[2]


def test_hand_split_counts_every_note_exactly_once() -> None:
    events = [
        note(0.0, 0.4, 45),
        note(0.0, 0.4, 72),
        note(0.5, 0.4, 48),
        note(0.5, 0.4, 74),
        note(1.0, 0.4, 50),
        note(1.0, 0.4, 76),
    ]
    assignment = split_hands(events)
    assert len(assignment.right_hand) + len(assignment.left_hand) == len(events)


def test_events_by_bar_buckets_on_the_grid() -> None:
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=4.0)
    events = [
        note(0.0, 0.4, 72),
        note(2.0, 0.4, 74),
        note(0.5, 0.4, 43),
    ]
    buckets = hand_events_by_bar(split_hands(events), grid)
    assert len(buckets) == grid.bar_count()
    assert any(n.midi == 72 for n in buckets[0][0])
    assert any(n.midi == 74 for n in buckets[1][0])
    assert any(n.midi == 43 for n in buckets[0][1])


def test_default_split_midi_is_middle_c() -> None:
    assert DEFAULT_SPLIT_MIDI == 60


def test_assignment_serialises_for_the_api() -> None:
    assignment = split_hands([note(0.0, 0.4, 45), note(0.0, 0.4, 72)])
    payload = assignment.to_dict()
    assert payload["rightHandCount"] + payload["leftHandCount"] == 2
    assert "splitMidi" in payload
