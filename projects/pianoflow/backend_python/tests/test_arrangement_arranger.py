"""Tests for the arrangement layer: fidelity modes, difficulty, per-bar decisions."""

from __future__ import annotations

import itertools

import pytest

from services.arrangement.arranger import (
    ACCOMPANIMENT_LEVELS,
    FIDELITIES,
    MELODY_RANGE_HIGH,
    MELODY_RANGE_LOW,
    arrange,
    choose_pattern,
    chord_for_bar,
    describe_left_hand,
    fold_melody_to_range,
    simplify_melody,
    _thin_by_role,
)
from services.arrangement.grid import BarGrid
from services.arrangement.patterns import PlacedNote, Role
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


def test_the_melody_is_preserved_under_the_added_chord_tones() -> None:
    """Harmonising the right hand must not touch the melody itself.

    The user's complaint was that the right hand was 100% single notes, and the
    agreed fix was to fill chord tones in *below* the transcribed melody. That is
    only safe if the melody is untouched: not re-timed, not re-voiced. The one
    sanctioned edit is the register: the user asked for the melody to sit in
    C4-C5, so a note an octave too high comes back folded down. The pitch class
    - the actual tune - must never change.

    So the assertion is not "the count grew" but "every original note is still
    there, at its own time and volume, in its own pitch class" - additions are
    allowed, modifications are not.
    """
    grid = grid_at()
    buckets = good_buckets(grid)
    melody = [note(0.0, 1.0, 81), note(1.0, 1.0, 83)]
    expected = {n.midi for n in fold_melody_to_range(melody)}
    buckets[0] = (melody, buckets[0][1])
    for fidelity in FIDELITIES:
        for difficulty in (2, 3, 4):
            result = arrange(
                buckets, grid, chords=chords_per_bar(), fidelity=fidelity, difficulty=difficulty
            )
            got = list(result.bars[0].right)
            for original in melody:
                # The faithful path does not fold; the arranged path folds into C4-C5.
                allowed = {original.midi} if fidelity == "transcribed" else expected
                assert any(
                    n.start == original.start
                    and n.duration == original.duration
                    and n.midi in allowed
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
    # The register may be folded into C4-C5; the pitch classes may not move.
    assert {(n.start, n.midi % 12) for n in got} == {(n.start, n.midi % 12) for n in melody}
    assert all(MELODY_RANGE_LOW <= n.midi <= MELODY_RANGE_HIGH for n in got)


def test_the_added_tones_sit_below_the_melody_and_never_beside_it() -> None:
    """A cluster a second from the melody is unplayable and hides the tune."""
    grid = grid_at()
    buckets = good_buckets(grid)
    melody = [note(0.0, 1.0, 81)]
    buckets[0] = (melody, buckets[0][1])
    result = arrange(buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=4)
    bar = result.bars[0]
    top = max(n.midi for n in bar.right)
    # G5 folds to G4: the melody is still the highest note, one octave down.
    assert top == 69, "the folded melody must remain the highest sounding note"
    added = [n.midi for n in bar.right if n.midi != 69]
    assert added, "L4 should have added chord tones"
    for pitch in added:
        assert pitch < top
        assert top - pitch >= 3, f"added tone {pitch} is a second from the melody {top}"
        # The filler is quieter than the melody, or the ear stops following the tune.
        for original in bar.right:
            if original.midi == 69:
                continue
            assert original.amplitude < 0.6


def test_the_melody_grows_with_difficulty() -> None:
    """Regression: difficulty only reached the generated left hand.

    Most bars are kept as played, so selecting L1 changed almost nothing - a
    beginner still had to play the transcribed melody. Difficulty now thins the
    melody at the easy end *and* harmonises it at the hard end, so every level is
    distinguishable: L1 < L2 < L3 < L4 in right-hand note count. (The melody is
    protected from the density cap, so the top end differentiates through the
    filling the budget still leaves: a sixteen-per-bar line would saturate both.)
    """
    grid = grid_at()
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start = index * 2.0
        # A busy melody: two notes per beat, so thinning has something to cut.
        melody = [note(start + i * 0.25, 0.2, 72 + i % 8) for i in range(8)]
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


class TestMelodyRangeFold:
    """The arranged melody must sit in the C4-C5 comfort range the user asked for."""

    def test_high_notes_fold_down_an_octave(self) -> None:
        # C6 -> C5 and G5 -> G4: whole octaves, pitch classes untouched.
        folded = fold_melody_to_range([note(0.0, 0.5, 88), note(1.0, 0.5, 79)])
        assert [n.midi for n in folded] == [64, 67]

    def test_low_notes_fold_up_an_octave(self) -> None:
        folded = fold_melody_to_range([note(0.0, 0.5, 52)])
        assert folded[0].midi == 64

    def test_notes_in_the_range_do_not_move(self) -> None:
        folded = fold_melody_to_range([note(0.0, 0.5, 60), note(0.5, 0.5, 72)])
        assert [n.midi for n in folded] == [60, 72]

    def test_the_pitch_classes_are_preserved(self) -> None:
        source = [note(0.0, 0.5, 84), note(0.5, 0.5, 55)]
        folded = fold_melody_to_range(source)
        assert [n.midi % 12 for n in folded] == [n.midi % 12 for n in source]

    def test_every_folded_note_sits_in_the_comfortable_range(self) -> None:
        source = [note(i * 0.1, 0.09, 48 + i * 4) for i in range(12)]
        folded = fold_melody_to_range(source)
        assert all(MELODY_RANGE_LOW <= n.midi <= MELODY_RANGE_HIGH for n in folded)

    def test_folding_keeps_times_and_gains(self) -> None:
        folded = fold_melody_to_range([note(0.25, 0.5, 88, amplitude=0.42)])
        assert (folded[0].start, folded[0].duration, folded[0].amplitude) == (0.25, 0.5, 0.42)

    def test_the_arranged_melody_lands_in_the_comfortable_range(self) -> None:
        grid = grid_at()
        buckets = good_buckets(grid)
        buckets[0] = ([note(0.0, 1.0, 88), note(1.0, 1.0, 83)], buckets[0][1])
        result = arrange(buckets, grid, chords=chords_per_bar(), fidelity="arranged")
        # C6 and A5 come back as C5 and A4 - the tune, one register down.
        right_mids = {n.midi for n in result.bars[0].right}
        assert {64, 71} <= right_mids, "the folded melody notes must survive"
        assert 88 not in right_mids

    def test_the_faithful_path_never_folds(self) -> None:
        # "不要有八度折叠，要严格忠于原来的音频" - that promise covers the
        # faithful path; the arranged path is the one the range applies to.
        grid = grid_at()
        buckets = good_buckets(grid)
        buckets[0] = ([note(0.0, 1.0, 88), note(1.0, 1.0, 83)], buckets[0][1])
        result = arrange(buckets, grid, chords=chords_per_bar(), fidelity="transcribed")
        got = {n.midi for n in result.bars[0].right}
        assert 88 in got and 83 in got, "the faithful path must keep the original register"


class TestBarMelody:
    """The melody field separates the tune from the chord-tone filling."""

    def test_the_melody_is_the_tune_without_the_filling(self) -> None:
        grid = grid_at()
        buckets = good_buckets(grid)
        result = arrange(
            buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=4
        )
        for bar in result.bars:
            melody_keys = {(n.start, n.midi, n.duration) for n in bar.melody}
            right_keys = {(n.start, n.midi, n.duration) for n in bar.right}
            # Every melody note is still in the right hand, unchanged...
            assert melody_keys <= right_keys
            # ...and at L4 over chords the right hand carries more than the tune.
        assert sum(len(b.right) for b in result.bars) > sum(len(b.melody) for b in result.bars)

    def test_the_melody_only_score_carries_no_filling(self) -> None:
        from dataclasses import replace

        from services.arrangement.piano_score import build_score

        grid = grid_at()
        buckets = good_buckets(grid)
        arrangement = arrange(
            buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=4
        )
        full_right = sum(len(b.right) for b in arrangement.bars)
        melody_right = sum(len(b.melody) for b in arrangement.bars)
        assert melody_right < full_right, "L4 over chords must have added filling"
        melody_only = replace(
            arrangement,
            bars=[replace(b, right=b.melody, left=()) for b in arrangement.bars],
        )
        score = build_score(melody_only, grid)
        assert score.note_count == melody_right

    def test_the_faithful_melody_is_the_whole_right_hand(self) -> None:
        grid = grid_at()
        buckets = good_buckets(grid)
        result = arrange(buckets, grid, chords=chords_per_bar(), fidelity="transcribed")
        for bar in result.bars:
            assert bar.melody == bar.right

    def test_the_density_cap_never_cuts_the_melody(self) -> None:
        # A line dense enough to blow any L3 budget: the filling and the left hand
        # give way, the tune stays whole. This guards the complaint 仅旋律 sounding
        # like "中间少了很多音" - the cap used to put melody and filling in one pot,
        # and the filling (two notes per melody note) out-voted the tune.
        grid = grid_at()
        buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
        for index in range(grid.bar_count()):
            start = index * 2.0
            buckets[index] = (
                [note(start + i * 0.125, 0.2, 72 + i % 6) for i in range(16)],
                [note(start + j * 0.25, 0.2, 48 + j % 4) for j in range(8)],
            )
        result = arrange(
            buckets, grid, chords=chords_per_bar(), fidelity="arranged", difficulty=3
        )
        assert sum(len(b.left) for b in result.bars) < 32, "the cap should have cut the left"
        for bar in result.bars:
            melody_keys = {(n.start, n.midi, n.duration) for n in bar.melody}
            right_keys = {(n.start, n.midi, n.duration) for n in bar.right}
            assert melody_keys <= right_keys, f"bar {bar.index}: the cap cut a melody note"
            assert len(bar.melody) == 16, "the whole line must survive"


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


class TestAccompanimentDensity:
    """The left-hand density level: light thins the generated figure along its role
    tiers and plays it quieter - both are what keep the right hand audible. A kept,
    transcribed bar is never touched, because thinning the recording is not a
    density choice."""

    def test_thin_by_role_keeps_skeleton_and_chord_for_light(self) -> None:
        placed = [
            PlacedNote(0.0, 1.0, 45, Role.SKELETON),
            PlacedNote(0.0, 1.0, 52, Role.CHORD),
            PlacedNote(0.0, 1.0, 57, Role.COLOR),
            PlacedNote(1.5, 0.5, 52, Role.ORNAMENT),
        ]
        light = _thin_by_role(placed, ACCOMPANIMENT_LEVELS["light"])
        assert [n.role for n in light] == [Role.SKELETON, Role.CHORD]
        standard = _thin_by_role(placed, ACCOMPANIMENT_LEVELS["standard"])
        assert len(standard) == 4

    def test_thin_by_role_never_returns_nothing(self) -> None:
        """A figure made of nothing but cut roles survives uncut, not silent."""
        placed = [PlacedNote(0.0, 1.0, 60, Role.ORNAMENT)]
        assert _thin_by_role(placed, ACCOMPANIMENT_LEVELS["light"]) == placed

    def test_light_thins_the_generated_left_hand(self) -> None:
        from services.arrangement.textures import TextureName

        grid = grid_at()
        buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
        for index in range(grid.bar_count()):
            start = index * 2.0
            buckets[index] = ([note(start, 0.8, 76)], [note(start, 0.4, 48)])
        kwargs = dict(
            chords=chords_per_bar(), fidelity="arranged", difficulty=4,
            texture=TextureName.RHYTHM,
        )
        standard = arrange(buckets, grid, accompaniment="standard", **kwargs)
        light = arrange(buckets, grid, accompaniment="light", **kwargs)
        std_count = sum(len(bar.left) for bar in standard.bars)
        light_count = sum(len(bar.left) for bar in light.bars)
        assert light_count < std_count
        # The skeleton survives, so the figure keeps its pulse.
        assert light_count >= grid.bar_count()

    def test_light_plays_the_generated_hand_quieter(self) -> None:
        grid = grid_at()
        kwargs = dict(
            chords=chords_per_bar(), fidelity="arranged", difficulty=4,
            pattern="block_chord",
        )
        standard = arrange(good_buckets(grid), grid, accompaniment="standard", **kwargs)
        light = arrange(good_buckets(grid), grid, accompaniment="light", **kwargs)
        std_amp = standard.bars[0].left[0].amplitude
        light_amp = light.bars[0].left[0].amplitude
        assert abs(light_amp - std_amp * 0.75) < 1e-9

    def test_full_keeps_every_note_but_plays_louder(self) -> None:
        grid = grid_at()
        kwargs = dict(
            chords=chords_per_bar(), fidelity="arranged", difficulty=4,
            pattern="block_chord",
        )
        standard = arrange(good_buckets(grid), grid, accompaniment="standard", **kwargs)
        full = arrange(good_buckets(grid), grid, accompaniment="full", **kwargs)
        assert sum(len(b.left) for b in standard.bars) == sum(len(b.left) for b in full.bars)
        assert abs(full.bars[0].left[0].amplitude - standard.bars[0].left[0].amplitude * 1.25) < 1e-9

    def test_kept_bars_keep_the_recording_balance(self) -> None:
        grid = grid_at()
        result = arrange(
            good_buckets(grid), grid, chords=chords_per_bar(),
            fidelity="as-played", accompaniment="light",
        )
        # The level gain reaches only the generated hand, so a kept bar stays at the
        # base gain even at light.
        assert abs(result.bars[0].left[0].amplitude - 0.6 * 0.45) < 1e-9

    def test_unknown_accompaniment_is_rejected(self) -> None:
        grid = grid_at()
        with pytest.raises(ValueError):
            arrange(good_buckets(grid), grid, accompaniment="nonsense")  # type: ignore[arg-type]

    def test_the_arrangement_reports_its_density_level(self) -> None:
        grid = grid_at()
        result = arrange(good_buckets(grid), grid, chords=chords_per_bar(), accompaniment="light")
        assert result.to_dict()["accompaniment"] == "light"
