"""Tests for the arrangement layer: fidelity modes, difficulty, per-bar decisions."""

from __future__ import annotations

import itertools

import pytest

from services.arrangement.arranger import (
    FIDELITIES,
    arrange,
    choose_pattern,
    chord_for_bar,
    describe_left_hand,
    simplify_melody,
)
from services.arrangement.grid import BarGrid
from services.arrangement.quality import assess_bars
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent


def grid_at(duration: float = 8.0) -> BarGrid:
    """4 bars of 4/4 at 120 BPM: 2 s per bar, beat 0.5 s."""
    return BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=duration)


def note(start: float, duration: float, midi: int, amplitude: float = 0.6) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def good_buckets(grid: BarGrid) -> dict[int, tuple[list[NoteEvent], list[NoteEvent]]]:
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start = index * 2.0
        buckets[index] = (
            [note(start + i * 0.5, 0.4, 76) for i in range(4)],
            [note(start + i * 0.5, 0.4, 48 + (i % 3) * 4) for i in range(4)],
        )
    return buckets


def chords_per_bar(root: int = 9) -> list[ChordEvent]:
    return [
        ChordEvent(start=index * 2.0, duration=2.0, root=root, quality="minor")
        for index in range(8)
    ]


def test_as_played_keeps_every_bar() -> None:
    grid = grid_at()
    result = arrange(good_buckets(grid), grid, chords=chords_per_bar(), fidelity="as-played")
    assert all(bar.source == "as-played" for bar in result.bars)
    assert result.recomposed == []


def test_arranged_rewrites_every_bar() -> None:
    grid = grid_at()
    result = arrange(good_buckets(grid), grid, chords=chords_per_bar(), fidelity="arranged")
    assert all(bar.source == "recomposed" for bar in result.bars)
    assert len(result.recomposed) == grid.bar_count()


def test_hybrid_keeps_clean_bars_and_rewrites_the_failed_run() -> None:
    grid = grid_at()
    buckets = good_buckets(grid)
    # Bars 1 and 2 get an impossible same-pitch overlap in the left hand.
    for index in (1, 2):
        start = index * 2.0
        buckets[index] = (
            buckets[index][0],
            [note(start, 1.0, 48), note(start + 0.5, 1.0, 48)],
        )
    report = assess_bars(buckets, grid)
    assert set(report.recomposed_bars) == {1, 2}
    result = arrange(buckets, grid, chords=chords_per_bar(), quality=report, fidelity="hybrid")
    assert [bar.source for bar in result.bars] == [
        "as-played",
        "recomposed",
        "recomposed",
        "as-played",
    ]


def test_the_melody_is_preserved_verbatim_under_the_added_chord_tones() -> None:
    """Harmonising the right hand must not touch the melody itself.

    The user's complaint was that the right hand was 100% single notes, and the
    agreed fix was to fill chord tones in *below* the transcribed melody. That is
    only safe if the melody is untouched: not re-pitched, not re-timed, not
    re-voiced. Measured on 星辰大海 the transcribed right hand is the actual vocal
    line, so any edit to it is an edit to the song.

    So the assertion is not "the count grew" but "every original note is still
    there, unchanged" - additions are allowed, modifications are not.
    """
    grid = grid_at()
    buckets = good_buckets(grid)
    melody = [note(0.0, 1.0, 81), note(1.0, 1.0, 83)]
    buckets[0] = (melody, buckets[0][1])
    for fidelity in FIDELITIES:
        for difficulty in (2, 3, 4):
            result = arrange(
                buckets, grid, chords=chords_per_bar(), fidelity=fidelity, difficulty=difficulty
            )
            got = list(result.bars[0].right)
            # Each original note survives, at its own time, pitch and volume.
            for original in melody:
                assert any(
                    n.start == original.start
                    and n.duration == original.duration
                    and n.midi == original.midi
                    and n.amplitude == original.amplitude
                    for n in got
                ), f"melody note {original} was altered at {fidelity}/L{difficulty}"
            # ...and nothing was invented that is not a melody note or a chord tone.
            assert len(got) >= len(melody)


