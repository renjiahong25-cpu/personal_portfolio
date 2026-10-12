"""Ornament heuristics and the melody contour line.

These are *suggestions*, not ground truth: the point is to point a learner at
"there is something decorative around 1:24", not to claim a scholarly edition.
Every threshold is a named constant so it can be tuned against real material.
"""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from .models import MelodyContour, Note, Ornament

#: A note this short right before a longer neighbour is a grace note (倚音).
GRACE_MAX_SECONDS = 0.14
GRACE_MAX_LEAP = 2  # semitones
#: A pitch move of this many semitones inside one note counts as a slide (滑音).
SLIDE_MIN_SEMITONES = 1.0
#: Alternations per second that count as a trill (颤音).
TRILL_MIN_RATE = 6.0
TRILL_MIN_EVENTS = 4
#: Semitone gap that still counts as "the same note" for contour purposes.
FLAT_SEMITONES = 0.5
#: A leap larger than this is a real jump, not a contour step.
JUMP_SEMITONES = 7.0


def detect_ornaments(notes: list[Note]) -> list[Ornament]:
    """Find grace notes, slides and trills in a note list."""
    found: list[Ornament] = []
    found.extend(_grace_notes(notes))
    found.extend(_trills(notes))
    found.sort(key=lambda item: item.start)
    return found


def _grace_notes(notes: list[Note]) -> list[Ornament]:
    graces: list[Ornament] = []
    for index, note in enumerate(notes[:-1]):
        if note.duration > GRACE_MAX_SECONDS:
            continue
        following = notes[index + 1]
        gap = following.start - note.end
        if gap > 0.08 or following.duration < 0.18:
            continue
        leap = following.midi - note.midi
        if abs(leap) > GRACE_MAX_LEAP:
            continue
        graces.append(
            Ornament(
                kind="grace",
                start=note.start,
                duration=note.duration,
                pitch=note.midi,
                detail=f"接 {following.pitch_name} 的短倚音",
            )
        )
    return graces


def _trills(notes: list[Note]) -> list[Ornament]:
    """Detect fast alternation between neighbouring pitches."""
    trills: list[Ornament] = []
    index = 0
    while index < len(notes) - 1:
        run_start = index
        alternations = 0
        current = index
        while current < len(notes) - 1:
            pair = notes[current : current + 2]
            if len(pair) < 2 or abs(pair[0].midi - pair[1].midi) > 2:
                break
            if pair[1].start - pair[0].end > 0.06:
                break
            alternations += 1
            current += 2 if alternations % 2 == 0 else 1
        span = notes[current - 1].end - notes[run_start].start if current > run_start else 0.0
        if alternations >= TRILL_MIN_EVENTS and span > 0:
            rate = alternations / max(span, 1e-6)
            if rate >= TRILL_MIN_RATE:
                trills.append(
                    Ornament(
                        kind="trill",
                        start=notes[run_start].start,
                        duration=round(span, 4),
                        pitch=notes[run_start].midi,
                        detail=f"{alternations} 次快速交替",
                    )
                )
        index = max(current, index + 1)
    return trills


def detect_slides(f0: np.ndarray, voiced: np.ndarray, sr: int, hop_length: int) -> list[Ornament]:
    """Detect within-note pitch glides from the frame-wise pitch track."""
    if f0 is None or len(f0) == 0:
        return []
    hop_seconds = hop_length / sr
    voiced = np.asarray(voiced, dtype=bool)
    finite = np.isfinite(f0)
    usable = voiced & finite
    if usable.sum() < 4:
        return []

    midi = librosa_hz_to_midi(f0)
    slides: list[Ornament] = []
    run: list[int] = []

    def flush() -> None:
        if len(run) >= 4:
            span = midi[run[-1]] - midi[run[0]]
            if abs(span) >= SLIDE_MIN_SEMITONES:
                slides.append(
                    Ornament(
                        kind="slide",
                        start=round(run[0] * hop_seconds, 4),
                        duration=round((run[-1] - run[0] + 1) * hop_seconds, 4),
                        detail=f"{'+' if span > 0 else ''}{span:.1f} 半音滑行",
                    )
                )
        run.clear()

    for index in range(len(midi)):
        if usable[index]:
            run.append(index)
        else:
            flush()
    flush()
    return slides


