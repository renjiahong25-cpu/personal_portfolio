"""Tests for the left-hand pattern builders and their difficulty pruning."""

from __future__ import annotations

import pytest

from services.arrangement.patterns import (
    BASS_HIGH,
    BASS_LOW,
    DIFFICULTY_BEATS,
    PATTERNS,
    Role,
    build_pattern,
    chord_intervals,
    place_bass,
    voice_chord,
)


def test_every_registered_pattern_builds_every_difficulty() -> None:
    for name in PATTERNS:
        for difficulty in (1, 2, 3, 4):
            notes = build_pattern(name, 9, "major", difficulty=difficulty)
            assert notes, f"{name} L{difficulty} produced nothing"


def test_place_bass_lands_inside_the_bass_register() -> None:
    for root in range(12):
        pitch = place_bass(root)
        assert BASS_LOW <= pitch <= BASS_HIGH


def test_place_bass_prefers_the_lower_of_two_valid_octaves() -> None:
    # E2 (40) and E3 (52) are both in range; the lower one is the bass note.
    assert place_bass(4) == 40


def test_voice_chord_stays_above_the_bass_and_inside_a_span() -> None:
    bass = place_bass(9)
    tones = voice_chord(chord_intervals("major"), bass)
    assert tones
    for pitch in tones:
        assert pitch > bass
        assert pitch - bass <= 19


def test_voice_chord_never_repeats_a_pitch() -> None:
    bass = place_bass(2)
    tones = voice_chord((0, 4, 7, 12), bass)
    assert len(tones) == len(set(tones))


def test_an_unknown_quality_falls_back_to_a_major_triad() -> None:
    assert chord_intervals("nonsense") == (0, 4, 7)


def test_an_unknown_pattern_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_pattern("does-not-exist", 0, "major")


def test_an_unsupported_difficulty_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_pattern("root_only", 0, "major", difficulty=7)


def test_l1_plays_at_half_speed() -> None:
    notes = build_pattern("root_only", 4, "major", difficulty=1)
    assert sorted({n.beat for n in notes}) == list(DIFFICULTY_BEATS[1])


def test_l1_keeps_only_the_skeleton() -> None:
    notes = build_pattern("root_octave", 4, "major", difficulty=1)
    assert {n.role for n in notes} == {Role.SKELETON}


def test_l2_keeps_the_chord_tones_but_drops_the_octave_doubling() -> None:
    notes = build_pattern("block_chord", 4, "major", difficulty=2)
    roles = {n.role for n in notes}
    assert Role.CHORD in roles
    assert Role.COLOR not in roles


def test_higher_difficulties_never_have_fewer_notes() -> None:
    counts = [len(build_pattern("broken_chord", 9, "minor7", difficulty=d)) for d in (1, 2, 3, 4)]
    assert counts == sorted(counts)


def test_the_top_difficulty_is_strictly_richer_than_the_one_below() -> None:
    """Regression: L4 used to equal L3 for every pattern but alberti.

    L4 opens the 1.0 and 1.5 subdivisions, which only the alberti figure
    occupied, so the top difficulty added nothing to the other four.
    """
    for name in PATTERNS:
        for quality in ("major", "minor7"):
            third = len(build_pattern(name, 9, quality, difficulty=3))
            fourth = len(build_pattern(name, 9, quality, difficulty=4))
            assert fourth > third, f"{name}/{quality}: L3={third} L4={fourth}"


def test_higher_difficulties_keep_strictly_more_roles() -> None:
    for name in PATTERNS:
        ranks = [
            max((n.role.rank for n in build_pattern(name, 4, "major", difficulty=d)), default=-1)
            for d in (1, 2, 3, 4)
        ]
        assert ranks == sorted(ranks), name


def test_alberti_bass_is_clipped_at_the_barline() -> None:
    notes = build_pattern("alberti_bass", 4, "major", difficulty=4)
    assert notes
    assert all(n.beat < 4.0 for n in notes)


def test_no_generated_note_falls_before_the_bar() -> None:
    for name in PATTERNS:
        for difficulty in (1, 2, 3, 4):
            for note in build_pattern(name, 7, "minor", difficulty=difficulty):
                assert note.beat >= 0.0, f"{name} L{difficulty}"


def test_notes_never_share_one_instant_and_pitch() -> None:
    """The generated left hand must be playable: no doubled key at one onset."""
    for name in PATTERNS:
        for difficulty in (1, 2, 3, 4):
            seen: set[tuple[float, int]] = set()
            for note in build_pattern(name, 2, "dominant7", difficulty=difficulty):
                key = (note.beat, note.midi)
                assert key not in seen, f"{name} L{difficulty} doubled {key}"
                seen.add(key)


def test_notes_are_sorted_by_position() -> None:
    for name in PATTERNS:
        notes = build_pattern(name, 5, "minor", difficulty=3)
        assert notes == sorted(notes, key=lambda n: (n.beat, n.midi))


def test_the_chord_is_actually_voiced_into_the_part() -> None:
    """A C major bar must contain the C major triad, not just a bass note."""
    notes = build_pattern("block_chord", 0, "major", difficulty=3)
    pitches = {n.midi % 12 for n in notes}
    assert pitches == {0, 4, 7}
    assert place_bass(0) in {n.midi for n in notes}


def test_a_minor_seventh_keeps_its_seventh() -> None:
    notes = build_pattern("block_chord", 9, "minor7", difficulty=3)
    pitches = {n.midi % 12 for n in notes}
    assert pitches == {9, 0, 4, 7}
    assert place_bass(9) in {n.midi for n in notes}


def test_duration_never_overruns_the_bar() -> None:
    for name in PATTERNS:
        for note in build_pattern(name, 3, "augmented", difficulty=4):
            assert note.beat + note.duration_beats <= 4.0 + 1e-9


def test_placed_note_serialises() -> None:
    payload = build_pattern("root_only", 4, "major", difficulty=2)[0].to_dict()
    assert payload["role"] == "skeleton"
    assert payload["midi"] == place_bass(4)
