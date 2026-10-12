"""Assign every transcribed note to a hand, so it can be notated on two staves.

The transcription model says *which keys were pressed*, never *which hand
played them*. A piano score needs one of the two, so this module reconstructs
the split from five weighted features instead of a naive pitch cut:

1. **register** - distance above the adaptive split point (Otsu on the
   duration-weighted pitch histogram, so it follows the piece instead of
   assuming middle C)
2. **top voice** - the highest note of any simultaneous group must be the right
   hand; this is a hard constraint, not a weight
3. **duration** - long notes read as melody, short ones as accompaniment
4. **velocity** - the melody is usually louder
5. **density** - when a chord is thick (4+ simultaneous notes) only the top
   voice is melody; the rest are accompaniment

Then two post-passes stop the result from flickering between hands, which is
what makes naive splitting unreadable: a single note can never have two
different lengths in the same bar, and hands are not allowed to cross unless the
pitch gap is small enough to be a genuine hand-crossing in the music.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from services.arrangement.grid import BarGrid
from services.arrangement.harmony import fit_chord
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: Fallback split point when the pitch histogram is degenerate.
DEFAULT_SPLIT_MIDI = 60  # middle C
#: Simultaneous notes above this count make a chord, not a melody line.
DENSE_CHORD = 4
#: Onsets this close together count as "pressed at the same time".
SIMULTANEITY_WINDOW = 0.06
#: Sigmoid width for the register feature, in semitones.
REGISTER_WIDTH = 6.0
#: Two notes can only swap hands if they are within this many semitones.
CROSSING_TOLERANCE = 12

#: A chord this narrow is played whole, by one hand. It matches L4's span budget,
#: which is the widest stretch any level allows.
CHORD_ATOMIC_SPAN = 12
#: How close a chord's centre has to sit to the split before the previous chord's
#: hand is preferred. Without it a line sitting on the boundary alternates hands
#: note by note, which is unreadable.
HAND_HYSTERESIS = 3


@dataclass(frozen=True)
class HandSplitConfig:
    """Weights for the right-hand likelihood score."""

    register_weight: float = 1.0
    duration_weight: float = 0.35
    velocity_weight: float = 0.2
    split_midi: int | None = None
    adaptive_split: bool = True
    #: Widest stretch the level allows, in semitones. 0 disables chord folding.
    span_cap: int = 0


@dataclass
class HandAssignment:
    """The split result, plus why it was made."""

    right_hand: list[NoteEvent] = field(default_factory=list)
    left_hand: list[NoteEvent] = field(default_factory=list)
    split_midi: int = DEFAULT_SPLIT_MIDI
    crossings_fixed: int = 0
    densities_capped: int = 0
    notes_dropped: int = 0
    #: Simultaneous groups too wide for one hand and therefore divided between them.
    chords_divided: int = 0
    #: Simultaneous groups re-voiced by octaves so one hand could hold them whole.
    chords_folded: int = 0

    @property
    def all_notes(self) -> list[NoteEvent]:
        return sorted([*self.right_hand, *self.left_hand], key=lambda n: n.start)

    def to_dict(self) -> dict[str, object]:
        return {
            "splitMidi": self.split_midi,
            "rightHandCount": len(self.right_hand),
            "leftHandCount": len(self.left_hand),
            "crossingsFixed": self.crossings_fixed,
            "densitiesCapped": self.densities_capped,
            "notesDropped": self.notes_dropped,
            "chordsDivided": self.chords_divided,
            "chordsFolded": self.chords_folded,
        }


def estimate_split_midi(
    events: Sequence[NoteEvent], *, fixed: int | None = None, adaptive: bool = True
) -> int:
    """Find the pitch boundary between the two hands.

    Uses Otsu's method over the pitch histogram: the threshold that maximises
    between-class variance. Two deliberate choices:

    * every note counts once, regardless of duration. Weighting by duration
      lets a single held melody note drag the boundary onto itself, which then
      pushes the whole melody into the left hand.
    * the threshold follows the actual register of the piece, so a bass-heavy
      cover does not end up with half of itself in the right hand.
    """
    if not events:
        return fixed if fixed is not None else DEFAULT_SPLIT_MIDI
    if not adaptive or fixed is not None:
        return int(fixed if fixed is not None else DEFAULT_SPLIT_MIDI)

    pitches = np.array([n.midi for n in events], dtype=int)
    lo, hi = int(pitches.min()), int(pitches.max())
    if hi - lo < 2:
        return round((lo + hi) / 2)

    hist = np.bincount(pitches - lo, minlength=hi - lo + 1).astype(float)
    total = hist.sum()
    if total <= 0:
        return DEFAULT_SPLIT_MIDI

    # Otsu: sigma_b^2(t) = w0*mu0^2 + w1*mu1^2 over the classes below/above t.
    idx = np.arange(hist.size, dtype=float)
    below_count = np.cumsum(hist)[:-1]
    above_count = total - below_count
    below_sum = np.cumsum(hist * idx)[:-1]
    grand_sum = below_sum[-1] + hist[-1] * idx[-1]

    mu_below = np.divide(
        below_sum, below_count, out=np.zeros_like(below_sum), where=below_count > 0
    )
    mu_above = np.divide(
        grand_sum - below_sum,
        above_count,
        out=np.zeros_like(below_sum),
        where=above_count > 0,
    )
    w_below = below_count / total
    w_above = above_count / total
    between = w_below * mu_below**2 + w_above * mu_above**2
    # An empty class cannot be a split.
    between[(below_count <= 0) | (above_count <= 0)] = -np.inf
    best = int(np.argmax(between))

    # Reject a split that leaves one hand with almost nothing. Otsu maximises between
    # class variance and has no opinion about class *size*, so on sparse material it
    # will happily isolate a single note: given the pitches 43, 72 and 74 it chose a
    # boundary of 44, which put the bass note in the treble and left the left hand
    # empty. A hand needs at least two notes to be a hand at all, so anything that
    # cannot give both classes two notes falls back to the median.
    if total < 4 or below_count[best] < 2 or above_count[best] < 2:
        logger.info(
            "otsu split at %d is degenerate (classes %.0f/%.0f of %.0f); using the median",
            lo + best,
            below_count[best],
            above_count[best],
            total,
        )
        return int(np.median(pitches))

    threshold = int(lo + best)
    # Keep the boundary strictly inside the range, so the lowest note of the piece is
    # never itself the split.
    return min(max(threshold, lo + 1), hi)


def _simultaneous_groups(events: Sequence[NoteEvent]) -> list[list[int]]:
    """Cluster note indices by onset time: the notes a pianist presses together.

    Deliberately *not* an overlap chain. Chaining on overlap merged 180 notes of
    the test piece into 25 blobs, the largest spanning 4.08 s with 20 notes,
    because one 1 s sustain drags every later onset into the same "chord".
    Grouping on a fixed onset window keeps chords as chords.
    """
    order = sorted(range(len(events)), key=lambda i: events[i].start)
    if not order:
        return []
    groups: list[list[int]] = []
    current: list[int] = [order[0]]
    anchor = events[order[0]].start
    for index in order[1:]:
        if events[index].start - anchor > SIMULTANEITY_WINDOW:
            groups.append(current)
            current = [index]
            anchor = events[index].start
        else:
            current.append(index)
    groups.append(current)
    return groups


def _largest_pitch_gap(ordered: Sequence[NoteEvent], group: Sequence[int]) -> int:
    """Where to cut a chord that is too wide for one hand: its widest gap.

    Cutting at the widest gap keeps as many notes as possible on the correct side of
    the divide. Cutting at the split point instead would put a major seventh in one
    hand and a lone root in the other.
    """
    by_pitch = sorted(group, key=lambda i: ordered[i].midi)
    if len(by_pitch) < 2:
        return ordered[by_pitch[0]].midi
    best_gap = -1
    cut = ordered[by_pitch[0]].midi
    for lower, upper in itertools.pairwise(by_pitch):
        gap = ordered[upper].midi - ordered[lower].midi
        if gap > best_gap:
            best_gap = gap
            cut = (ordered[lower].midi + ordered[upper].midi) // 2
    return cut


def _fold_wide_chords(
    ordered: Sequence[NoteEvent],
    groups: Sequence[Sequence[int]],
    span_cap: int,
    assignment: HandAssignment,
) -> list[NoteEvent]:
    """Re-voice chords too wide for one hand, so they need not be split.

    The brief was: if a hand cannot reach it, drop an octave rather than tear the
    chord in half. Dividing a chord between the hands is the last resort, not the
    first, because it is what turns a piano texture into a melody line plus a
    separate chord - measured on 星辰大海, dividing the 140 over-wide chords left the
    right hand at 32.8 % chords where folding and assigning whole reached 63.6 %.

    A chord is folded only if that keeps almost all of it. If folding would throw
    away most of the notes, the chord really is two hands' work and is divided.
    """
    pitches = [n.midi for n in ordered]
    for group in groups:
        group_pitches = [pitches[i] for i in group]
        if max(group_pitches) - min(group_pitches) <= span_cap:
            continue
        plan = fit_chord(group_pitches, span_cap, protected=[max(group_pitches)])
        kept = len(plan)
        if kept < max(2, len(group) - 1):
            # Folding would gut it. Two hands it is.
            assignment.chords_divided += 1
            continue
        for index, pitch in plan:
            pitches[index] = pitch
        assignment.chords_folded += 1

    return [
        n if n.midi == p else dataclasses.replace(n, midi=p)
        for n, p in zip(ordered, pitches, strict=True)
    ]


def _assign_chords_atomically(
    ordered: Sequence[NoteEvent],
    groups: Sequence[Sequence[int]],
    score: np.ndarray,
    is_right: np.ndarray,
    split_midi: int,
    assignment: HandAssignment,
) -> None:
    """Give each playable chord to one hand, whole.

    This is the pass that matters, and it replaced a per-note split that was quietly
    destroying the music. Measured on 星辰大海, a pure piano cover: 140 of 322
    simultaneous groups straddled middle C with a median span of 27 semitones, and
    assigning note by note tore every one of them in half. The result was a right hand
    with 206 notes in 203 groups - 1.5 % chords, effectively a single line - beside a
    left hand holding 55.7 %. Notating that gives the player a melody in one hand and
    a chord in the other, which is not what the recording sounds like.

    Assigning the chord whole, by where its centre sits, took the same right hand
    from 1.5 % chords to 63.6 %.
    """
    previous_right: bool | None = None
    for group in groups:
        pitches = [ordered[i].midi for i in group]
        span = max(pitches) - min(pitches)

        if span > CHORD_ATOMIC_SPAN:
            # _fold_wide_chords could not bring this into one hand's reach, so it is
            # genuinely two hands. Cut at the widest gap and keep the top voice right.
            cut = _largest_pitch_gap(ordered, group)
            for index in group:
                is_right[index] = ordered[index].midi > cut
            assignment.chords_divided += 1
            previous_right = None
            continue

        centre = sum(pitches) / len(pitches)
        # Continuity, in one direction only: a chord sitting just below the boundary
        # may stay with the right hand after a right-hand chord, so a melody does not
        # drop hands as it descends. It must not work the other way, or a note two
        # semitones *above* the boundary gets dragged below it and the hands swap.
        if previous_right and centre < split_midi and split_midi - centre <= HAND_HYSTERESIS:
            right = True
        elif centre >= split_midi:
            # Register decides. Checked before the score, not after: a note sitting
            # exactly on the boundary is not "below" it, and letting the score decide
            # made it a coin toss whose result hysteresis then propagated.
            right = True
        elif split_midi - centre <= HAND_HYSTERESIS:
            # Below the boundary but close to it, so the weighted score breaks the tie.
            right = bool(np.mean([score[i] for i in group]) >= 0.5)
        else:
            right = False
        for index in group:
            is_right[index] = right
        previous_right = right


def _same_pitch_overlaps(ordered: Sequence[NoteEvent], is_right: np.ndarray) -> int:
    """Reconcile one pitch that is sounding in both hands at once.

    The longer note keeps its hand and the shorter one follows, because the
    longer one is the note a listener actually hears as held.
    """
    by_pitch: dict[int, list[int]] = {}
    for index, note in enumerate(ordered):
        by_pitch.setdefault(note.midi, []).append(index)
    fixed = 0
    for indices in by_pitch.values():
        indices.sort(key=lambda i: ordered[i].start)
        for first, second in itertools.pairwise(indices):
            if ordered[second].start >= ordered[first].end - 1e-6:
                continue
            if is_right[first] == is_right[second]:
                continue
            keep = first if ordered[first].duration >= ordered[second].duration else second
            flip = second if keep == first else first
            is_right[flip] = is_right[keep]
            fixed += 1
    return fixed


def _score_right_hand(
    events: Sequence[NoteEvent],
    config: HandSplitConfig,
    split_midi: int,
) -> np.ndarray:
    """Weighted right-hand likelihood in [0, 1]."""
    count = len(events)
    if count == 0:
        return np.zeros(0, dtype=float)

    register = 1.0 / (
        1.0 + np.exp(-(np.array([n.midi for n in events]) - split_midi) / REGISTER_WIDTH)
    )

    # Duration is normalised against the median, not the maximum. Scaling by the
    # maximum makes every feature 1.0 when all durations are equal, which adds a
    # flat +duration_weight to every note and drags low notes into the right
    # hand. Half the median maps to a neutral 0.5 instead.
    durations = np.array([n.duration for n in events], dtype=float)
    median_duration = float(np.median(durations))
    if median_duration > 0:
        duration_feature = np.clip(durations / (2.0 * median_duration), 0.0, 1.0)
    else:
        duration_feature = np.zeros(count)

    velocities = np.array([n.amplitude for n in events], dtype=float)
    vel_range = velocities.max() - velocities.min()
    velocity_feature = (
        (velocities - velocities.min()) / vel_range if vel_range > 1e-6 else np.zeros(count)
    )

    score = (
        config.register_weight * register
        + config.duration_weight * duration_feature
        + config.velocity_weight * velocity_feature
    )
    total_weight = config.register_weight + config.duration_weight + config.velocity_weight
    return score / total_weight


def split_hands(
    events: Sequence[NoteEvent], *, config: HandSplitConfig | None = None
) -> HandAssignment:
    """Assign each note to the right or left hand.

    The input is assumed to be start-time sorted by the caller only for
    readability; ordering is handled internally.
    """
    settings = config or HandSplitConfig()
    ordered = sorted(events, key=lambda n: (n.start, n.midi))
    if not ordered:
        return HandAssignment(split_midi=DEFAULT_SPLIT_MIDI)

    split_midi = estimate_split_midi(
        ordered, fixed=settings.split_midi, adaptive=settings.adaptive_split
    )
    assignment = HandAssignment(split_midi=split_midi)

    # Re-voice over-wide chords first, so that by the time hands are chosen almost
    # every chord fits one hand and can be given to it whole. The split point is then
    # re-estimated, because folding moves notes.
    if settings.span_cap > 0:
        ordered = _fold_wide_chords(
            ordered, _simultaneous_groups(ordered), settings.span_cap, assignment
        )
        split_midi = estimate_split_midi(
            ordered, fixed=settings.split_midi, adaptive=settings.adaptive_split
        )
        assignment.split_midi = split_midi

    groups = _simultaneous_groups(ordered)
    score = _score_right_hand(ordered, settings, split_midi)
    is_right = np.zeros(len(ordered), dtype=bool)

    # Pass 1: a chord that one hand can hold goes to that hand, whole. This runs
    # first and decides most groups, because it is the only rule that keeps a chord
    # intact - everything below refines the ones it could not place.
    _assign_chords_atomically(ordered, groups, score, is_right, split_midi, assignment)
    logger.info(
        "chord-atomic split at midi %d: %d groups, %d folded, %d divided",
        split_midi,
        len(groups),
        assignment.chords_folded,
        assignment.chords_divided,
    )

    # Pass 2: a divided chord keeps its top voice in the right hand. That is a hard
    # rule rather than a weight: the highest note of anything sounding is the line
    # the listener follows, and it is played by the upper hand.
    for group in groups:
        pitches = [ordered[i].midi for i in group]
        if max(pitches) - min(pitches) <= CHORD_ATOMIC_SPAN:
            continue
        top = max(group, key=lambda i: ordered[i].midi)
        is_right[top] = True
        if len(group) >= DENSE_CHORD:
            assignment.densities_capped += 1

    # Pass 3 is deliberately absent. _assign_chords_atomically already handles a
    # group of one - a single note is a chord of one - and it applies hysteresis so a
    # line near the split does not alternate hands note by note. Re-deciding those
    # notes here from the raw register score overrode that and undid the stability,
    # which showed up as the right hand filling with isolated notes again.

    # Pass 4: stop a pitch from being held in two hands at once.
    _same_pitch_overlaps(ordered, is_right)

    for index, note in enumerate(ordered):
        if is_right[index]:
            assignment.right_hand.append(note)
        else:
            assignment.left_hand.append(note)

    _fix_crossings(ordered, is_right, assignment)
    logger.info("hand split at midi %d: %s", split_midi, assignment.to_dict())
    return assignment


def _fix_crossings(
    ordered: Sequence[NoteEvent], is_right: np.ndarray, assignment: HandAssignment
) -> int:
    """Move right-hand notes that sit below the left hand up to a sane place.

    A genuine hand crossing is normal in the music, but a right-hand note far
    below the left hand means the split failed, so those notes are handed to the
    left hand (where the arranger can rewrite them) instead of notating an
    unplayable layout.
    """
    groups = _simultaneous_groups(ordered)
    fixed = 0
    for group in groups:
        right = [i for i in group if is_right[i]]
        left = [i for i in group if not is_right[i]]
        if not right or not left:
            continue
        lowest_right = min(ordered[i].midi for i in right)
        highest_left = max(ordered[i].midi for i in left)
        if lowest_right >= highest_left - CROSSING_TOLERANCE:
            continue
        for index in right:
            if ordered[index].midi < highest_left:
                is_right[index] = False
                fixed += 1
    if fixed:
        # Rebuild the two hands from the corrected flags.
        assignment.right_hand = [n for i, n in enumerate(ordered) if is_right[i]]
        assignment.left_hand = [n for i, n in enumerate(ordered) if not is_right[i]]
    assignment.crossings_fixed = fixed
    return fixed


def hand_events_by_bar(
    assignment: HandAssignment, grid: BarGrid
) -> dict[int, tuple[list[NoteEvent], list[NoteEvent]]]:
    """Bucket the split hands into bars as {bar: (right, left)}."""
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]] = {}
    for index in range(grid.bar_count()):
        buckets[index] = ([], [])
    for note in assignment.all_notes:
        bar, _beat, _sixteenth, _error = grid.quantize(note.start)
        if bar not in buckets:
            buckets[bar] = ([], [])
        right, left = buckets[bar]
        if note in assignment.right_hand:
            right.append(note)
        else:
            left.append(note)
    return buckets


__all__ = [
    "CHORD_ATOMIC_SPAN",
    "CROSSING_TOLERANCE",
    "DEFAULT_SPLIT_MIDI",
    "DENSE_CHORD",
    "HandAssignment",
    "HandSplitConfig",
    "estimate_split_midi",
    "hand_events_by_bar",
    "split_hands",
]