def librosa_hz_to_midi(f0: np.ndarray) -> np.ndarray:
    """Vectorised hz->midi that tolerates NaN (librosa raises on NaN)."""
    safe = np.where(np.isfinite(f0), f0, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69.0 + 12.0 * np.log2(np.where(safe > 0, safe, np.nan) / 440.0)
    return np.nan_to_num(midi, nan=0.0)


def build_contour(notes: list[Note], *, bucket_seconds: float = 2.0) -> list[MelodyContour]:
    """Summarise the melody into up/down/flat segments for the UI."""
    if len(notes) < 2:
        return []
    ordered = sorted(notes, key=lambda note: note.start)
    segments: list[MelodyContour] = []
    window_start = ordered[0].start
    window_notes: list[Note] = []

    def close_window() -> None:
        nonlocal window_start, window_notes
        if len(window_notes) < 2:
            window_start = window_notes[-1].end if window_notes else window_start
            window_notes = []
            return
        pitches = [note.midi for note in window_notes]
        span = pitches[-1] - pitches[0]
        direction = "flat" if abs(span) < FLAT_SEMITONES else ("up" if span > 0 else "down")
        segments.append(
            MelodyContour(
                start=round(window_start, 4),
                end=round(window_notes[-1].end, 4),
                direction=direction,  # type: ignore[arg-type]
                semitones=float(span),
            )
        )
        window_start = window_notes[-1].end
        window_notes = []

    for note in ordered:
        if note.start - window_start >= bucket_seconds and window_notes:
            close_window()
        window_notes.append(note)
    close_window()

    # Collapse a run of the same direction into one arrow for readability.
    merged: list[MelodyContour] = []
    for segment in segments:
        if (
            merged
            and merged[-1].direction == segment.direction
            and abs(merged[-1].end - segment.start) < bucket_seconds
        ):
            merged[-1] = MelodyContour(
                start=merged[-1].start,
                end=segment.end,
                direction=segment.direction,
                semitones=merged[-1].semitones + segment.semitones,
            )
            continue
        merged.append(segment)
    return merged


def contour_summary(contour: list[MelodyContour]) -> str:
    """Chinese one-liner such as 上行-平缓-下行 for the API payload."""
    labels = {"up": "上行", "down": "下行", "flat": "平缓"}
    parts: list[str] = []
    for segment in contour:
        label = labels[segment.direction]
        if parts and parts[-1] == label:
            continue
        parts.append(label)
    return "-".join(parts[:8]) if parts else "数据不足"


def leap_statistics(notes: list[Note]) -> dict[str, Any]:
    """Melody-range statistics the aesthetic rules need."""
    if len(notes) < 2:
        return {"range": 0, "leaps": 0, "leapRatio": 0.0, "meanStep": 0.0}
    pitches = [note.midi for note in sorted(notes, key=lambda n: n.start)]
    steps = [abs(b - a) for a, b in itertools.pairwise(pitches)]
    leaps = [step for step in steps if step >= JUMP_SEMITONES]
    return {
        "range": int(max(pitches) - min(pitches)),
        "leaps": len(leaps),
        "leapRatio": round(len(leaps) / len(steps), 4) if steps else 0.0,
        "meanStep": round(float(np.mean(steps)), 3) if steps else 0.0,
    }


__all__ = [
    "GRACE_MAX_SECONDS",
    "SLIDE_MIN_SEMITONES",
    "TRILL_MIN_RATE",
    "build_contour",
    "contour_summary",
    "detect_ornaments",
    "detect_slides",
    "leap_statistics",
]
