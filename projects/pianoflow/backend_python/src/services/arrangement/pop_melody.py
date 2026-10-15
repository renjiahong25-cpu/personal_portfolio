"""Explicit vocal-source pipeline. Never identify mixed polyphony as vocals."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from core.errors import ValidationError
from services.arrangement.grid import BarGrid
from services.arrangement.vocal_melody import VocalMelodyConfig, VocalMelodyExtractor
from services.audio_analysis.polyphony import NoteEvent


def extract_pop_melody(
    y: np.ndarray, sr: int, grid: BarGrid, *, source: str, tonic: int, mode: str
) -> tuple[list[NoteEvent], dict[str, object]]:
    """Separate the analysis window, then extract chromatic F0 from its vocal stem.

    Demucs must have a complete local pretrained repository (checkpoint + YAML).
    No download is attempted: missing resources fail with an actionable message.
    A caller selecting vocal_stem declares that their upload is already isolated.
    """
    with tempfile.TemporaryDirectory(prefix="pianoflow-pop-") as temporary:
        root = Path(temporary)
        clip = root / "analysis.wav"
        sf.write(clip, y, sr)
        stem = clip
        if source == "vocal":
            interpreter = os.environ.get("PIANOFLOW_SEPARATOR_PYTHON", "")
            checkpoint = Path(os.environ.get("PIANOFLOW_DEMUCS_CHECKPOINT", ""))
            if not interpreter or not Path(interpreter).is_file() or not checkpoint.is_file():
                raise ValidationError(
                    "人声分离资源不可用：请配置本地 Demucs 模型，或选择「已分离人声」/「混音高声部（实验）」",
                    details={
                        "required": ["PIANOFLOW_SEPARATOR_PYTHON", "PIANOFLOW_DEMUCS_CHECKPOINT"],
                        "fallbackUsed": False,
                    },
                )
            try:
                completed = subprocess.run(
                    [
                        interpreter,
                        str(
                            Path(__file__).resolve().parents[3]
                            / "scripts"
                            / "separate_vocals_worker.py"
                        ),
                        str(clip),
                        str(root / "vocals.wav"),
                        "--checkpoint",
                        str(checkpoint),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValidationError(
                    "人声分离启动失败；未使用混音替代人声", details={"reason": str(exc)}
                ) from exc
            stem = root / "vocals.wav"
            if completed.returncode != 0 or not stem.is_file():
                raise ValidationError(
                    "人声分离失败；未使用混音替代人声",
                    details={"reason": completed.stderr[-800:], "fallbackUsed": False},
                )
        config = VocalMelodyConfig(
            tempo_bpm=grid.tempo_bpm,
            beats_per_bar=grid.beats_per_bar,
            phase_seconds=grid.phase_seconds,
            cell_division=4,
            tonic_pc=tonic,
            key_mode=mode,
            duration=len(y) / sr,
            snap_to_key=False,
        )
        melody = VocalMelodyExtractor().extract(stem, config)
        events = melody.to_note_events()
        if not events:
            raise ValidationError(
                "未检测到可用的人声旋律；请检查人声轨或改选混音实验模式",
                details={"source": source, "fallbackUsed": False},
            )
        return events, {
            "source": source,
            "extractor": "RMVPE",
            "separated": source == "vocal",
            "inputDeclaredVocalStem": source == "vocal_stem",
            "snapToKey": False,
            "stats": dict(melody.stats),
            "fallbackUsed": False,
        }
