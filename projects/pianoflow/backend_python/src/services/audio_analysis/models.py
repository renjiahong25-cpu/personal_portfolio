"""Value objects for the analysis stage.

Everything here is plain data so it can cross the thread boundary (librosa runs
in a worker thread) and serialise straight to JSON for the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: Scientific pitch names for MIDI note numbers (60 = C4).
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

QUALITY_LABELS = {
    "major": "",
    "minor": "m",
    "diminished": "dim",
    "augmented": "aug",
    "dominant7": "7",
    "minor7": "m7",
    "major7": "maj7",
}


def midi_to_pitch(midi: int) -> str:
    """60 -> 'C4', 61 -> 'C#4'."""
    return f"{PITCH_NAMES[midi % 12]}{midi // 12 - 1}"


def midi_to_freq(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


@dataclass(frozen=True)
class Note:
    """One detected note event."""

    start: float
    duration: float
    midi: int
    velocity: int = 80
    confidence: float = 1.0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def pitch_name(self) -> str:
        return midi_to_pitch(self.midi)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 4),
            "duration": round(self.duration, 4),
            "midi": self.midi,
            "pitch": self.pitch_name,
            "velocity": self.velocity,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True)
class ChordEvent:
    start: float
    duration: float
    root: int
    quality: str
    confidence: float = 1.0

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def name(self) -> str:
        return f"{PITCH_NAMES[self.root % 12]}{QUALITY_LABELS.get(self.quality, '')}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 4),
            "duration": round(self.duration, 4),
            "root": self.root % 12,
            "quality": self.quality,
            "name": self.name,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True)
class Ornament:
    """A detected decorative figure.

    These are heuristics, not transcription ground truth; the UI labels them as
    suggestions.
    """

    kind: Literal["grace", "slide", "trill", "tremolo"]
    start: float
    duration: float
    pitch: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": round(self.start, 4),
            "duration": round(self.duration, 4),
            "pitch": midi_to_pitch(self.pitch) if self.pitch is not None else None,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class MelodyContour:
    """Direction of the melody between two points, for the UI's contour line."""

    start: float
    end: float
    direction: Literal["up", "down", "flat"]
    semitones: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 4),
            "end": round(self.end, 4),
            "direction": self.direction,
            "semitones": round(self.semitones, 2),
        }


@dataclass
class AnalysisResult:
    duration: float
    notes: list[Note] = field(default_factory=list)
    chords: list[ChordEvent] = field(default_factory=list)
    ornaments: list[Ornament] = field(default_factory=list)
    contour: list[MelodyContour] = field(default_factory=list)
    tempo_bpm: float | None = None
    key_root: int | None = None
    key_quality: str | None = None
    sample_rate: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def key_name(self) -> str | None:
        if self.key_root is None or self.key_quality is None:
            return None
        suffix = "major" if self.key_quality == "major" else "minor"
        return f"{PITCH_NAMES[self.key_root % 12]} {suffix}"

    @property
    def note_count(self) -> int:
        return len(self.notes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 3),
            "sampleRate": self.sample_rate,
            "tempoBpm": round(self.tempo_bpm, 2) if self.tempo_bpm else None,
            "key": self.key_name,
            "keyRoot": self.key_root,
            "keyQuality": self.key_quality,
            "noteCount": self.note_count,
            "notes": [note.to_dict() for note in self.notes],
            "chords": [chord.to_dict() for chord in self.chords],
            "ornaments": [ornament.to_dict() for ornament in self.ornaments],
            "contour": [segment.to_dict() for segment in self.contour],
            "warnings": self.warnings,
        }


__all__ = [
    "PITCH_NAMES",
    "QUALITY_LABELS",
    "AnalysisResult",
    "ChordEvent",
    "MelodyContour",
    "Note",
    "Ornament",
    "midi_to_freq",
    "midi_to_pitch",
]
