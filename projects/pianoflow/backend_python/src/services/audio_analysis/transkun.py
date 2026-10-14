"""Transkun as a drop-in transcriber, run out-of-process.

Why this one
------------
Measured on the reference recording (``cde3a16bbb536a15661e1f25efc98ca059dbde2c.wav``,
239.5 s, piano, heavily pedalled), same audio and same duration definition for all three:

    model          notes   median    p90      p99      max      >=1.98s   CPU
    basic-pitch     1382   0.383 s  0.859 s  2.118 s  3.137 s      18     2.3 s
    ByteDance       1199   1.220 s  2.000 s  2.000 s  2.000 s     351    67.2 s
    Transkun        1214   0.386 s  0.901 s  2.971 s  7.887 s      31    87.8 s

ByteDance's row is a defect, not a preference: p90, p99 and max are *identical*, which is
the signature of a hard cut rather than a distribution tail. Its onset-offset rule
amplifies the note frame by the pedal, and on this recording the damper never lifted
(``pedal_frame`` median 1.000; one note read 0.970, 0.991, 0.994, 0.997 over two seconds -
rising). So ``frame * 11`` never crossed threshold and all 351 of those notes ended at the
search cap. Transkun instead has a learned refined-offset head and ships the "No Pedal
Ext" variant, which does not lengthen notes by the sustain pedal at all - so its durations
are spread and its tail is real.

Its median duration of 0.386 s is worth a second look, because it is an independent check
rather than a comparison: this piece measures 152 BPM, so a beat is 0.395 s. Transkun's
median note is one beat and its median onset spacing, 0.174 s, is near an eighth. ByteDance's
1.220 s median is three beats, which for a piece at this tempo would mean most notes are
held - not what the recording does.

What it costs
-------------
2.7x realtime on CPU (87.8 s for 239.5 s) against basic-pitch's 38x, and it needs torch -
which is why it runs in its own environment. 53.8 MB of weights, MIT licensed, and it is
the smallest model in the tree apart from basic-pitch.

**It has no sustain pedal head.** Its checkpoint contains ``backbone``,
``framewiseFeatureExtractor``, ``refinedOFPredictor``, ``scorer`` and ``velocityPredictor``
and nothing else; the pedal F1 of 0.95 in the literature belongs to a different checkpoint
that is not published in the package. So this replaces the note and offset work only. See
:mod:`services.audio_analysis.highres` for the pedal side.

Honest limit: without ground-truth MIDI none of the numbers above can be called correct.
They establish that Transkun is not truncated and is not inflated by the pedal, and that
its durations are metrically consistent with the measured tempo. They do not establish that
0.386 s is the right note length; that would need MAESTRO, which this project has not
downloaded.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from core.errors import ValidationError
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parents[3] / "scripts" / "transkun_worker.py"

#: Where the isolated environment is expected. Overridable, because a machine that put
#: conda envs somewhere else should not have to edit code.
DEFAULT_PYTHON_CANDIDATES = (
    Path(r"D:\conda_envs\transkun\python.exe"),
    Path.home() / ".conda" / "envs" / "transkun" / "python.exe",
    Path("/opt/conda/envs/transkun/bin/python"),
)

#: Generous. A 10-minute piece is about 220 s on CPU, so this only fires on a hang.
DEFAULT_TIMEOUT_SECONDS = 1800

#: Shorter than this and there is nothing to transcribe.
MINIMUM_SECONDS = 1.0


def _worker_python(explicit: str | None) -> Path:
    """Locate the interpreter that has torch, the way ffmpeg is located for uploads."""
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise ValidationError(
            f"the configured Transkun interpreter does not exist: {candidate}",
            details={"hint": "set PIANOFLOW_TRANSKUN_PYTHON to a python that has transkun"},
        )
    from_env = os.environ.get("PIANOFLOW_TRANSKUN_PYTHON")
    if from_env:
        candidate = Path(from_env)
        if candidate.is_file():
            return candidate
        raise ValidationError(
            f"PIANOFLOW_TRANSKUN_PYTHON points at nothing: {candidate}",
            details={"hint": "unset it, or point it at a python that has transkun"},
        )
    for candidate in DEFAULT_PYTHON_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise ValidationError(
        "Transkun is not installed, so it cannot be used as a transcriber",
        details={"hint": "set PIANOFLOW_TRANSKUN_PYTHON, or run pip install transkun"},
    )


def drop_pseudo_events(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Mirror of the worker's filter, so the rule can be tested without torch.

    Kept here rather than imported from the worker because that file runs under a
    different interpreter and must not depend on this package.
    """
    notes = [r for r in records if 21 <= int(r["pitch"]) <= 108]
    artifacts = [r for r in records if not 21 <= int(r["pitch"]) <= 108]
    return notes, artifacts


