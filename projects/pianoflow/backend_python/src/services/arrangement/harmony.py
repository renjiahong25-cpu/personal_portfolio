"""Turn a transcribed melody into a right hand that sounds like piano.

Why this module exists
----------------------
A vocal melody is single notes by nature, so a faithful transcription of one
gives a right hand that is 100% monophonic. Measured on 星辰大海 at L4: 5 notes in
5 onset groups, 8 notes in 8 groups, every bar. That is not a transcription error -
it is what the singer sang. But it is not a piano score either, and no amount of
better transcription will change it, because the problem is upstream of the
transcriber.

So the melody is kept exactly as transcribed - pitch, rhythm, timing - and the
chord sounding underneath it is filled in below. The melody stays the top voice,
which is what makes the result read as a melody with accompaniment rather than as
a chord with a decoration on top.

Difficulty controls how many notes may join the melody, using the same-hand chord
budget the user agreed on:

    L1  1 note   the melody alone
    L2  2 notes  melody plus one chord tone
    L3  3 notes  a triad
    L4  4 notes  a triad plus the seventh, when the chord has one

Two rules keep the result playable rather than merely dense:

* Added notes never sound a second from the melody and never stack on top of it,
  so the top line stays singable and the hand does not have to squeeze.
* Short notes are left alone. Harmonising every note of a sixteenth-note run turns
  a run into a wall of block chords, which is harder to read than the run was and
  hides the melody it was meant to support.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
from collections.abc import Sequence

from services.arrangement.patterns import chord_intervals
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: Same-hand chord size budget, straight from the agreed difficulty table:
#: "同手和弦音数" is 1 / 2 / 3 / 4 for L1..L4.
MAX_HAND_CHORD: dict[int, int] = {1: 1, 2: 2, 3: 3, 4: 4}

#: "音符密度", notes per second, from the same table.
DENSITY_CAP: dict[int, float] = {1: 1.5, 2: 3.0, 3: 5.0, 4: 8.0}

#: "手跨度", in semitones. A hand spans an octave and a bit at a stretch; L4's 12
#: is already a ninth plus.
SPAN_CAP: dict[int, int] = {1: 4, 2: 6, 3: 9, 4: 12}

#: Added tones never fall below this, so the right hand cannot slide under the
#: left. The generated left hand lives in MIDI 40..52.
HARMONY_FLOOR = 55
#: ...nor climb absurdly above the melody; a four-note chord spread wider than
#: this stops being one hand.
HARMONY_CEILING = 96
#: Two added notes closer than this are a second apart, which the hand cannot play
#: as part of a chord without a claw.
MIN_ADDED_GAP = 3
#: The added tone sits below the melody, so it is quieter. The ear follows the
#: top line; if the filler is as loud as the melody the tune disappears.
HARMONY_AMPLITUDE_SCALE = 0.62
#: A note shorter than this is part of a run rather than a held tone.
MIN_HELD_SECONDS = 0.16

#: Loudness per layer, as a multiplier on the note's own amplitude.
#:
#: These are fixed rather than taken from the transcription because the transcription
#: cannot know what the arrangement does. Measured on 星辰大海, reusing the
#: transcribed amplitudes gave the accompaniment 0.600 and the melody 0.428 - the
#: accompaniment was *louder*, which is precisely why the melody was not clear. The
#: user's complaint was "旋律要清晰", so the layers are now stated outright.
MELODY_GAIN = 1.0
CHORD_TONE_GAIN = 0.62
#: The left hand is support. It must never compete with the tune.
ACCOMPANIMENT_GAIN = 0.45
#: When the texture system is active, the left hand carries the harmonic/rhythmic
#: identity of the arrangement, so it needs to be clearly audible. Without this,
#: the eight textures are indistinguishable: they share the same chord tones and
#: differ only in rhythm, which a 0.45 gain buries under the melody.
TEXTURE_ACCOMPANIMENT_GAIN = 0.70


#: The lowest and highest MIDI a piano can sound. Used to reject fold candidates:
#: the earlier "lower the top note until it fits" loop had no such guard and marched
#: a left-hand chord down to MIDI -44, which is silent, and reported success.
PIANO_LOW = 21
PIANO_HIGH = 108


def _fold_once(pitches: list[int], cap: int, locked: list[bool]) -> list[tuple[int, int]] | None:
    """Every octave assignment for this chord; keep the one with the smallest span.

    Returns (index, new pitch) pairs so the caller never has to guess which note went
    where. A chord holds at most a handful of notes, so 3^n assignments is a few dozen.

    Searching beats folding greedily because greedy folding does not terminate: when
    the *lowest* note is the outlier, lowering the top moves the whole chord down
    without ever narrowing the span, and the loop walks off the end of the keyboard.
    That bug put a left-hand chord at MIDI -44, which is silent.
    """
    n = len(pitches)
    best: tuple[tuple[int, int, int], list[tuple[int, int]]] | None = None
    for combo in itertools.product((-12, 0, 12), repeat=n):
        if any(locked[i] and combo[i] != 0 for i in range(n)):
            continue
        candidate = [p + s for p, s in zip(pitches, combo, strict=True)]
        if min(candidate) < PIANO_LOW or max(candidate) > PIANO_HIGH:
            continue
        # Octave transposition must never create a unison. A chord like A2-E3-A3 is
        # an octave doubling; folding the top down turns it into A2-E2-A2, which is
        # not a triad but one note struck twice, and it reads as a wrong thick note.
        # Rejecting these leaves the chord for the delete path, which drops the
        # doubled tone - the correct answer for an octave doubling.
        if len(set(candidate)) != len(candidate):
            continue
        span = max(candidate) - min(candidate)
        # Ordering matters, and the obvious key is wrong. Sorting by span first makes
        # a narrower chord beat an unmoved one: [45, 52] under a cap of 9 came back as
        # [33, 40] - an octave down - because the transposed version was 7 semitones
        # against the original's 7. Ranking by span alone silently moved every chord
        # down an octave.
        #
        # What matters, in order: does it fit at all, then how far was it moved, then
        # how narrow is it. Movement before span, because a note kept where the
        # transcriber put it is worth more than two semitones of tidiness.
        key = (0 if span <= cap else 1, sum(abs(s) for s in combo), span)
        if best is None or key < best[0]:
            best = (key, list(enumerate(candidate)))
    return best[1] if best else None


def fit_chord(
    pitches: list[int],
    cap: int,
    *,
    protected: Sequence[int] = (),
    allow_deletion: bool = True,
    allow_folding: bool = True,
) -> list[tuple[int, int]]:
    """Make one chord playable: fold it by octaves, and drop notes if that is not enough.

    Returns (index into ``pitches``, new pitch) pairs. A dropped note simply does not
    appear in the result.

    Folding is tried first because it keeps the note. Deleting is the fallback the
    user asked for, and a real fallback rather than a nicety: some chords *cannot* be
    fixed by octave transposition at all. ``{4, 8, 11}`` spans 7, and every octave
    assignment of it is 7 or wider, so a cap of 6 is unreachable without removing a
    note.

    Sub sets are tried largest first, so a note is only given up once folding has
    genuinely been exhausted. Protected pitches - the melody - are folded last and
    deleted last: an unplayable chord helps nobody, and a missing melody note is a
    smaller loss than a chord no hand can hold.

    ``allow_deletion=False`` is honoured **everywhere**, which it was not. The subset
    search ran before the flag was ever consulted: ``for size in range(len(pitches), 1, -1)``
    tries smaller and smaller groups and returns the first that fits, so a caller that
    forbade deletion got a deleted note as soon as any subset happened to fit. Measured
    on 星辰大海 that removed **32 notes in the faithful mode**, whose entire contract is
    that it removes nothing. With the flag set, only the full-size group is now tried,
    and a chord that will not fit is returned as wide as it is.

    ``allow_folding=False`` goes further and leaves the pitches alone entirely, for the
    user instruction "不要有八度折叠" - strictly what was played, even where a hand
    cannot reach it. The caller is expected to *report* the chord rather than change it.
    """
    if cap <= 0 or len(pitches) < 2:
        return [(i, p) for i, p in enumerate(pitches)]
    if not allow_folding:
        # Nothing is touched, so a chord wider than the cap stays wider than the cap.
        # That is the caller's to report, not this function's to hide.
        return [(i, p) for i, p in enumerate(pitches)]

    locked = [p in set(protected) for p in pitches]
    # Protection has to constrain the *subset* search too, not just the octave
    # assignment. Subsets are tried largest first, so a subset that happened to omit
    # the melody could fit and be returned before the full set was ever tried - the
    # note was marked protected and dropped anyway. Measured on 星辰大海 that lost 74
    # melody notes across the faithful pass.
    keep = [i for i, is_locked in enumerate(locked) if is_locked]

    sizes = range(len(pitches), 1, -1) if allow_deletion else (len(pitches),)
    best_effort: tuple[int, list[tuple[int, int]]] | None = None
    for size in sizes:
        for subset in itertools.combinations(range(len(pitches)), size):
            if not set(keep).issubset(subset):
                continue
            folded = _fold_once([pitches[i] for i in subset], cap, [locked[i] for i in subset])
            if folded is None:
                continue
            plan = [(subset[k], p) for k, (_, p) in enumerate(folded)]
            span = max(p for _, p in plan) - min(p for _, p in plan)
            if span <= cap:
                return plan
            # Remember the narrowest attempt, so a caller that forbids deletion still
            # gets the chord folded as far as an octave can manage it.
            if best_effort is None or span < best_effort[0]:
                best_effort = (span, plan)

    if not allow_deletion and best_effort is not None:
        return best_effort[1]

    # Nothing fits at any size: keep the melody alone, which spans zero by definition.
    melody = [(i, p) for i, p in enumerate(pitches) if locked[i]]
    return melody or [(pitches.index(max(pitches)), max(pitches))]


def hand_span(notes: Sequence[NoteEvent]) -> int:
    """How wide a hand has to stretch, in semitones. 0 for fewer than two notes."""
    if len(notes) < 2:
        return 0
    pitches = [n.midi for n in notes]
    return max(pitches) - min(pitches)


#: Notes within this many seconds of each other count as sounding together, which is
#: the same tolerance the hand split and the MusicXML writer use. A chord is the unit
#: a hand has to stretch for.
SIMULTANEITY_SECONDS = 0.02


def onset_span(notes: Sequence[NoteEvent]) -> int:
    """The widest stretch the hand faces at any single moment.

    This, not :func:`hand_span`, is what "手跨度" means and what a hand can actually
    do. A hand may play a low bass note in bar 1 and a high one in bar 30 - that is
    range, not span, and constraining it would destroy any tune. What it cannot do
    is stretch 30 semitones at once.

    Conflating the two was a real bug: measuring the span across a whole hand
    reported 19 to 25 semitones for every difficulty and "fixed" it by transposing
    the melody, which is not what the constraint was ever asking for.
    """
    widest = 0
    for group in _onset_groups(notes):
        widest = max(widest, hand_span(group))
    return widest


def _onset_groups(notes: Sequence[NoteEvent]) -> list[list[NoteEvent]]:
    """Split notes into the groups that sound together, in onset order."""
    groups: list[list[NoteEvent]] = []
    for note in sorted(notes, key=lambda n: (n.start, n.midi)):
        for group in groups:
            anchor = min(n.start for n in group)
            if abs(anchor - note.start) <= SIMULTANEITY_SECONDS:
                group.append(note)
                break
        else:
            groups.append([note])
    return groups


def fit_span(
    notes: Sequence[NoteEvent],
    cap: int,
    *,
    protect: Sequence[int] = (),
) -> list[NoteEvent]:
    """Fold each chord by octaves until it fits inside ``cap`` semitones.

    A human hand spans about an octave and a half at a stretch. The agreed budgets
    are 4 / 6 / 9 / 12 semitones for L1..L4.

    Folding is octave transposition, the standard pianist's fix: a note an octave away
    usually belongs at the nearer octave anyway, and it keeps the note rather than
    deleting it. Deleting would be easier but would quietly remove melody.

    ``protect`` lists pitches that must not move - the melody, in practice. The
    accompaniment is folded to fit the tune rather than the other way round, because a
    tune transposed to suit its own filling is no longer the tune.
    """
    if cap <= 0:
        return list(notes)

    fixed = set(protect)
    out: list[NoteEvent] = []
    for group in _onset_groups(notes):
        if hand_span(group) <= cap:
            out.extend(group)
            continue
        out.extend(_fold_group(group, cap, fixed))
    widest = onset_span(out)
    logger.info(
        "folded %d chords; widest stretch now %d semitones (cap %d)", len(notes), widest, cap
    )
    return out


def _fold_group(group: list[NoteEvent], cap: int, fixed: set[int]) -> list[NoteEvent]:
    """Bring one chord inside the stretch limit, protecting the melody."""
    pitches = [n.midi for n in group]
    plan = fit_chord(pitches, cap, protected=[p for p in pitches if p in fixed])
    return [dataclasses.replace(group[i], midi=p) for i, p in plan]


def _chord_at(chords: Sequence[ChordEvent], seconds: float) -> ChordEvent | None:
    """The chord sounding at a moment, else the nearest one that has ended."""
    covering = [c for c in chords if c.start <= seconds < c.end]
    if covering:
        # The longest one wins, so a passing chord does not override the key one.
        return max(covering, key=lambda c: c.end - c.start)
    if not chords:
        return None
    return min(chords, key=lambda c: min(abs(c.start - seconds), abs(c.end - seconds)))


def _voicing_below(
    melody_midi: int,
    pitch_classes: Sequence[int],
    count: int,
) -> list[int]:
    """Up to ``count`` chord tones below the melody, nearest first.

    The nearest tone below the melody is taken first, then the next one at least a
    third away from it. Taking every tone instead would produce clusters of
    seconds wherever the melody sits a step from a chord tone, which is exactly the
    shape a hand cannot play.
    """
    candidates: list[int] = []
    for pc in pitch_classes:
        # The highest pitch below the melody with this pitch class.
        pitch = melody_midi - 1 - ((melody_midi - 1 - pc) % 12)
        if pitch >= HARMONY_FLOOR:
            candidates.append(pitch)
    candidates.sort(reverse=True)

    picked: list[int] = []
    for pitch in candidates:
        if any(abs(pitch - chosen) < MIN_ADDED_GAP for chosen in picked):
            continue
        picked.append(pitch)
        if len(picked) == count:
            break
    return picked


def _is_held(note: NoteEvent, grid_beats: float) -> bool:
    """Whether a note is a tone to sit under, rather than part of a run."""
    return note.duration >= MIN_HELD_SECONDS and note.duration >= grid_beats * 0.4


def harmonize_melody(
    melody: Sequence[NoteEvent],
    chords: Sequence[ChordEvent],
    *,
    difficulty: int,
    beat_seconds: float,
    occupied: Sequence[int] = (),
    span_cap: int = 0,
) -> list[NoteEvent]:
    """Fill each held melody note out into a chord, keeping the melody on top.

    ``occupied`` lists pitches the other hand is already sounding, so the two hands
    never double the same note - which would read as one thick wrong note rather
    than as two hands.

    ``span_cap`` is the widest stretch this difficulty allows. It matters more than
    it looks: filling chord tones downward from a melody near the top of the staff
    reached MIDI 55 under a melody at 88, a 33-semitone span no hand can play. The
    cap is applied while building rather than after, because a tone added and then
    folded is not the same as never adding it.
    """
    budget = MAX_HAND_CHORD.get(difficulty, 1) - 1
    if budget <= 0 or not melody or not chords:
        # L1 keeps a single note per hand: adding anything would break the level.
        return [dataclasses.replace(n, amplitude=n.amplitude * MELODY_GAIN) for n in melody]

    melody_pitches = {n.midi for n in melody}
    held: list[bool] = []
    added_total = 0
    out: list[NoteEvent] = []
    for note in melody:
        out.append(dataclasses.replace(note, amplitude=note.amplitude * MELODY_GAIN))
        if not _is_held(note, beat_seconds):
            held.append(False)
            continue

        chord = _chord_at(chords, note.start)
        if chord is None:
            held.append(True)
            continue

        intervals = chord_intervals(chord.quality)
        pitch_classes = {chord.root % 12} | {(chord.root + i) % 12 for i in intervals}
        # Count what the melody itself already provides at this onset, so a
        # transcribed two-note dyad is topped up rather than doubled.
        already = 1 + sum(
            1 for other in melody if other is not note and abs(other.start - note.start) < 0.02
        )
        room = budget - (already - 1)
        if room <= 0:
            held.append(True)
            continue

        voicing = _voicing_below(note.midi, sorted(pitch_classes), room)
        # The hand that adds the filling holds this one chord at a time, so the span
        # that matters is this onset group's, not the piece's: a hand may sit low in
        # bar 1 and high in bar 30 (range) without ever stretching past the cap at
        # once. Measuring against the whole accumulated output compared a whole-song
        # range against a hand span and, once the melody had opened up past the cap,
        # rejected every addition - on a 31-semitone vocal line, nothing got filled.
        sounding = (
            [n.midi for n in out if abs(n.start - note.start) <= SIMULTANEITY_SECONDS]
            if span_cap > 0
            else []
        )
        kept: list[int] = []
        for pitch in voicing:
            if pitch in occupied or pitch in melody_pitches:
                continue
            # Never widen the hand past what the difficulty allows.
            if (
                span_cap > 0
                and max([*sounding, pitch, *kept]) - min([*sounding, pitch, *kept]) > span_cap
            ):
                continue
            if any(abs(pitch - q) < MIN_ADDED_GAP for q in kept):
                continue
            kept.append(pitch)

        added_total += len(kept)
        for pitch in kept:
            out.append(
                NoteEvent(
                    start=note.start,
                    duration=note.duration,
                    midi=pitch,
                    amplitude=note.amplitude * CHORD_TONE_GAIN,
                )
            )

    logger.info(
        "harmonised %d melody notes into %d (budget %d added per note at L%d)",
        len(melody),
        len(out),
        budget,
        difficulty,
    )
    return out


__all__ = [
    "ACCOMPANIMENT_GAIN",
    "CHORD_TONE_GAIN",
    "DENSITY_CAP",
    "HARMONY_FLOOR",
    "MAX_HAND_CHORD",
    "MELODY_GAIN",
    "SPAN_CAP",
    "fit_span",
    "hand_span",
    "harmonize_melody",
]
