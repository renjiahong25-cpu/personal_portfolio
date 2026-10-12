"""Tests for the fixed playback rules and for not rewriting the performance.

Two groups, and the second exists because the first would happily pass on a score the
player never played:

* :mod:`services.arrangement.expression` - how a note is played, decided once.
* the faithful mode - that nothing the player played is moved or removed.
"""

from __future__ import annotations

import itertools

import numpy as np

from services.arrangement.arranger import arrange
from services.arrangement.expression import (
    MAX_VELOCITY_STEP,
    RIGHT_HAND_OFFSET,
    ExpressionProfile,
    limit_velocity_steps,
    velocities,
)
from services.arrangement.grid import BarGrid
from services.arrangement.harmony import fit_chord
from services.arrangement.piano_midi import velocity_lookup, write_midi
from services.arrangement.piano_score import PianoScore, build_score
from services.audio_analysis.dynamics import VELOCITY_CEIL, VELOCITY_FLOOR
from services.audio_analysis.polyphony import NoteEvent

SR = 22050


def note(start: float, duration: float, midi: int, amplitude: float = 0.5) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def grid() -> BarGrid:
    return BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=8.0)


def transcribed_score(duration: float = 8.0) -> PianoScore:
    g = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=duration)
    # A spread of amplitudes on purpose: a fixture where every note is equally loud
    # cannot tell a working velocity apart from a constant one.
    right = [
        note(b * 0.5, 0.4, 60 + b % 5, 0.15 + 0.7 * (b % 7) / 7) for b in range(int(duration * 2))
    ]
    left = [
        note(b * 0.5, 0.4, 36 + b % 4, 0.15 + 0.7 * (b % 5) / 5) for b in range(int(duration * 2))
    ]
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(g.bar_count()):
        start, end = g.bar_start(index), g.bar_end(index)
        buckets[index] = (
            [n for n in right if start <= n.start < end],
            [n for n in left if start <= n.start < end],
        )
    result = arrange(buckets, g, fidelity="transcribed")
    return build_score(result, g, title="expression")


# --- PR-4, no sudden increase ------------------------------------------------


def test_consecutive_velocities_cannot_jump_up_past_the_limit() -> None:
    """The "突然响度增大" rule.

    Measured on 星辰大海 before this existed: 40.3 % of consecutive notes jumped by more
    than 24 velocity units and the largest by 125 - roughly 40 dB between neighbours. That
    is the complaint, stated as a bound.
    """
    limited = limit_velocity_steps([60, 120, 60, 61, 110], MAX_VELOCITY_STEP)
    for previous, current in itertools.pairwise(limited):
        assert current - previous <= MAX_VELOCITY_STEP
    # The first note is the player's, so it is kept.
    assert limited[0] == 60
    # A drop is allowed further, because a diminuendo and a released chord both look
    # like one and clamping it as hard would flatten phrasing that is in the performance.
    assert limited[2] == 60


def test_a_flat_track_is_left_alone() -> None:
    assert limit_velocity_steps([70, 71, 69, 70], MAX_VELOCITY_STEP) == [70, 71, 69, 70]
    assert limit_velocity_steps([], MAX_VELOCITY_STEP) == []


# --- PR-3, the melody sits on top --------------------------------------------


def test_the_right_hand_is_played_above_the_left() -> None:
    loud = [0.5, 0.5]
    hands = ["right", "left"]
    played = velocities(loud, hands)
    assert played[0] - played[1] == RIGHT_HAND_OFFSET


def test_the_hand_offset_survives_the_slew_limit() -> None:
    """The rule and the limiter protect different things.

    The hand offset is a deliberate part of the performance; the slew limit exists to stop
    measurement noise. Letting the limiter clamp the offset would quietly undo the rule it
    is meant to protect, so the offset is re-asserted afterwards.
    """
    loud = [0.5, 0.5] * 40
    hands = ["right", "left"] * 40
    played = velocities(loud, hands)
    right = [v for v, h in zip(played, hands, strict=True) if h == "right"]
    left = [v for v, h in zip(played, hands, strict=True) if h == "left"]
    assert np.median(right) > np.median(left)


def test_rules_can_be_switched_off_for_a_literal_rendering() -> None:
    """The round-trip tests compare like with like, so they need the raw measurement."""
    raw = velocities(
        [0.2, 0.9], ["right", "left"], profile=ExpressionProfile().with_rules_disabled()
    )
    assert raw == [
        round(VELOCITY_FLOOR + (VELOCITY_CEIL - VELOCITY_FLOOR) * 0.2),
        round(VELOCITY_FLOOR + (VELOCITY_CEIL - VELOCITY_FLOOR) * 0.9),
    ]


# --- PR-5, the range a performance occupies ----------------------------------


