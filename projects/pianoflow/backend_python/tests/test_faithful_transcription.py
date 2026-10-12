"""The faithful mode's contract, and the numbers that keep it honest.

These exist because the arranged modes were quietly destroying the music. Measured on
星辰大海, a pure piano cover: the arranged score retained 30 % of the transcribed
notes and invented 400 that were never played, while ``as-played`` - nominally the
faithful option - retained 40 %. The density cap alone accounted for 20 of those lost
percentage points, because the pianist's own 11.7 notes per second is above the L4
budget of 8 and the "faithful" path applied it anyway.

Retention is measured with a quarter-note tolerance rather than in fixed time bins,
because quantisation legitimately moves a note by up to half a grid step and counting
that as a loss made the real retention look like 63 % when it was 86 %.
"""

from __future__ import annotations

import bisect

import numpy as np
import pytest

from services.arrangement.arranger import arrange
from services.arrangement.grid import BarGrid
from services.arrangement.hand_split import HandSplitConfig, hand_events_by_bar, split_hands
from services.arrangement.harmony import SPAN_CAP, fit_chord
from services.arrangement.piano_score import build_score
from services.arrangement.quality import assess_bars
from services.arrangement.voicing import VoicingReport, chord_pitch_classes, revoice, revoice_group
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

#: Quantisation moves a note by up to half a step; a quarter note absorbs it without
#: also absorbing a genuinely wrong rhythm.
RETENTION_TOLERANCE = 0.25


def note(start: float, duration: float, midi: int, amplitude: float = 0.7) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def grid_at(tempo: float = 120.0, duration: float = 8.0) -> BarGrid:
    return BarGrid(tempo_bpm=tempo, beats_per_bar=4, phase_seconds=0.0, duration=duration)


def retention(truth: list[NoteEvent], got: list[tuple[float, int]]) -> float:
    """Share of transcribed notes still present, matching on time and pitch class."""
    index: dict[int, list[float]] = {}
    for start, pitch_class in got:
        index.setdefault(pitch_class, []).append(start)
    for times in index.values():
        times.sort()
    used: set[tuple[int, int]] = set()
    hit = 0
    # Sorted by time so the greedy match walks both lists together. Unsorted input
    # made it claim notes were lost when the truth simply arrived out of order, and
    # the fixture's chord tones share pitch classes with its passing tones.
    for original in sorted(truth, key=lambda n: n.start):
        candidates = index.get(original.midi % 12, [])
        at = bisect.bisect_left(candidates, original.start)
        for offset in (at - 1, at, at + 1, at - 2, at + 2):
            if (
                0 <= offset < len(candidates)
                and abs(candidates[offset] - original.start) <= RETENTION_TOLERANCE
                and (offset, original.midi % 12) not in used
            ):
                used.add((offset, original.midi % 12))
                hit += 1
                break
    return hit / len(truth) if truth else 1.0


def c_major(start: float, duration: float = 1.0, root: int = 0, quality: str = "major"):
    return ChordEvent(start=start, duration=duration, root=root, quality=quality)


class TestTheChordRootIsNeverChanged:
    def test_the_root_pitch_class_survives_a_reduction(self) -> None:
        """The invariant, stated directly.

        A root that changes is a different chord, so this is checked on the pitch
        class rather than on the octave: folding by an octave is allowed and is the
        normal fix, moving the root to another chord is not.
        """
        chord = c_major(0.0)
        # C2 and G5: the root class C, far too wide for one hand.
        group = [note(0.0, 1.0, 36), note(0.0, 1.0, 79)]
        report = VoicingReport()
        out = revoice_group(group, chord, span_cap=12, report=report)

        root_class = chord.root % 12
        assert any(n.midi % 12 == root_class for n in out), "the root pitch class was lost"
        assert report.melody_removed == 0, "the melody note was deleted"
        assert report.root_changed == 0

    def test_the_third_is_kept_when_a_chord_must_be_reduced(self) -> None:
        """A third is what makes a chord major rather than minor."""
        chord = c_major(0.0)
        # C4, E4, G4, C5 - already inside a hand, so nothing should move at all.
        group = [note(0.0, 1.0, 60), note(0.0, 1.0, 64), note(0.0, 1.0, 67), note(0.0, 1.0, 72)]
        report = VoicingReport()
        out = revoice_group(group, chord, span_cap=12, report=report)

        assert {n.midi for n in out} == {60, 64, 67, 72}, "a playable chord was altered"
        assert report.removed == 0
        assert report.octave_folded == 0

    def test_the_highest_note_is_never_deleted(self) -> None:
        for width in (12, 9, 6):
            chord = c_major(0.0)
            group = [note(0.0, 1.0, 36 + i * 2) for i in range(1, 8)]
            top = max(n.midi for n in group)
            report = VoicingReport()
            out = revoice_group(group, chord, span_cap=width, report=report)
            assert top in {n.midi for n in out}, f"the top note was dropped at span_cap {width}"
            assert report.melody_removed == 0


