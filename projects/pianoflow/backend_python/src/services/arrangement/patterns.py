"""Left-hand accompaniment patterns: what the part looks like.

Difficulty is deliberately *not* handled inside the patterns. Each builder emits
its full figure and tags every note with a role, so ``difficulty`` can prune
without the patterns needing to know it exists:

``skeleton``  the bass note. Every difficulty keeps it.
``chord``     the harmony above the bass.
``color``     octave doubling that makes it sound finished.
``ornament``  small decorative notes; only the top difficulty keeps them.

Difficulty has two independent axes, both set by ``DIFFICULTY_BEATS`` and
``DIFFICULTY_OFFSETS``:

* which beats inside the bar get a hit (L1 plays only beats 1 and 3, i.e. half
  speed)
* which subdivisions inside a beat get a note (L1/L2 on the beat, L3 on eighths,
  L4 up to sixteenths)

Voicing keeps the bass inside E2..E3 and the upper voices within an octave of
it, because a generated left hand that leaps two octaves is worse than no left
hand at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from services.audio_analysis.chords import QUALITIES

logger = logging.getLogger(__name__)

#: Bass register the generated left hand stays inside: MIDI 40 (E2) to 52 (E3).
BASS_LOW = 40
BASS_HIGH = 52
#: Upper chord tones never rise more than this far above the bass.
UPPER_SPAN = 19


class Role(StrEnum):
    """How hard a note is to play, ordered easiest to hardest."""

    SKELETON = "skeleton"
    CHORD = "chord"
    COLOR = "color"
    ORNAMENT = "ornament"

    @property
    def rank(self) -> int:
        return _ROLE_ORDER[self]


_ROLE_ORDER = {Role.SKELETON: 0, Role.CHORD: 1, Role.COLOR: 2, Role.ORNAMENT: 3}

#: Beats inside the bar that receive a hit. L1 plays at half speed.
DIFFICULTY_BEATS: dict[int, tuple[float, ...]] = {
    1: (0.0, 2.0),
    2: (0.0, 1.0, 2.0, 3.0),
    3: (0.0, 1.0, 2.0, 3.0),
    4: (0.0, 1.0, 2.0, 3.0),
}

#: Sub-beat positions, in beats after the beat, that may carry a note.
DIFFICULTY_OFFSETS: dict[int, tuple[float, ...]] = {
    1: (0.0,),
    2: (0.0,),
    3: (0.0, 0.5),
    4: (0.0, 0.5, 1.0, 1.5),
}

#: Highest role rank a difficulty keeps.
DIFFICULTY_MAX_ROLE: dict[int, int] = {1: 0, 2: 1, 3: 2, 4: 3}


@dataclass(frozen=True)
class PlacedNote:
    """A note positioned in beats within a bar, already voiced to real MIDI."""

    beat: float
    duration_beats: float
    midi: int
    role: Role

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "beat": self.beat,
            "durationBeats": self.duration_beats,
            "midi": self.midi,
            "role": self.role.value,
        }


def chord_intervals(quality: str) -> tuple[int, ...]:
    """Semitone intervals for a chord quality, defaulting to a major triad."""
    return QUALITIES.get(quality, QUALITIES["major"])


def place_bass(root_pc: int, *, low: int = BASS_LOW, high: int = BASS_HIGH) -> int:
    """Put a root pitch class in the bass register, preferring the lower octave."""
    for octave in range(-2, 8):
        pitch = root_pc + 12 * octave
        if low <= pitch <= high:
            return pitch
    return min(max(root_pc, low), high)


def voice_chord(intervals: Sequence[int], bass: int, *, span: int = UPPER_SPAN) -> list[int]:
    """Place chord tones above the bass, never below it and never duplicated."""
    tones: list[int] = []
    for interval in intervals:
        pitch = bass + interval
        while pitch < bass:
            pitch += 12
        while pitch > bass + span:
            pitch -= 12
        if pitch > bass and pitch not in tones:
            tones.append(pitch)
    return tones


def pattern_root_only(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """One bass note per hit: the simplest thing a left hand can do."""
    leading = tones[-1] if tones else bass + 7
    return [
        PlacedNote(0.0, 1.0, bass, Role.SKELETON),
        PlacedNote(1.5, 0.5, leading, Role.ORNAMENT),
    ]


def pattern_root_octave(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """Bass plus its octave: thicker, still trivially playable."""
    leading = tones[-1] if tones else bass + 7
    return [
        PlacedNote(0.0, 1.0, bass, Role.SKELETON),
        PlacedNote(0.0, 1.0, bass + 12, Role.COLOR),
        PlacedNote(1.5, 0.5, leading, Role.ORNAMENT),
    ]


def pattern_block_chord(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """A block chord on every hit: the rhythm-chord style."""
    notes = [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    notes.extend(PlacedNote(0.0, 1.0, pitch, Role.CHORD) for pitch in tones)
    if tones:
        notes.append(PlacedNote(1.5, 0.5, tones[-1], Role.ORNAMENT))
    return notes


def pattern_broken_chord(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """Bass on the beat, the upper voice between the beats: the pop staple."""
    if not tones:
        return pattern_root_only(bass, tones, beats_per_bar)
    return [
        PlacedNote(0.0, 0.5, bass, Role.SKELETON),
        PlacedNote(0.5, 0.5, tones[0], Role.CHORD),
        PlacedNote(1.0, 0.5, tones[-1], Role.ORNAMENT),
    ]


def pattern_alberti_bass(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """Low-high-middle-high, the classical accompaniment figure.

    The third and fourth notes fall after the beat, so the last beat of the bar
    drops them; ``build_pattern`` clips anything past the barline.
    """
    middle = tones[0] if tones else bass + 7
    return [
        PlacedNote(0.0, 0.5, bass, Role.SKELETON),
        PlacedNote(0.5, 0.5, bass + 12, Role.COLOR),
        PlacedNote(1.0, 0.5, middle, Role.CHORD),
        PlacedNote(1.5, 0.5, bass + 12, Role.ORNAMENT),
    ]


#: Pattern registry. The key is what the API and the UI expose.
PATTERNS: dict[str, Callable[[int, Sequence[int], int], list[PlacedNote]]] = {
    "root_only": pattern_root_only,
    "root_octave": pattern_root_octave,
    "block_chord": pattern_block_chord,
    "broken_chord": pattern_broken_chord,
    "alberti_bass": pattern_alberti_bass,
}

DEFAULT_PATTERN = "broken_chord"


def build_pattern(
    name: str,
    root_pc: int,
    quality: str,
    *,
    difficulty: int = 3,
    beats_per_bar: int = 4,
) -> list[PlacedNote]:
    """Build one bar of a pattern, pruned to the difficulty's role budget."""
    if name not in PATTERNS:
        raise ValueError(f"unknown pattern: {name}")
    if difficulty not in DIFFICULTY_BEATS:
        raise ValueError(f"unsupported difficulty: {difficulty}")

    bass = place_bass(root_pc)
    tones = voice_chord(chord_intervals(quality), bass)
    cell = PATTERNS[name](bass, tones, beats_per_bar)
    allowed_offsets = set(DIFFICULTY_OFFSETS[difficulty])
    max_rank = DIFFICULTY_MAX_ROLE[difficulty]

    notes: list[PlacedNote] = []
    emitted: set[tuple[float, int]] = set()
    for beat in DIFFICULTY_BEATS[difficulty]:
        for note in cell:
            position = round(beat + note.beat, 4)
            if position >= beats_per_bar:
                continue
            if note.beat not in allowed_offsets:
                continue
            if note.role.rank > max_rank:
                continue
            # A note that lands after its beat (Alberti's third and fourth) can
            # coincide with the next beat's own note at the same instant and
            # pitch, which would be one key struck twice.
            key = (position, note.midi)
            if key in emitted:
                continue
            emitted.add(key)
            notes.append(
                PlacedNote(
                    beat=position,
                    duration_beats=min(note.duration_beats, beats_per_bar - position),
                    midi=note.midi,
                    role=note.role,
                )
            )
    return sorted(notes, key=lambda n: (n.beat, n.midi))


__all__ = [
    "BASS_HIGH",
    "BASS_LOW",
    "DEFAULT_PATTERN",
    "DIFFICULTY_BEATS",
    "DIFFICULTY_MAX_ROLE",
    "DIFFICULTY_OFFSETS",
    "PATTERNS",
    "UPPER_SPAN",
    "PlacedNote",
    "Role",
    "build_pattern",
    "chord_intervals",
    "place_bass",
    "voice_chord",
]
