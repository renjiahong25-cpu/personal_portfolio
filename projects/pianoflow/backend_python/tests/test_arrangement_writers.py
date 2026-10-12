"""Tests for the two-staff MusicXML and MIDI writers."""

from __future__ import annotations

import re
from pathlib import Path

import mido
import pytest

from services.arrangement.arranger import arrange
from services.arrangement.grid import BarGrid
from services.arrangement.piano_midi import (
    LEFT_TRACK_NAME,
    META_TRACK_NAME,
    RIGHT_TRACK_NAME,
    beats_to_ticks,
    key_name_for,
    midi_summary,
    seconds_to_ticks,
    write_midi,
)
from services.arrangement.piano_musicxml import musicxml_summary, to_musicxml
from services.arrangement.piano_score import Measure, PianoScore, ScoredNote, build_score
from services.audio_analysis.polyphony import NoteEvent


def note(start: float, duration: float, midi: int, amplitude: float = 0.6) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def grid_at(duration: float = 8.0, tempo: float = 120.0) -> BarGrid:
    return BarGrid(tempo_bpm=tempo, beats_per_bar=4, phase_seconds=0.0, duration=duration)


def score_for(
    right: list[NoteEvent],
    left: list[NoteEvent],
    *,
    title: str = "Test",
    key_tonic: int | None = None,
    key_mode: str | None = None,
) -> PianoScore:
    grid = grid_at()
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        start, end = grid.bar_start(index), grid.bar_end(index)
        buckets[index] = (
            [n for n in right if start <= n.start < end],
            [n for n in left if start <= n.start < end],
        )
    result = arrange(buckets, grid)
    return build_score(result, grid, title=title, key_tonic=key_tonic, key_mode=key_mode)


def two_hand_score() -> PianoScore:
    """Melody in the right hand over a bass line, four bars.

    The hands must not share a pitch: music21 treats two notes of the same pitch
    in one part as an unplayed chord member, and the export drops them.
    """
    right = [note(b * 0.5, 0.4, 72 + (b % 5)) for b in range(16)]
    left = [note(b * 0.5, 0.4, 43 + (b % 4) * 5) for b in range(16)]
    return score_for(right, left)


def test_musicxml_declares_two_staves(tmp_path: Path) -> None:
    path = to_musicxml(two_hand_score(), tmp_path / "out.musicxml")
    text = path.read_text(encoding="utf-8")
    assert "<staves>2</staves>" in text


def test_every_note_carries_a_staff_number(tmp_path: Path) -> None:
    """Including the padding rests: a rest also needs its staff."""
    path = to_musicxml(two_hand_score(), tmp_path / "out.musicxml")
    text = path.read_text(encoding="utf-8")
    elements = text.count("<note>")
    assert elements > 0
    assert text.count("<staff>") == elements


def test_both_staves_get_padding_rests_in_every_measure(tmp_path: Path) -> None:
    """Two rests per measure, so neither staff can end the bar early."""
    text = to_musicxml(score_for([note(0.0, 0.4, 76)], []), tmp_path / "sparse.musicxml").read_text(
        encoding="utf-8"
    )
    assert text.count("<rest/>") == text.count("<measure ") * 2


def test_both_staff_numbers_are_used(tmp_path: Path) -> None:
    path = to_musicxml(two_hand_score(), tmp_path / "out.musicxml")
    text = path.read_text(encoding="utf-8")
    assert text.count("<staff>1</staff>") > 0
    assert text.count("<staff>2</staff>") > 0


#: One element of a measure, in document order. MusicXML has no per-note position:
#: a note sits wherever the tick cursor is, so the elements that move that cursor
#: are part of the timing and cannot be skipped when replaying a measure.
_MEASURE_ELEMENTS = re.compile(
    r"<note>.*?</note>|<forward>.*?</forward>|<backup>.*?</backup>", re.DOTALL
)