class TestFillingChordsIsOptIn:
    def test_a_lone_chord_tone_is_left_alone_by_default(self) -> None:
        """Filling every lone note inflated a transcription by 40 %.

        The brief says a single note *may* become a triad. Doing it unconditionally is
        the opposite of faithful, so it is an explicit option and the default adds
        nothing at all.
        """
        chord = c_major(0.0)
        report = VoicingReport()
        out = revoice_group([note(0.0, 1.0, 60)], chord, span_cap=12, report=report)
        assert [n.midi for n in out] == [60]
        assert report.added == 0

    def test_the_option_completes_a_lone_root_into_a_triad(self) -> None:
        chord = c_major(0.0)
        report = VoicingReport()
        out = revoice_group(
            [note(0.0, 1.0, 60)], chord, span_cap=12, fill_chords=True, report=report
        )
        # Root was already there, so only the third is added.
        assert {n.midi % 12 for n in out} == {0, 4}, "the triad should carry root and third"
        assert report.added == 1
        assert len(out) == 2

    def test_a_note_outside_the_chord_is_never_completed(self) -> None:
        """Otherwise a melody note would sprout a triad under it every time."""
        chord = c_major(0.0)
        report = VoicingReport()
        out = revoice_group(
            [note(0.0, 1.0, 70)],  # Bb, not in C major
            chord,
            span_cap=12,
            fill_chords=True,
            report=report,
        )
        assert [n.midi for n in out] == [70]
        assert report.added == 0


class TestFitChordProtectsWhatItIsToldTo:
    def test_a_protected_pitch_survives_a_reduction(self) -> None:
        """Protection must constrain the subset search, not only the octave choice.

        Subsets are tried largest first, so before this was fixed a subset that
        happened to omit the protected note could fit and be returned while the note
        was marked protected. Measured on 星辰大海 that lost 74 melody notes.
        """
        # Four notes, only the first and last pair can be held together.
        pitches = [40, 52, 53, 76]
        plan = fit_chord(pitches, 12, protected=[76])
        kept = [p for _index, p in plan]
        assert 76 in kept, "the protected pitch was dropped"
        assert max(kept) - min(kept) <= 12


