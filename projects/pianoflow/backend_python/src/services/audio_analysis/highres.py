"""High-resolution piano transcription, with a real pedal curve.

Why this exists alongside basic-pitch
-------------------------------------
:mod:`services.audio_analysis.polyphony` runs basic-pitch: a 225 KB Onsets and Frames
model, committed to the repository, good enough to read a melody off a recording. Two
things it cannot do, and both are complaints that were measured rather than felt:

* **Offsets.** Onsets and Frames has no offset head at all. Every note's end is inferred
  from the frame activation decaying, which is why every note in the render used to
  sustain for exactly as long as the pedal held it - the median and the 90th percentile
  came out identical at 1.18 s. The source's are 0.81 s and 1.18 s.
* **Pedals.** There is no pedal model at all, so :func:`services.score.render._pedal_segments`
  guesses one press per bar. That is not a pedal, it is block pedalling on a metronome.

This module runs ByteDance's high-resolution model (Kong et al. 2020, onset F1 96.77 and
pedal F1 91.86 on MAESTRO), whose heads cover both. The weights are a third-party ONNX
export of an Apache-2.0 project and are *not* committed - see ``models/manifest.json``.

What this model is *not* good for, measured on this project's reference recording
---------------------------------------------------------------------------
Do not use its note offsets as they stand. ByteDance's rule ends a note when the frame
activation decays, amplified by the pedal so a held pedal holds the note. On
``cde3a16bbb536a15661e1f25efc98ca059dbde2c.wav`` (a heavily pedalled piece) that rule has
no signal to work on:

* ``pedal_frame`` has median 1.000, above threshold for 868 of the first 1201 frames.
* Individual note frames do not decay. Pitch 40 from 1.21 s reads 0.970, 0.991, 0.994,
  0.997 at 0, 0.5, 1.0 and 1.99 s - it rises.

With the damper permanently down, ``frame * 11`` never crosses 0.5, so every note ends at
:data:`OFFSET_SEARCH_FRAMES` and all 113 notes in the first 30 s come out at exactly
2.00 s, against basic-pitch's 0.44 s median and 0.91 s p90, and the source audio's
measured 0.81 s / 1.18 s. No threshold fixes a signal that does not decay.

What survives is the pedal curve, and only if it is kept separate from note length: the
curve is a fact about the recording, while a printed note's length is a fact about what
the pianist played. Sustain belongs in the renderer, not in the score. So
:func:`HighResTranscriber.transcribe` returns both, and callers are expected to keep the
measured per-note envelope for durations and use these segments for sustain - see
``services/audio_analysis/dynamics.py`` for the envelope this project already trusts.

What the model actually takes
------------------------------
Verified by running it, not read off a model card:

    input    waveform float32 [1, 160000]        10 s at 16 kHz, mono
    outputs  reg_onset / reg_offset / frame / velocity     [1, 1001, 88]
             reg_pedal_onset / reg_pedal_offset / pedal_frame  [1, 1001, 1]

1001 frames over 10 s is a 10 ms hop, and 88 bins is A0 to C8. Every output is already
sigmoid-activated, so no threshold is applied to a logit anywhere here. This particular
export carries a **velocity** head, which the 2020 paper's release does not.

One loading detail that is not optional: ORT's MatMul + BatchNorm fusion mis-infers a
rank-3 Gemm in this graph, so graph optimisation is disabled. With it on, the model runs
and returns quietly wrong numbers rather than failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.paths import PROJECT_ROOT
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: The model's own sample rate and window. Taken from the graph, verified by running it.
TARGET_SR = 16000
WINDOW_SAMPLES = 160000
HOP_SAMPLES = 160  # 10 ms, which is what 1001 frames over 10 s implies.
FRAME_RATE = TARGET_SR / HOP_SAMPLES  # 100.0 frames per second

#: Lowest and highest MIDI the graph has a bin for.
MIDI_LOW = 21
MIDI_HIGH = 108

#: Segment overlap in seconds. A note whose onset falls in the last few frames of a
#: window is truncated by the convolution's receptive field, so windows overlap and the
#: seam notes are kept from the window that sees them whole.
SEGMENT_MARGIN_SECONDS = 0.5

#: Detection thresholds. ByteDance's own post-processing values; the note model was
#: trained with them and they are not tuned per piece.
ONSET_THRESHOLD = 0.5
OFFSET_THRESHOLD = 0.5
FRAME_THRESHOLD = 0.5
VELOCITY_THRESHOLD = 0.1
PEDAL_ONSET_THRESHOLD = 0.5
PEDAL_FRAME_THRESHOLD = 0.5

#: Onsets closer together than this many frames are separate notes, not one note with a
#: smear. Without it a tremolo collapses into a single attack.
ONSET_SPLIT_FRAMES = 6
#: How far past an onset the offset search looks before giving up and holding the note.
#: ByteDance's own default is 200 frames - 2 s at this hop, longer than almost any note.
OFFSET_SEARCH_FRAMES = 200
#: Consecutive pedal onsets closer than this are one pedal event.
PEDAL_ONSET_SPLIT_FRAMES = 3

#: The pedal multiplies the note frame by this when looking for an offset. It is the
#: mechanism that makes a pedalled note sustain: the damper is up, so the frame stays
#: high and the offset is not found until the pedal lifts. This is why a guessed pedal
#: gives every note the same length and a real one does not.
PEDAL_AMPLIFY = 10.0

MODEL_PATH = PROJECT_ROOT / "models" / "bytedance" / "piano_transcription.onnx"


@dataclass
class HighResResult:
    """Notes, a pedal curve, and what the model actually did."""

    notes: list[NoteEvent] = field(default_factory=list)
    #: (down_seconds, up_seconds). The pedal curve of the recording, not a metronome.
    pedal_segments: list[tuple[float, float]] = field(default_factory=list)
    #: Per-note model velocity in 0..1, parallel to ``notes``. Reported rather than
    #: trusted: this project measures velocity from the audio, and a second opinion is
    #: only useful once the two have been compared.
    velocity: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "noteCount": len(self.notes),
            "pedalSegments": len(self.pedal_segments),
            "pedalSeconds": round(sum(max(0.0, up - down) for down, up in self.pedal_segments), 3),
            "velocityCorrelationWithModel": None,
        }


def _resample(y: np.ndarray, sr: int) -> np.ndarray:
    if sr == TARGET_SR:
        return y.astype(np.float32)
    import librosa

    return librosa.resample(
        y.astype(np.float32), orig_sr=sr, target_sr=TARGET_SR, res_type="soxr_hq"
    )


def _local_max(values: np.ndarray) -> np.ndarray:
    """A frame is a candidate only if it is the largest along axis 0 (time).

    Each pitch column is compared with its own neighbours; padding is zero, so a rising
    signal at the very first frame still counts as a local maximum.

    The last clause matters more than it looks. Comparing with ``>=`` on both sides makes
    every frame of a flat run a candidate - five zeros report five local maxima - and the
    amplitude threshold downstream would only mask it. Requiring a strict rise on at least
    one side makes a plateau zero candidates while still keeping an isolated peak and a
    signal that starts high.
    """
    padded = np.pad(values, [(1, 1)] + [(0, 0)] * (values.ndim - 1), mode="constant")
    left, right = padded[:-2], padded[2:]
    return (values >= left) & (values >= right) & ((values > left) | (values > right))


def _split_close(frames: np.ndarray, minimum_gap: int) -> list[int]:
    """Drop candidates that sit within ``minimum_gap`` of the one before them."""
    kept: list[int] = []
    for frame in frames:
        if kept and int(frame) - kept[-1] < minimum_gap:
            continue
        kept.append(int(frame))
    return kept


def _decode_pedal(
    pedal_onset: np.ndarray, pedal_offset: np.ndarray, pedal_frame: np.ndarray
) -> list[tuple[float, float]]:
    """Pedal down/up points, in seconds.

    The three pedal heads are three different questions, and reading only the first is
    what made an earlier attempt merge a whole phrase into one 10.9 s "pedal":

    * ``reg_pedal_onset``  - the pedal goes **down** here.
    * ``reg_pedal_offset`` - the pedal comes **up** here. Despite the name this is not a
      sub-frame refinement of the onset, it is the release head.
    * ``pedal_frame``      - the damper is down at this frame, used only as a fallback
      when a release was not detected at all.

    Getting the name wrong here is not cosmetic: with releases ignored, every pedal
    segment ran on until the next press, which made every note inside it hit the offset
    search cap and come out at exactly the same length.
    """
    frames = pedal_frame.shape[0]
    downs = _split_close(
        np.nonzero(_local_max(pedal_onset) & (pedal_onset >= PEDAL_ONSET_THRESHOLD))[0],
        PEDAL_ONSET_SPLIT_FRAMES,
    )
    ups = _split_close(
        np.nonzero(_local_max(pedal_offset) & (pedal_offset >= PEDAL_ONSET_THRESHOLD))[0],
        PEDAL_ONSET_SPLIT_FRAMES,
    )

    segments: list[tuple[float, float]] = []
    for start in downs:
        releases = [up for up in ups if up > start]
        if releases:
            end = releases[0]
        else:
            # The release head found nothing; ask the frame head when the damper lifted.
            below = np.nonzero(pedal_frame[start:, 0] < PEDAL_FRAME_THRESHOLD)[0]
            end = start + int(below[0]) if below.size else frames - 1
        if end > start:
            segments.append((start / FRAME_RATE, max(end, start + 1) / FRAME_RATE))
    return segments


def _decode_notes(
    reg_onset: np.ndarray,
    frame: np.ndarray,
    velocity: np.ndarray,
    pedal_segments: list[tuple[float, float]],
) -> list[tuple[int, int, int, int, float]]:
    """(start_frame, midi, end_frame, midi_velocity, model_velocity), unsorted.

    The offset rule is the whole point of this model and it is worth stating plainly.
    For each accepted onset the note lasts until the first frame where the note's frame
    activation falls below threshold - and that activation is multiplied by the pedal, so a
    held pedal holds the note. This reproduces ByteDance's own post-processing, including
    its search limit and its pedal amplification.
    """
    frames, pitches = frame.shape
    pedal = np.zeros((frames, 1), dtype=np.float32)
    for down, up in pedal_segments:
        start = max(0, round(float(down * FRAME_RATE)))
        end = min(frames, round(float(up * FRAME_RATE)))
        if end > start:
            pedal[start:end, 0] = 1.0
    scaled_frame = frame * (1.0 + pedal * PEDAL_AMPLIFY)

    onset_candidates = _local_max(reg_onset) & (reg_onset >= ONSET_THRESHOLD)
    active = frame >= FRAME_THRESHOLD

    notes: list[tuple[int, int, int, int, float]] = []
    capped = 0
    for pitch in range(pitches):
        found = np.nonzero(onset_candidates[:, pitch])[0]
        if not found.size:
            continue
        for start in _split_close(found, ONSET_SPLIT_FRAMES):
            if not active[start, pitch]:
                # An onset with no sustain behind it is noise, not a note.
                continue
            limit = min(start + OFFSET_SEARCH_FRAMES, frames)
            window = scaled_frame[start:limit, pitch]
            silent = np.nonzero(window < OFFSET_THRESHOLD)[0]
            if silent.size:
                end = start + int(silent[0])
            else:
                end = limit
                capped += 1

            # The two ``reg_`` heads are deliberately not used to move the note. Every
            # output in this graph is named ``Sigmoid*``, so these are probability-like
            # activations, not signed offsets; rounding one to a frame shift can only ever
            # push a note later, by 0 or 1 frames, and that bias is real (a head value of
            # 1.0 at the onset moved the note a frame late). Their whole scale is 10 ms,
            # an order of magnitude below the 27 ms grid error already in this
            # transcription. The argument names are kept for the same reason the dead
            # pedal-offset call site was: they are inputs, and dropping them silently
            # would hide that fact.
            end = max(end, start + 1)
            notes.append(
                (
                    start,
                    pitch + MIDI_LOW,
                    end,
                    round(float(velocity[start, pitch]) * 127),
                    float(velocity[start, pitch]),
                )
            )
    # Surfaced rather than swallowed: a high cap rate means the pedal curve is holding
    # notes open artificially, which flattens the whole duration distribution.
    logger.debug("offset search capped for %d of %d notes", capped, len(notes))
    return notes


#: Two pedal segments closer together than this are the same pedal seen by two windows.
PEDAL_MERGE_SECONDS = 0.2


def _merge_pedals(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort and fuse overlapping or touching pedal segments.

    Windows overlap by :data:`SEGMENT_MARGIN_SECONDS`, so a pedal that straddles a seam is
    decoded twice and the two copies arrive a frame apart. Without this they both survive
    and the curve reports the same press twice.
    """
    merged: list[tuple[float, float]] = []
    for down, up in sorted(segments):
        if merged and down <= merged[-1][1] + PEDAL_MERGE_SECONDS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], up))
        else:
            merged.append((down, up))
    return merged


