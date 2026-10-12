"""The ballad arranger: extracted melody + chords -> two-hand piano."""

from __future__ import annotations

from pathlib import Path

import mido

from services.arrangement.ballad import (
    BalladConfig,
    SectionSpan,
    arrange,
)
from services.arrangement.grid import BarGrid
from services.arrangement.harmony import harmonize_melody
from services.arrangement.piano_midi import write_midi
from services.arrangement.vocal_melody import MelodyNote
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

SECTIONS: tuple[SectionSpan, ...] = (
    SectionSpan(0, 4, "verse"),
    SectionSpan(4, 8, "chorus"),
)


def grid_8_bars() -> BarGrid:
    bar_seconds = 4 * 60.0 / 84.0
    return BarGrid(tempo_bpm=84.0, beats_per_bar=4, phase_seconds=0.2, duration=8 * bar_seconds)


def chords_8_bars(grid: BarGrid) -> list[ChordEvent]:
    rotations = [(4, "major"), (11, "major"), (1, "minor7"), (1, "major7")]
    return [
        ChordEvent(
            start=grid.bar_start(bar),
            duration=grid.bar_seconds,
            root=rotations[bar % len(rotations)][0],
            quality=rotations[bar % len(rotations)][1],
        )
        for bar in range(grid.bar_count())
    ]


def melody_notes(grid: BarGrid) -> list[MelodyNote]:
    beat = grid.beat_seconds
    beats = [(2, 0.0, 76), (2, 1.0, 74), (2, 2.0, 72), (5, 0.0, 80), (5, 1.5, 83), (5, 3.0, 80)]
    return [
        MelodyNote(
            start=grid.bar_start(bar) + position * beat,
            end=grid.bar_start(bar) + (position + 1) * beat,
            midi=midi,
            velocity=80,
        )
        for bar, position, midi in beats
    ]


def right_in_bar(score, bar: int) -> list:
    return [n for m in score.measures if m.index == bar for n in m.right]


def left_in_bar(score, bar: int) -> list:
    return [n for m in score.measures if m.index == bar for n in m.left]


def test_arrange_smoke() -> None:
    grid = grid_8_bars()
    result = arrange(
        melody_notes(grid),
        chords_8_bars(grid),
        grid,
        SECTIONS,
        title="test",
        key_tonic=4,
        key_mode="major",
    )
    assert result.score.note_count > 0
    assert len(result.score.measures) == 8
    low, high = result.score.pitch_range()
    assert 21 <= low <= high <= 108


def test_left_hand_stays_under_the_melody_register() -> None:
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, SECTIONS)
    left = [n for m in result.score.measures for n in m.left]
    assert left
    assert max(n.midi for n in left) <= 64  # bass 40..52 + one octave of chord tones
    assert min(n.midi for n in left) >= 36


def test_melody_notes_survive_verbatim() -> None:
    grid = grid_8_bars()
    melody = melody_notes(grid)
    result = arrange(melody, chords_8_bars(grid), grid, SECTIONS)
    timeline = {
        (n["bar"] - 1, round(n["beat"], 4), n["midi"])
        for n in result.score.note_timeline()
        if n["hand"] == "right"
    }
    for note in melody:
        bar, beat, sixteenth, _error = grid.quantize(note.start)
        full_beat = beat + sixteenth / 4  # the timeline reports beats including sixteenths
        assert (bar, round(full_beat, 4), note.midi) in timeline, (
            f"melody note {note.midi} at bar {bar} beat {full_beat} is missing"
        )


def test_octave_double_in_the_chorus() -> None:
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, SECTIONS)
    chorus_bar = right_in_bar(result.score, 5)
    pitches_at_beat0 = sorted(n.midi for n in chorus_bar if n.beat == 0.0)
    assert 80 in pitches_at_beat0  # the melody
    assert 92 in pitches_at_beat0  # its octave, quieter
    # The doubled peak is exactly a twelve-semitone span, playable in one hand.
    assert max(pitches_at_beat0) - min(pitches_at_beat0) <= 12


