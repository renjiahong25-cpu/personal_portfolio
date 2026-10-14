"""The piano score: one model both writers render from.

Two staves per system, one part, because that is what a piano score *is*. The
MusicXML writer emits ``<staves>2</staves>`` with ``<staff>1</staff>`` /
``<staff>2</staff>`` on every note, and the MIDI writer emits two tracks, so
neither has to guess where a note belongs.

Divisions are 4 per quarter note. That is the smallest denominator that can
express a sixteenth without rounding, which the arrangement reaches at L4; a
tied sixteenth across a barline needs it.

The one thing a hand-rolled piano writer always gets wrong is a note that starts
near the end of a bar and rings into the next one. Notating it as a single note
in one staff produces an invalid measure - the duration overflows the bar - so
:func:`build_score` splits it at the barline and marks the continuation with a
tie.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any, Literal

from services.arrangement.arranger import Arrangement
from services.arrangement.grid import BarGrid
from services.arrangement.harmony import SPAN_CAP, fit_chord
from services.audio_analysis.models import midi_to_pitch
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: Duration resolution per quarter note. 4 = sixteenths are exact.
DIVISIONS = 4

Clef = Literal["treble", "bass"]

#: Pitch class -> position on the circle of fifths.
_FIFTHS_BY_PC = {5: -1, 0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: 7, 8: 8, 3: 9, 10: 10}
#: Below this pitch a staff reads better as bass clef.
BASS_CLEF_MAX = 59


@dataclass(frozen=True)
class ScoredNote:
    """A note placed on the notated grid, in beats from the start of its bar."""

    bar: int
    beat: float
    duration_beats: float
    midi: int
    amplitude: float = 1.0
    tied_to_next: bool = False
    #: Set on the first half of a split note.
    tie_start: bool = False
    #: First note of a legato run of changing pitch: the score draws a slur here.
    slur_start: bool = False
    #: Last note of the same run: the slur ends here.
    slur_stop: bool = False

    @property
    def end_beat(self) -> float:
        return self.beat + self.duration_beats

    @property
    def duration_ticks(self) -> int:
        return max(1, round(self.duration_beats * DIVISIONS))

    def to_dict(self) -> dict[str, float | int | bool]:
        return {
            "bar": self.bar,
            "beat": round(self.beat, 4),
            "durationBeats": round(self.duration_beats, 4),
            "midi": self.midi,
            "amplitude": round(self.amplitude, 4),
            "tiedToNext": self.tied_to_next,
        }


@dataclass(frozen=True)
class Measure:
    index: int
    right: tuple[ScoredNote, ...] = ()
    left: tuple[ScoredNote, ...] = ()

    @property
    def note_count(self) -> int:
        return len(self.right) + len(self.left)

    def to_dict(self) -> dict[str, object]:
        return {
            "bar": self.index,
            "rightHandCount": len(self.right),
            "leftHandCount": len(self.left),
        }


@dataclass(frozen=True)
class PianoScore:
    measures: tuple[Measure, ...]
    tempo_bpm: float
    beats_per_bar: int
    right_clef: Clef = "treble"
    left_clef: Clef = "bass"
    key_fifths: int = 0
    title: str = ""
    divisions: int = DIVISIONS
    #: Where bar 1 falls in the audio, in seconds.
    #:
    #: The grid finds this by sliding the beat grid until the most onset energy
    #: lands on the beat positions, and on real material it is not zero - measured
    #: 0.14 s to 1.27 s. Without it, every notated time is shifted by that much
    #: and the follow-along cursor leads or trails the audio from the first bar.
    phase_seconds: float = 0.0

    @property
    def note_count(self) -> int:
        return sum(m.note_count for m in self.measures)

    @property
    def bar_seconds(self) -> float:
        return self.beats_per_bar * 60.0 / self.tempo_bpm

    def note_timeline(self) -> list[dict[str, Any]]:
        """Every note as (time, duration, pitch, hand) for the playback cursor.

        This is what the follow-along mode needs: a piano key has to light up at
        the exact moment its note sounds, which is impossible from the per-measure
        *counts* the score view would otherwise have.

        Times are absolute seconds **in the audio**, including ``phase_seconds``,
        so the cursor tracks what is actually playing rather than a notated clock
        that starts at zero.
        """
        beat_seconds = 60.0 / self.tempo_bpm
        events: list[dict[str, Any]] = []
        for measure in self.measures:
            # phase_seconds places bar 1 in the audio; without it every note time
            # is wrong by the phase, which is up to a second on real material.
            bar_offset = self.phase_seconds + measure.index * self.beats_per_bar * beat_seconds
            for hand, notes in (("right", measure.right), ("left", measure.left)):
                for note in notes:
                    start = bar_offset + note.beat * beat_seconds
                    events.append(
                        {
                            "time": round(start, 4),
                            "duration": round(note.duration_beats * beat_seconds, 4),
                            "midi": note.midi,
                            "pitch": midi_to_pitch(note.midi),
                            "hand": hand,
                            "staff": 1 if hand == "right" else 2,
                            # Where the note sits on the staff, so the view can line a
                            # playing note up with its engraved note-head. The time
                            # alone is not enough for that: several notes share a time
                            # and only the bar and beat say which one is which.
                            "bar": note.bar + 1,
                            "beat": note.beat,
                            "amplitude": round(note.amplitude, 3),
                            # MusicXML cannot tie across a barline without two
                            # note-heads, so a note that overruns is split. Exposing
                            # that here lets a consumer count *key presses* rather than
                            # note-heads - a tied pair is one press, and the density
                            # budget is about presses.
                            "tiedToNext": note.tied_to_next,
                            "tieStart": note.tie_start,
                            "slurStart": note.slur_start,
                            "slurStop": note.slur_stop,
                        }
                    )
        events.sort(key=lambda e: (e["time"], e["staff"], e["midi"]))
        return events

    def pitch_range(self) -> tuple[int, int]:
        """Lowest and highest MIDI pitch anywhere in the score."""
        pitches = [n.midi for m in self.measures for staff in (m.right, m.left) for n in staff]
        if not pitches:
            return (48, 84)
        return (min(pitches), max(pitches))

    @property
    def tied_count(self) -> int:
        return sum(
            1 for m in self.measures for staff in (m.right, m.left) for n in staff if n.tied_to_next
        )

    @property
    def slur_count(self) -> int:
        """Number of legato runs, one per slur that starts."""
        return sum(
            1 for m in self.measures for staff in (m.right, m.left) for n in staff if n.slur_start
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "tempoBpm": round(self.tempo_bpm, 2),
            "beatsPerBar": self.beats_per_bar,
            "divisions": self.divisions,
            "rightClef": self.right_clef,
            "leftClef": self.left_clef,
            "keyFifths": self.key_fifths,
            "measureCount": len(self.measures),
            "noteCount": self.note_count,
            "tiedNoteCount": self.tied_count,
            "slurCount": self.slur_count,
            "phaseSeconds": round(self.phase_seconds, 4),
            "barSeconds": round(self.bar_seconds, 4),
            #: Every note with its exact time, so the UI can light the right piano
            #: key at the right moment instead of guessing from bar counts.
            "notes": self.note_timeline(),
            "pitchRange": list(self.pitch_range()),
            "measures": [m.to_dict() for m in self.measures],
        }


def fifths_for(tonic: int | None, mode: str | None) -> int:
    """Circle-of-fifths position for a key, respelled into +/-7.

    A minor key sits three semitones below its relative major, so the shift is
    applied and then wrapped: G# minor would otherwise demand 8 sharps.
    """
    if tonic is None:
        return 0
    base = _FIFTHS_BY_PC.get(tonic % 12)
    if base is None:
        return 0
    value = base - 3 if (mode or "").lower().startswith("min") else base
    while value > 7:
        value -= 12
    while value < -7:
        value += 12
    return value


def choose_clef(notes: list[ScoredNote], default: Clef) -> Clef:
    """Bass clef unless the part actually lives in the treble register."""
    if not notes:
        return default
    mean = sum(n.midi for n in notes) / len(notes)
    if mean <= BASS_CLEF_MAX:
        return "bass"
    return "treble"


def _quantize_within(
    note: NoteEvent, grid: BarGrid, beats_per_bar: int
) -> tuple[int, float, float]:
    """Return (bar, beat in bar, duration in beats)."""
    bar, beat, sixteenth, _error = grid.quantize(note.start)
    step = grid.sixteenth_seconds
    length_beats = max(note.duration / grid.beat_seconds, 1.0 / grid.beats_per_bar)
    # Snap the length to the grid too, so written durations stay on the grid.
    length_beats = max(round(length_beats * 4) / 4, 0.25)
    return bar, beat + sixteenth * step / grid.beat_seconds, length_beats


def _split_across_bars(note: ScoredNote, beats_per_bar: int) -> list[ScoredNote]:
    """Break a note that crosses a barline into tied halves."""
    remaining = note.duration_beats
    beat = note.beat
    bar = note.bar
    pieces: list[ScoredNote] = []
    first = True
    while remaining > 1e-9:
        available = beats_per_bar - beat
        if available <= 1e-9:
            bar += 1
            beat = 0.0
            available = beats_per_bar
        take = min(remaining, available)
        is_last = take >= remaining - 1e-9
        pieces.append(
            ScoredNote(
                bar=bar,
                beat=round(beat, 4),
                duration_beats=round(take, 4),
                midi=note.midi,
                amplitude=note.amplitude,
                tied_to_next=not is_last,
                tie_start=not first,
            )
        )
        beat = round(beat + take, 4)
        remaining -= take
        first = False
    return pieces


def _layout(notes: list[NoteEvent], grid: BarGrid, beats_per_bar: int) -> list[ScoredNote]:
    placed: list[ScoredNote] = []
    for note in notes:
        bar, beat, length = _quantize_within(note, grid, beats_per_bar)
        if bar < 0:
            continue
        placed.append(
            ScoredNote(
                bar=bar,
                beat=round(max(beat, 0.0), 4),
                duration_beats=length,
                midi=note.midi,
                amplitude=note.amplitude,
            )
        )
    laid_out: list[ScoredNote] = []
    for scored in placed:
        laid_out.extend(_split_across_bars(scored, beats_per_bar))
    return sorted(laid_out, key=lambda n: (n.bar, n.beat, n.midi))


#: Onsets this close, in beats, are the same instant. Mirrors the XML writer's
#: grouping tolerance, so a run the writer sees as one chord is one run here too.
_LEGATO_ONSET_TOLERANCE = 1e-3
#: A next onset this far past a note's written end still counts as "played
#: continuously". Quantisation keeps a truly contiguous pair exact, so this is a
#: rounding guard rather than a musical window - a real gap (even one sixteenth)
#: is a lift of the fingers and must not be tied or slurred.
_LEGATO_GAP_BEATS = 0.02


def _legato_marks(notes: list[ScoredNote], beats_per_bar: int) -> list[ScoredNote]:
    """Mark slurs on one hand's legato runs, per the notation standard.

    The standard, as the user stated it: 连着弹的要连在一起 - specifically, notes
    of *different* pitch that begin without a lift of the fingers are a legato
    line, and the score must draw the slur. A note held across a barline is
    already a tie from :func:`_split_across_bars`, and a same-pitch repeat is a
    re-strike by design (see the MIDI writer's "a repeated note is not cut
    short"): neither gets a slur here, and a repeat also *breaks* the legato
    run, because a slur means the fingers stay down.

    Only monophonic onsets take marks: a slur over a chord group would not say
    which voice it belongs to, so a chord (two or more notes at one onset)
    breaks the run.
    """
    if not notes:
        return notes
    ordered = sorted(notes, key=lambda n: (n.bar, n.beat, n.midi))

    # Group onsets the way the XML writer does, so "one attack" means the same
    # thing at both ends.
    groups: list[list[ScoredNote]] = []
    for note in ordered:
        if (
            groups
            and note.bar == groups[-1][0].bar
            and abs(note.beat - groups[-1][0].beat) <= _LEGATO_ONSET_TOLERANCE
        ):
            groups[-1].append(note)
        else:
            groups.append([note])

    def start_beat(note: ScoredNote) -> float:
        return note.bar * beats_per_bar + note.beat

    # Contiguous monophonic runs. A chord group closes the run without starting
    # a new one: the legato line simply stops at the chord.
    runs: list[list[ScoredNote]] = []
    current: list[ScoredNote] = []
    for group in groups:
        if len(group) != 1:
            if current:
                runs.append(current)
                current = []
            continue
        note = group[0]
        if current:
            previous = current[-1]
            gap = start_beat(note) - (start_beat(previous) + previous.duration_beats)
            if -_LEGATO_GAP_BEATS <= gap <= _LEGATO_GAP_BEATS:
                current.append(note)
                continue
            runs.append(current)
            current = []
        current.append(note)
    if current:
        runs.append(current)

    updates: dict[int, dict[str, bool]] = {}

    def set_fields(note: ScoredNote, **fields: bool) -> None:
        updates.setdefault(id(note), {}).update(fields)

    for run in runs:
        # A same-pitch adjacency is a repeat: the fingers leave the key, so it
        # ends one legato segment and starts the next.
        segment: list[ScoredNote] = []
        for note in run:
            if segment and segment[-1].midi == note.midi:
                _slur_segment(segment, set_fields)
                segment = [note]
            else:
                segment.append(note)
        _slur_segment(segment, set_fields)

    if not updates:
        return notes
    return [
        dataclasses.replace(note, **updates[id(note)]) if id(note) in updates else note
        for note in notes
    ]


def _slur_segment(segment: list[ScoredNote], set_fields) -> None:
    """One run of changing pitch, no repeats inside: the slur belongs to it."""
    if len(segment) < 2:
        return
    set_fields(segment[0], slur_start=True)
    set_fields(segment[-1], slur_stop=True)


def _fold_scored_span(
    notes: list[ScoredNote], cap: int, *, allow_deletion: bool = True, allow_folding: bool = True
) -> list[ScoredNote]:
    """Fold each notated chord into the stretch a hand can manage, or thin it.

    This has to happen *after* :func:`_layout`, not before. Quantisation snaps
    onsets onto the beat grid, so two notes 30 ms apart can land on the same beat
    and become a chord that never existed in the arrangement. On 星辰大海 that
    manufactured three impossible chords - a low B3 against an E5, 17 semitones
    apart - out of material that was individually fine. The arrangement could not
    have caught them, because at that point they were not chords yet.

    The notated score is what the player reads, so the notated score is where the
    limit belongs.

    Folding is tried first and deletion second; see :func:`fit_chord` for why some
    chords genuinely cannot be fixed by octave transposition alone. With
    ``allow_folding=False`` - the faithful mode - the notes are left exactly as they
    were played and a chord wider than a hand can hold stays wide. That is a
    notational impossibility the player can see and redistribute, which is strictly
    better than a note silently moved an octave.
    """
    if cap <= 0 or not allow_folding:
        return notes

    by_onset: dict[tuple[int, float], list[ScoredNote]] = {}
    for note in notes:
        by_onset.setdefault((note.bar, note.beat), []).append(note)

    for key, group in by_onset.items():
        pitches = [n.midi for n in group]
        if max(pitches) - min(pitches) <= cap:
            continue
        # The top voice of a chord is the melody far more often than not, so it is
        # the one protected from being deleted.
        melody = max(pitches)
        plan = fit_chord(pitches, cap, protected=[melody], allow_deletion=allow_deletion)
        by_onset[key] = [dataclasses.replace(group[i], midi=p) for i, p in plan]
        logger.info(
            "folded a notated chord %d -> %d semitones (%d -> %d notes)",
            max(pitches) - min(pitches),
            max(n.midi for n in by_onset[key]) - min(n.midi for n in by_onset[key]),
            len(group),
            len(by_onset[key]),
        )

    return [n for group in by_onset.values() for n in group]


def build_score(
    arrangement: Arrangement,
    grid: BarGrid,
    *,
    title: str = "",
    tempo_bpm: float | None = None,
    key_tonic: int | None = None,
    key_mode: str | None = None,
) -> PianoScore:
    """Render an arrangement as a two-staff piano score."""
    beats_per_bar = grid.beats_per_bar
    right_notes = [n for bar in arrangement.bars for n in bar.right]
    left_notes = [n for bar in arrangement.bars for n in bar.left]

    # Read the budget off the arrangement rather than taking a new argument, so the
    # notated limits cannot drift from the ones the arranger applied.
    cap = SPAN_CAP.get(arrangement.difficulty, 0)
    # The faithful mode never removes a note. Quantisation has already folded two
    # notes an eighth apart onto the same beat by this point, so this pass sees chords
    # the arranger never saw; on the test fixture that turned a treble passing tone
    # 24 semitones above its chord into a deleted note. A chord a hand cannot hold is
    # a notational problem there, and the report names it instead of hiding it.
    allow_deletion = arrangement.fidelity != "transcribed"
    # Strictly what was played: no octave folding either, per "不要有八度折叠". Before
    # this, the faithful pass folded 52 notes and deleted 32 through this path and the
    # re-voicer's - a mode that claims to retain what was played, changing 84 notes.
    allow_folding = arrangement.fidelity != "transcribed"
    right = _legato_marks(
        _fold_scored_span(
            _layout(right_notes, grid, beats_per_bar),
            cap,
            allow_deletion=allow_deletion,
            allow_folding=allow_folding,
        ),
        beats_per_bar,
    )
    left = _legato_marks(
        _fold_scored_span(
            _layout(left_notes, grid, beats_per_bar),
            cap,
            allow_deletion=allow_deletion,
            allow_folding=allow_folding,
        ),
        beats_per_bar,
    )

    by_bar: dict[int, tuple[list[ScoredNote], list[ScoredNote]]] = {
        index: ([], []) for index in range(len(arrangement.bars))
    }
    for note in right:
        by_bar.setdefault(note.bar, ([], []))[0].append(note)
    for note in left:
        by_bar.setdefault(note.bar, ([], []))[1].append(note)

    measures = tuple(
        Measure(
            index=index,
            right=tuple(sorted(by_bar.get(index, ([], []))[0], key=lambda n: (n.beat, n.midi))),
            left=tuple(sorted(by_bar.get(index, ([], []))[1], key=lambda n: (n.beat, n.midi))),
        )
        for index in sorted(by_bar)
    )

    score = PianoScore(
        measures=measures,
        tempo_bpm=tempo_bpm if tempo_bpm else grid.tempo_bpm,
        beats_per_bar=beats_per_bar,
        right_clef=choose_clef(right, "treble"),
        left_clef=choose_clef(left, "bass"),
        key_fifths=fifths_for(key_tonic, key_mode),
        title=title,
        phase_seconds=grid.phase_seconds,
    )
    logger.info(
        "score: %d measures, %d notes, %d tied, %s/%s, key %+d, phase %.3fs",
        len(measures),
        score.note_count,
        score.tied_count,
        score.right_clef,
        score.left_clef,
        score.key_fifths,
        score.phase_seconds,
    )
    return score


__all__ = [
    "BASS_CLEF_MAX",
    "DIVISIONS",
    "Clef",
    "Measure",
    "PianoScore",
    "ScoredNote",
    "build_score",
    "choose_clef",
    "fifths_for",
]
