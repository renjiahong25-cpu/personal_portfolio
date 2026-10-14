"""Tests for the tie/slur marks a contiguous line must carry in the score.

The user's rule, per the notation standard: 连着弹的要连在一起. Same pitch, no
lift of the fingers, is one held note written as tied heads; different pitch,
no lift, is a legato run written as a slur.
"""

from __future__ import annotations

from services.arrangement.piano_musicxml import _note_xml
from services.arrangement.piano_score import ScoredNote, _legato_marks


def note(bar: int, beat: float, dur: float, midi: int, **kw) -> ScoredNote:
    return ScoredNote(bar=bar, beat=beat, duration_beats=dur, midi=midi, **kw)


def by_beat(notes: list[ScoredNote]) -> dict[float, ScoredNote]:
    return {n.beat: n for n in notes}


class TestLegatoMarks:
    def test_a_same_pitch_repeat_is_not_slurred(self) -> None:
        # A same-pitch adjacency is a re-strike, not legato: the fingers leave
        # the key, so no slur - and no new tie (the repeat must stay audible).
        marked = _legato_marks(
            [note(0, 0.0, 1.0, 60), note(0, 1.0, 1.0, 60), note(0, 2.0, 2.0, 60)], 4
        )
        assert not any(n.slur_start or n.slur_stop for n in marked)
        assert not any(n.tied_to_next or n.tie_start for n in marked)

    def test_a_different_pitch_contiguous_run_is_slurred(self) -> None:
        marked = _legato_marks(
            [note(0, 0.0, 0.5, 60), note(0, 0.5, 0.5, 62), note(0, 1.0, 1.0, 64)], 4
        )
        run = by_beat(marked)
        assert run[0.0].slur_start and not run[0.0].slur_stop
        assert not (run[0.5].slur_start or run[0.5].slur_stop)
        assert run[1.0].slur_stop and not run[1.0].slur_start
        # Different pitches are not tied.
        assert not any(n.tied_to_next or n.tie_start for n in marked)

    def test_a_gap_breaks_the_run(self) -> None:
        marked = _legato_marks(
            [note(0, 0.0, 0.5, 60), note(0, 0.75, 0.5, 62)], 4
        )
        assert not any(n.tied_to_next or n.tie_start for n in marked)
        assert not any(n.slur_start or n.slur_stop for n in marked)

    def test_a_chord_breaks_the_run(self) -> None:
        # 60-62 is a slurred pair; the 64+67 chord stops the line; 69 stands alone.
        marked = _legato_marks(
            [
                note(0, 0.0, 1.0, 60),
                note(0, 1.0, 1.0, 62),
                note(0, 2.0, 1.0, 64),
                note(0, 2.0, 1.0, 67),
                note(0, 3.0, 1.0, 69),
            ],
            4,
        )
        first = [n for n in marked if n.beat == 0.0][0]
        second = [n for n in marked if n.beat == 1.0][0]
        chord = [n for n in marked if n.beat == 2.0]
        last = [n for n in marked if n.beat == 3.0][0]
        assert first.slur_start
        assert second.slur_stop
        assert not any(n.slur_start or n.slur_stop for n in chord)
        assert not last.slur_start and not last.slur_stop

    def test_a_run_continues_across_a_barline(self) -> None:
        marked = _legato_marks(
            [note(0, 3.0, 1.0, 60), note(1, 0.0, 1.0, 62)], 4
        )
        assert marked[0].slur_start
        assert marked[1].slur_stop

    def test_an_existing_barline_tie_is_not_remarked(self) -> None:
        a = note(0, 3.0, 1.0, 60, tied_to_next=True)
        b = note(1, 0.0, 2.0, 60, tie_start=True)
        marked = _legato_marks([a, b], 4)
        assert marked[0].tied_to_next and marked[1].tie_start
        # Same pitch only: the tie already says it all, no slur on top.
        assert not marked[0].slur_start and not marked[1].slur_stop

    def test_a_repeat_breaks_the_slur(self) -> None:
        # C C D: the C-C is a repeat that lifts the fingers, so the slur starts
        # after it and runs only over C-D.
        marked = _legato_marks(
            [
                note(0, 0.0, 0.5, 60),
                note(0, 0.5, 0.5, 60),
                note(0, 1.0, 1.0, 62),
            ],
            4,
        )
        run = by_beat(marked)
        assert not (run[0.0].slur_start or run[0.0].slur_stop)
        assert run[0.5].slur_start and not run[0.5].slur_stop
        assert run[1.0].slur_stop and not run[1.0].slur_start
        # No ties: repeats stay re-strikes.
        assert not any(n.tied_to_next or n.tie_start for n in marked)

    def test_isolated_notes_are_untouched(self) -> None:
        marked = _legato_marks([note(0, 0.0, 0.5, 60), note(0, 2.0, 0.5, 64)], 4)
        assert marked == [note(0, 0.0, 0.5, 60), note(0, 2.0, 0.5, 64)]


class TestMusicXmlSlurEmission:
    def test_a_slur_start_is_written(self) -> None:
        xml = _note_xml(note(0, 0.0, 0.5, 60, slur_start=True), 1, key_alters=set())
        assert '<slur number="1" type="start"/>' in xml

    def test_a_slur_stop_is_written(self) -> None:
        xml = _note_xml(note(0, 1.0, 1.0, 62, slur_stop=True), 1, key_alters=set())
        assert '<slur number="1" type="stop"/>' in xml

    def test_tie_and_slur_share_one_notations_block(self) -> None:
        xml = _note_xml(
            note(0, 0.0, 0.5, 60, tied_to_next=True, slur_start=True), 1, key_alters=set()
        )
        assert xml.count("<notations>") == 1
        assert "<tied type=" in xml
        assert "<slur " in xml

    def test_a_plain_note_has_no_notations(self) -> None:
        xml = _note_xml(note(0, 0.0, 0.5, 60), 1, key_alters=set())
        assert "<notations>" not in xml