def replay_measure(measure_body: str) -> tuple[int, dict[int, int]]:
    """Walk a measure's tick stream the way a renderer does.

    Returns the measure's total length in ticks and, per staff, the tick its first
    note landed on. A `<chord/>` member does not advance the cursor - it shares the
    position of the note it chords with - which is why the position is recorded only
    on the first element of each group.
    """
    cursor = 0
    first_tick: dict[int, int] = {}
    for element in _MEASURE_ELEMENTS.findall(measure_body):
        duration = int(re.search(r"<duration>(\d+)</duration>", element).group(1))
        if element.startswith("<forward>"):
            cursor += duration
            continue
        if element.startswith("<backup>"):
            cursor -= duration
            continue
        staff = int(re.search(r"<staff>(\d)</staff>", element).group(1))
        if "<chord/>" in element:
            continue
        first_tick.setdefault(staff, cursor)
        cursor += duration
    return cursor, first_tick


def measure_bodies(text: str) -> list[str]:
    return re.findall(r'<measure number="\d+">(.*?)</measure>', text, re.DOTALL)


def test_every_measure_spans_exactly_one_bar(tmp_path: Path) -> None:
    """No measure may be longer or shorter than its own time signature.

    This is the property the old writer violated on every single measure of the real
    fixture - 17 of 17 overflowed, the worst at 40 ticks against a 16-tick bar - and
    nothing caught it because the tests only counted elements. A measure whose ticks do
    not add up to the bar is invalid MusicXML, and it also silently detaches the
    engraved positions from the score's own ``(bar, beat)``, which is what the
    follow-along highlight is keyed on.
    """
    text = to_musicxml(two_hand_score(), tmp_path / "out.musicxml").read_text(encoding="utf-8")
    for index, body in enumerate(measure_bodies(text), start=1):
        total, _first = replay_measure(body)
        assert total == 16, f"measure {index} spans {total} ticks, expected 16"


def test_the_second_staff_starts_at_the_barline(tmp_path: Path) -> None:
    """The left hand begins on beat 1, not where the right hand finished.

    A piano measure is two voices that both start at the barline. Without a
    ``<backup>`` the second staff simply continued the first one's tick cursor, which
    put the left hand at tick 24 of a 16-tick bar on the real fixture - engraved one
    and a half bars late, and unmatchable by any highlight keyed on ``(bar, beat)``.
    """
    text = to_musicxml(two_hand_score(), tmp_path / "out.musicxml").read_text(encoding="utf-8")
    for index, body in enumerate(measure_bodies(text), start=1):
        _total, first = replay_measure(body)
        for staff in (1, 2):
            if staff in first:
                assert first[staff] == 0, (
                    f"measure {index} staff {staff} starts at tick {first[staff]}"
                )


def test_a_note_lands_on_the_beat_it_was_written_on(tmp_path: Path) -> None:
    """A gap inside a bar becomes a ``<forward>``, so the note after it keeps its beat.

    Without ``<forward>`` the note is engraved wherever the previous note's duration
    left the cursor. This is what collapsed interior rests and pulled the rest of the
    bar left, and it is invisible in the element counts the other tests looked at.

    The score is spelled out here rather than arranged, because this is a property of
    the writer: the arranger is free to fill bars, and when it does there is no gap
    left to notate.
    """
    score = PianoScore(
        measures=(
            Measure(
                index=0,
                right=(
                    ScoredNote(bar=0, beat=1.75, duration_beats=0.5, midi=57),
                    ScoredNote(bar=0, beat=2.5, duration_beats=0.5, midi=73),
                ),
                left=(ScoredNote(bar=0, beat=3.0, duration_beats=1.0, midi=40),),
            ),
        ),
        tempo_bpm=120.0,
        beats_per_bar=4,
    )
    text = to_musicxml(score, tmp_path / "gaps.musicxml").read_text(encoding="utf-8")
    body = measure_bodies(text)[0]
    assert "<forward>" in body, "an interior gap produced no <forward>"

    # Each note's engraved tick must be its own beat times the divisions, not the sum
    # of the durations before it.
    for staff, expected in ((1, (1.75, 2.5)), (2, (3.0,))):
        assert note_ticks(body, staff=staff) == [round(b * score.divisions) for b in expected]


