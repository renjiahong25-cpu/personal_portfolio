"""Eight accompaniment textures for pop-song-to-piano arrangement.

Each texture is a generator that takes a chord (root + quality) and produces
a bar of left-hand notes. They reuse the infrastructure from ``patterns.py``
(``PlacedNote``, ``Role``, ``place_bass``, ``voice_chord``, ``chord_intervals``)
so the two modules stay consistent.

The eight textures, ordered from simplest to most elaborate:

``block``      柱式：every chord tone on the beat, all at once.
``perc332``    打击感 3-3-2：a percussive figure that subdivides the bar.
``rhythm``     节奏动机：a rhythmic motif that repeats across the bar.
``half``       半分解：bass on the beat, chord tones between the beats.
``bass``       低音线条：a single bass line, the simplest possible.
``arplong``    全分解长线条：a flowing arpeggio that spans the whole bar.
``arpfull``    全分解和弦琶音：a full arpeggio, one note per subdivision.
``riff``       riff 乐段动机：a short motif that repeats every two bars.

Two modes are supported:

``accompaniment``  伴奏曲：the texture is the whole left hand, selectable
                   or combined.
``full``           完整曲：the melody is kept in the right hand and the
                   texture varies across sections via a ``TexturePlan``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from services.arrangement.patterns import (
    PlacedNote,
    Role,
    chord_intervals,
    place_bass,
    voice_chord,
)

logger = logging.getLogger(__name__)


class TextureName(StrEnum):
    """The eight textures, as strings the API and UI expose."""

    BLOCK = "block"
    PERC332 = "perc332"
    RHYTHM = "rhythm"
    HALF = "half"
    BASS = "bass"
    ARLONG = "arplong"
    ARPFULL = "arpfull"
    RIFF = "riff"


#: Human-readable labels for the UI.
TEXTURE_LABELS: dict[TextureName, str] = {
    TextureName.BLOCK: "柱式",
    TextureName.PERC332: "打击感332",
    TextureName.RHYTHM: "节奏动机",
    TextureName.HALF: "半分解",
    TextureName.BASS: "低音线条",
    TextureName.ARLONG: "全分解长线条副旋律",
    TextureName.ARPFULL: "全分解和弦琶音",
    TextureName.RIFF: "riff乐段动机",
}

#: Which textures can be combined with each other. Block and percussive
#: textures are mutually exclusive because they both own the downbeat.
COMBINABLE: dict[TextureName, set[TextureName]] = {
    TextureName.BLOCK: {TextureName.BASS},
    TextureName.PERC332: {TextureName.BASS},
    TextureName.RHYTHM: {TextureName.BASS, TextureName.HALF},
    TextureName.HALF: {TextureName.BASS, TextureName.RHYTHM},
    TextureName.BASS: {
        TextureName.BLOCK,
        TextureName.PERC332,
        TextureName.RHYTHM,
        TextureName.HALF,
        TextureName.BASS,
    },
    TextureName.ARLONG: {TextureName.BASS},
    TextureName.ARPFULL: {TextureName.BASS},
    TextureName.RIFF: {TextureName.BASS},
}


def _block(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """柱式：root-position block chords with three-layer voicing.

    Block chords use close voicing (all chord tones within one octave above
    the bass) and play on every beat. The three layers are:
    - Bass: root note, sustained
    - Middle: chord tones (3rd, 7th) in close position
    - Top: root or 5th doubled an octave up for fullness
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    notes: list[PlacedNote] = []
    for beat in range(beats_per_bar):
        # Bass layer: root note
        notes.append(PlacedNote(float(beat), 1.0, bass, Role.SKELETON))
        # Middle layer: chord tones in close position
        for tone in tones:
            notes.append(PlacedNote(float(beat), 1.0, tone, Role.CHORD))
        # Top layer: double the root an octave up for fullness
        notes.append(PlacedNote(float(beat), 1.0, bass + 12, Role.COLOR))
    return notes