class TranskunTranscriber:
    """Transcribe a waveform with Transkun, returning the same events as basic-pitch.

    Blocking and CPU bound, and it costs a subprocess: call it through
    ``asyncio.to_thread`` like the other transcribers.
    """

    def __init__(
        self,
        python_executable: str | None = None,
        *,
        device: str = "cpu",
        timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._python = _worker_python(python_executable)
        self._device = device
        self._timeout = timeout

    @classmethod
    def is_available(cls, python_executable: str | None = None) -> bool:
        """Whether a usable interpreter was found. Lets a caller degrade instead of fail."""
        try:
            _worker_python(python_executable)
        except ValidationError:
            return False
        return WORKER.is_file()

    def transcribe_file(self, path: Path) -> list[NoteEvent]:
        """Transcribe an audio file in place - no re-encoding, so no quality loss."""
        return self._run(Path(path))

    def transcribe_array(self, y: np.ndarray, sr: int) -> list[NoteEvent]:
        """Transcribe a mono waveform by writing it to a temporary WAV first."""
        from scipy.io import wavfile

        audio = np.asarray(y, dtype=np.float32).reshape(-1)
        if audio.size < int(MINIMUM_SECONDS * sr):
            raise ValidationError(f"audio too short for transcription: {audio.size / sr:.2f}s")
        with tempfile.TemporaryDirectory(prefix="pianoflow-transkun-") as tmp:
            wav = Path(tmp) / "clip.wav"
            wavfile.write(str(wav), int(sr), np.clip(audio, -1.0, 1.0))
            return self._run(wav)

    def _run(self, audio_path: Path) -> list[NoteEvent]:
        if not audio_path.is_file():
            raise ValidationError(f"audio file not found: {audio_path}")

        with tempfile.TemporaryDirectory(prefix="pianoflow-transkun-out-") as tmp:
            out_path = Path(tmp) / "notes.json"
            command = [
                str(self._python),
                str(WORKER),
                str(audio_path),
                str(out_path),
                "--device",
                self._device,
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                logger.error("transkun worker failed (%s): %s", completed.returncode, detail)
                raise ValidationError(
                    "Transkun transcription failed",
                    details={"reason": detail[-800:] if detail else "no output"},
                )
            if not out_path.is_file():
                raise ValidationError(
                    "Transkun produced no output",
                    details={"reason": (completed.stderr or "").strip()[-800:]},
                )
            payload = json.loads(out_path.read_text(encoding="utf-8"))

        notes, artifacts = drop_pseudo_events(payload.get("notes", []))
        meta = payload.get("meta", {})
        discarded = int(payload.get("pseudoEvents", len(artifacts)))
        logger.info(
            "transkun: %d notes from %s (%.1fs audio, %d discarded as non-notes), %s",
            len(notes),
            meta.get("rawEvents", "?"),
            float(meta.get("audioSeconds", 0.0)),
            discarded,
            sys.platform,
        )
        return [
            NoteEvent(
                start=float(n["start"]),
                duration=max(float(n["end"]) - float(n["start"]), 0.01),
                midi=int(n["pitch"]),
                amplitude=min(max(float(n.get("velocity", 64)) / 127.0, 0.0), 1.0),
            )
            for n in notes
        ]


__all__ = [
    "DEFAULT_PYTHON_CANDIDATES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MINIMUM_SECONDS",
    "WORKER",
    "TranskunTranscriber",
    "drop_pseudo_events",
]
