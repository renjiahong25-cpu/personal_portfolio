"""Measure how loud each note actually was, instead of trusting the model's guess.

The problem
-----------
Follow-along sounded flat: every key the same volume, which reads as harsh rather
than as a performance. The note velocities were not missing - basic-pitch reports 943
distinct values on 星辰大海, and they survive into the MIDI intact - but they are a
weak estimate. Rendered, they landed between velocity 31 and 103 with a standard
deviation of 14.5 around 67: everything in the middle, no accents, no swells, no
diminuendo. Onsets and Frames, the paper basic-pitch comes from, notes that its
velocity head exists mainly to make transcriptions "more natural-sounding", and the
correlation with a pianist's actual touch is weak.

The fix is to stop asking the model and measure the recording.

How
---
For every note, take the energy in a narrow band around its own fundamental over the
window where it sounds. One complication: at any moment several notes sound at once,
so the raw band energy of each is contaminated by its neighbours and by their
harmonics. A median taken across the piano's pitch axis at the same instant estimates
that shared background, and the excess above it is the note's own contribution.

The result is converted to decibels, then mapped onto a velocity range by percentile
rather than linearly. That matters: linear scaling of a roughly bell-shaped
distribution produces the flat middle it was meant to fix, whereas rank-based mapping
spreads the performance across the piano's actual dynamic range and leaves the
loudest notes accented.

What this does and does not fix
-------------------------------
It recovers dynamics, not timbre. A synthesised piano will still not be the
instrument in the recording; see the timbre comparison in ``render.py`` for that. But
dynamics are what make a sequence of notes sound like someone playing them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: Velocity range the mapping targets. The floor is above 1 so a note is never
#: inaudible; the ceiling is below 127 so the loudest note has somewhere to go.
VELOCITY_FLOOR = 28
VELOCITY_CEIL = 106
#: CQT bins per octave. 36 is the usual choice: fine enough to separate semitones,
#: coarse enough that a bin is not narrower than the pitch's own uncertainty.
CQT_BINS_PER_OCTAVE = 36
#: A note this far below the piece's median band level is treated as inaudible and
#: given the floor velocity, rather than a dynamic the player never performed.
QUIET_MARGIN_DB = 12.0
#: The percentile treated as the softest played note and the loudest. Real
#: performances do not use the whole keyboard, so the extremes of the measured
#: distribution are noise rather than intent.
LOUD_PERCENTILE = 97.0
QUIET_PERCENTILE = 8.0

#: A pitch needs at least this many notes before its own baseline is trusted.
#:
#: Fewer than this and the "typical note in this register" is one or two samples, which
#: normalises the register against its own outliers and makes it worse. Such pitches
#: fall back to their octave, and if the octave is sparse too, to no normalisation.
REGISTER_BASELINE_MIN_NOTES = 6


def _register_baselines(midi_values: np.ndarray, levels: np.ndarray) -> dict[int, float]:
    """The typical measured level of each register, so loudness means the same in all of them.

    The measurement above is the absolute level in a note's own CQT band, and that
    quantity is not comparable between registers: a bass note concentrates far more
    energy into the band that contains it than a treble note does, simply because it has
    more partials down there. Mapping those absolute levels onto velocity therefore
    hands the top of the range to the bass and the bottom to the melody.

    Measured on 星辰大海 the result was a velocity-to-pitch correlation of **-0.311**,
    with the bottom two octaves at velocity 95 and a tenth of them pinned at 127 - the
    maximum hammer force, which is the "砸下去" the user reported. Per register the
    recording is not bass-heavy: the loudest octave is C3-C5 and the melody octave sits
    within 1 dB of it. The gradient was in the measurement, not in the performance.

    So each register is centred on its own typical note. A note is then loud or quiet
    *for the register it is in*, which is the only comparison the ear makes.

    This is not the keyboard-wide median that was tried and rejected earlier: that
    baseline is the noise floor whenever the spectrum is mostly silent. This one is
    built from notes that were actually played, in the same register, so it tracks the
    performance rather than the noise.
    """
    baselines: dict[int, float] = {}
    inaudible = levels <= 0.0
    for pitch in np.unique(midi_values):
        key = int(pitch)
        same = (midi_values == key) & ~inaudible
        if int(same.sum()) >= REGISTER_BASELINE_MIN_NOTES:
            baselines[key] = float(np.median(levels[same]))
    # Whatever a pitch could not answer for itself inherits its octave's.
    for pitch in np.unique(midi_values):
        key = int(pitch)
        if key in baselines:
            continue
        octave = key // 12 * 12
        siblings = [baselines[other] for other in baselines if other // 12 == octave // 12]
        if siblings:
            baselines[key] = float(np.median(siblings))
    return baselines


def _centre_by_register(midi_values: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Subtract each register's own baseline, so a bass note and a treble note compare."""
    baselines = _register_baselines(midi_values, levels)
    if not baselines:
        return levels
    offset = np.array([baselines.get(int(p), 0.0) for p in midi_values])
    return levels - offset


