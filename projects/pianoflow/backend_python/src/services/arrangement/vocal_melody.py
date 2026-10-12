"""Vocal melody extraction: RMVPE F0 on a beat grid, generalised.

What this is
------------
The pipeline that was tuned on 星辰大海 in ``scripts/melody_v2.py`` (v7), moved into
the service layer and parameterised so it runs on any vocal stem:

1. **Beat grid** - one eighth-note (or finer) cell per grid position. The phase is
   anchored to a reference MIDI's onsets when one is given, else estimated from
   onset energy.
2. **F0** - RMVPE, run out-of-process in an environment that has torch (the same
   shell-out pattern as :class:`services.audio_analysis.transkun.TranskunTranscriber`).
3. **Per-cell pitch** - the *median* of the voiced F0 inside the cell. The median,
   not the mode or the mean, because a downward trill or a glide into the note pulls
   the mode/mean flat by a step, which is what made the melody sound a whole tone low.
4. **Melody gate** - a cell is melody when its peak amplitude clears a fraction of
   the p99.5 peak AND it is mostly voiced. Amplitude, not word timing, decides
   presence, because a sung hum has no words but is clearly melody.
5. **Octave correction** - per cell, all-or-nothing: RMVPE sometimes locks an octave
   low on high notes, so the cell's spectrum is compared as the harmonic series of
   f0 (A) against 2*f0 (B). Lift when B wins AND the reported f0 is weak against its
   double (a strong f0 means RMVPE was already right) AND the lift stays below the
   cap (a lift above C#6 was never real on this material).
6. **Key fit** - every pitch goes to the nearest scale tone; the tolerance is half
   the scale's largest gap, which is the smallest width that assigns every pitch on
   the keyboard, so there is no chromatic escape.
7. **Low-outlier fold** - a cell more than an octave below its neighbours is
   subharmonic residue and is folded up one octave, but never above the cap and
   never down: a note an octave *above* is a genuine high note.
8. **Note formation** - adjacent cells with the same fitted pitch merge into one
   sustained note; only a pitch change splits. Splitting on word boundaries (the v7
   rule) made a sustained note across two syllables sound like two staccato clicks,
   which the user heard as "一拍一个音".
9. **Velocity** - the p85 of the note's amplitude envelope, mapped into the
   configured MIDI range.

The ML part (RMVPE + Whisper word timestamps) costs a subprocess and needs torch and
faster-whisper, which deliberately live in a separate environment. The pure core
(grid, gate, median, octave rule, key fit, fold, merge, velocity) is plain numpy and
runs anywhere, which is also what the tests exercise.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.errors import ValidationError
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parents[3] / "scripts" / "vocal_melody_worker.py"

#: Where the ML environment is expected. Resolution order (see :func:`_worker_python`):
#: the ``PIANOFLOW_ML_PYTHON`` environment variable first, then these well-known conda
#: locations. No machine-specific paths: a machine that put conda envs somewhere else
#: sets the variable.
DEFAULT_PYTHON_CANDIDATES = (
    Path.home() / ".conda" / "envs" / "pop2piano" / "python.exe",
    Path.home() / "miniconda3" / "envs" / "pop2piano" / "python.exe",
    Path("/opt/conda/envs/pop2piano/bin/python"),
)

#: A 10-minute song is about 3-4 minutes for RMVPE plus Whisper on CPU, so this only
#: fires on a hang.
DEFAULT_TIMEOUT_SECONDS = 1800

#: The sample rate the whole pipeline works at. RMVPE resamples to 16 kHz internally;
#: the STFT and the amplitude gate use this rate.
PIPELINE_SAMPLE_RATE = 22050
#: STFT size for the per-cell octave check. 4096 at 22.05 kHz is 12.6 Hz per bin,
#: fine enough for the fundamental/double comparison without blurring them together.
STFT_NFFT = 4096

#: Scale degree patterns, as semitone offsets above the tonic.
SCALES: Mapping[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
}


def scale_pcs(tonic_pc: int, mode: str = "major") -> frozenset[int]:
    """The pitch classes of the key, e.g. E major = {1, 3, 4, 6, 8, 9, 11}."""
    degrees = SCALES.get(mode, SCALES["major"])
    return frozenset((int(tonic_pc) + i) % 12 for i in degrees)


def _max_gap_cents(mode: str) -> float:
    """Largest gap between adjacent scale tones, in cents.

    The tolerance a key fit needs to be *total*: the worst-case pitch sits in the
    middle of that gap, so anything below half of it leaves part of the keyboard
    unassignable. For every diatonic mode the largest gap is a whole tone, 200 cents.
    """
    degrees = SCALES.get(mode, SCALES["major"])
    gaps = [
        (degrees[(i + 1) % len(degrees)] + (12 if i + 1 >= len(degrees) else 0) - degrees[i])
        * 100.0
        for i in range(len(degrees))
    ]
    return max(gaps)


def fit_to_scale(pitch: float, tonic_pc: int, mode: str = "major") -> int:
    """One continuous pitch (MIDI float) -> the nearest scale tone.

    The tolerance is set by the scale, not chosen: half its largest gap, which is the
    smallest width that assigns every pitch on the keyboard. There is no chromatic
    escape - a transcribing piano arrangement wants the key, and a "chromatic" pitch
    more than a whole tone from any scale tone is intonation drift, not a note.
    """
    tonic_pc = int(tonic_pc) % 12
    degrees = SCALES.get(mode, SCALES["major"])
    # best_pitch is seeded so the return type is int without an assert (bandit B101):
    # every scale in SCALES contains degree 0, so the first iteration always replaces it.
    # The sentinel distance has to be larger than any real one, or a tie against the
    # seeded value would leave the tonic at the wrong octave.
    best_pitch = tonic_pc
    best_distance = float("inf")
    for degree in degrees:
        base = tonic_pc + degree
        candidate = base + 12 * round((pitch - base) / 12.0)
        distance = abs(candidate - pitch)
        if distance < best_distance:
            best_pitch = candidate
            best_distance = distance
    return best_pitch


@dataclass(frozen=True)
class VocalMelodyConfig:
    """Everything the pipeline needs, so a new song is one config, not a new script."""

    tempo_bpm: float = 84.0
    beats_per_bar: int = 4
    tonic_pc: int = 4
    key_mode: str = "major"
    #: Grid cells per beat. 2 = eighth notes, the resolution the tuned pipeline used.
    cell_division: int = 2
    #: Explicit grid phase in seconds. Wins over ``reference_midi`` when set.
    phase_seconds: float | None = None
    #: A MIDI whose onsets anchor the grid phase (the reference recording's own part).
    reference_midi: Path | None = None
    #: A cell is melody when its peak amplitude clears this fraction of the p99.5 peak.
    amp_ratio: float = 0.15
    #: ...and at least this fraction of its frames are voiced.
    voiced_frac: float = 0.4
    #: Octave rule: lift when the 2*f0 series beats the f0 series by this factor.
    octave_thr: float = 0.5
    #: ...and the reported f0 is weaker than this fraction of its double (fundamental
    #: absence). A strong f0 means RMVPE locked correctly.
    octave_gate: float = 0.6
    #: Never lift a frame whose double would exceed this frequency (the tuned cap sat
    #: just below C#6 = 1108.7 Hz doubled... i.e. a lift target above ~C#6 is suspect).
    octave_cap_hz: float = 1150.0
    #: A low outlier may be folded up an octave, but never above this MIDI pitch.
    fold_cap: int = 85
    #: Merge adjacent same-pitch cells into one sustained note (the v7 default split on
    #: word boundaries instead, which read as staccato).
    merge_same_pitch: bool = True
    vel_min: int = 52
    vel_max: int = 106
    #: Analysis window in seconds; defaults to the audio's own length.
    duration: float | None = None


@dataclass(frozen=True)
class MelodyNote:
    start: float
    end: float
    midi: int
    velocity: int

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, float | int]:
        return {
            "start": round(self.start, 4),
            "end": round(self.end, 4),
            "midi": self.midi,
            "velocity": self.velocity,
        }


@dataclass(frozen=True)
class VocalMelody:
    notes: tuple[MelodyNote, ...]
    tempo_bpm: float
    phase_seconds: float
    cell_seconds: float
    tonic_pc: int
    key_mode: str
    stats: Mapping[str, object]

    def to_note_events(self) -> list[NoteEvent]:
        """The service's note model; amplitude is velocity normalised to 0..1."""
        return [
            NoteEvent(
                start=n.start,
                duration=n.duration,
                midi=n.midi,
                amplitude=n.velocity / 127.0,
            )
            for n in self.notes
        ]

    def to_midi(self, path: Path) -> Path:
        """A single-track MIDI of the extracted line, on the grid at the configured tempo."""
        import mido

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tps = 480 * self.tempo_bpm / 60.0
        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=int(60_000_000 / self.tempo_bpm), time=0))
        track.append(mido.Message("program_change", channel=0, program=0, time=0))
        last = 0
        for note in self.notes:
            start = round(note.start * tps)
            end = round(note.end * tps)
            if end <= start:
                end = start + 1
            track.append(
                mido.Message(
                    "note_on",
                    note=note.midi,
                    velocity=note.velocity,
                    channel=0,
                    time=max(0, start - last),
                )
            )
            track.append(
                mido.Message(
                    "note_off", note=note.midi, velocity=0, channel=0, time=max(0, end - start)
                )
            )
            last = max(start, end)
        mid.save(str(target))
        return target

    def to_dict(self) -> dict[str, object]:
        return {
            "tempoBpm": self.tempo_bpm,
            "phaseSeconds": round(self.phase_seconds, 4),
            "cellSeconds": round(self.cell_seconds, 4),
            "tonicPc": self.tonic_pc,
            "keyMode": self.key_mode,
            "notes": [n.to_dict() for n in self.notes],
            "stats": dict(self.stats),
        }


