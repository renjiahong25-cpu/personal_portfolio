"""Polyphonic transcription with the basic-pitch ICASSP 2022 ONNX graph.

Why this file exists instead of ``pip install basic-pitch``
----------------------------------------------------------
Every published basic-pitch release (up to 0.4.0) hard-depends on
``tensorflow<2.15.1`` on Windows + Python >= 3.11, which we refuse to install.
The parts we actually need -- onset inference, frequency masking, polyphonic
note grouping and frame->time mapping -- are ~200 lines of pure numpy/scipy.

Provenance and licence
----------------------
Those functions are ported from Spotify's `basic-pitch`, which is licensed
under the Apache License 2.0. They are vendored rather than installed so that:

* we do not drag in TensorFlow,
* we do not depend on ``mir_eval`` / ``resampy`` / ``pretty_midi``, which the
  upstream module imports at module level but only uses for sonification and
  MIDI export (we generate both ourselves),
* our code does not break when upstream reorganises its package layout.

Graph contract (verified against models/basic_pitch/nmp.onnx, 225 KB)
---------------------------------------------------------------------
* input : float32 ``[batch, 43844, 1]`` -- raw mono waveform at **22050 Hz**,
  a 2 second window (``22050 * 2 - FFT_HOP``), so the constant-Q transform is
  computed *inside* the graph. That is also why the file is only 225 KB.
* output: three heads, ``[batch, 172, 88]``, ``[batch, 172, 88]`` and
  ``[batch, 172, 264]``. 172 = ``(22050 // 256) * 2`` frames; 264 = 88 semitone
  bins x 3 (the pitch-contour head, which we do not need because we ignore
  pitch bends).

Accuracy caveat
---------------
This is an instrument-agnostic multi-pitch model trained on piano/synth/guitar.
It transcribes whatever it hears, including the vocal line in a 弹唱 track. It
is a starting point for notation, not a scholarly edition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import scipy.signal

from core.errors import NotFoundError, ValidationError
from core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

MODEL_PATH = PROJECT_ROOT / "models" / "basic_pitch" / "nmp.onnx"

# --- graph contract -------------------------------------------------------
AUDIO_SAMPLE_RATE = 22050
FFT_HOP = 256
AUDIO_WINDOW_SECONDS = 2
ANNOT_N_FRAMES = (AUDIO_SAMPLE_RATE // FFT_HOP) * AUDIO_WINDOW_SECONDS  # 172
AUDIO_N_SAMPLES = AUDIO_SAMPLE_RATE * AUDIO_WINDOW_SECONDS - FFT_HOP  # 43844
N_FREQ_BINS = 88
#: Bin 0 is A0, i.e. MIDI 21.
MIDI_OFFSET = 21
BASE_FREQUENCY = 27.5
FRAME_SECONDS = FFT_HOP / AUDIO_SAMPLE_RATE

# --- note grouping (ported from basic-pitch) ------------------------------
DEFAULT_OVERLAPPING_FRAMES = 30
DEFAULT_MIN_NOTE_LEN = 11
ENERGY_TOLERANCE = 11
DEFAULT_ONSET_THRESHOLD = 0.5
# Raised from the upstream 0.3. Measured on a 30 s piano cover (星辰大海):
#   frame_thresh 0.3 -> 199 notes, 77 overlapping octave pairs, 50 weak notes
#   frame_thresh 0.4 -> 180 notes, 46 overlapping octave pairs, 14 weak notes
# The octave ghosts come from harmonics leaking into the neighbouring bin, and a
# phantom voice is far more damaging to a piano score than a missed inner note.
DEFAULT_FRAME_THRESHOLD = 0.4
MAGIC_ALIGNMENT_OFFSET = 0.0018
MAX_FREQ_IDX = 87

#: The melodia trick recovers notes that have no onset of their own, but on our
#: test signal it only ever added phantom octave duplicates (D6, C2 for a C-E-G
#: triad). Off by default; kept because it is the only path to sustained
#: legato notes in some material.
DEFAULT_USE_MELODIA_TRICK = False


@dataclass(frozen=True)
class NoteEvent:
    """One transcribed note. ``amplitude`` is 0..1, mapped to velocity later."""

    start: float
    duration: float
    midi: int
    amplitude: float = 1.0

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict[str, Any]:
        from .models import midi_to_pitch

        return {
            "start": round(self.start, 4),
            "duration": round(self.duration, 4),
            "midi": self.midi,
            "pitch": midi_to_pitch(self.midi),
            "amplitude": round(self.amplitude, 4),
        }


def infer_onsets_from_frames(onsets: np.ndarray, frames: np.ndarray, n_diff: int = 2) -> np.ndarray:
    """Add onsets where frame activations rise sharply.

    The model sometimes misses the attack of a note that was already sounding;
    a sudden increase in frame energy is a good proxy.
    """
    diffs = []
    for step in range(1, n_diff + 1):
        padded = np.concatenate([np.zeros((step, frames.shape[1])), frames])
        diffs.append(padded[step:, :] - padded[:-step, :])
    frame_diff = np.min(diffs, axis=0)
    frame_diff[frame_diff < 0] = 0
    frame_diff[:n_diff, :] = 0
    peak = frame_diff.max()
    if peak > 0:
        # Rescale to the onset head's own maximum. When the model reports no
        # onsets at all the divisor would be zero and silently erase every
        # inferred onset, so fall back to the frame peak instead.
        divisor = float(onsets.max())
        if divisor <= 0:
            divisor = float(peak)
        frame_diff = divisor * frame_diff / peak
    return np.max([onsets, frame_diff], axis=0)


def mask_frequency_range(
    onsets: np.ndarray,
    frames: np.ndarray,
    min_freq: float | None,
    max_freq: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Zero out activations outside the requested frequency range."""
    import librosa

    n_freqs = onsets.shape[1]
    low, high = 0, n_freqs
    if min_freq is not None:
        low = int(np.round(librosa.hz_to_midi(min_freq) - MIDI_OFFSET))
    if max_freq is not None:
        high = int(np.round(librosa.hz_to_midi(max_freq) - MIDI_OFFSET))
    onsets[:, :low] = 0
    frames[:, :low] = 0
    onsets[:, high:] = 0
    frames[:, high:] = 0
    return onsets, frames