def test_no_note_reaches_maximum_hammer_force() -> None:
    played = velocities(np.linspace(0.0, 1.0, 500).tolist(), ["right"] * 500)
    assert max(played) <= VELOCITY_CEIL
    assert min(played) >= VELOCITY_FLOOR
    # The loudest note must be an accent, not the ceiling on every note.
    assert max(played) == VELOCITY_CEIL


# --- the profile is reusable --------------------------------------------------


def test_the_profile_survives_a_round_trip_through_json() -> None:
    original = ExpressionProfile(right_hand_offset=11, max_velocity_step=17)
    restored = ExpressionProfile.from_dict(original.to_dict())
    assert restored == original


def test_a_profile_with_missing_or_junk_fields_falls_back_to_the_defaults() -> None:
    base = ExpressionProfile()
    assert ExpressionProfile.from_dict({}) == base
    assert ExpressionProfile.from_dict({"rightHandOffset": "not a number"}) == base


# --- the exported MIDI and the render must agree ----------------------------


def test_the_exported_midi_uses_the_same_velocities_as_the_render(tmp_path) -> None:
    """One source of truth.

    The MIDI writer computed ``amplitude * 127`` on its own, so a file exported from a
    score played with the measured dynamics and the slew limit still came out with the
    full 0..127 range and the wrong hand balance - the score and its own playback file
    contradicting each other. When the lookup was first wired up it keyed on seconds while
    the MIDI works in beats from bar 0, which silently missed every note and filled the
    file with one flat velocity; the assertion below is what caught that.
    """
    from services.score.render import note_events

    score = transcribed_score()
    # A list, not a dict keyed by onset: every note here is in a chord, so keying by onset
    # silently drops half of them (32 notes became 16) and the comparison below would then
    # pass or fail for the wrong reason.
    played = [e[3] for e in note_events(score)]

    import mido

    path = write_midi(score, tmp_path / "out.mid")
    midi = mido.MidiFile(str(path))
    exported = sorted(
        m.velocity for tr in midi.tracks for m in tr if m.type == "note_on" and m.velocity > 0
    )
    assert exported, "the exported file has no notes"
    # The point of the test: the synth's note list and the written MIDI file carry the same
    # velocities. A missed lookup would not raise here, it would leave the two disagreeing.
    assert sorted(played) == exported
    # Not one flat velocity: a missed lookup would produce exactly that.
    assert len(set(exported)) > 10
    assert max(exported) <= VELOCITY_CEIL


def test_the_lookup_is_keyed_by_notated_position(tmp_path) -> None:
    score = transcribed_score()
    lookup = velocity_lookup(score)
    for measure in score.measures:
        for scored in (*measure.right, *measure.left):
            assert (scored.bar + 1, round(scored.beat, 4), scored.midi) in lookup


# --- the faithful mode changes nothing the player played ---------------------


def test_fit_chord_without_deletion_deletes_nothing() -> None:
    """``allow_deletion=False`` used to be consulted only after the subset search.

    The subset loop tries smaller and smaller groups and returns the first that fits, so
    a chord that could be made playable by dropping a note was made playable by dropping a
    note - 32 times on 星辰大海, in the mode whose contract is that it removes nothing.
    """
    pitches = [40, 52, 64, 76]
    plan = fit_chord(pitches, 12, protected=[76], allow_deletion=False)
    assert len(plan) == len(pitches), "a note was dropped despite allow_deletion=False"


def test_fit_chord_without_folding_changes_nothing() -> None:
    pitches = [28, 64, 76]
    plan = fit_chord(pitches, 12, protected=[76], allow_deletion=False, allow_folding=False)
    assert [p for _i, p in plan] == pitches


def test_the_faithful_mode_reports_instead_of_rewriting() -> None:
    """A chord a hand cannot hold is named, not silently changed."""
    g = grid()
    wide = [note(b * 0.5, 0.4, 28) for b in range(4)] + [note(b * 0.5, 0.4, 76) for b in range(4)]
    buckets = {
        index: ([], [n for n in wide if g.bar_start(index) <= n.start < g.bar_end(index)])
        for index in range(g.bar_count())
    }
    result = arrange(buckets, g, fidelity="transcribed")
    report = result.voicing
    assert report["octaveFolded"] == 0
    assert report["removed"] == 0
    assert report["unreachableGroups"] > 0, "an impossible chord was not reported"
    assert report["maxUnreachableSpan"] >= 40


def test_the_arranging_modes_still_fit_chords_to_the_hand() -> None:
    """Turning folding off must not turn it off everywhere.

    The arranged modes still promise a playable hand, and the tests that check that would
    pass vacuously if the fitter had been disabled rather than scoped.
    """
    pitches = [28, 52, 76]
    folded = fit_chord(pitches, 12, protected=[76])
    assert max(p for _i, p in folded) - min(p for _i, p in folded) <= 12