def test_harmonisation_fills_held_notes_in_the_verse() -> None:
    """Mid-range notes get chord tones below, with the melody on top."""
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, SECTIONS)
    verse_bar = right_in_bar(result.score, 2)
    pitches_at_beat0 = sorted(n.midi for n in verse_bar if n.beat == 0.0)
    assert 76 in pitches_at_beat0  # the melody
    assert any(p < 76 for p in pitches_at_beat0), "L3 should add chord tones below"
    assert max(pitches_at_beat0) == 76, "the melody stays the top voice"


def test_peak_notes_are_not_also_harmonised() -> None:
    """Double XOR harmony: a doubled peak carries no chord tones underneath."""
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, SECTIONS)
    chorus_bar = right_in_bar(result.score, 5)
    pitches_at_beat15 = [n.midi for n in chorus_bar if n.beat == 1.5]
    assert 83 in pitches_at_beat15 and 95 in pitches_at_beat15
    assert len(pitches_at_beat15) == 2, f"a doubled peak stays a dyad, got {pitches_at_beat15}"


def test_no_octave_double_below_the_peak_threshold() -> None:
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, SECTIONS)
    verse_pitches = {n.midi for n in right_in_bar(result.score, 2)}
    assert 76 in verse_pitches
    assert 88 not in verse_pitches  # 76 < 78: below the peak threshold, not doubled
    assert 95 not in verse_pitches


def test_doubling_follows_the_peak_not_the_section() -> None:
    """A high peak is doubled even where the section label says 'verse': the singer's
    high note is a peak wherever it happens to be."""
    grid = grid_8_bars()
    peak_in_verse = [
        MelodyNote(
            start=grid.bar_start(2),
            end=grid.bar_start(2) + grid.beat_seconds,
            midi=80,  # >= octave_double_above (78)
            velocity=80,
        )
    ]
    result = arrange(peak_in_verse, chords_8_bars(grid), grid, SECTIONS)
    verse_pitches = {n.midi for n in right_in_bar(result.score, 2)}
    assert 80 in verse_pitches and 92 in verse_pitches


def test_melody_free_bars_of_a_fill_section_get_an_arpeggio() -> None:
    grid = grid_8_bars()
    sections = (SectionSpan(0, 4, "intro"), SectionSpan(4, 8, "chorus"))
    chorus_melody = [
        MelodyNote(
            start=grid.bar_start(5) + grid.beat_seconds,
            end=grid.bar_start(5) + 2 * grid.beat_seconds,
            midi=80,
            velocity=80,
        )
    ]
    result = arrange(chorus_melody, chords_8_bars(grid), grid, sections)
    intro_bar = right_in_bar(result.score, 0)
    assert len(intro_bar) == 8  # one fill note per eighth
    assert all(55 <= n.midi <= 83 for n in intro_bar)
    assert len(right_in_bar(result.score, 1)) == 8  # every melody-free bar is filled


def test_no_fill_where_the_melody_sings() -> None:
    grid = grid_8_bars()
    sections = (SectionSpan(0, 4, "intro"), SectionSpan(4, 8, "chorus"))
    intro_melody = [
        MelodyNote(
            start=grid.bar_start(0),
            end=grid.bar_start(0) + grid.beat_seconds,
            midi=76,
            velocity=80,
        )
    ]
    result = arrange(intro_melody, chords_8_bars(grid), grid, sections)
    bar0 = right_in_bar(result.score, 0)
    # bar 0 has melody, so it carries the melody (harmonised), not a fill arpeggio:
    # a fill would put a note on every eighth of the bar, the melody occupies one.
    assert any(n.midi == 76 for n in bar0)
    assert len(bar0) < 8


def test_sections_without_names_use_the_default_style() -> None:
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, (SectionSpan(0, 8, "unknown"),))
    assert result.score.note_count > 0  # the default style handles the bar, no crash