def _dedupe(
    notes: list[tuple[int, int, int, int, float]],
) -> list[tuple[int, int, int, int, float]]:
    """One note per (midi, onset frame), keeping the longest.

    Windows overlap, so a note near a seam is decoded twice. The two copies differ only by
    a frame or two; keeping the longest end is the conservative choice, since a truncated
    note would otherwise be the one the player hears cut off early.
    """
    best: dict[tuple[int, int], tuple[int, int, int, float]] = {}
    for start, midi, end, vel, model_vel in notes:
        current = best.get((midi, start))
        if current is None or end > current[1]:
            best[(midi, start)] = (start, end, vel, model_vel)
    # The key carries (midi, start) and the value the rest, so the note has to be put
    # back together; sorting by the key also orders by pitch within a frame.
    return [
        (start, midi, end, vel, model_vel)
        for (midi, start), (start, end, vel, model_vel) in sorted(best.items())
    ]


class HighResTranscriber:
    """ByteDance's high-resolution transcription with pedals, on CPU."""

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime as ort

        self.path = Path(model_path or MODEL_PATH)
        if not self.path.is_file():
            raise FileNotFoundError(
                f"the high-resolution model is not installed at {self.path}; "
                "run backend_python/scripts/fetch_models.py --only bytedance"
            )
        options = ort.SessionOptions()
        # Not optional. ORT's MatMul + BatchNorm fusion mis-infers a rank-3 Gemm in this
        # graph: the model runs and returns quietly wrong numbers instead of failing.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        self.session = ort.InferenceSession(
            str(self.path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def _segment_starts(self, total_samples: int) -> list[int]:
        """Window starts with overlap, so a note at a seam is seen whole by one of them."""
        if total_samples <= WINDOW_SAMPLES:
            return [0]
        stride = WINDOW_SAMPLES - int(SEGMENT_MARGIN_SECONDS * TARGET_SR)
        starts = list(range(0, total_samples - WINDOW_SAMPLES + 1, stride))
        tail = total_samples - WINDOW_SAMPLES
        if starts[-1] != tail:
            starts.append(tail)
        return starts

    def transcribe(self, y: np.ndarray, sr: int) -> HighResResult:
        """Transcribe mono audio at any sample rate."""
        audio = _resample(np.asarray(y, dtype=np.float32).reshape(-1), sr)
        if audio.size < WINDOW_SAMPLES:
            audio = np.pad(audio, (0, WINDOW_SAMPLES - audio.size))

        decoded: list[tuple[int, int, int, int, float]] = []
        pedals: list[tuple[float, float]] = []

        for start_sample in self._segment_starts(audio.size):
            window = audio[start_sample : start_sample + WINDOW_SAMPLES]
            if window.size < WINDOW_SAMPLES:
                window = np.pad(window, (0, WINDOW_SAMPLES - window.size))
            outputs = self.session.run(None, {self.input_name: window[None, :]})
            # Every head comes back as [1, 1001, n]; the batch axis is dropped here so the
            # decoders below can index (frame, pitch) directly. ``reg_offset`` is unused -
            # see the note in _decode_notes - and named with a leading underscore so that
            # is visible at the call site rather than hidden.
            reg_onset, _reg_offset, frame, velocity, pedal_onset, pedal_offset, pedal_frame = (
                np.squeeze(np.asarray(o, dtype=np.float32), axis=0) for o in outputs
            )
            segment_pedal = _decode_pedal(pedal_onset, pedal_offset, pedal_frame)
            base = start_sample // HOP_SAMPLES
            for note_start, midi, end, vel, model_vel in _decode_notes(
                reg_onset, frame, velocity, segment_pedal
            ):
                decoded.append((note_start + base, midi, end + base, vel, model_vel))
            window_start = start_sample / TARGET_SR
            window_end = window_start + WINDOW_SAMPLES / TARGET_SR
            for down, up in segment_pedal:
                pedals.append((down + window_start, min(up + window_start, window_end)))

        deduped = _dedupe(decoded)
        events = [
            NoteEvent(
                start=round(start / FRAME_RATE, 4),
                duration=round(max(end - start, 1) / FRAME_RATE, 4),
                midi=midi,
                amplitude=round(min(max(velocity / 127.0, 0.0), 1.0), 4),
            )
            for start, midi, end, velocity, _mv in deduped
        ]
        events.sort(key=lambda n: (n.start, n.midi))
        held = sum(max(0.0, up - down) for down, up in pedals)
        logger.info(
            "high-resolution transcription: %d notes, %d pedal segments holding %.1fs of "
            "%.1fs, pitch range %d..%d",
            len(events),
            len(pedals),
            held,
            audio.size / TARGET_SR,
            min((n.midi for n in events), default=0),
            max((n.midi for n in events), default=0),
        )
        return HighResResult(
            notes=events,
            pedal_segments=_merge_pedals(pedals),
            velocity=[round(mv, 4) for *_rest, mv in deduped],
        )


__all__ = [
    "FRAME_RATE",
    "MIDI_HIGH",
    "MIDI_LOW",
    "MODEL_PATH",
    "TARGET_SR",
    "WINDOW_SAMPLES",
    "HighResResult",
    "HighResTranscriber",
]