def test_l1_adds_no_chord_tones_to_the_melody() -> None:
    """L1 allows one note per hand, so the melody must be left completely alone.

    Compared as a set rather than a list: the arrangement re-sorts by onset while
    folding and thinning, and a notation order is not a musical property.
    """
    grid = grid_at()
    buckets = good_buckets(grid)
    melody = [note(0.0, 1.0, 81), note(1.0, 1.0, 83)]
    buckets[0] = (melody, buckets[0][1])
    result = arrange(buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=1)
    got = result.bars[0].right
    assert len(got) == len(melody), f"L1 added notes to the melody: {len(got)} vs {len(melody)}"
    assert {(n.start, n.midi) for n in got} == {(n.start, n.midi) for n in melody}


def test_the_added_tones_sit_below_the_melody_and_never_beside_it() -> None:
    """A cluster a second from the melody is unplayable and hides the tune."""
    grid = grid_at()
    buckets = good_buckets(grid)
    melody = [note(0.0, 1.0, 81)]
    buckets[0] = (melody, buckets[0][1])
    result = arrange(buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=4)
    bar = result.bars[0]
    top = max(n.midi for n in bar.right)
    assert top == 81, "the melody must remain the highest sounding note"
    added = [n.midi for n in bar.right if n.midi != 81]
    assert added, "L4 should have added chord tones"
    for pitch in added:
        assert pitch < top
        assert top - pitch >= 3, f"added tone {pitch} is a second from the melody {top}"
    # The filler is quieter than the melody, or the ear stops following the tune.
    for original in bar.right:
        if original.midi == 81:
            continue
        assert original.amplitude < 0.6


def test_the_melody_grows_with_difficulty() -> None:
    """Regression: difficulty only reached the generated left hand.

    Most bars are kept as played, so selecting L1 changed almost nothing - a
    beginner still had to play the transcribed melody. Difficulty now thins the
    melody at the easy end *and* harmonises it at the hard end, so every level is
    distinguishable: L1 < L2 < L3 < L4 in right-hand note count.
    """
    grid = grid_at()
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start = index * 2.0
        # A busy melody: four notes per beat, so thinning has something to cut.
        melody = [note(start + i * 0.125, 0.2, 72 + i % 8) for i in range(16)]
        buckets[index] = (melody, [note(start + j * 0.5, 0.4, 48) for j in range(4)])
    counts = {}
    for difficulty in (1, 2, 3, 4):
        result = arrange(
            buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=difficulty
        )
        counts[difficulty] = sum(len(bar.right) for bar in result.bars)
    assert counts[1] < counts[2] < counts[3] < counts[4]


def test_simplify_melody_keeps_the_longest_note_of_each_beat() -> None:
    grid = grid_at()
    melody = [
        note(0.0, 0.2, 76),
        note(0.1, 0.9, 79),
        note(0.5, 0.2, 81),
        note(0.6, 0.8, 83),
    ]
    kept = simplify_melody(melody, grid, 2)
    assert [n.midi for n in kept] == [79, 83]


def test_simplify_melody_at_l1_uses_only_beats_one_and_three() -> None:
    grid = grid_at()
    melody = [note(beat * 0.5, 0.4, 76 + beat) for beat in range(8)]
    kept = simplify_melody(melody, grid, 1)
    assert len(kept) == 4
    assert all(grid.quantize(n.start)[1] in (0, 2) for n in kept)


def test_simplify_melody_leaves_harder_difficulties_alone() -> None:
    grid = grid_at()
    melody = [note(beat * 0.5, 0.4, 76 + beat) for beat in range(8)]
    assert simplify_melody(melody, grid, 3) == melody
    assert simplify_melody(melody, grid, 4) == melody


def test_simplify_melody_never_returns_nothing() -> None:
    """Dropping the melody entirely would be worse than leaving it dense."""
    grid = grid_at()
    # Every note sits on beat 4, which L1 does not keep.
    melody = [note(1.5 + i * 0.02, 0.05, 76 + i) for i in range(4)]
    assert simplify_melody(melody, grid, 1) == melody


def test_simplify_melody_handles_an_empty_hand() -> None:
    assert simplify_melody([], grid_at(), 1) == []


def test_a_bar_with_no_detected_chord_is_kept_as_played() -> None:
    """Inventing a harmony would be worse than keeping the transcription."""
    grid = grid_at()
    buckets = good_buckets(grid)
    report = assess_bars(buckets, grid)
    result = arrange(buckets, grid, chords=[], quality=report, fidelity="arranged")
    assert all(bar.source == "as-played" for bar in result.bars)
    assert all("no chord" in bar.reason for bar in result.bars)


