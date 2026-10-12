"""Pitch, tempo and key estimation.

All functions are synchronous and CPU heavy by design: the caller runs them via
``asyncio.to_thread`` (see :mod:`services.audio_analysis.pipeline`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import librosa
import numpy as np

from .models import Note

logger = logging.getLogger(__name__)

#: Musical range we care about for a piano/vocal melody: C2 (36) .. B6 (95).
MIN_MIDI = 36
MAX_MIDI = 96
#: Notes shorter than this are almost always noise or ornament noise.
MIN_NOTE_SECONDS = 0.08
#: A note must hold this fraction of consecutive frames to survive.
MIN_CONSISTENCY = 0.6
#: pYIN frame rate in Hz is sr/hop_length; quantise to semitones over this many
#: seconds of stability to avoid splitting a held note.
STABILITY_SECONDS = 0.05
#: A note isolated from its neighbours in both pitch and time, and shorter than
#: this, is treated as an artefact rather than music.
BLIP_MAX_SECONDS = 0.12
BLIP_MAX_LEAP = 4  # semitones
BLIP_MAX_GAP = 0.15  # seconds

# Krumhansl-Schmuckler key profiles.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


@dataclass(frozen=True)
class PitchOptions:
    fmin: float = 55.0  # A1
    fmax: float = 1760.0  # A6
    hop_length: int = 512
    frame_length: int = 2048
    min_note_seconds: float = MIN_NOTE_SECONDS
    min_consistency: float = MIN_CONSISTENCY


def detect_notes(
    y: np.ndarray,
    sr: int,
    options: PitchOptions | None = None,
) -> tuple[list[Note], np.ndarray, np.ndarray]:
    """Extract note events with librosa.pyin.

    Returns the notes plus the raw ``(f0, voiced)`` arrays so callers can compute
    ornaments (which need sub-note detail) without paying for pyin twice.
    """
    opts = options or PitchOptions()
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y,
        fmin=opts.fmin,
        fmax=opts.fmax,
        sr=sr,
        frame_length=opts.frame_length,
        hop_length=opts.hop_length,
        fill_na=np.nan,
    )
    notes = quantise_to_notes(f0, voiced_flag, sr, opts)
    return notes, f0, voiced_probs


def quantise_to_notes(
    f0: np.ndarray,
    voiced_flag: np.ndarray,
    sr: int,
    options: PitchOptions | None = None,
) -> list[Note]:
    """Turn a frame-wise pitch track into discrete note events."""
    opts = options or PitchOptions()
    hop_seconds = opts.hop_length / sr
    min_frames = max(1, round(opts.min_note_seconds / hop_seconds))

    notes: list[Note] = []
    current_midi: int | None = None
    run_start = 0

    def flush(end_frame: int) -> None:
        nonlocal current_midi
        if current_midi is not None:
            length = end_frame - run_start
            if length >= min_frames and MIN_MIDI <= current_midi <= MAX_MIDI:
                notes.append(
                    Note(
                        start=round(run_start * hop_seconds, 4),
                        duration=round(length * hop_seconds, 4),
                        midi=current_midi,
                    )
                )
        current_midi = None

    previous_raw: float | None = None
    for index, (frequency, voiced) in enumerate(zip(f0, voiced_flag, strict=True)):
        midi_float = librosa.hz_to_midi(frequency) if np.isfinite(frequency) else None
        # A raw pitch wiggle of less than a quarter tone is vibrato, not a new
        # note; a larger jump is a real move.
        if midi_float is None or previous_raw is None or abs(midi_float - previous_raw) > 0.25:
            candidate: int | None = round(midi_float) if midi_float is not None else None
        else:
            candidate = current_midi
        if not bool(voiced) or midi_float is None:
            candidate = None

        if candidate != current_midi:
            flush(index)
            if candidate is not None:
                run_start = index
            current_midi = candidate
        if midi_float is not None:
            previous_raw = midi_float

    flush(len(f0))

    return _drop_blips(notes)


def _drop_blips(notes: list[Note]) -> list[Note]:
    """Drop notes that are neither long nor harmonically connected.

    A real melodic note either lasts or sits next to a neighbour in the same
    region; a one-frame artefact surrounded by silence does neither.
    """
    if len(notes) < 3:
        return notes
    kept: list[Note] = []
    for index, note in enumerate(notes):
        neighbours = [notes[i] for i in (index - 1, index + 1) if 0 <= i < len(notes)]
        isolated = all(
            abs(other.midi - note.midi) > BLIP_MAX_LEAP
            or abs(other.start - note.end) > BLIP_MAX_GAP
            for other in neighbours
        )
        if isolated and note.duration < BLIP_MAX_SECONDS:
            continue
        kept.append(note)
    return kept


def estimate_tempo(y: np.ndarray, sr: int, *, hop_length: int = 512) -> float | None:
    """Tempo via onset-strength autocorrelation; ``None`` when unreliable."""
    try:
        onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
        if onset.size < 4 or not np.any(onset > 0):
            return None
        tempo = librosa.feature.tempo(
            onset_envelope=onset, sr=sr, hop_length=hop_length, aggregate=None
        )
        value = float(np.asarray(tempo).reshape(-1)[0])
    except Exception as exc:  # noqa: BLE001 - librosa raises many error types
        logger.info("tempo estimation failed: %s", exc)
        return None
    # 50-200 BPM covers almost all pop/ballad material; fold outliers in.
    while value < 50:
        value *= 2
    while value > 200:
        value /= 2
    return round(value, 2) if 50 <= value <= 200 else None


def estimate_key(
    y: np.ndarray, sr: int, *, hop_length: int = 2048
) -> tuple[int | None, str | None, float]:
    """Krumhansl-Schmuckler key estimation from the chroma profile.

    Returns ``(root, quality, score)`` where score is the best correlation.
    """
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    except Exception as exc:  # noqa: BLE001
        logger.info("chroma failed: %s", exc)
        return None, None, 0.0

    profile = np.mean(chroma, axis=1)
    if not np.any(profile > 0):
        return None, None, 0.0
    profile = (profile - profile.mean()) / (profile.std() + 1e-9)

    best = (0, "major", -2.0)
    for root in range(12):
        for quality, template in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
            rolled = np.roll(template, root)
            rolled = (rolled - rolled.mean()) / (rolled.std() + 1e-9)
            score = float(np.dot(rolled, profile) / len(template))
            if score > best[2]:
                best = (root, quality, score)
    return best[0], best[1], round(best[2], 4)


def chroma_profile(y: np.ndarray, sr: int, *, hop_length: int = 2048) -> np.ndarray:
    """Mean chroma vector (12,), used by both key and chord estimation."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    return np.mean(chroma, axis=1)


__all__ = [
    "MAX_MIDI",
    "MIN_MIDI",
    "PitchOptions",
    "chroma_profile",
    "detect_notes",
    "estimate_key",
    "estimate_tempo",
    "quantise_to_notes",
]