def note_ticks(measure_body: str, *, staff: int) -> list[int]:
    """The tick every non-chord note of one staff landed on, in order."""
    cursor = 0
    ticks: list[int] = []
    for element in _MEASURE_ELEMENTS.findall(measure_body):
        duration = int(re.search(r"<duration>(\d+)</duration>", element).group(1))
        if element.startswith("<forward>"):
            cursor += duration
            continue
        if element.startswith("<backup>"):
            cursor -= duration
            continue
        if "<chord/>" in element:
            continue
        element_staff = int(re.search(r"<staff>(\d)</staff>", element).group(1))
        if element_staff == staff and "<rest/>" not in element:
            ticks.append(cursor)
        cursor += duration
    return ticks


def test_overlapping_notes_in_one_staff_keep_both_onsets(tmp_path: Path) -> None:
    """A note still ringing when the next one arrives must not cost a note-head.

    One staff is one voice, and a voice has one note sounding at a time. Quantisation
    can still produce that - a note whose length runs past its successor's onset - and
    the writer used to resolve it by dropping whatever would not fit: nine notes across
    the real fixture, six of its seventeen measures. A dropped onset stays in
    ``note_timeline``, so the keyboard lit a key that had no note-head on the page, which
    is the same "the highlight does not match the note" failure as before, only quieter.

    Shortening the earlier note keeps every onset, which is what the player's fingers
    and the highlight both need. The cost is a slightly shorter sustain, which is a far
    smaller loss than a missing note.
    """
    score = PianoScore(
        measures=(
            Measure(
                index=0,
                right=(
                    # 1.0 beat long, but the next note lands on beat 2.0.
                    ScoredNote(bar=0, beat=1.0, duration_beats=1.0, midi=72),
                    ScoredNote(bar=0, beat=2.0, duration_beats=1.0, midi=74),
                    # Overruns the barline and must not swallow the following measure.
                    ScoredNote(bar=0, beat=3.5, duration_beats=2.0, midi=76),
                ),
                left=(),
            ),
            Measure(
                index=1,
                right=(ScoredNote(bar=1, beat=0.0, duration_beats=1.0, midi=79),),
                left=(),
            ),
        ),
        tempo_bpm=120.0,
        beats_per_bar=4,
    )
    text = to_musicxml(score, tmp_path / "overlap.musicxml").read_text(encoding="utf-8")
    bodies = measure_bodies(text)

    # Every onset in measure 1 survives, at its own beat.
    assert note_ticks(bodies[0], staff=1) == [4, 8, 14]
    # The note that overran stops at the barline rather than running into measure 2,
    # and measure 2 still holds its own note.
    assert note_ticks(bodies[1], staff=1) == [0]
    for index, body in enumerate(bodies, start=1):
        total, _first = replay_measure(body)
        assert total == 16, f"measure {index} spans {total} ticks, expected 16"


def test_musicxml_uses_treble_and_bass_clefs(tmp_path: Path) -> None:
    path = to_musicxml(two_hand_score(), tmp_path / "out.musicxml")
    text = path.read_text(encoding="utf-8")
    assert '<clef number="1">' in text
    assert '<clef number="2">' in text
    assert "<sign>G</sign>" in text
    assert "<sign>F</sign>" in text


def test_musicxml_parses_back_as_a_two_staff_piano_part(tmp_path: Path) -> None:
    """music21 reads one <part> with two staves back as two PartStaff objects."""
    path = to_musicxml(two_hand_score(), tmp_path / "out.musicxml")
    summary = musicxml_summary(path)
    assert summary["staves"] == 2
    assert summary["stavesDeclared"]
    assert summary["measures"] == 4
    # 32 notes plus two padding rests per measure.
    assert summary["staffTags"] == 32 + 8
    assert summary["notes"] == 32