def _perc332(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """打击感 3-3-2：a percussive figure that subdivides the bar.

    The bar is divided into three groups: 3 + 3 + 2 eighth notes. The bass
    plays on the first note of each group, the chord tones fill the rest.
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    group_starts = (0.0, 1.5, 3.0)
    group_lengths = (3, 3, 2)
    notes: list[PlacedNote] = []
    for start, length in zip(group_starts, group_lengths, strict=True):
        notes.append(PlacedNote(start, 0.5, bass, Role.SKELETON))
        for step in range(1, length):
            tone = tones[step % len(tones)]
            notes.append(PlacedNote(start + step * 0.5, 0.5, tone, Role.CHORD))
    return notes


def _rhythm(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """节奏动机：a rhythmic motif that repeats across the bar.

    The motif is: bass on beat 1, chord tone on the "and" of 1, bass on
    beat 2, chord tone on beat 3, bass on the "and" of 3. This creates a
    syncopated feel that works well for pop and rock.
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    return [
        PlacedNote(0.0, 0.5, bass, Role.SKELETON),
        PlacedNote(0.5, 0.5, tones[0], Role.CHORD),
        PlacedNote(1.0, 0.5, bass, Role.SKELETON),
        PlacedNote(2.0, 0.5, tones[-1], Role.CHORD),
        PlacedNote(3.0, 0.5, bass, Role.SKELETON),
        PlacedNote(3.5, 0.5, tones[0], Role.ORNAMENT),
    ]


def _half(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """半分解：open voicing with bass line movement.

    Half-broken chords use open voicing (chord tones spread across two octaves)
    and a moving bass line (root → fifth → root → fifth). The two layers are:
    - Bass: alternating root and fifth for harmonic movement
    - Upper: chord tones in open position, played on off-beats
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    notes: list[PlacedNote] = []
    fifth = bass + 7
    for beat in range(beats_per_bar):
        # Bass line: alternate root and fifth
        bass_note = bass if beat % 2 == 0 else fifth
        notes.append(PlacedNote(float(beat), 0.5, bass_note, Role.SKELETON))
        # Upper voices: chord tones in open position on off-beats
        for i, tone in enumerate(tones):
            # Spread tones across two octaves for open voicing
            octave_shift = 12 if i >= 2 else 0
            notes.append(PlacedNote(beat + 0.5, 0.5, tone + octave_shift, Role.CHORD))
    return notes


def _bass(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """低音线条：a single bass note per beat, the simplest possible."""
    return [PlacedNote(float(beat), 1.0, bass, Role.SKELETON) for beat in range(beats_per_bar)]


def _arplong(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """全分解长线条：a flowing arpeggio that spans the whole bar.

    The arpeggio plays one note per beat, cycling through the chord tones.
    The bass plays on beat 1, then the arpeggio takes over.
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    notes = [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    for beat in range(1, beats_per_bar):
        tone = tones[beat % len(tones)]
        notes.append(PlacedNote(float(beat), 1.0, tone, Role.CHORD))
    return notes


def _arpfull(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """全分解和弦琶音：a full arpeggio, one note per subdivision.

    The arpeggio plays one note per eighth note, cycling through the chord
    tones. The bass plays on beat 1, then the arpeggio takes over.
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    notes = [PlacedNote(0.0, 0.5, bass, Role.SKELETON)]
    subdivisions = beats_per_bar * 2
    for step in range(1, subdivisions):
        tone = tones[step % len(tones)]
        notes.append(PlacedNote(step * 0.5, 0.5, tone, Role.CHORD))
    return notes


def _riff(bass: int, tones: Sequence[int], beats_per_bar: int) -> list[PlacedNote]:
    """riff 乐段动机：a short motif that repeats every two bars.

    The motif is: bass on beat 1, chord tone on the "and" of 1, chord tone
    on beat 2, bass on the "and" of 2. This creates a driving, repetitive
    figure that works well for pop and rock.
    """
    if not tones:
        return [PlacedNote(0.0, 1.0, bass, Role.SKELETON)]
    return [
        PlacedNote(0.0, 0.5, bass, Role.SKELETON),
        PlacedNote(0.5, 0.5, tones[0], Role.CHORD),
        PlacedNote(1.0, 0.5, tones[-1], Role.CHORD),
        PlacedNote(1.5, 0.5, bass, Role.SKELETON),
        PlacedNote(2.0, 0.5, tones[0], Role.CHORD),
        PlacedNote(2.5, 0.5, tones[-1], Role.CHORD),
        PlacedNote(3.0, 0.5, bass, Role.SKELETON),
        PlacedNote(3.5, 0.5, tones[0], Role.ORNAMENT),
    ]


#: Texture registry. The key is what the API and the UI expose.
TEXTURES: dict[TextureName, Callable[[int, Sequence[int], int], list[PlacedNote]]] = {
    TextureName.BLOCK: _block,
    TextureName.PERC332: _perc332,
    TextureName.RHYTHM: _rhythm,
    TextureName.HALF: _half,
    TextureName.BASS: _bass,
    TextureName.ARLONG: _arplong,
    TextureName.ARPFULL: _arpfull,
    TextureName.RIFF: _riff,
}

DEFAULT_TEXTURE = TextureName.HALF


def build_texture(
    name: TextureName | str,
    root_pc: int,
    quality: str,
    *,
    beats_per_bar: int = 4,
) -> list[PlacedNote]:
    """Build one bar of a texture, voiced to real MIDI."""
    if isinstance(name, str):
        try:
            name = TextureName(name)
        except ValueError as exc:
            raise ValueError(f"unknown texture: {name}") from exc
    if name not in TEXTURES:
        raise ValueError(f"unknown texture: {name}")

    bass = place_bass(root_pc)
    tones = voice_chord(chord_intervals(quality), bass)
    return TEXTURES[name](bass, tones, beats_per_bar)


def build_texture_combined(
    names: Sequence[TextureName | str],
    root_pc: int,
    quality: str,
    *,
    beats_per_bar: int = 4,
) -> list[PlacedNote]:
    """Combine multiple textures into one bar.

    The first texture provides the skeleton (bass), the others add their
    notes on top. This allows combinations like "block + bass" where the
    block chord is the main figure and the bass line adds movement.
    """
    if not names:
        raise ValueError("at least one texture is required")
    resolved: list[TextureName] = []
    for name in names:
        if isinstance(name, str):
            try:
                name = TextureName(name)
            except ValueError as exc:
                raise ValueError(f"unknown texture: {name}") from exc
        resolved.append(name)

    for i, first in enumerate(resolved):
        for second in resolved[i + 1 :]:
            if second not in COMBINABLE.get(first, set()):
                raise ValueError(f"cannot combine {first} with {second}")

    bass = place_bass(root_pc)
    tones = voice_chord(chord_intervals(quality), bass)
    notes: list[PlacedNote] = []
    for name in resolved:
        notes.extend(TEXTURES[name](bass, tones, beats_per_bar))

    # Remove duplicates: the same pitch at the same position.
    seen: set[tuple[float, int]] = set()
    unique: list[PlacedNote] = []
    for note in sorted(notes, key=lambda n: (n.beat, n.midi)):
        key = (note.beat, note.midi)
        if key in seen:
            continue
        seen.add(key)
        unique.append(note)
    return unique


@dataclass(frozen=True)
class TextureSection:
    """One section of a texture plan: a range of bars and a texture."""

    start_bar: int
    end_bar: int
    texture: TextureName

    def to_dict(self) -> dict[str, object]:
        return {
            "startBar": self.start_bar,
            "endBar": self.end_bar,
            "texture": self.texture.value,
        }


@dataclass(frozen=True)
class TexturePlan:
    """A plan that maps bar ranges to textures.

    Used in "full" mode where the melody is kept in the right hand and the
    left-hand texture varies across sections.
    """

    sections: tuple[TextureSection, ...]
    default_texture: TextureName = DEFAULT_TEXTURE

    def texture_for_bar(self, bar: int) -> TextureName:
        """The texture for a given bar."""
        for section in self.sections:
            if section.start_bar <= bar < section.end_bar:
                return section.texture
        return self.default_texture

    def to_dict(self) -> dict[str, object]:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "defaultTexture": self.default_texture.value,
        }


def build_texture_plan(
    sections: Sequence[tuple[int, int, TextureName | str]],
    *,
    default_texture: TextureName | str = DEFAULT_TEXTURE,
) -> TexturePlan:
    """Build a texture plan from a list of (start_bar, end_bar, texture) tuples."""
    if isinstance(default_texture, str):
        try:
            default_texture = TextureName(default_texture)
        except ValueError as exc:
            raise ValueError(f"unknown texture: {default_texture}") from exc
    resolved = tuple(
        TextureSection(start, end, TextureName(tex) if isinstance(tex, str) else tex)
        for start, end, tex in sections
    )
    return TexturePlan(sections=resolved, default_texture=default_texture)


__all__ = [
    "COMBINABLE",
    "DEFAULT_TEXTURE",
    "TEXTURES",
    "TEXTURE_LABELS",
    "TextureName",
    "TexturePlan",
    "TextureSection",
    "build_texture",
    "build_texture_combined",
    "build_texture_plan",
]
