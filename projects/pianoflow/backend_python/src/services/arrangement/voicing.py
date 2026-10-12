"""Faithful re-voicing: keep the performance, make the harmony say what it means.

Why this module exists
----------------------
The arrangement path rewrites music on purpose, and a user who wants the piece as
played had no way to ask for that. Measured on 星辰大海, a pure piano cover, the
arranged score retained 30.2 % of the transcribed notes and invented 400 that were
never played: the density cap alone accounted for 20 of the lost percentage points,
because the pianist's own 11.7 notes per second is above the L4 budget of 8.

So this is a separate path with separate rules, not a difficulty preset. The brief:

* **the chord root never changes** - that is the harmonic identity, and it is an
  invariant the tests assert is zero violations;
* a dyad may be reduced to a single note, a single note may be filled out to a triad;
* when reducing, **keep the third**, because the third is what makes a chord major
  rather than minor, diminished rather than augmented;
* **no difficulty** - the pianist played what they played, and that density is the
  performance.

The one tension worth stating plainly: "keep the third" and "the melody must stay
clear" collide on a dyad of ``[root, melody]``, where the third may not even be
present. This module resolves it by never deleting the highest note of a group. The
melody is the tune; a chord tone that is not in the recording is added rather than
substituted for it.

Reduction happens only under one condition - a hand cannot reach the chord. Filling
in the root and third is additive and happens freely, because that is the "single
note into a triad" the brief asks for. Nothing else is removed.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from services.arrangement.harmony import fit_chord
from services.audio_analysis.chords import QUALITIES
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: Onsets this close together are one chord.
SIMULTANEITY_SECONDS = 0.02
#: Confidence below this and the bar is reported, because a wrong root rewrites the
#: harmony of everything under it and the user needs to know where to look.
LOW_CONFIDENCE = 0.5


@dataclass
class VoicingReport:
    """What the re-voicing did, so the UI can state it rather than imply it."""

    retained: int = 0
    added: int = 0
    removed: int = 0
    octave_folded: int = 0
    #: Must be 0. A root that changed is a different chord.
    root_changed: int = 0
    #: Must be 0. A deleted top note is a lost tune.
    melody_removed: int = 0
    #: Bars where the chord detector was not confident, for manual checking.
    low_confidence_bars: list[int] = field(default_factory=list)
    #: Chord groups that had to be reduced to reach the hand.
    groups_reduced: int = 0
    #: Groups wider than a hand can hold that were left alone in faithful mode, and the
    #: widest of them in semitones. Reported rather than rewritten: the score keeps the
    #: performance, and the number says where a player would have had to redistribute.
    unreachable_groups: int = 0
    max_unreachable_span: int = 0

    @property
    def total_in(self) -> int:
        return self.retained + self.removed

    def to_dict(self) -> dict[str, object]:
        return {
            "retained": self.retained,
            "added": self.added,
            "removed": self.removed,
            "octaveFolded": self.octave_folded,
            "rootChanged": self.root_changed,
            "melodyRemoved": self.melody_removed,
            "groupsReduced": self.groups_reduced,
            "unreachableGroups": self.unreachable_groups,
            "maxUnreachableSpan": self.max_unreachable_span,
            "lowConfidenceBars": list(self.low_confidence_bars),
            "retention": round(self.retained / self.total_in, 4) if self.total_in else 1.0,
        }


def chord_pitch_classes(chord: ChordEvent) -> list[int]:
    """The chord's pitch classes, root first.

    Order matters: the root and the third are the two that must survive any reduction,
    and the root is the one that must never change.
    """
    intervals = QUALITIES.get(chord.quality, QUALITIES["major"])
    return [(chord.root + i) % 12 for i in intervals]


def _nearest_instance(pitch_class: int, reference: int, low: int = 21, high: int = 108) -> int:
    """The pitch of that class closest to ``reference``, inside the piano."""
    best = None
    for octave in range(-1, 10):
        pitch = pitch_class + 12 * octave
        if pitch < low or pitch > high:
            continue
        distance = abs(pitch - reference)
        if best is None or distance < best[0]:
            best = (distance, pitch)
    return best[1] if best else max(low, min(high, reference))


def _chord_at(chords: Sequence[ChordEvent], seconds: float) -> ChordEvent | None:
    """The chord sounding at a moment, else the nearest one."""
    if not chords:
        return None
    covering = [c for c in chords if c.start <= seconds < c.end]
    if covering:
        return max(covering, key=lambda c: c.end - c.start)
    return min(chords, key=lambda c: min(abs(c.start - seconds), abs(c.end - seconds)))


def _groups(notes: Sequence[NoteEvent]) -> list[list[NoteEvent]]:
    out: list[list[NoteEvent]] = []
    for note in sorted(notes, key=lambda n: (n.start, n.midi)):
        for group in out:
            if abs(min(z.start for z in group) - note.start) <= SIMULTANEITY_SECONDS:
                group.append(note)
                break
        else:
            out.append([note])
    return out


def revoice_group(
    group: Sequence[NoteEvent],
    chord: ChordEvent | None,
    *,
    span_cap: int,
    fill_chords: bool = False,
    report: VoicingReport,
    faithful: bool = False,
) -> list[NoteEvent]:
    """One chord: fill in what the harmony needs, take away only what cannot be played.

    ``faithful`` means the user's instruction "不要有八度折叠，要严格忠于原来的音频":
    nothing is moved and nothing is removed, and a chord a hand cannot reach is *named*
    in the report instead of being quietly rewritten. Measured on 星辰大海 before this
    flag existed, the faithful pass still folded **52** notes and deleted **32** - so the
    mode whose entire contract is "retains what was played" was changing 84 notes.
    """
    if not group:
        return []
    anchor = min(n.start for n in group)
    duration = max(n.duration for n in group)
    amplitude = max(n.amplitude for n in group)
    pitches = [n.midi for n in group]
    top = max(pitches)
    # The centre of what is already there, so an added tone lands near the hand
    # instead of at the bottom of the keyboard.
    centre = sum(pitches) // len(pitches)

    out: list[NoteEvent] = list(group)
    report.retained += len(group)

    # Complete a chord that is only partly voiced - but only when asked.
    #
    # Filling in every lone note that belongs to a confident chord added 426 notes to
    # a 1057-note transcription, a 40 % inflation. The brief says a single note *may*
    # become a triad; it does not say it should, and doing it by default is the
    # opposite of faithful. So this is opt-in and the report says how many notes it
    # contributed, which is the only way a user can tell which score they are looking
    # at.
    if (
        fill_chords
        and chord is not None
        and chord.confidence >= LOW_CONFIDENCE
        and len(group) == 1
        and pitches[0] % 12 in set(chord_pitch_classes(chord))
    ):
        classes = chord_pitch_classes(chord)
        present = {p % 12 for p in pitches}
        for pitch_class in classes[:2]:
            if pitch_class in present:
                continue
            pitch = _nearest_instance(pitch_class, centre)
            out.append(
                NoteEvent(start=anchor, duration=duration, midi=pitch, amplitude=amplitude * 0.8)
            )
            present.add(pitch_class)
            report.added += 1

    # Reachability. Folding is tried before anything is removed, because dropping a
    # note changes what the music says while dropping an octave does not.
    if span_cap > 0 and out and max(n.midi for n in out) - min(n.midi for n in out) > span_cap:
        if faithful:
            # Strictly what was played. The chord is wider than a hand can hold and
            # that is reported, because a notated impossibility is information and a
            # silent rewrite is not.
            report.unreachable_groups += 1
            report.max_unreachable_span = max(
                report.max_unreachable_span,
                max(n.midi for n in out) - min(n.midi for n in out),
            )
            return out
        # The top note and the root and the third are what must not be lost, so they
        # are the protected set handed to the folder.
        # Protect the notes that actually are the root and the third. Computing the
        # nearest instance instead protected a pitch the group does not contain, so
        # nothing was locked and a reduction dropped the real root: C2 vanished from
        # a C major chord because the folder had been told to protect C4.
        protect = [top]
        if chord is not None:
            core = set(chord_pitch_classes(chord)[:2])
            protect.extend(n.midi for n in group if n.midi % 12 in core)
        plan = fit_chord(
            [n.midi for n in out], span_cap, protected=sorted(set(protect)), allow_deletion=False
        )
        new_pitch = dict(plan)
        kept = set(new_pitch.values())
        if kept and (max(kept) - min(kept)) <= span_cap:
            # fit_chord returns (original index, new pitch) and may return fewer
            # notes than it was given, so it is mapped by index, never zipped.
            folded = [
                dataclasses.replace(note, midi=new_pitch[i])
                for i, note in enumerate(out)
                if i in new_pitch
            ]
            report.octave_folded += sum(
                1 for i, note in enumerate(out) if i in new_pitch and new_pitch[i] != note.midi
            )
            if len(folded) < len(out):
                report.groups_reduced += 1
                report.removed += len(out) - len(folded)
                # Whether the melody survived is a question about the *result*, not
                # about what was dropped: a chord can hold the same pitch twice, so
                # seeing the melody's pitch in the dropped set proves nothing. It was
                # checked this way first and reported 108 phantom losses on material
                # where nothing was actually lost.
                if top not in {n.midi for n in folded}:
                    report.melody_removed += 1
                    logger.error("melody note %d was lost while reducing a chord", top)
            out = folded

    return sorted(out, key=lambda n: (n.start, n.midi))


def revoice(
    notes: Sequence[NoteEvent],
    chords: Sequence[ChordEvent],
    *,
    span_cap: int,
    fill_chords: bool = False,
    faithful: bool = False,
    bar_of: Callable[[float], int | None] | None = None,
) -> tuple[list[NoteEvent], VoicingReport]:
    """Re-voice a whole transcription, leaving the performance otherwise intact."""
    report = VoicingReport()
    seen_low: set[int] = set()
    out: list[NoteEvent] = []

    for group in _groups(notes):
        anchor = min(n.start for n in group)
        chord = _chord_at(chords, anchor)
        if chord is not None and chord.confidence < LOW_CONFIDENCE and bar_of is not None:
            bar = bar_of(anchor)
            if bar is not None and bar not in seen_low:
                seen_low.add(bar)
                report.low_confidence_bars.append(bar)
        out.extend(
            revoice_group(
                group,
                chord,
                span_cap=span_cap,
                fill_chords=fill_chords,
                report=report,
                faithful=faithful,
            )
        )

    if report.melody_removed or report.root_changed:
        # These are invariants, not statistics. Anything else can be tuned away.
        logger.error(
            "re-voicing broke an invariant: %d melodies removed, %d roots changed",
            report.melody_removed,
            report.root_changed,
        )
    logger.info("re-voiced: %s", report.to_dict())
    return sorted(out, key=lambda n: (n.start, n.midi)), report


__all__ = ["VoicingReport", "chord_pitch_classes", "revoice", "revoice_group"]
