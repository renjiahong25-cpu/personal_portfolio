"""Tests for joining tied note halves back into single MIDI note-ons."""

from __future__ import annotations

import pytest

from services.arrangement.piano_midi import _merge_ties
from services.arrangement.piano_score import Measure, PianoScore, ScoredNote


def score_of() -> PianoScore:
    return PianoScore(
        measures=(Measure(index=0, right=(), left=()),),
        tempo_bpm=60.0,
        beats_per_bar=4,
        right_clef="treble",
        left_clef="bass",
        key_fifths=0,
        title="t",
        divisions=480,
        phase_seconds=0.0,
    )


def piece(bar: int, beat: float, beats: float, midi: int, *, tied: bool, start: bool) -> ScoredNote:
    """One written note, with its two tie flags set independently.

    A note in the middle of a tie is ``tied`` and ``start`` at the same time, so the two
    flags cannot be derived from each other.
    """
    return ScoredNote(
        bar=bar,
        beat=beat,
        duration_beats=beats,
        midi=midi,
        amplitude=0.7,
        tied_to_next=tied,
        tie_start=start,
    )


def first(bar: int, beat: float, beats: float, midi: int) -> ScoredNote:
    """The opening half of a tie: ties on to a later note, starts nothing."""
    return piece(bar, beat, beats, midi, tied=True, start=False)


def middle(bar: int, beat: float, beats: float, midi: int) -> ScoredNote:
    """A note tied on both sides: it continues one note and starts the next."""
    return piece(bar, beat, beats, midi, tied=True, start=True)


def last(bar: int, beat: float, beats: float, midi: int) -> ScoredNote:
    """The closing half of a tie: continues the previous note, ties on to nothing."""
    return piece(bar, beat, beats, midi, tied=False, start=True)


def plain(bar: int, beat: float, beats: float, midi: int) -> ScoredNote:
    return piece(bar, beat, beats, midi, tied=False, start=False)


def spans_of(pieces: list[tuple[ScoredNote, float, float]]) -> list[tuple[float, float]]:
    return sorted((round(start, 4), round(end, 4)) for _, start, end in pieces)


def test_two_adjacent_halves_join_into_one_note() -> None:
    score = score_of()
    merged = _merge_ties(score, (first(0, 3.0, 1.0, 60), last(1, 0.0, 1.0, 60)))
    assert len(merged) == 1
    # Beats are absolute from zero, so bar 0 beat 3 is beat 3 and bar 1 beat 0 is beat 4.
    assert merged[0][1] == pytest.approx(3.0)
    assert merged[0][2] == pytest.approx(5.0)


def test_chord_tones_between_the_halves_do_not_block_the_join() -> None:
    """The regression that made playback stutter.

    A sustained note crossing a barline is written underneath the next chord, so in a
    list sorted by ``(bar, beat, midi)`` the two halves of that note are not adjacent -
    the intervening chord tones are. Pairing only against the previous element therefore
    missed the join and re-struck the note.
    """
    score = score_of()
    merged = _merge_ties(
        score,
        (
            first(0, 3.0, 1.0, 60),  # the sustained note, first half
            first(0, 3.0, 1.0, 64),  # chord tone at the same instant
            last(1, 0.0, 1.0, 60),  # its continuation, after the chord tone
            plain(1, 0.0, 1.0, 64),  # chord tone at the same instant
        ),
    )
    assert len(merged) == 3
    assert spans_of(merged) == [(3.0, 4.0), (3.0, 5.0), (4.0, 5.0)]


def test_the_join_keeps_the_first_half_and_total_length() -> None:
    score = score_of()
    opening = first(0, 3.0, 1.0, 60)
    merged = _merge_ties(score, (opening, last(1, 0.0, 1.5, 60)))
    assert len(merged) == 1
    assert merged[0][0] is opening
    assert merged[0][2] - merged[0][1] == pytest.approx(2.5)


def test_different_pitches_are_never_joined() -> None:
    score = score_of()
    merged = _merge_ties(score, (first(0, 3.0, 1.0, 60), last(1, 0.0, 1.0, 62)))
    assert len(merged) == 2


def test_a_tie_flag_without_continuity_does_not_join() -> None:
    """A marking error must still produce two audible notes rather than a wrong merge."""
    score = score_of()
    merged = _merge_ties(score, (first(0, 3.0, 1.0, 60), last(1, 2.0, 1.0, 60)))
    assert len(merged) == 2


def test_a_note_tied_over_several_barlines_becomes_one_note() -> None:
    score = score_of()
    merged = _merge_ties(
        score,
        (
            first(0, 3.0, 1.0, 55),  # beats 3-4
            middle(1, 0.0, 4.0, 55),  # beats 4-8
            last(2, 0.0, 1.0, 55),  # beats 8-9
        ),
    )
    assert len(merged) == 1
    assert merged[0][1] == pytest.approx(3.0)
    assert merged[0][2] == pytest.approx(9.0)


def test_an_untied_note_does_not_cancel_another_notes_pending_tie() -> None:
    """A plain note on the same key must not consume the pending tie of a sustained one.

    Two notes on one key cannot genuinely sound together, but a quantised score can
    contain them, and dropping the sustained note's pending tie would re-strike it.
    """
    score = score_of()
    merged = _merge_ties(
        score, (first(0, 0.0, 2.0, 60), plain(0, 3.0, 1.0, 60), last(2, 0.0, 1.0, 60))
    )
    # The third note is not continuous with the first (2 != 8), so it stays its own note.
    assert len(merged) == 3


def test_ties_on_different_pitches_are_tracked_independently() -> None:
    score = score_of()
    merged = _merge_ties(
        score,
        (
            first(0, 3.0, 1.0, 60),
            first(0, 3.0, 1.0, 64),
            last(1, 0.0, 1.0, 64),
            last(1, 0.0, 1.0, 60),
        ),
    )
    assert len(merged) == 2
    assert spans_of(merged) == [(3.0, 5.0), (3.0, 5.0)]


def test_a_long_tie_is_not_broken_by_the_notes_underneath_it() -> None:
    """Three chord tones tied across a barline, written under a moving bass line."""
    score = score_of()
    merged = _merge_ties(
        score,
        (
            first(0, 3.0, 1.0, 60),
            first(0, 3.0, 1.0, 64),
            first(0, 3.0, 1.0, 67),
            last(1, 0.0, 2.0, 67),
            last(1, 0.0, 2.0, 64),
            last(1, 0.0, 2.0, 60),
        ),
    )
    assert len(merged) == 3
    assert spans_of(merged) == [(3.0, 6.0), (3.0, 6.0), (3.0, 6.0)]