# --------------------------------------------------------------------- pure core


def grid_phase(reference_midi: Path, cell_seconds: float, tempo_bpm: float) -> float:
    """The grid phase as the median residue of a reference MIDI's onsets.

    The reference part was written on the same grid, so its onsets all sit at the
    same offset modulo the cell; the median of those residues is that offset. A mean
    would smear when the reference contains ties across barlines.
    """
    import mido

    path = Path(reference_midi)
    if not path.is_file():
        raise ValidationError(f"reference MIDI not found: {path}")
    mid = mido.MidiFile(str(path))
    ticks_per_second = mid.ticks_per_beat * tempo_bpm / 60.0
    onsets: list[float] = []
    open_: dict[int, list[int]] = {}
    for track in mid.tracks:
        ticks = 0
        for message in track:
            ticks += message.time
            if message.type == "note_on" and message.velocity > 0:
                open_.setdefault(message.note, []).append(ticks)
                continue
            # A note-off (or a zero-velocity note-on) closes the oldest open pitch;
            # meta messages have no .note at all, so the type comes first.
            if message.type != "note_off" and not (
                message.type == "note_on" and message.velocity == 0
            ):
                continue
            stack = open_.get(message.note)
            if stack:
                onsets.append(stack.pop(0) / ticks_per_second)
    if not onsets:
        raise ValidationError(
            "the reference MIDI has no note onsets to anchor the grid", details={"file": path.name}
        )
    residues = np.array([o % cell_seconds for o in sorted(onsets)])
    logger.info(
        "grid phase from %s: %d onsets, phase %.3f s",
        path.name,
        len(onsets),
        float(np.median(residues)),
    )
    return float(np.median(residues))