def test_write_midi_round_trips(tmp_path: object) -> None:
    grid = grid_8_bars()
    result = arrange(melody_notes(grid), chords_8_bars(grid), grid, SECTIONS)
    path = write_midi(result.score, Path(tmp_path) / "ballad.mid")  # type: ignore[arg-type]
    mid = mido.MidiFile(str(path))
    assert len(mid.tracks) == 3  # meta + right + left
    velocities = [
        msg.velocity
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    ]
    assert velocities
    assert all(1 <= v <= 127 for v in velocities)


def test_harmonise_span_is_per_onset_not_per_song() -> None:
    """Regression: the span budget measures the chord being voiced, not the piece.

    A melody that spans 31 semitones over the song still gets filled note by note;
    the old whole-output comparison rejected every addition once the range opened.
    """
    chords = [ChordEvent(start=0.0, duration=3.0, root=0, quality="major")]
    melody = [
        NoteEvent(start=0.0, duration=1.0, midi=62, amplitude=0.8),
        NoteEvent(start=1.5, duration=1.0, midi=85, amplitude=0.8),
    ]
    out = harmonize_melody(melody, chords, difficulty=4, beat_seconds=0.75, span_cap=12)
    by_onset: dict[float, set[int]] = {}
    for note in out:
        by_onset.setdefault(round(note.start, 3), set()).add(note.midi)
    assert 62 in by_onset[0.0] and len(by_onset[0.0]) > 1, "the low note was not filled"
    assert 85 in by_onset[1.5] and len(by_onset[1.5]) > 1, "the high note was not filled"
    for onset in (0.0, 1.5):
        pitches = by_onset[onset]
        assert max(pitches) - min(pitches) <= 12, "one hand must not stretch past the cap"


def test_dense_section_escalates_to_the_chorus_texture() -> None:
    """A 'bridge' the voice sings as a chorus (>= 4 melody notes per voiced bar) gets
    the full chorus left hand instead of the sparse arpeggio."""
    grid = grid_8_bars()
    sections = (SectionSpan(0, 2, "intro"), SectionSpan(2, 8, "bridge"))
    dense = [
        MelodyNote(
            start=grid.bar_start(bar) + beat * grid.beat_seconds,
            end=grid.bar_start(bar) + (beat + 1) * grid.beat_seconds,
            midi=72,
            velocity=80,
        )
        for bar in range(2, 8)
        for beat in range(4)
    ]
    result = arrange(dense, chords_8_bars(grid), grid, sections)
    bridge_left = left_in_bar(result.score, 3)
    assert len(bridge_left) >= 6  # ARPFULL (~8/bar), not ARLONG (4/bar)
    intro_left = left_in_bar(result.score, 0)
    assert len(intro_left) <= 4  # the sparse section stays sparse


def test_sparse_section_stays_sparse() -> None:
    """Below the density threshold the label wins: a 'bridge' the voice barely sings
    keeps its own texture."""
    grid = grid_8_bars()
    sections = (SectionSpan(0, 2, "intro"), SectionSpan(2, 8, "bridge"))
    sparse = [
        MelodyNote(
            start=grid.bar_start(bar),
            end=grid.bar_start(bar) + grid.beat_seconds,
            midi=72,
            velocity=80,
        )
        for bar in range(2, 8)
    ]
    result = arrange(sparse, chords_8_bars(grid), grid, sections)
    bridge_left = left_in_bar(result.score, 3)
    assert len(bridge_left) <= 4  # ARLONG, the label's texture


def test_config_defaults_drive_the_sections() -> None:
    from services.arrangement.textures import TextureName

    config = BalladConfig()
    assert config.styles["chorus"].texture is TextureName.ARPFULL
    assert config.styles["intro"].dynamic < config.styles["chorus"].dynamic
    assert config.escalate_density > 0