def extract_polyphonic_notes(
    frames: np.ndarray,
    onsets: np.ndarray,
    *,
    onset_threshold: float = DEFAULT_ONSET_THRESHOLD,
    frame_threshold: float = DEFAULT_FRAME_THRESHOLD,
    min_note_len: int = DEFAULT_MIN_NOTE_LEN,
    energy_tol: int = ENERGY_TOLERANCE,
    do_infer_onsets: bool = True,
    min_freq: float | None = None,
    max_freq: float | None = None,
    use_melodia_trick: bool = DEFAULT_USE_MELODIA_TRICK,
) -> list[tuple[int, int, int, float]]:
    """Group frame activations into ``(start_frame, end_frame, midi, amplitude)``.

    Two passes: first every predicted onset is tracked until its energy decays,
    then the "melodia trick" recovers notes that had no onset of their own by
    consuming the strongest remaining energy.
    """
    n_frames = frames.shape[0]
    onsets, frames = mask_frequency_range(onsets, frames, min_freq, max_freq)
    if do_infer_onsets:
        onsets = infer_onsets_from_frames(onsets, frames)

    peaks = scipy.signal.argrelmax(onsets, axis=0)
    peak_matrix = np.zeros(onsets.shape)
    peak_matrix[peaks] = onsets[peaks]
    onset_index = np.where(peak_matrix >= onset_threshold)

    # Walk time backwards so the strongest/later onsets win contested energy.
    onset_times = onset_index[0][::-1]
    onset_freqs = onset_index[1][::-1]

    remaining = frames.copy()
    notes: list[tuple[int, int, int, float]] = []

    def track(start: int, freq: int) -> tuple[int, int] | None:
        index = start + 1
        below = 0
        while index < n_frames - 1 and below < energy_tol:
            if remaining[index, freq] < frame_threshold:
                below += 1
            else:
                below = 0
            index += 1
        return start, index - below

    # numpy integer indices are used throughout the ported loops; coercing them
    # keeps numpy scalars from leaking into the returned note tuples.
    for start_value, freq_value in zip(onset_times, onset_freqs, strict=True):
        start = int(start_value)
        freq = int(freq_value)
        if start >= n_frames - 1:
            continue
        span = track(start, freq)
        if span is None:
            continue
        begin, end = span
        if end - begin <= min_note_len:
            continue
        remaining[begin:end, freq] = 0
        # Suppress the neighbouring bins: harmonics leak into them.
        if freq < MAX_FREQ_IDX:
            remaining[begin:end, freq + 1] = 0
        if freq > 0:
            remaining[begin:end, freq - 1] = 0
        notes.append((begin, end, freq + MIDI_OFFSET, float(np.mean(frames[begin:end, freq]))))

    if use_melodia_trick:
        while remaining.size and remaining.max() > frame_threshold:
            mid_value, freq_value = np.unravel_index(int(np.argmax(remaining)), remaining.shape)
            mid = int(mid_value)
            freq = int(freq_value)
            remaining[mid, freq] = 0

            forward = mid + 1
            below = 0
            while forward < n_frames - 1 and below < energy_tol:
                if remaining[forward, freq] < frame_threshold:
                    below += 1
                else:
                    below = 0
                remaining[forward, freq] = 0
                if freq < MAX_FREQ_IDX:
                    remaining[forward, freq + 1] = 0
                if freq > 0:
                    remaining[forward, freq - 1] = 0
                forward += 1
            end = forward - 1 - below

            backward = mid - 1
            below = 0
            while backward > 0 and below < energy_tol:
                if remaining[backward, freq] < frame_threshold:
                    below += 1
                else:
                    below = 0
                remaining[backward, freq] = 0
                if freq < MAX_FREQ_IDX:
                    remaining[backward, freq + 1] = 0
                if freq > 0:
                    remaining[backward, freq - 1] = 0
                backward -= 1
            begin = backward + 1 + below

            if end - begin <= min_note_len:
                continue
            notes.append((begin, end, freq + MIDI_OFFSET, float(np.mean(frames[begin:end, freq]))))

    return sorted(notes)