def cell_windows(
    duration: float, tempo_bpm: float, cell_division: int, phase_seconds: float
) -> list[tuple[int, float, float]]:
    """(index, start, end) for every cell touching [0, duration]."""
    if tempo_bpm <= 0 or cell_division < 1:
        raise ValueError("tempo_bpm must be > 0 and cell_division >= 1")
    cell = 60.0 / tempo_bpm / cell_division
    k_min = max(0, int(np.floor(-phase_seconds / cell)))
    k_max = int(np.ceil(duration / cell))
    return [
        (k, phase_seconds + k * cell, phase_seconds + (k + 1) * cell) for k in range(k_min, k_max)
    ]


def stft_spectrum(y: np.ndarray, sr: int, hop_seconds: float) -> tuple[np.ndarray, int, int]:
    """Magnitude STFT sized for the octave check."""
    import librosa

    hop = max(1, round(hop_seconds * sr))
    stft = np.abs(librosa.stft(np.asarray(y, dtype=np.float32), n_fft=STFT_NFFT, hop_length=hop))
    return stft, sr, STFT_NFFT


def spectral_level(stft: np.ndarray, sr: int, n_fft: int, freq: float, frames: np.ndarray) -> float:
    """Mean STFT amplitude at ``freq`` (linearly interpolated) over ``frames``."""
    bins = stft.shape[0]
    frames = frames[frames < stft.shape[1]]
    if frames.size == 0:
        return 0.0
    position = np.clip(freq * n_fft / sr, 0, bins - 1.001)
    lo = position.astype(int)
    frac = position - lo
    hi = np.minimum(lo + 1, bins - 1)
    return float((stft[lo, frames] * (1 - frac) + stft[hi, frames] * frac).mean())