@dataclass(frozen=True)
class DynamicsProfile:
    """Measured loudness for a set of notes, and what it was derived from."""

    #: One value per note, in the order given, already scaled to 0..1.
    loudness: list[float]
    #: Correlation with whatever the transcriber guessed. Near zero means the guess
    #: was worthless and this measurement is the only usable signal.
    correlation: float
    #: Median dynamic range in decibels across the notes.
    spread_db: float


def midi_to_hz(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _band_index(frequencies: np.ndarray, hz: float) -> int:
    """The CQT bin nearest a frequency, or -1 when it is off the keyboard."""
    if frequencies.size == 0:
        return -1
    at = int(np.argmin(np.abs(frequencies - hz)))
    return at if abs(frequencies[at] - hz) <= frequencies[1] - frequencies[0] else -1


def measure(
    y: np.ndarray,
    sr: int,
    notes: Sequence[NoteEvent],
    *,
    hop_length: int = 512,
    normalise_register: bool = True,
) -> DynamicsProfile:
    """Measure how loud each note was in the recording.

    ``y`` must be the same audio the notes were transcribed from, at ``sr``.

    ``normalise_register`` centres every register on its own typical note, which is what
    makes "how loud was this note" mean the same thing in the bass and at the top. See
    :func:`_register_baselines` for the measurement that says it has to.
    """
    if not notes:
        return DynamicsProfile(loudness=[], correlation=0.0, spread_db=0.0)

    import math

    import librosa

    # The lowest note a piano can play and the highest, so the background median is
    # taken over the keyboard rather than over frequencies nothing was played on.
    fmin = max(midi_to_hz(min(n.midi for n in notes)) / 2, 20.0)
    fmax = min(midi_to_hz(max(n.midi for n in notes)) * 2, sr / 2)
    if fmax <= fmin * 2:
        fmax = min(fmin * 4, sr / 2)
    # This librosa has no fmax parameter, so the bin count is derived from the range.
    # Rounded up so the top of the keyboard is inside the analysis rather than clipped.
    n_bins = max(12, math.ceil(CQT_BINS_PER_OCTAVE * math.log2(fmax / fmin)))
    spectrum = np.abs(
        librosa.cqt(
            y,
            sr=sr,
            hop_length=hop_length,
            fmin=fmin,
            n_bins=n_bins,
            bins_per_octave=CQT_BINS_PER_OCTAVE,
            # norm=None, and this matters more than it looks. The default is L2, which
            # normalises *per frame* - so it divides out precisely the level difference
            # between one note and the next. Two notes an octave apart in loudness both
            # measured 89 dB above the background with the default, and the whole
            # point of this module is that difference.
            norm=None,
        )
    )
    # The bin centres follow from n_bins and fmin alone; the pitch axis is geometric,
    # so no sample rate is needed here.
    frequencies = librosa.cqt_frequencies(
        n_bins=n_bins,
        fmin=fmin,
        bins_per_octave=CQT_BINS_PER_OCTAVE,
    )
    seconds_per_frame = hop_length / sr
    energy_db = 20.0 * np.log10(np.maximum(spectrum, 1e-10))

    values: list[float] = []
    for note in notes:
        index = _band_index(frequencies, midi_to_hz(note.midi))
        if index < 0:
            values.append(0.0)
            continue
        start = max(0, int(note.start / seconds_per_frame))
        span = max(1, int(min(note.duration, 0.6) / seconds_per_frame))
        end = min(start + span, energy_db.shape[1])
        if end <= start:
            values.append(0.0)
            continue
        band = energy_db[index]

        # The absolute level of this pitch's own band while the note sounds. An earlier
        # version subtracted a baseline and was wrong twice over: a keyboard-wide median
        # is the noise floor whenever the spectrum is mostly silent, and this pitch's
        # level *before* the onset is the previous note's sustain, so in dense music
        # nearly every note measured as barely above nothing.
        #
        # Ranking on the absolute level asks the question that is actually wanted - how
        # loud is this note in the recording - and has no baseline that can invert it.
        # The loudest tenth of a second, not the mean: perceived loudness is the attack,
        # and the mean of a decaying note understates it.
        values.append(float(np.percentile(band[start:end], 90)))

    raw = np.array(values, dtype=float)
    midi_values = np.array([n.midi for n in notes], dtype=int)
    # Two different questions, and they have to be asked in the right order.
    #
    # **Did this note sound at all?** is a question about the recording. **How loud was
    # it?** is only answerable against something, and after register centring that
    # something is the note's own register.
    #
    # Both therefore use the same baseline, and using the *global* median for the first
    # one while centring for the second let the register bias back in through the side
    # door: a treble note sits 12 dB under a bass-heavy piece's global median without
    # being remotely inaudible, so it was zeroed, and 31.8 % of the score came out at
    # the minimum velocity. Centred, "12 dB below its own register" means what it says.
    centred = _centre_by_register(midi_values, raw) if normalise_register and raw.size > 2 else raw
    audible_mask = centred >= -QUIET_MARGIN_DB
    scaled = _to_unit(centred, audible_mask)
    guessed = np.array([n.amplitude for n in notes], dtype=float)
    correlation = (
        float(np.corrcoef(scaled, guessed)[0, 1])
        if scaled.size > 2 and guessed.std() > 1e-9 and scaled.std() > 1e-9
        else 0.0
    )
    audible = raw[raw > 0]
    spread = float(np.percentile(audible, 90) - np.percentile(audible, 10)) if audible.size else 0.0

    logger.info(
        "measured dynamics: %.1f dB spread, correlation with the model %.2f, %d/%d audible",
        spread,
        correlation,
        int((raw > 0).sum()),
        raw.size,
    )
    return DynamicsProfile(loudness=list(scaled), correlation=correlation, spread_db=spread)


def _quiet_floor(levels: np.ndarray) -> float:
    """Below this measured level, a note is treated as inaudible.

    Compared against the piece's own median rather than an absolute threshold, so the
    same rule works on a quiet passage and a loud one.
    """
    if levels.size == 0:
        return 0.0
    return float(np.median(levels)) - QUIET_MARGIN_DB


def _to_unit(db: np.ndarray, audible: np.ndarray | None = None) -> np.ndarray:
    """Map measured decibels onto 0..1 by percentile, not linearly.

    Linear scaling of a roughly bell-shaped loudness distribution lands everything in
    the middle - the flatness this module exists to fix. Ranking against the piece's
    own quietest and loudest played notes puts the performance across the range
    instead, and leaves the top of it accented.

    ``audible`` selects the notes the percentile is computed over, because after
    register centring a perfectly audible note can have a *negative* level - it is
    simply typical for its register. Selecting on ``db > 0`` would throw away half the
    score and halve the range with it.
    """
    mask = np.ones(db.shape, dtype=bool) if audible is None else np.asarray(audible, dtype=bool)
    played = db[mask]
    if played.size == 0:
        return np.zeros_like(db)
    quiet = float(np.percentile(played, QUIET_PERCENTILE))
    loud = float(np.percentile(played, LOUD_PERCENTILE))
    if loud - quiet < 1e-6:
        return np.where(mask, 0.5, 0.0)
    scaled = (db - quiet) / (loud - quiet)
    # Inaudible notes stay silent; audible ones are clipped into 0..1.
    return np.where(mask, np.clip(scaled, 0.0, 1.0), 0.0)


def to_velocity(
    loudness: float, *, floor: int = VELOCITY_FLOOR, ceiling: int = VELOCITY_CEIL
) -> int:
    """The MIDI velocity for a measured loudness in 0..1."""
    return round(floor + (ceiling - floor) * float(np.clip(loudness, 0.0, 1.0)))


def apply(
    notes: Sequence[NoteEvent],
    profile: DynamicsProfile,
) -> list[NoteEvent]:
    """Replace the guessed amplitude with the measured one.

    Uses ``dataclasses.replace`` because ``NoteEvent`` is frozen. The measured value
    replaces the guess outright rather than blending with it: the guess is the thing
    being corrected, and averaging the two would leave half the flatness in place.
    """
    import dataclasses

    if len(profile.loudness) != len(notes):
        logger.warning(
            "dynamics profile has %d entries for %d notes; keeping the model's estimate",
            len(profile.loudness),
            len(notes),
        )
        return list(notes)
    return [
        dataclasses.replace(note, amplitude=round(float(loud), 4))
        for note, loud in zip(notes, profile.loudness, strict=True)
    ]


__all__ = [
    "VELOCITY_CEIL",
    "VELOCITY_FLOOR",
    "DynamicsProfile",
    "apply",
    "measure",
    "midi_to_hz",
    "to_velocity",
]