#: A phantom octave voice is reported far below the amplitude of the real note
#: (measured 0.37-0.43 against 0.56-0.71 on a piano cover). A genuine octave
#: doubling has both notes at similar amplitude, so the ratio is the test.
OCTAVE_GHOST_RATIO = 0.6
OCTAVE_INTERVALS = (12, 24)


def suppress_octave_ghosts(
    events: list[NoteEvent],
    *,
    ratio: float = OCTAVE_GHOST_RATIO,
) -> tuple[list[NoteEvent], int]:
    """Drop the weaker of two overlapping notes exactly an octave apart.

    Harmonics leak into the neighbouring frequency bin, which the model reports
    as a phantom voice an octave away. Deleting *every* octave pair would be
    wrong: octave doubling is ordinary piano writing, so a pair is only treated
    as a ghost when one side is clearly quieter than the other.

    Returns the surviving notes and how many were removed.
    """
    if not events or ratio <= 0:
        return list(events), 0

    ordered = sorted(events, key=lambda note: (note.start, note.midi))
    doomed: set[int] = set()
    for index, note in enumerate(ordered):
        if index in doomed:
            continue
        for other_index in range(index + 1, len(ordered)):
            other = ordered[other_index]
            if other.start >= note.end:
                break
            gap = abs(other.midi - note.midi)
            if gap not in OCTAVE_INTERVALS:
                continue
            if note.amplitude < ratio * other.amplitude:
                doomed.add(index)
                break
            if other.amplitude < ratio * note.amplitude:
                doomed.add(other_index)

    survivors = [note for index, note in enumerate(ordered) if index not in doomed]
    if doomed:
        logger.info("octave ghost suppression removed %d note(s)", len(doomed))
    return survivors, len(doomed)


def frame_index_to_seconds(n_frames: int) -> np.ndarray:
    import librosa

    original = librosa.core.frames_to_time(
        np.arange(n_frames), sr=AUDIO_SAMPLE_RATE, hop_length=FFT_HOP
    )
    window_numbers = np.floor(np.arange(n_frames) / ANNOT_N_FRAMES)
    window_offset = (FFT_HOP / AUDIO_SAMPLE_RATE) * (
        ANNOT_N_FRAMES - (AUDIO_N_SAMPLES / FFT_HOP)
    ) + MAGIC_ALIGNMENT_OFFSET
    return original - (window_offset * window_numbers)