def cell_octave_lift(
    stft: np.ndarray,
    sr: int,
    n_fft: int,
    f0_median: float,
    frames: np.ndarray,
    config: VocalMelodyConfig,
) -> bool:
    """Decide, for one cell, whether the detector locked an octave low.

    All-or-nothing per cell, because a frame-by-frame decision on a sustained note
    bimodally split one note into two octaves. Two hypotheses for the harmonic
    series:

        A: f0 is the fundamental  -> energy at f0, 2f0, 3f0, 4f0
        B: 2*f0 is the fundamental -> energy at 2f0, 4f0, 6f0

    Lift when B clearly wins, AND the reported f0 is weak against its double (a
    strong f0 means the detector was already right), AND the lift target stays under
    the cap.
    """
    if not np.isfinite(f0_median) or f0_median <= 0 or frames.size == 0:
        return False
    m1 = spectral_level(stft, sr, n_fft, f0_median, frames)
    m2 = spectral_level(stft, sr, n_fft, 2.0 * f0_median, frames)
    m3 = spectral_level(stft, sr, n_fft, 3.0 * f0_median, frames)
    m4 = spectral_level(stft, sr, n_fft, 4.0 * f0_median, frames)
    m6 = spectral_level(stft, sr, n_fft, 6.0 * f0_median, frames)
    series_a = m1 + m2 + m3 + m4
    series_b = m2 + m4 + m6
    return bool(
        series_b > series_a * config.octave_thr
        and m1 < config.octave_gate * m2
        and 2.0 * f0_median < config.octave_cap_hz
    )


def fold_low_outliers(cell_note: Mapping[int, int], fold_cap: int) -> tuple[dict[int, int], int]:
    """Fold a gross low outlier up an octave; never fold a high note down.

    A cell more than a major seventh below both neighbours is subharmonic residue
    (the detector heard the half-frequency), and one octave up it is closer to the
    neighbours - so it goes up. A note an octave *above* its neighbours is a genuine
    peak and is never touched. The cap stops the fold from inventing a note above the
    top of the instrument's real range for the song.
    """
    out = dict(cell_note)
    folded = 0
    keys = sorted(out)
    position = {k: i for i, k in enumerate(keys)}
    for key in keys:
        i = position[key]
        neighbours: list[int] = []
        if i > 0:
            neighbours.append(out[keys[i - 1]])
        if i + 1 < len(keys):
            neighbours.append(out[keys[i + 1]])
        if len(neighbours) < 2:
            continue
        centre = float(np.median(neighbours))
        if out[key] < centre - 10:
            candidate = out[key] + 12
            if candidate <= fold_cap and abs(candidate - centre) < abs(out[key] - centre):
                out[key] = candidate
                folded += 1
    return out, folded