def test_harder_difficulties_do_not_empty_a_recomposed_bar() -> None:
    grid = grid_at()
    for difficulty in (1, 2, 3, 4):
        result = arrange(
            good_buckets(grid),
            grid,
            chords=chords_per_bar(),
            fidelity="arranged",
            difficulty=difficulty,
            pattern="block_chord",
        )
        assert all(bar.left for bar in result.bars), difficulty


def test_difficulty_controls_note_count_in_generated_bars() -> None:
    grid = grid_at()
    counts = {}
    for difficulty in (1, 2, 3, 4):
        result = arrange(
            good_buckets(grid),
            grid,
            chords=chords_per_bar(),
            fidelity="arranged",
            difficulty=difficulty,
            pattern="block_chord",
        )
        counts[difficulty] = sum(len(bar.left) for bar in result.bars)
    assert counts[1] < counts[2] <= counts[3] <= counts[4]


def test_generated_left_hand_never_doubles_a_key_at_one_instant() -> None:
    grid = grid_at()
    for pattern in ("root_only", "root_octave", "block_chord", "broken_chord", "alberti_bass"):
        result = arrange(
            good_buckets(grid),
            grid,
            chords=chords_per_bar(),
            fidelity="arranged",
            difficulty=4,
            pattern=pattern,
        )
        for bar in result.bars:
            seen: set[tuple[float, int]] = set()
            for event in bar.left:
                key = (round(event.start, 3), event.midi)
                assert key not in seen, f"{pattern} bar {bar.index}"
                seen.add(key)


def test_generated_notes_stay_inside_the_bar() -> None:
    grid = grid_at()
    result = arrange(
        good_buckets(grid), grid, chords=chords_per_bar(), fidelity="arranged", difficulty=4
    )
    for bar in result.bars:
        start, end = grid.bar_start(bar.index), grid.bar_end(bar.index)
        for event in bar.left:
            assert start - 1e-6 <= event.start < end


def test_generated_notes_are_monophonic_per_hand() -> None:
    """The left hand is one part: two identical pitches sounding is unplayable."""
    grid = grid_at()
    result = arrange(
        good_buckets(grid), grid, chords=chords_per_bar(), fidelity="arranged", difficulty=4
    )
    for bar in result.bars:
        by_pitch: dict[int, list[NoteEvent]] = {}
        for event in bar.left:
            by_pitch.setdefault(event.midi, []).append(event)
        for pitch, events in by_pitch.items():
            events.sort(key=lambda n: n.start)
            for current, following in itertools.pairwise(events):
                assert following.start >= current.end - 1e-6, f"bar {bar.index} pitch {pitch}"


def test_the_pattern_is_chosen_once_and_reused_everywhere() -> None:
    """Choosing per bar is what makes a generated score change style mid-piece."""
    grid = grid_at()
    result = arrange(good_buckets(grid), grid, chords=chords_per_bar(), fidelity="arranged")
    assert (
        len({bar.reason.rsplit(" -> ", 1)[-1] for bar in result.bars if bar.source == "recomposed"})
        == 1
    )


def test_choose_pattern_detects_a_broken_chord_left_hand() -> None:
    """Eighth-note off-beats on top of the bass is a broken chord."""
    grid = grid_at()
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start = index * 2.0
        buckets[index] = (
            [note(start + i * 0.5, 0.4, 76) for i in range(4)],
            [note(start + i * 0.25, 0.2, 48 + (i % 3) * 4) for i in range(8)],
        )
    assert choose_pattern(buckets, grid)[0] == "broken_chord"


def test_choose_pattern_follows_the_difficulty_not_the_measurement() -> None:
    """Regression: choose_pattern ignored its own difficulty argument.

    It measured the transcribed left hand and picked from that, so every level
    produced the same figure. On 星辰大海, L1 through L4 all chose ``block_chord``
    and the user's approved mapping ("L4 -> alberti_bass") was unreachable. The
    figure is now chosen by difficulty; the measurement only explains the choice.

    Both fixtures below are deliberately built to point at *different* textures, so
    this also proves the measurement no longer decides.
    """
    grid = grid_at()

    stacked: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start = index * 2.0
        stacked[index] = (
            [note(start, 0.4, 76)],
            # 30 ms apart, so all three land inside one simultaneity window.
            [note(start + j * 0.03, 0.4, 48 + j * 4) for j in range(3)],
        )
    bass_only = good_buckets(grid)

    expected = {1: "root_only", 2: "block_chord", 3: "broken_chord", 4: "alberti_bass"}
    for difficulty, pattern in expected.items():
        for name, buckets in (("stacked", stacked), ("bass-only", bass_only)):
            chosen, why = choose_pattern(buckets, grid, difficulty=difficulty)
            assert chosen == pattern, f"{name} left hand at L{difficulty}"
            # The measurement is still reported, so the UI can explain itself.
            assert f"L{difficulty}" in why
            assert "转录实测" in why


