"""Tests for the hand alignment: snap the figure to the melody, anchor the handoffs.

The user's rule: 一定要有对齐的左右手弹才能保持连续. A left-hand attack close to
a right-hand attack moves onto it; at the melody's phrase boundaries the left
hand must be present, or a small bass+chord block is added (右手结束那个音，
左手开始弹).
"""

from __future__ import annotations

from services.arrangement.grid import BarGrid
from services.arrangement.interlock import interlock, phrase_boundary_beats
from services.arrangement.patterns import PlacedNote, Role
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

BEATS_PER_BAR = 4


def grid() -> BarGrid:
    # 120 bpm, 4/4: one beat is 0.5 s, one bar is 2 s.
    return BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=20.0)


def event_beat(absolute_beat: float, midi: int = 72) -> NoteEvent:
    """A melody note whose onset is ``absolute_beat`` beats from bar 1."""
    return NoteEvent(start=absolute_beat * 0.5, duration=0.4, midi=midi)


CHORD = ChordEvent(0.0, 2.0, 0, "maj")


class TestPhraseBoundaries:
    def test_first_attack_and_rest_edges_are_boundaries(self) -> None:
        g = grid()
        buckets = {
            0: ([event_beat(0), event_beat(0.5), event_beat(1.0)], []),
            1: ([event_beat(5)], []),
        }
        boundaries = phrase_boundary_beats(buckets, g)
        # 0 (first attack), bar 0 beat 1 (phrase ends before the 4-beat rest),
        # bar 1 beat 1 (the phrase that starts after it).
        assert boundaries[0] == [0.0, 1.0]
        assert boundaries[1] == [1.0]

    def test_continuous_melody_has_only_the_first_attack(self) -> None:
        g = grid()
        buckets = {
            0: ([event_beat(b) for b in (0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5)], []),
        }
        boundaries = phrase_boundary_beats(buckets, g)
        assert boundaries == {0: [0.0]}

    def test_a_bar_without_a_melody_has_no_boundaries(self) -> None:
        g = grid()
        buckets = {0: ([], [event_beat(0)])}
        assert phrase_boundary_beats(buckets, g) == {}


class TestSnap:
    def test_a_left_attack_a_sixteenth_off_moves_onto_the_melody(self) -> None:
        g = grid()
        right = [event_beat(1.0)]
        placed = [PlacedNote(1.25, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD)
        assert out[0].beat == 1.0

    def test_a_left_attack_a_quarter_off_stays(self) -> None:
        g = grid()
        right = [event_beat(1.0)]
        placed = [PlacedNote(1.5, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD)
        assert out[0].beat == 1.5

    def test_an_already_aligned_attack_is_untouched(self) -> None:
        g = grid()
        right = [event_beat(1.0)]
        placed = [PlacedNote(1.0, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD)
        assert out == placed

    def test_a_snap_never_pushes_a_note_past_the_barline(self) -> None:
        g = grid()
        right = [event_beat(3.75)]
        placed = [PlacedNote(3.5, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD)
        assert out[0].beat == 3.75
        assert out[0].beat + out[0].duration_beats <= BEATS_PER_BAR + 1e-9


class TestAnchor:
    def test_a_phrase_end_with_no_left_hand_gets_a_handoff_block(self) -> None:
        g = grid()
        # The melody plays beats 0-1, rests to beat 3: the boundary at 1.0 must
        # carry the left hand, but the figure has nothing there.
        right = [event_beat(0), event_beat(0.5), event_beat(1.0)]
        placed = [PlacedNote(0.0, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD, boundary_beats=[1.0])
        at_boundary = [n for n in out if abs(n.beat - 1.0) <= 1e-6]
        assert len(at_boundary) == 2
        assert {n.role for n in at_boundary} == {Role.SKELETON, Role.CHORD}

    def test_a_phrase_start_without_a_left_hand_is_anchored_too(self) -> None:
        g = grid()
        right = [event_beat(3.0)]
        placed = [PlacedNote(0.0, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD, boundary_beats=[3.0])
        at_boundary = [n for n in out if abs(n.beat - 3.0) <= 1e-6]
        assert len(at_boundary) == 2

    def test_a_covered_boundary_gets_no_anchor(self) -> None:
        g = grid()
        right = [event_beat(1.0)]
        placed = [PlacedNote(1.0, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD, boundary_beats=[1.0])
        assert out == placed

    def test_an_anchor_lands_within_a_sixteenth_of_the_boundary(self) -> None:
        g = grid()
        right = [event_beat(1.0)]
        placed = [PlacedNote(0.9, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, CHORD, boundary_beats=[1.0])
        # The existing note is within a sixteenth, so it snaps instead of
        # spawning a second block.
        assert not any(
            abs(n.beat - 1.0) <= 1e-6 and n.role == Role.CHORD for n in out
        )
        assert any(abs(n.beat - 1.0) <= 1e-6 and n.role == Role.SKELETON for n in out)

    def test_no_anchor_without_a_chord(self) -> None:
        g = grid()
        right = [event_beat(0)]
        placed = [PlacedNote(0.0, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, right, g, 0, None, boundary_beats=[1.0])
        assert out == placed

    def test_no_melody_no_changes(self) -> None:
        g = grid()
        placed = [PlacedNote(0.0, 0.5, 60, Role.SKELETON)]
        out = interlock(placed, [], g, 0, CHORD)
        assert out == placed