def _word_of_cell(
    k: int, phase_seconds: float, cell: float, words: Sequence[tuple[float, float]]
) -> int:
    """The word (by index) that owns most of cell k, or -1 for none."""
    if not words:
        return -1
    a, b = phase_seconds + k * cell, phase_seconds + (k + 1) * cell
    best, best_overlap = -1, 0.0
    for index, (w_start, w_end) in enumerate(words):
        overlap = min(b, w_end) - max(a, w_start)
        if overlap > best_overlap:
            best, best_overlap = index, overlap
    return best if best_overlap >= 0.3 * cell else -1


def form_notes(
    cell_note: Mapping[int, int],
    phase_seconds: float,
    cell: float,
    *,
    words: Sequence[tuple[float, float]] = (),
    merge_same_pitch: bool = True,
) -> list[dict[str, int]]:
    """Adjacent same-pitch cells become one sustained note.

    A pitch change always splits. A word boundary splits only when
    ``merge_same_pitch`` is off (the v7 rule); on by default, because a note sung
    across two syllables is one note on the piano and splitting it read as
    "一拍一个音".
    """
    notes: list[dict[str, int]] = []
    for k in sorted(cell_note):
        pitch = cell_note[k]
        word = _word_of_cell(k, phase_seconds, cell, words)
        if (
            notes
            and notes[-1]["p"] == pitch
            and notes[-1]["ke"] == k
            and (merge_same_pitch or notes[-1]["w"] == word)
        ):
            notes[-1]["ke"] = k + 1
        else:
            notes.append({"ks": k, "ke": k + 1, "p": pitch, "w": word})
    return notes


def resolve_phase(y: np.ndarray, sr: int, config: VocalMelodyConfig) -> float:
    """The grid phase: explicit > reference-MIDI anchor > onset-energy search."""
    cell = 60.0 / config.tempo_bpm / config.cell_division
    if config.phase_seconds is not None:
        return float(config.phase_seconds)
    if config.reference_midi is not None:
        return grid_phase(config.reference_midi, cell, config.tempo_bpm)
    import librosa

    from services.arrangement.grid import ONSET_HOP, estimate_phase

    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=ONSET_HOP)
    phase = estimate_phase(onset, cell, len(y) / sr, hop_length=ONSET_HOP)
    logger.info("grid phase estimated from onset energy: %.3f s", phase)
    return phase


