"""Align the left hand to the right hand.

Why this exists
---------------
The right hand carries the transcribed melody, with irregular onsets; the left
hand carries a generated figure on a fixed beat grid. Nothing linking the two,
the left hand plays alone in the melody's gaps and its attacks land between
the right hand's - which is the arrangement heard as "左手单独弹很奇怪", and
why the melody is not reinforced at the moments that matter.

The rules are the user's, in arrangement terms: 一定要有对齐的左右手弹才能
保持连续.

1. **Snap** - a left-hand attack within a sixteenth of a right-hand attack
   moves onto it, so the hands strike together.
2. **Anchor** - at the melody's phrase boundaries (the last attack before a
   rest of at least :data:`PHRASE_GAP_BEATS`, and the first attack after it)
   the left hand must be present at that instant; where it is not, a small
   block (bass + chord tone) is added. That is the handoff: 右手结束那个音，
   左手开始弹.

Both rules are deterministic and touch only the generated left hand. The
faithful (transcribed) path never calls here: a performance as played is not
rewritten to align, it is kept.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence

from services.arrangement.grid import SUBDIVISION, BarGrid
from services.arrangement.patterns import (
    PlacedNote,
    Role,
    chord_intervals,
    place_bass,
    voice_chord,
)
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: A left-hand attack within this of a right-hand attack moves onto it. One
#: sixteenth: close enough to be the "same" attack after quantisation, far
#: enough to keep the figure's own rhythm when the two really do disagree.
SNAP_BEATS = 0.25
#: A melody rest this long (or longer) ends a phrase.
PHRASE_GAP_BEATS = 1.0
#: How long the handoff block sounds.
ANCHOR_DURATION_BEATS = 0.5


def _onset_beats(
    notes: Sequence[NoteEvent], grid: BarGrid, bar: int
) -> list[float]:
    """The note onsets of one bar, in beats within the bar.

    ``quantize`` reports the beat whole and the sixteenth offset separately;
    the figure's beats are fractions, so the offset is added back.
    """
    beats = set()
    for note in notes:
        b, beat, sixteenth, _error = grid.quantize(note.start)
        if b == bar:
            beats.add(round(beat + sixteenth / SUBDIVISION, 3))
    return sorted(beats)


def phrase_boundary_beats(
    buckets: Mapping[int, tuple[Sequence[NoteEvent], Sequence[NoteEvent]]],
    grid: BarGrid,
) -> dict[int, list[float]]:
    """Per bar, the beats where a melody phrase starts or ends.

    A boundary is an attack that abuts a rest of at least
    :data:`PHRASE_GAP_BEATS`: the last attack before the rest and the first
    attack after it. The piece's first attack is a boundary as well. Bars
    without a melody get no boundaries - there is nothing to hand off to or
    from.
    """
    onsets: list[tuple[int, float]] = []
    seen: set[tuple[int, float]] = set()
    for bar in sorted(buckets):
        for beat in _onset_beats(buckets[bar][0], grid, bar):
            key = (bar, beat)
            if key not in seen:
                seen.add(key)
                onsets.append(key)
    onsets.sort()

    def absolute(bar: int, beat: float) -> float:
        return bar * grid.beats_per_bar + beat

    boundaries: set[tuple[int, float]] = set()
    for index, (bar, beat) in enumerate(onsets):
        if index == 0:
            boundaries.add((bar, beat))
        else:
            gap = absolute(bar, beat) - absolute(*onsets[index - 1])
            if gap >= PHRASE_GAP_BEATS - 1e-9:
                boundaries.add((bar, beat))  # the phrase that starts
                boundaries.add(onsets[index - 1])  # the phrase that ends

    per_bar: dict[int, list[float]] = {}
    for bar, beat in boundaries:
        per_bar.setdefault(bar, []).append(beat)
    return {bar: sorted(beats) for bar, beats in per_bar.items()}


def _nearest(onsets: Sequence[float], beat: float) -> float | None:
    if not onsets:
        return None
    return min(onsets, key=lambda candidate: abs(candidate - beat))


def _handoff_block(
    beat: float, chord: ChordEvent, beats_per_bar: int
) -> list[PlacedNote]:
    """The small block an anchor needs: bass under a chord tone."""
    bass = place_bass(chord.root)
    tones = voice_chord(chord_intervals(chord.quality), bass)
    notes = [
        PlacedNote(
            beat,
            min(ANCHOR_DURATION_BEATS, max(beats_per_bar - beat, 0.25)),
            bass,
            Role.SKELETON,
        )
    ]
    if tones:
        notes.append(
            PlacedNote(
                beat,
                min(ANCHOR_DURATION_BEATS, max(beats_per_bar - beat, 0.25)),
                tones[0],
                Role.CHORD,
            )
        )
    return notes


def interlock(
    placed: Sequence[PlacedNote],
    right: Sequence[NoteEvent],
    grid: BarGrid,
    bar: int,
    chord: ChordEvent | None,
    boundary_beats: Sequence[float] = (),
) -> list[PlacedNote]:
    """Snap the figure to the melody's attacks and anchor its phrase ends.

    ``right`` is the transcribed right-hand bucket of this bar: its onsets are
    where the fingers actually move, before any melody processing.
    """
    rh_onsets = _onset_beats(right, grid, bar)
    if not rh_onsets:
        return list(placed)

    out: list[PlacedNote] = []
    snapped = 0
    for note in placed:
        target = _nearest(rh_onsets, note.beat)
        if target is not None and 1e-9 < abs(target - note.beat) <= SNAP_BEATS:
            beat = target
            duration = min(
                note.duration_beats, max(grid.beats_per_bar - beat, 0.25)
            )
            out.append(dataclasses.replace(note, beat=beat, duration_beats=duration))
            snapped += 1
        else:
            out.append(note)

    anchored = 0
    for boundary in boundary_beats:
        covered = any(
            abs(note.beat - boundary) <= SNAP_BEATS for note in out
        )
        if not covered and chord is not None:
            out.extend(_handoff_block(boundary, chord, grid.beats_per_bar))
            anchored += 1

    # Deduplicate: a snapped note and an anchor can land on the same attack.
    out.sort(key=lambda n: (n.beat, n.midi))
    seen: set[tuple[float, int]] = set()
    unique: list[PlacedNote] = []
    for note in out:
        key = (round(note.beat, 3), note.midi)
        if key in seen:
            continue
        seen.add(key)
        unique.append(note)

    if snapped or anchored:
        logger.info(
            "interlock bar %d: %d snapped, %d anchored", bar, snapped, anchored
        )
    return unique


__all__ = [
    "ANCHOR_DURATION_BEATS",
    "PHRASE_GAP_BEATS",
    "SNAP_BEATS",
    "interlock",
    "phrase_boundary_beats",
]
