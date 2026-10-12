"""Analysis pipeline: wav on disk -> :class:`AnalysisResult`.

Everything in this module is synchronous. The HTTP layer calls
:func:`analyze_file` through ``asyncio.to_thread`` so that librosa never runs on
the event loop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from core.errors import NotFoundError, ValidationError

from .chords import chord_progression, detect_chords
from .models import AnalysisResult
from .ornaments import (
    build_contour,
    contour_summary,
    detect_ornaments,
    detect_slides,
    leap_statistics,
)
from .pitch import PitchOptions, detect_notes, estimate_key, estimate_tempo

logger = logging.getLogger(__name__)

#: Longer clips are analysed in one pass anyway, but the UI needs a warning
#: because the whole thing runs on the CPU.
LONG_CLIP_SECONDS = 600.0
SUPPORTED_SUFFIXES = (".wav", ".mp3", ".m4a", ".flac", ".ogg")


def load_audio(path: Path, *, target_sr: int = 22050) -> tuple[np.ndarray, int]:
    """Load and mono-ify any audio librosa can read."""
    if not path.is_file():
        raise NotFoundError(f"audio file not found: {path.name}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValidationError(
            f"unsupported audio format {path.suffix!r}",
            details={"supported": list(SUPPORTED_SUFFIXES)},
        )
    try:
        y, sr = librosa.load(path, sr=target_sr, mono=True)
    except Exception as exc:
        raise ValidationError(f"cannot decode audio: {exc}") from exc
    if y.size == 0:
        raise ValidationError("audio file is empty")
    # librosa types sr as int | float depending on the resampler used; the
    # pipeline only ever does integer arithmetic with it.
    return y, int(sr)


def analyze_array(
    y: np.ndarray,
    sr: int,
    *,
    options: PitchOptions | None = None,
    with_slides: bool = True,
) -> AnalysisResult:
    """Run the full analysis on a loaded signal."""
    opts = options or PitchOptions()
    result = AnalysisResult(
        duration=float(len(y) / sr),
        sample_rate=sr,
    )
    if result.duration > LONG_CLIP_SECONDS:
        result.warnings.append(f"音频超过 {int(LONG_CLIP_SECONDS // 60)} 分钟，分析结果仅供参考")

    notes, f0, voiced = detect_notes(y, sr, opts)
    result.notes = notes

    result.tempo_bpm = estimate_tempo(y, sr)
    root, quality, score = estimate_key(y, sr)
    result.key_root = root
    result.key_quality = quality
    if root is not None and score < 0.55:
        result.warnings.append("调号判断置信度较低")

    result.chords = detect_chords(y, sr)
    result.ornaments = detect_ornaments(notes)
    if with_slides:
        result.ornaments.extend(detect_slides(f0, voiced, sr, opts.hop_length))
        result.ornaments.sort(key=lambda item: item.start)

    result.contour = build_contour(notes)

    if not notes:
        result.warnings.append("没有检测到明显旋律，可能是纯伴奏或人声太弱")
    return result


def analyze_file(path: Path, *, options: PitchOptions | None = None) -> AnalysisResult:
    """Blocking entry point used by the worker thread."""
    y, sr = load_audio(path)
    return analyze_array(y, sr, options=options)


def summarise(result: AnalysisResult) -> dict[str, Any]:
    """The compact payload the analysis panel and the rules engine both read."""
    return {
        "duration": round(result.duration, 2),
        "tempoBpm": result.tempo_bpm,
        "key": result.key_name,
        "noteCount": result.note_count,
        "chordProgression": chord_progression(result.chords),
        "melodyContour": contour_summary(result.contour),
        "ornamentCount": len(result.ornaments),
        "ornamentKinds": sorted({item.kind for item in result.ornaments}),
        "leaps": leap_statistics(result.notes),
        "warnings": result.warnings,
    }


__all__ = [
    "LONG_CLIP_SECONDS",
    "SUPPORTED_SUFFIXES",
    "analyze_array",
    "analyze_file",
    "load_audio",
    "summarise",
]