def build_melody(
    y: np.ndarray,
    sr: int,
    f0: np.ndarray,
    hop_seconds: float,
    config: VocalMelodyConfig,
    *,
    words: Sequence[tuple[float, float]] = (),
    phase_seconds: float,
) -> VocalMelody:
    """The full pure pipeline: waveform + F0 track -> melody. No torch required."""
    duration = config.duration if config.duration else len(y) / sr
    cell = 60.0 / config.tempo_bpm / config.cell_division

    f0 = np.asarray(f0, dtype=float)
    voiced = np.isfinite(f0) & (f0 > 0)
    frame_times = np.arange(f0.size) * hop_seconds
    semitones = np.full(f0.size, np.nan)
    semitones[voiced] = 69.0 + 12.0 * np.log2(f0[voiced] / 440.0)

    stft, stft_sr, n_fft = stft_spectrum(y, sr, hop_seconds)
    envelope = np.abs(y)
    peak99 = float(np.percentile(envelope, 99.5)) if envelope.size else 1.0
    amplitude_threshold = config.amp_ratio * peak99

    raw: dict[int, float] = {}
    lifted = 0
    for k, a, b in cell_windows(duration, config.tempo_bpm, config.cell_division, phase_seconds):
        i0 = int(np.searchsorted(frame_times, a, side="left"))
        i1 = int(np.searchsorted(frame_times, b, side="left"))
        if i1 <= i0:
            continue
        frac_voiced = float(np.mean(voiced[i0:i1]))
        j0, j1 = int(a * sr), int(min(b, len(y) / sr) * sr)
        amp = float(envelope[j0:j1].max()) if j1 > j0 else 0.0
        cell_semitones = semitones[i0:i1]
        cell_semitones = cell_semitones[np.isfinite(cell_semitones)]
        median = float(np.median(cell_semitones)) if cell_semitones.size else None
        if amp > amplitude_threshold and frac_voiced > config.voiced_frac and median is not None:
            f0_median = 440.0 * 2.0 ** ((median - 69.0) / 12.0)
            lift = cell_octave_lift(stft, stft_sr, n_fft, f0_median, np.arange(i0, i1), config)
            if lift:
                lifted += 1
            raw[k] = median + (12.0 if lift else 0.0)

    cell_note = {
        k: fit_to_scale(pitch, config.tonic_pc, config.key_mode) for k, pitch in raw.items()
    }
    cell_note, folded = fold_low_outliers(cell_note, config.fold_cap)
    runs = form_notes(
        cell_note,
        phase_seconds,
        cell,
        words=words,
        merge_same_pitch=config.merge_same_pitch,
    )

    notes: list[MelodyNote] = []
    for run in runs:
        start = phase_seconds + run["ks"] * cell
        end = phase_seconds + run["ke"] * cell
        j0, j1 = int(start * sr), int(min(end, len(y) / sr) * sr)
        segment = envelope[j0:j1]
        amp = float(np.percentile(segment, 85)) if segment.size else 0.0
        velocity = int(
            np.clip(
                config.vel_min + (config.vel_max - config.vel_min) * (amp / max(peak99, 1e-9)),
                config.vel_min,
                config.vel_max,
            )
        )
        notes.append(
            MelodyNote(
                start=round(start, 4), end=round(end, 4), midi=int(run["p"]), velocity=velocity
            )
        )

    pitches = [n.midi for n in notes]
    scale = scale_pcs(config.tonic_pc, config.key_mode)
    stats: dict[str, object] = {
        "cells": len(raw),
        "octaveLifted": lifted,
        "lowOutliersFolded": folded,
        "notes": len(notes),
        "pitchMin": min(pitches) if pitches else 0,
        "pitchMax": max(pitches) if pitches else 0,
        "outOfKey": sum(1 for p in pitches if p % 12 not in scale),
        "durationSeconds": round(float(duration), 3),
    }
    logger.info(
        "vocal melody: %d cells (%d octave-lifted, %d folded) -> %d notes, range %s",
        len(raw),
        lifted,
        folded,
        len(notes),
        stats["pitchMin"],
    )
    return VocalMelody(
        notes=tuple(notes),
        tempo_bpm=config.tempo_bpm,
        phase_seconds=phase_seconds,
        cell_seconds=cell,
        tonic_pc=config.tonic_pc % 12,
        key_mode=config.key_mode,
        stats=stats,
    )


# --------------------------------------------------------------------- ML facade


def _worker_python(explicit: str | None) -> Path:
    """Locate the interpreter that has torch + faster-whisper, the way transkun does."""
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise ValidationError(
            f"the configured ML interpreter does not exist: {candidate}",
            details={
                "hint": "set PIANOFLOW_ML_PYTHON to a python that has torch and faster-whisper"
            },
        )
    from_env = os.environ.get("PIANOFLOW_ML_PYTHON")
    if from_env:
        candidate = Path(from_env)
        if candidate.is_file():
            return candidate
        raise ValidationError(
            f"PIANOFLOW_ML_PYTHON points at nothing: {candidate}",
            details={"hint": "unset it, or point it at a python that has torch and faster-whisper"},
        )
    for candidate in DEFAULT_PYTHON_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise ValidationError(
        "no ML environment with torch and faster-whisper was found",
        details={"hint": "set PIANOFLOW_ML_PYTHON to the pop2piano environment's python"},
    )