def test_a_tied_note_is_written_with_a_tie(tmp_path: Path) -> None:
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    buckets[0] = ([note(0.2, 2.5, 79)], [])
    score = build_score(arrange(buckets, grid), grid)
    assert score.tied_count == 1
    path = to_musicxml(score, tmp_path / "tied.musicxml")
    text = path.read_text(encoding="utf-8")
    assert "<tie" in text
    assert "<tied" in text


def test_every_measure_spans_the_full_bar(tmp_path: Path) -> None:
    """A measure where one hand rests must still be four beats long."""
    score = score_for([note(0.0, 0.4, 76)], [])
    path = to_musicxml(score, tmp_path / "sparse.musicxml")
    assert musicxml_summary(path)["measures"] == 4
    assert musicxml_summary(path)["stavesDeclared"]


def test_musicxml_writes_the_key_signature(tmp_path: Path) -> None:
    score = score_for([note(0.0, 0.4, 76)], [note(0.0, 0.4, 48)], key_tonic=7, key_mode="major")
    path = to_musicxml(score, tmp_path / "key.musicxml")
    assert "<fifths>1</fifths>" in path.read_text(encoding="utf-8")


def test_musicxml_writes_the_tempo(tmp_path: Path) -> None:
    path = to_musicxml(two_hand_score(), tmp_path / "tempo.musicxml")
    text = path.read_text(encoding="utf-8")
    assert "<per-minute>120</per-minute>" in text
    assert '<sound tempo="120"/>' in text