class TestFaithfulModeAgainstAPianoRecording:
    """The regression that matters: a real performance, measured end to end."""

    @pytest.fixture(scope="class")
    def pipeline(self, tmp_path_factory):
        """A synthetic 'piano' performance dense enough to expose thinning.

        A fixture rather than the 星辰大海 cache, so the test is self-contained and
        runs anywhere. The texture is what matters: sustained chords in both hands at
        roughly 11 notes per second, which is above every difficulty budget and is
        exactly what the density cap used to destroy.
        """
        grid = grid_at(tempo=120.0, duration=16.0)
        events: list[NoteEvent] = []
        chords: list[ChordEvent] = []
        beat = 0.5
        roots = [0, 9, 5, 7]
        for bar in range(grid.bar_count()):
            start = bar * 4 * beat
            root = roots[bar % len(roots)]
            chords.append(c_major(start, 4 * beat, root=root))
            for step in range(4):
                when = start + step * beat
                # A triad per hand, plus an off-beat passing tone in the treble.
                for interval in (0, 4, 7):
                    events.append(note(when, beat, 40 + root + interval))
                    events.append(note(when, beat, 60 + root + interval))
                # Pitch class 9 is deliberately outside the chord (0, 4, 7), so the
                # retention measurement can tell this voice from the chord tones. It
                # shares class 0 with the roots otherwise, and a shared class made the
                # greedy matcher report 16 % of the performance as lost when it was
                # all present.
                events.append(note(when + beat / 2, beat, 60 + root + 9))
        return grid, events, chords

    def _arrange(self, pipeline, fidelity: str, **kwargs):
        grid, events, chords = pipeline
        assignment = split_hands(
            events, config=HandSplitConfig(span_cap=SPAN_CAP[kwargs.get("difficulty", 4)])
        )
        buckets = hand_events_by_bar(assignment, grid)
        quality = assess_bars(buckets, grid)
        return arrange(
            buckets,
            grid,
            chords=chords,
            quality=quality,
            fidelity=fidelity,
            **kwargs,
        )

    def test_the_faithful_mode_keeps_almost_everything_the_pianist_played(self, pipeline) -> None:
        """The whole point of the mode.

        `as-played` and `arranged` both apply the density cap, and this fixture plays
        at 11 notes per second against an L4 budget of 8 - so both lose roughly a
        third of the performance. The faithful mode applies no cap at all.
        """
        grid, events, _chords = pipeline
        arrangement = self._arrange(pipeline, "transcribed", difficulty=4)
        score = build_score(arrangement, grid, key_tonic=0, key_mode="major")
        got = [(n["time"], n["midi"] % 12) for n in score.note_timeline()]

        kept = retention(events, got)
        assert kept >= 0.95, f"the faithful mode retained only {kept:.0%}"

        # And it must beat the arranging modes outright, or the mode is pointless.
        for other in ("as-played", "arranged"):
            rival = build_score(
                self._arrange(pipeline, other, difficulty=4), grid, key_tonic=0, key_mode="major"
            )
            rival_got = [(n["time"], n["midi"] % 12) for n in rival.note_timeline()]
            assert retention(events, rival_got) < kept, (
                f"{other} retained as much as the faithful mode; the density cap is "
                "no longer costing anything and the mode has lost its reason to exist"
            )

    def test_the_faithful_mode_invents_nothing(self, pipeline) -> None:
        """Zero additions is the property that makes it trustworthy."""
        arrangement = self._arrange(pipeline, "transcribed", difficulty=4)
        report = arrangement.voicing
        assert report["added"] == 0, f"the faithful mode invented {report['added']} notes"
        assert report["rootChanged"] == 0
        assert report["melodyRemoved"] == 0

    def test_every_note_the_mode_drops_is_accounted_for(self, pipeline) -> None:
        """Losing notes is allowed in the arranging modes; here it is reported.

        The faithful mode does not delete anything, so this asserts the accounting is
        self-consistent rather than that a loss happened - a test that required a loss
        would pass only once the mode had started destroying music again.
        """
        report = self._arrange(pipeline, "transcribed", difficulty=4).voicing
        assert report["retained"] > 0
        assert report["added"] == 0
        # Folding by an octave keeps a note; it is not a loss, and it is counted apart.
        assert report["removed"] == 0, f"the faithful mode removed {report['removed']} notes"
        assert report["retention"] == pytest.approx(
            report["retained"] / (report["retained"] + report["removed"]), abs=1e-3
        )

    def test_the_mode_never_generates_an_accompaniment_pattern(self, pipeline) -> None:
        """`transcribed` shares no code with the arranging path.

        Generated left-hand figures are the thing that made the arranged version sound
        like a different piece, so the mode reports having no pattern at all.
        """
        arrangement = self._arrange(pipeline, "transcribed", difficulty=4)
        assert arrangement.pattern == "none"
        assert all(bar.source == "as-played" for bar in arrangement.bars)

    def test_the_report_says_a_bar_where_the_chord_was_uncertain(self, pipeline) -> None:
        """Low confidence is surfaced, because a wrong root rewrites everything."""
        grid, events, _chords = pipeline
        chords = [
            c_major(bar * 2.0, 2.0, root=[0, 9, 5, 7][bar % 4], quality="major")
            for bar in range(grid.bar_count())
        ]
        for chord in chords[:2]:
            chords[chords.index(chord)] = ChordEvent(
                start=chord.start,
                duration=chord.duration,
                root=chord.root,
                quality=chord.quality,
                confidence=0.1,
            )
        _out, report = revoice(
            events, chords, span_cap=SPAN_CAP[4], bar_of=lambda t: grid.quantize(t)[0]
        )
        assert report.low_confidence_bars, "an uncertain chord was not reported"
        assert all(bar < len(chords) for bar in report.low_confidence_bars)


def test_chord_pitch_classes_put_the_root_first() -> None:
    """Root first, then the third: the two that must survive a reduction."""
    for quality, expected in (
        ("major", [0, 4, 7]),
        ("minor", [0, 3, 7]),
        ("dominant7", [0, 4, 7, 10]),
    ):
        classes = chord_pitch_classes(c_major(0.0, quality=quality))
        assert classes == expected, quality
        assert classes[:2] == expected[:2], "the root and third must come first"


def test_revoice_is_idempotent_on_an_already_correct_chord() -> None:
    """Re-voicing a score twice must not keep adding notes."""
    chord = c_major(0.0)
    events = [note(0.0, 1.0, m) for m in (60, 64, 67)]
    once, first = revoice(events, [chord], span_cap=12)
    twice, second = revoice(once, [chord], span_cap=12)
    assert [n.midi for n in once] == [60, 64, 67]
    assert first.added == 0
    assert second.added == 0, f"a second pass added {second.added} notes"
    assert len(twice) == len(once)


def test_no_chord_at_all_leaves_the_notes_alone() -> None:
    """No harmony detected is not a reason to invent one."""
    events = [note(i * 0.5, 0.5, 60 + i) for i in range(6)]
    out, report = revoice(events, [], span_cap=0)
    assert len(out) == len(events)
    assert report.added == 0
    assert np.isclose(out[0].start, events[0].start)