@runtime_checkable
class Transcriber(Protocol):
    """What the score pipeline needs from a model that turns audio into notes.

    A protocol rather than a base class because the implementations share nothing:
    :class:`BasicPitchTranscriber` runs an ONNX graph in this process, while
    :class:`services.audio_analysis.transkun.TranskunTranscriber` shells out to a
    separate environment because that is where torch lives. The pipeline only ever calls
    ``transcribe_array``, so that is the whole contract - and being able to say "anything
    satisfying this" is what lets a third model be added without touching the pipeline.

    Both are blocking and CPU bound; callers must use ``asyncio.to_thread``.
    """

    def transcribe_array(self, y: np.ndarray, sr: int) -> list[NoteEvent]: ...


class BasicPitchTranscriber:
    """Runs the ONNX graph over a waveform and returns note events.

    Blocking and CPU bound: call it through ``asyncio.to_thread``.
    """

    def __init__(
        self,
        model_path: Path | None = None,
        *,
        onset_threshold: float = DEFAULT_ONSET_THRESHOLD,
        frame_threshold: float = DEFAULT_FRAME_THRESHOLD,
        min_note_len: int = DEFAULT_MIN_NOTE_LEN,
        energy_tol: int = ENERGY_TOLERANCE,
        overlapping_frames: int = DEFAULT_OVERLAPPING_FRAMES,
        batch_size: int = 16,
        min_freq: float | None = None,
        max_freq: float | None = None,
        use_melodia_trick: bool = DEFAULT_USE_MELODIA_TRICK,
        octave_ghost_ratio: float | None = OCTAVE_GHOST_RATIO,
    ) -> None:
        self._path = Path(model_path or MODEL_PATH)
        self._onset_threshold = onset_threshold
        self._frame_threshold = frame_threshold
        self._min_note_len = min_note_len
        self._energy_tol = energy_tol
        self._overlapping_frames = overlapping_frames
        self._batch_size = batch_size
        self._min_freq = min_freq
        self._max_freq = max_freq
        self._use_melodia_trick = use_melodia_trick
        self._octave_ghost_ratio = octave_ghost_ratio
        self._session: Any = None
        self._head_indices: tuple[int, int] | None = None

    # ------------------------------------------------------------- loading
    def load(self) -> Any:
        if self._session is not None:
            return self._session
        if not self._path.is_file():
            raise NotFoundError(
                f"basic-pitch ONNX model missing: {self._path.name}. "
                "Run: python backend_python/scripts/fetch_models.py"
            )
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ValidationError("onnxruntime is required for polyphonic transcription") from exc

        options = ort.SessionOptions()
        options.intra_op_num_threads = 0  # let onnxruntime decide
        self._session = ort.InferenceSession(
            str(self._path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        input_meta = self._session.get_inputs()[0]
        expected = int(input_meta.shape[1]) if isinstance(input_meta.shape[1], int) else -1
        if expected not in (-1, AUDIO_N_SAMPLES):
            raise ValidationError(
                f"unexpected model window size {expected}, expected {AUDIO_N_SAMPLES}"
            )
        logger.info("basic-pitch model loaded: %s", self._path.name)
        return self._session

    @property
    def input_name(self) -> str:
        return self.load().get_inputs()[0].name

    # ---------------------------------------------------------- inference
    def _windows(self, y: np.ndarray) -> np.ndarray:
        stride = (ANNOT_N_FRAMES - self._overlapping_frames) * FFT_HOP
        usable = max(0, len(y) - AUDIO_N_SAMPLES)
        count = usable // stride + 1
        windows = np.zeros((count, AUDIO_N_SAMPLES, 1), dtype=np.float32)
        for index in range(count):
            begin = index * stride
            chunk = y[begin : begin + AUDIO_N_SAMPLES]
            windows[index, : len(chunk), 0] = chunk
        return windows

    def _run_graph(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        session = self.load()
        name = self.input_name
        # Collect every 88-bin head from every batch, flattened to (frames, 88).
        collected: list[np.ndarray] = []
        for start in range(0, len(windows), self._batch_size):
            batch = windows[start : start + self._batch_size]
            outputs = session.run(None, {name: batch})
            for output in outputs:
                array = np.asarray(output, dtype=np.float32)
                if array.ndim != 3 or array.shape[2] != N_FREQ_BINS:
                    continue  # the 3x88 contour head, which we do not need
                collected.append(array.reshape(-1, N_FREQ_BINS))
        if len(collected) < 2:
            raise ValidationError("model did not return two 88-bin heads")

        first = np.concatenate(collected[0::2], axis=0)
        second = np.concatenate(collected[1::2], axis=0)

        # The onset head is sparse and transient; the note head is sustained.
        # Identifying them by sparsity avoids hard-coding an output order that
        # the exporter could change.
        if _sparsity(second) < _sparsity(first):
            notes_head, onsets_head = first, second
        else:
            notes_head, onsets_head = second, first
        logger.info(
            "heads identified: notes sparsity=%.4f onsets sparsity=%.4f",
            _sparsity(notes_head),
            _sparsity(onsets_head),
        )
        return notes_head, onsets_head

    def _stitch(self, head: np.ndarray, n_windows: int) -> np.ndarray:
        """Overlap-add: keep every window but drop the repeated tail frames."""
        keep = self._overlapping_frames
        pieces = [head[:ANNOT_N_FRAMES]]
        for index in range(1, n_windows):
            begin = index * ANNOT_N_FRAMES + keep
            end = (index + 1) * ANNOT_N_FRAMES
            pieces.append(head[begin:end])
        return np.concatenate(pieces, axis=0)

    def transcribe_array(self, y: np.ndarray, sr: int) -> list[NoteEvent]:
        """Transcribe a mono waveform. Resamples to 22050 Hz when needed."""
        import librosa

        if sr != AUDIO_SAMPLE_RATE:
            y = librosa.resample(y, orig_sr=sr, target_sr=AUDIO_SAMPLE_RATE)
        if y.size < AUDIO_N_SAMPLES:
            raise ValidationError(
                f"audio too short for transcription: {y.size / AUDIO_SAMPLE_RATE:.2f}s"
            )

        windows = self._windows(np.asarray(y, dtype=np.float32))
        note_head, onset_head = self._run_graph(windows)

        frames = self._stitch(note_head, len(windows))
        onsets = self._stitch(onset_head, len(windows))

        raw = extract_polyphonic_notes(
            frames,
            onsets,
            onset_threshold=self._onset_threshold,
            frame_threshold=self._frame_threshold,
            min_note_len=self._min_note_len,
            energy_tol=self._energy_tol,
            do_infer_onsets=True,
            min_freq=self._min_freq,
            max_freq=self._max_freq,
            use_melodia_trick=self._use_melodia_trick,
        )
        times = frame_index_to_seconds(frames.shape[0])
        events = [
            NoteEvent(
                start=float(times[begin]),
                duration=float(max(0.0, times[min(end, len(times) - 1)] - times[begin])),
                midi=midi,
                amplitude=amplitude,
            )
            for begin, end, midi, amplitude in raw
            if 0 <= begin < len(times)
        ]
        events.sort(key=lambda note: (note.start, note.midi))
        if self._octave_ghost_ratio is not None:
            events, removed = suppress_octave_ghosts(events, ratio=self._octave_ghost_ratio)
            logger.info(
                "transcribed %d notes (%d octave ghosts removed) in %.1fs of audio",
                len(events),
                removed,
                len(y) / AUDIO_SAMPLE_RATE,
            )
        return events

    def transcribe_file(self, path: Path) -> list[NoteEvent]:
        import librosa

        y, sr = librosa.load(str(path), sr=AUDIO_SAMPLE_RATE, mono=True)
        if y.size == 0:
            raise ValidationError("audio file is empty")
        return self.transcribe_array(y, int(sr))


def _sparsity(head: np.ndarray) -> float:
    if head.size == 0:
        return 0.0
    return float(np.mean(head > 0.5))


__all__ = [
    "ANNOT_N_FRAMES",
    "AUDIO_N_SAMPLES",
    "AUDIO_SAMPLE_RATE",
    "FFT_HOP",
    "MODEL_PATH",
    "OCTAVE_GHOST_RATIO",
    "BasicPitchTranscriber",
    "NoteEvent",
    "Transcriber",
    "extract_polyphonic_notes",
    "frame_index_to_seconds",
    "infer_onsets_from_frames",
    "mask_frequency_range",
    "suppress_octave_ghosts",
]