def test_musicxml_creates_missing_directories(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "out.musicxml"
    assert to_musicxml(two_hand_score(), target).is_file()


def test_midi_has_a_meta_track_and_two_hands(tmp_path: Path) -> None:
    path = write_midi(two_hand_score(), tmp_path / "out.mid")
    midi = mido.MidiFile(str(path))
    names = [
        message.name for track in midi.tracks for message in track if message.type == "track_name"
    ]
    assert names == [META_TRACK_NAME, RIGHT_TRACK_NAME, LEFT_TRACK_NAME]


def test_midi_puts_the_melody_in_the_right_hand_track(tmp_path: Path) -> None:
    path = write_midi(two_hand_score(), tmp_path / "out.mid")
    midi = mido.MidiFile(str(path))
    right_pitches = {m.note for m in midi.tracks[1] if m.type == "note_on" and m.velocity > 0}
    left_pitches = {m.note for m in midi.tracks[2] if m.type == "note_on" and m.velocity > 0}
    assert min(right_pitches) >= 72
    assert max(left_pitches) < 60
    assert not (right_pitches & left_pitches)


def test_midi_tracks_carry_the_piano_program(tmp_path: Path) -> None:
    path = write_midi(two_hand_score(), tmp_path / "out.mid")
    midi = mido.MidiFile(str(path))
    for index in (1, 2):
        programs = [m.program for m in midi.tracks[index] if m.type == "program_change"]
        assert programs == [0]


def test_midi_summary_reports_the_tempo(tmp_path: Path) -> None:
    summary = midi_summary(write_midi(two_hand_score(), tmp_path / "out.mid"))
    assert summary["tempos"] == [120.0]
    assert summary["tracks"] == 3
    assert summary["noteOns"] == 32


def test_midi_note_offsets_are_monotonic(tmp_path: Path) -> None:
    """mido stores delta times; a negative one means the events were mis-sorted."""
    path = write_midi(two_hand_score(), tmp_path / "out.mid")
    for track in mido.MidiFile(str(path)).tracks:
        for message in track:
            assert message.time >= 0


def test_a_repeated_note_is_not_cut_short(tmp_path: Path) -> None:
    """note_off must precede note_on at the same tick, or the repeat is silent."""
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    buckets[0] = ([note(0.0, 0.5, 76), note(0.5, 0.5, 76)], [])
    path = write_midi(build_score(arrange(buckets, grid), grid), tmp_path / "repeat.mid")
    track = mido.MidiFile(str(path)).tracks[1]
    sounding = [
        (i, m.type)
        for i, m in enumerate(track)
        if m.type in ("note_on", "note_off") and (m.type == "note_off" or m.velocity > 0)
    ]
    types = [kind for _index, kind in sounding]
    assert types == ["note_on", "note_off", "note_on", "note_off"]


def test_a_tied_note_is_written_as_one_long_midi_note(tmp_path: Path) -> None:
    """MIDI has no ties; two halves would re-articulate the note and stutter."""
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    buckets[0] = ([note(0.2, 2.5, 79)], [])
    score = build_score(arrange(buckets, grid), grid)
    assert score.tied_count == 1
    path = write_midi(score, tmp_path / "tied.mid")
    track = mido.MidiFile(str(path)).tracks[1]
    ons = [m for m in track if m.type == "note_on" and m.velocity > 0]
    assert len(ons) == 1
    # The single note must span both halves: 5 beats at 480 ticks per beat.
    on_tick = track.index(ons[0])
    off_index = next(
        index
        for index in range(on_tick + 1, len(track))
        if track[index].type == "note_off" and track[index].note == 79
    )
    # mido stores delta times, so the sounding length is note_off.time.
    total = track[off_index].time
    assert total == pytest.approx(5 * 480)


def test_midi_writes_the_key_as_a_name(tmp_path: Path) -> None:
    """mido's key_signature takes a key name, not a fifths count."""
    score = score_for([note(0.0, 0.4, 76)], [note(0.0, 0.4, 48)], key_tonic=7, key_mode="major")
    assert score.key_fifths == 1
    meta = mido.MidiFile(str(write_midi(score, tmp_path / "key.mid"))).tracks[0]
    keys = [m.key for m in meta if m.type == "key_signature"]
    assert keys == ["G"]


def test_every_representable_key_writes(tmp_path: Path) -> None:
    """Regression: fifths=4 raised "invalid key 4" and aborted the whole write."""
    for fifths in range(-7, 8):
        assert key_name_for(fifths) is not None
    assert key_name_for(4) == "E"
    assert key_name_for(-3) == "Eb"
    assert key_name_for(8) is None


def test_an_unrepresentable_key_is_skipped_not_fatal(tmp_path: Path) -> None:
    """fifths outside -7..7 has no mido name; the file must still be written."""
    from dataclasses import replace

    score = replace(two_hand_score(), key_fifths=9)
    path = write_midi(score, tmp_path / "odd.mid")
    assert path.is_file()
    meta = mido.MidiFile(str(path)).tracks[0]
    assert not [m for m in meta if m.type == "key_signature"]


def test_midi_tempo_and_time_signature_are_written(tmp_path: Path) -> None:
    path = write_midi(two_hand_score(), tmp_path / "out.mid")
    meta = mido.MidiFile(str(path)).tracks[0]
    kinds = {m.type for m in meta}
    assert {"set_tempo", "time_signature"} <= kinds


def test_midi_creates_missing_directories(tmp_path: Path) -> None:
    assert write_midi(two_hand_score(), tmp_path / "a" / "b" / "out.mid").is_file()


def test_seconds_to_ticks_converts_wall_clock() -> None:
    assert seconds_to_ticks(0.5, 120.0) == 480
    assert seconds_to_ticks(1.0, 60.0) == 480


def test_beats_to_ticks_converts_notated_beats() -> None:
    assert beats_to_ticks(1.0) == 480
    assert beats_to_ticks(5.0) == 2400


def test_an_empty_score_still_writes(tmp_path: Path) -> None:
    grid = grid_at()
    empty = build_score(arrange({}, grid), grid)
    assert write_midi(empty, tmp_path / "empty.mid").is_file()
    assert midi_summary(tmp_path / "empty.mid")["noteOns"] == 0
    assert to_musicxml(empty, tmp_path / "empty.musicxml").is_file()
    summary = musicxml_summary(tmp_path / "empty.musicxml")
    assert summary["staves"] == 2
    assert summary["notes"] == 0


def test_a_title_with_xml_characters_is_escaped(tmp_path: Path) -> None:
    """A song title comes from a web page, so it can contain anything."""
    import xml.etree.ElementTree as ET
    from dataclasses import replace

    nasty = "A & B <script> \"x\" 'y'"
    score = replace(two_hand_score(), title=nasty)
    path = to_musicxml(score, tmp_path / "nasty.musicxml")
    root = ET.parse(path).getroot()
    title = root.find("./work/work-title")
    assert title is not None
    assert title.text == nasty


def test_simultaneous_notes_are_written_as_a_chord(tmp_path: Path) -> None:
    """Regression: the score rendered as a single-note line.

    MusicXML marks the second and later notes of a simultaneous group with
    <chord/>. Without it every renderer reads them as consecutive notes, so a
    three-note chord was drawn as three notes in a row - which looked like the
    score had no chords at all.
    """
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    # A2-C3-E3: a three-note chord spanning 7 semitones, which fits inside the
    # default L3 hand budget. A2-E3-A3 spans 12 and the span fitter correctly drops
    # the octave doubling, leaving two notes - so that fixture tested the fitter, not
    # the chord writer.
    buckets[0] = ([], [note(0.0, 0.5, 45), note(0.0, 0.5, 48), note(0.0, 0.5, 52)])
    text = to_musicxml(
        build_score(arrange(buckets, grid), grid), tmp_path / "chord.musicxml"
    ).read_text(encoding="utf-8")
    assert text.count("<chord/>") == 2, "a three-note chord needs two <chord/> markers"
    # The first member carries no marker.
    first_note = text[text.index("<note>") :]
    assert "<chord/>" not in first_note[: first_note.index("</note>")]


def test_a_chord_lasts_as_long_as_its_shortest_member(tmp_path: Path) -> None:
    """A chord cannot outlast its shortest member, or it overlaps the next one."""
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    buckets[0] = ([], [note(0.0, 1.0, 45), note(0.0, 0.5, 52)])
    text = to_musicxml(
        build_score(arrange(buckets, grid), grid), tmp_path / "mix.musicxml"
    ).read_text(encoding="utf-8")
    first = text[text.index("<note>") :]
    second = first[first.index("<note>") :]
    # Both members of the chord must carry the half-note duration.
    first_duration = re.search(r"<duration>(\d+)</duration>", first).group(1)
    second_duration = re.search(r"<duration>(\d+)</duration>", second).group(1)
    assert first_duration == second_duration, (first_duration, second_duration)


def test_a_two_handed_chord_is_marked_per_staff(tmp_path: Path) -> None:
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    buckets[0] = (
        [note(0.0, 0.5, 72), note(0.0, 0.5, 76)],
        [note(0.0, 0.5, 45), note(0.0, 0.5, 52)],
    )
    text = to_musicxml(
        build_score(arrange(buckets, grid), grid), tmp_path / "two.musicxml"
    ).read_text(encoding="utf-8")
    # Each staff contributes one marker, so two chords give two markers.
    assert text.count("<chord/>") == 2
    # Both hands must appear, on their own staves.
    assert "<staff>1</staff>" in text
    assert "<staff>2</staff>" in text


def test_sequential_notes_are_not_marked_as_chords(tmp_path: Path) -> None:
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    buckets[0] = ([], [note(0.0, 0.5, 45), note(0.5, 0.5, 52)])
    text = to_musicxml(
        build_score(arrange(buckets, grid), grid), tmp_path / "seq.musicxml"
    ).read_text(encoding="utf-8")
    assert "<chord/>" not in text


def test_grouping_tolerates_snapping_noise() -> None:
    """Beats that differ only by snapping noise are one chord, not two.

    Snapping rounds to four decimals, so two members of the same chord can end up
    a thousandth of a beat apart. An exact comparison would split them and the
    renderer would draw a roll instead of a chord.
    """
    from services.arrangement.piano_musicxml import _group_by_onset
    from services.arrangement.piano_score import ScoredNote

    notes = (
        ScoredNote(bar=0, beat=1.0, duration_beats=1.0, midi=45),
        ScoredNote(bar=0, beat=1.0002, duration_beats=1.0, midi=52),
        ScoredNote(bar=0, beat=2.0, duration_beats=1.0, midi=60),
    )
    groups = _group_by_onset(notes)
    assert [len(members) for _onset, members in groups] == [2, 1]


def test_consecutive_sixteenths_are_not_one_chord() -> None:
    """Regression: a sixteenth-note tolerance merged real runs into chords.

    Using a sixteenth note (0.25 beats) as the tolerance produced 74 chord
    markers where the score only contains 45 chords, because every pair of
    consecutive sixteenths was written as if struck together.
    """
    from services.arrangement.piano_musicxml import _group_by_onset
    from services.arrangement.piano_score import ScoredNote

    run = tuple(
        ScoredNote(bar=0, beat=i * 0.25, duration_beats=0.25, midi=48 + i) for i in range(8)
    )
    assert [len(members) for _onset, members in _group_by_onset(run)] == [1] * 8


def test_a_chord_next_to_a_run_still_groups_correctly() -> None:
    from services.arrangement.piano_musicxml import _group_by_onset
    from services.arrangement.piano_score import ScoredNote

    notes = (
        ScoredNote(bar=0, beat=0.0, duration_beats=0.5, midi=45),
        ScoredNote(bar=0, beat=0.0, duration_beats=0.5, midi=52),
        ScoredNote(bar=0, beat=0.25, duration_beats=0.25, midi=57),
        ScoredNote(bar=0, beat=0.5, duration_beats=0.5, midi=60),
    )
    assert [len(members) for _onset, members in _group_by_onset(notes)] == [2, 1, 1]


def test_musicxml_is_well_formed_xml(tmp_path: Path) -> None:
    """Cheap guard against a malformed document: one root element, closed."""
    import xml.etree.ElementTree as ET

    path = to_musicxml(two_hand_score(), tmp_path / "out.musicxml")
    tree = ET.parse(path)
    assert tree.getroot().tag == "score-partwise"
    assert re.search(r"<staves>2</staves>", path.read_text(encoding="utf-8"))


def test_writers_agree_except_on_tied_notes(tmp_path: Path) -> None:
    """MIDI merges tied halves into one note, MusicXML keeps both.

    So MIDI has *fewer* note_ons than notated notes, by exactly the number of
    ties. Anything else would mean a split note was lost or double counted.
    """
    score = score_for([note(0.0, 2.5, 79), note(3.0, 0.4, 81)], [note(0.0, 0.4, 45)])
    # The 2.5 s note is 5 beats from 0.4 beats in, so it spans bars 0 and 1.
    assert score.note_count == 4
    assert score.tied_count == 1
    summary = midi_summary(write_midi(score, tmp_path / "out.mid"))
    assert summary["noteOns"] == score.note_count - score.tied_count


def test_writers_agree_when_nothing_is_tied(tmp_path: Path) -> None:
    score = score_for([note(0.0, 0.4, 79), note(3.0, 0.4, 81)], [note(0.0, 0.4, 45)])
    assert score.tied_count == 0
    assert midi_summary(write_midi(score, tmp_path / "out.mid"))["noteOns"] == score.note_count
