"""Chord recognition by chroma template matching.

Deliberately simple and inspectable: every chord is scored against 12 roots x 7
qualities, then the frame-wise decisions are smoothed and merged into events. It
is not a trained model, so ``confidence`` is a normalised score, not a
probability, and the UI must present chords as suggestions.
"""

from __future__ import annotations

import logging

import librosa
import numpy as np

from .models import ChordEvent

logger = logging.getLogger(__name__)

QUALITIES: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "diminished": (0, 3, 6),
    "augmented": (0, 4, 8),
    "dominant7": (0, 4, 7, 10),
    "minor7": (0, 3, 7, 10),
    "major7": (0, 4, 7, 11),
}

#: Minimum chord length; anything shorter is a passing tone.
MIN_CHORD_SECONDS = 0.30
#: Peak amplitude below which a frame carries no chord at all.
MIN_CHORD_ENERGY = 1e-3
#: Smoothing window in seconds before voting.
SMOOTH_SECONDS = 0.60


def _templates() -> np.ndarray:
    matrix = np.zeros((12 * len(QUALITIES), 12), dtype=np.float32)
    for index, quality in enumerate(QUALITIES):
        for root in range(12):
            for interval in QUALITIES[quality]:
                matrix[index * 12 + root, (root + interval) % 12] = 1.0
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return matrix / norms


_TEMPLATES = _templates()
_QUALITY_NAMES = list(QUALITIES)


def detect_chords(
    y: np.ndarray,
    sr: int,
    *,
    hop_length: int = 2048,
    min_chord_seconds: float = MIN_CHORD_SECONDS,
    min_energy: float = MIN_CHORD_ENERGY,
) -> list[ChordEvent]:
    """Estimate a chord timeline for the whole clip."""
    # Silence would otherwise be "matched" to an arbitrary template with a
    # score near zero and reported as a chord.
    if float(np.max(np.abs(y))) < min_energy:
        return []
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    except Exception as exc:  # noqa: BLE001
        logger.info("chroma failed: %s", exc)
        return []
    if chroma.size == 0:
        return []

    frames = chroma.T  # (time, 12)
    # L2-normalise each frame so the dot product is a true cosine in [-1, 1];
    # raw librosa chroma is not unit-norm and would saturate the confidence.
    norms = np.linalg.norm(frames, axis=1, keepdims=True) + 1e-9
    frames = frames / norms
    scores = frames @ _TEMPLATES.T  # (time, 84)

    hop_seconds = hop_length / sr
    window = max(1, round(SMOOTH_SECONDS / hop_seconds))
    smoothed = _moving_average(scores, window)
    best = np.argmax(smoothed, axis=1)
    best_scores = smoothed[np.arange(len(best)), best]

    min_frames = max(1, round(min_chord_seconds / hop_seconds))
    events: list[ChordEvent] = []
    run_start = 0
    for index in range(1, len(best) + 1):
        if index == len(best) or best[index] != best[run_start]:
            length = index - run_start
            if length >= min_frames:
                label = best[run_start]
                quality = _QUALITY_NAMES[label // 12]
                root = int(label % 12)
                mean_score = float(np.mean(best_scores[run_start:index]))
                events.append(
                    ChordEvent(
                        start=round(run_start * hop_seconds, 4),
                        duration=round(length * hop_seconds, 4),
                        root=root,
                        quality=quality,
                        confidence=round(float(np.clip(mean_score, 0.0, 1.0)), 3),
                    )
                )
            run_start = index
    return events


def _moving_average(matrix: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return matrix
    kernel = np.ones(window, dtype=np.float32) / window
    padded = np.pad(matrix, ((window // 2, window // 2), (0, 0)), mode="edge")
    smoothed = np.empty_like(matrix)
    for column in range(matrix.shape[1]):
        smoothed[:, column] = np.convolve(padded[:, column], kernel, mode="valid")[
            : matrix.shape[0]
        ]
    return smoothed


def chord_progression(events: list[ChordEvent], *, limit: int = 16) -> list[str]:
    """Compact ``["C", "Am", "F", "G"]`` view for the API payload.

    Only *consecutive* repeats are collapsed: a real progression may revisit a
    chord (C - Am - F - C), and dropping that would misrepresent the harmony.
    """
    names: list[str] = []
    for event in events[:limit]:
        if not names or names[-1] != event.name:
            names.append(event.name)
    return names


__all__ = [
    "MIN_CHORD_ENERGY",
    "MIN_CHORD_SECONDS",
    "QUALITIES",
    "chord_progression",
    "detect_chords",
]