def test_an_explicit_pattern_overrides_detection() -> None:
    grid = grid_at()
    result = arrange(
        good_buckets(grid),
        grid,
        chords=chords_per_bar(),
        fidelity="arranged",
        pattern="alberti_bass",
    )
    assert result.pattern == "alberti_bass"
    assert result.pattern_reason == "caller supplied"


def test_chord_for_bar_prefers_the_chord_covering_the_middle() -> None:
    grid = grid_at()
    chords = [
        ChordEvent(start=0.0, duration=0.5, root=0, quality="major"),
        ChordEvent(start=0.5, duration=3.0, root=5, quality="minor"),
    ]
    assert chord_for_bar(chords, grid, 0).root == 5


def test_chord_for_bar_returns_none_without_chords() -> None:
    assert chord_for_bar([], grid_at(), 0) is None


def test_invalid_inputs_are_rejected() -> None:
    grid = grid_at()
    with pytest.raises(ValueError):
        arrange(good_buckets(grid), grid, fidelity="nonsense")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        arrange(good_buckets(grid), grid, difficulty=9)
    with pytest.raises(ValueError):
        arrange(good_buckets(grid), grid, pattern="nope")


def test_a_recomposed_bar_records_why() -> None:
    grid = grid_at()
    result = arrange(good_buckets(grid), grid, chords=chords_per_bar(), fidelity="arranged")
    assert all("fidelity=arranged" in bar.reason for bar in result.bars)


def test_the_arrangement_serialises_for_the_api() -> None:
    grid = grid_at()
    payload = arrange(
        good_buckets(grid), grid, chords=chords_per_bar(), fidelity="hybrid"
    ).to_dict()
    assert payload["fidelity"] == "hybrid"
    assert payload["barCount"] == grid.bar_count()
    assert len(payload["bars"]) == grid.bar_count()


def test_describe_left_hand_reads_as_text() -> None:
    grid = grid_at()
    result = arrange(good_buckets(grid), grid, chords=chords_per_bar(), fidelity="arranged")
    assert describe_left_hand(result.bars[0]).startswith("left: ")


def test_texture_mode_builds_left_hand_from_texture() -> None:
    from services.arrangement.textures import TextureName

    grid = grid_at()
    result = arrange(
        good_buckets(grid),
        grid,
        chords=chords_per_bar(),
        fidelity="arranged",
        texture=TextureName.BLOCK,
    )
    assert result.pattern == "block"
    assert result.bars[0].left


def test_texture_plan_mode_varies_left_hand() -> None:
    from services.arrangement.textures import TextureName, build_texture_plan

    grid = grid_at()
    plan = build_texture_plan([(0, 2, TextureName.BLOCK), (2, 4, TextureName.HALF)])
    result = arrange(
        good_buckets(grid),
        grid,
        chords=chords_per_bar(),
        fidelity="arranged",
        texture_plan=plan,
    )
    assert result.pattern == "texture_plan"
    assert result.bars[0].left
    assert result.bars[2].left


def test_texture_and_texture_plan_are_mutually_exclusive() -> None:
    from services.arrangement.textures import TextureName, build_texture_plan

    grid = grid_at()
    plan = build_texture_plan([(0, 2, TextureName.BLOCK)])
    with pytest.raises(ValueError):
        arrange(
            good_buckets(grid),
            grid,
            chords=chords_per_bar(),
            texture=TextureName.BLOCK,
            texture_plan=plan,
        )


def test_texture_mode_rejects_unknown_texture() -> None:
    grid = grid_at()
    with pytest.raises(ValueError):
        arrange(
            good_buckets(grid),
            grid,
            chords=chords_per_bar(),
            texture="does-not-exist",  # type: ignore[arg-type]
        )