def _parse_worker_output(text: str) -> tuple[np.ndarray, float, tuple[tuple[float, float], ...]]:
    """The worker's JSON -> (f0, hop_seconds, word spans), refusing a malformed payload.

    The worker runs under a different interpreter, so its output is untrusted data
    rather than a typed return: a missing or wrong-shaped field fails here with a
    reason, not as a KeyError two steps into the pipeline.
    """
    try:
        payload = json.loads(text)
        f0_values = payload["f0"]
        hop_seconds = float(payload["hop"])
        words_value = payload.get("words", [])
    except (TypeError, KeyError, ValueError) as exc:
        raise ValidationError(
            "the worker output is not a valid F0 payload", details={"reason": str(exc)}
        ) from exc
    f0 = np.asarray(f0_values, dtype=float)
    if f0.ndim != 1 or f0.size == 0 or not np.isfinite(hop_seconds) or hop_seconds <= 0:
        raise ValidationError(
            "the worker output carries no usable F0 track",
            details={"frames": int(f0.size), "hop": hop_seconds},
        )
    words: list[tuple[float, float]] = []
    for span in words_value:
        if isinstance(span, (list, tuple)) and len(span) == 2:
            words.append((float(span[0]), float(span[1])))
    return f0, hop_seconds, tuple(words)


class VocalMelodyExtractor:
    """RMVPE F0 + Whisper word spans out-of-process, then the pure pipeline here.

    Blocking and CPU bound, and it costs a subprocess: call it through
    ``asyncio.to_thread`` like the other transcribers.
    """

    def __init__(
        self,
        python_executable: str | None = None,
        *,
        whisper_model: str = "small",
        whisper_prompt: str | None = None,
        device: str = "cpu",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._python = _worker_python(python_executable)
        self._whisper_model = whisper_model
        self._whisper_prompt = whisper_prompt
        self._device = device
        self._timeout = timeout

    @classmethod
    def is_available(cls, python_executable: str | None = None) -> bool:
        """Whether a usable interpreter and worker were found; degrade instead of fail."""
        try:
            _worker_python(python_executable)
        except ValidationError:
            return False
        return WORKER.is_file()

    def extract(self, audio_path: Path, config: VocalMelodyConfig) -> VocalMelody:
        """Extract the melody from a vocal stem."""
        import librosa

        path = Path(audio_path)
        if not path.is_file():
            raise ValidationError(f"vocal stem not found: {path}")
        f0, hop_seconds, words = self._run_worker(path)
        y, sr = librosa.load(str(path), sr=PIPELINE_SAMPLE_RATE, mono=True)
        if y.size == 0:
            raise ValidationError("the vocal stem is empty")
        phase = resolve_phase(y, int(sr), config)
        return build_melody(
            y,
            int(sr),
            f0,
            hop_seconds,
            config,
            words=words,
            phase_seconds=phase,
        )

    def _run_worker(
        self, audio_path: Path
    ) -> tuple[np.ndarray, float, tuple[tuple[float, float], ...]]:
        with tempfile.TemporaryDirectory(prefix="pianoflow-vocal-melody-") as tmp:
            out_path = Path(tmp) / "frames.json"
            command = [
                str(self._python),
                str(WORKER),
                str(audio_path),
                str(out_path),
                "--whisper-model",
                self._whisper_model,
                "--device",
                self._device,
            ]
            if self._whisper_prompt:
                command += ["--prompt", self._whisper_prompt]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode != 0 or not out_path.is_file():
                detail = (completed.stderr or completed.stdout or "").strip()
                logger.error("vocal melody worker failed (%s): %s", completed.returncode, detail)
                raise ValidationError(
                    "the vocal melody worker failed",
                    details={"reason": detail[-800:] if detail else "no output"},
                )
            return _parse_worker_output(out_path.read_text(encoding="utf-8"))


__all__ = [
    "DEFAULT_PYTHON_CANDIDATES",
    "DEFAULT_TIMEOUT_SECONDS",
    "PIPELINE_SAMPLE_RATE",
    "SCALES",
    "STFT_NFFT",
    "WORKER",
    "MelodyNote",
    "VocalMelody",
    "VocalMelodyConfig",
    "VocalMelodyExtractor",
    "build_melody",
    "cell_octave_lift",
    "cell_windows",
    "fit_to_scale",
    "fold_low_outliers",
    "form_notes",
    "grid_phase",
    "resolve_phase",
    "scale_pcs",
    "spectral_level",
    "stft_spectrum",
]
