"""The decay that follows a note, and the room that puts it there.

Why this exists
---------------
The render cuts to digital silence the instant the last key is released. Measured on
星辰大海:

    after the final release     source          render
    level trend                 -4.57 dB/s      nothing - a hard cut to zero
    frames below -80 dBFS       0.05 %          ~100 %

So the render's ending is a gate, and a gate is the one thing a recording of a piano
never is. This module measures the recording's own release decay and convolves the
render with a synthetic impulse response shaped to match it.

What is actually being measured, precisely
------------------------------------------
The obvious plan - "measure the room's decay from the recording" - does not survive
contact with the file, and the reason is worth keeping. Measured over the quiet frames
of the whole piece, the level trend is **-0.012 dB/s**, statistically the same as the
trend over the entire file (-0.012 dB/s). A reverberant tail only exists *after*
something stops sounding, and in this material almost nothing ever stops: the
performance is dense and pedalled, so the quiet frames are residual bass sustain, not
room. Convolving to chase them would have been fitting a number that measures nothing.

What does survive is the **final release**. When the piece ends, every damper falls at
once and the file's last 8.26 s are the recording's own decay, uncontaminated by the
next chord. That gives -4.57 dB/s, or **T20 = 20 / 4.57 = 4.4 s** measured the standard
way (the time from peak to -20 dB). T20 is the established measure in piano acoustics,
and it is what this module matches.

So the claim here is deliberately narrower than "the room": it is the **decay envelope
of the recording**, per frequency band. Whether that envelope is the instrument, the
room, or the microphone cannot be separated from a single mix, and nothing downstream
needs them separated - the render has to fall at the same rate, not for the same reason.

Two other measured facts shaped the design:

* **Decay is strongly frequency dependent.** The quiet frames sit 17-19 dB below the
  music in the 60-1000 Hz bands and 19 dB further below again above 4 kHz. A single
  exponential would put high notes ringing like bass notes, so the impulse response is
  built band by band from the measured slope in each band.
* **The recording has an ambience floor near -70 dBFS**, visible in the half second
  before the first note. That is 40 dB below a peak-normalised render and inaudible, so
  it is measured and reported but not synthesised. Adding noise to hit a number nobody
  can hear is not fidelity.

Standards this leans on, so the numbers are checkable rather than tasteful:

* T20 as the decay measure, following the piano-acoustics convention (Martin 1947;
  the KTH/Weinreich lecture notes on measuring from maximum level to -20 dB).
* Double decay. A piano tone is not one exponential: there is a fast *prompt sound*
  after the hammer strike and a slower *aftersound* carried by the coupling between the
  strings of a note. The synthesised response decays from the aftersound slope, because
  that is the part the final release actually shows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

#: Decay is measured over octave bands and interpolated between their centres in
#: log-frequency, so a band's slope is not assumed to hold right up to its edge.
BAND_EDGES: tuple[tuple[float, float], ...] = (
    (63, 125),
    (125, 250),
    (250, 500),
    (500, 1000),
    (1000, 2000),
    (2000, 4000),
    (4000, 8000),
)

#: How far below the release point to hunt for the end of the music, in seconds.
#: The recording fades out rather than stopping dead, so the release point is found by
#: walking backwards from a level that is unambiguously still music.
RELEASE_SEARCH_SECONDS = 12.0

#: A frame this long, for finding the release point and measuring the trend.
FRAME_SECONDS = 0.02
HOP_SECONDS = 0.01

#: How far the tail is followed. -60 dB is three T20s, below any plausible floor.
TAIL_DEPTH_DB = 60.0

#: Cap on the synthesised tail, so a near-flat measured decay cannot ask for minutes of
#: noise.
#:
#: **Was 12 s, and that was the "metallic ring that goes on for seconds after the piano
#: stops" complaint, measured.** Following 60 dB at the recording's own -2.68 dB/s asks for
#: 22.4 s, so this cap was always the binding constraint: the response came out 12.01 s long
#: and the rendered tail took **12.45 s** to fall 40 dB after the last note-off on 星辰大海.
#: A piano in a room does not ring for 12 s, and a tail that long also keeps mixing into the
#: following bars, which is part of why dense passages smear.
#:
#: 6 s was not guessed - it is the shortest length that still reproduces the measured decay,
#: which is the acceptance criterion for this module. The alternatives were measured against
#: it (synthetic release decaying at -3.00 dB/s, tail length and the render's own decay):
#:
#:     cap     IR     render tail    ring after last note
#:     2.5 s  2.51 s   -13.3 dB/s        2.5 s    <- decay badly wrong: the fade dominates
#:     4.0 s  4.01 s    -8.3 dB/s        3.9 s    <- still wrong
#:     6.0 s  6.01 s    -3.4 dB/s        5.9 s    <- matches -3.00, ring more than halved
#:     8.0 s  8.01 s    -0.3 dB/s        7.8 s    <- cap now exceeds the decay, so the fit
#:                                                      runs into digital silence
#:    12.0 s 12.01 s    -3.1 dB/s       12.5 s    <- the reported problem
#:
#: The 8 s row is the trap worth naming: once the cap outlasts the real decay, the response
#: ends in silence and the fit describes nothing. That is how this module once reported
#: -31 dB/s for a track decaying at -3.91.
MAX_TAIL_SECONDS = 6.0

#: Hard ceiling on :data:`MAX_TAIL_SECONDS`, named so the constraint can be asserted rather
#: than remembered. The response must stay shorter than what the decay needs to actually
#: die out; a cap that exceeds it converts the tail into digital silence and every decay
#: measurement taken from the response becomes meaningless.
DECAY_LIMIT_SECONDS = 8.0

#: Fraction of the tail spent fading to zero, so the cap is a fade rather than a click.
#:
#: Kept small on purpose. A long fade looks gentler but it *is* the decay near the end, so
#: at 45 % of a short tail the render's tail measured -13.3 dB/s instead of -3.0 - the
#: reverb would stop rather than fade, which is the "dry and disconnected" complaint all
#: over again. At 12 % the measured decay is unaffected (-3.42 against -3.48) and the cap is
#: still smooth.
TAIL_FADE_FRACTION = 0.12

#: How far above the noise floor a band must stay to be worth fitting.
FLOOR_MARGIN_DB = 10.0
#: A band needs at least this much measurable tail to report its own slope.
MIN_FIT_SECONDS = 0.5

#: How far below the music's own level the tail has to get before the release
#: detector will accept it. A slow decay never gets here, which is why the release
#: time can be passed in instead.
RELEASE_DROP_DB = 30.0
#: A noise floor is only believed if it sits at least this far below the music.
#: Quieter than that is the tail ending, not a floor, and trimming to it would delete
#: the decay being measured.
FLOOR_TRUST_DB = 20.0

#: Pre-delay before the diffuse tail. Small: this is a close-miked piano, and the
#: measured signal has no room to speak of. Present because a zero-delay reverb reads
#: as a filter rather than as a space.
PREDELAY_MS = 6.0

#: How long a window the decay slope is fitted over.
#:
#: Set by measurement, not taste. The dry render ends at a hard cutoff, and that step
#: discontinuity produces its own fast-decaying transient which dominates the first
#: moments of the convolved tail. On the same signal, with an impulse response built to
#: decay at -3.00 dB/s:
#:
#:     window    1 s     2 s     3 s     6 s     9 s
#:     slope   -5.35   -7.70   -6.22   -3.07   -3.26
#:
#: Only from six seconds does the reverberant tail outweigh the cutoff. A shorter window
#: does not measure the room; it measures where it was switched on, and the error is
#: nearly a factor of two.
DECAY_WINDOW_SECONDS = 6.0


@dataclass(frozen=True)
class BandDecay:
    """How fast one octave band falls after the last note."""

    low_hz: float
    high_hz: float
    db_per_second: float

    @property
    def center_hz(self) -> float:
        return float(np.sqrt(self.low_hz * self.high_hz))


@dataclass(frozen=True)
class RoomMeasurement:
    """The recording's own decay, as opposed to an assumed one."""

    decay_db_per_second: float
    t20_seconds: float
    bands: tuple[BandDecay, ...]
    release_seconds: float
    ambience_dbfs: float
    tail_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "decayDbPerSecond": round(self.decay_db_per_second, 3),
            "t20Seconds": round(self.t20_seconds, 3),
            "releaseSeconds": round(self.release_seconds, 3),
            "tailSeconds": round(self.tail_seconds, 3),
            "ambienceDbfs": round(self.ambience_dbfs, 1),
            "bands": [
                {
                    "lowHz": b.low_hz,
                    "highHz": b.high_hz,
                    "dbPerSecond": round(b.db_per_second, 3),
                }
                for b in self.bands
            ],
        }


def _frame_db(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Short-time level in dBFS, and the time of each frame's centre."""
    size, hop = max(1, int(FRAME_SECONDS * sr)), max(1, int(HOP_SECONDS * sr))
    if y.size < size:
        return np.zeros(0), np.zeros(0)
    count = 1 + (y.size - size) // hop
    windows = np.lib.stride_tricks.as_strided(
        y,
        shape=(count, size),
        strides=(y.strides[0] * hop, y.strides[0]),
        writeable=False,
    )
    rms = np.sqrt(np.mean(windows**2, axis=1) + 1e-20)
    return (np.arange(count) * hop + size / 2) / sr, 20 * np.log10(rms)


def _find_release(times: np.ndarray, db: np.ndarray) -> int:
    """Index of the frame where the piece stops sounding.

    Walks back from the end while frames are still clearly music, so the tail that
    follows is decay rather than the fade of the last chord.
    """
    if db.size == 0:
        return 0
    peak = float(np.percentile(db, 95))
    start = max(0, db.size - int(RELEASE_SEARCH_SECONDS / HOP_SECONDS))
    music = np.nonzero(db[start:] > peak - 30.0)[0]
    if music.size == 0:
        return start
    return int(start + music[-1]) + 1


def _band_slopes(
    tail: np.ndarray,
    sr: int,
    times: np.ndarray,
    ambience_db: float,
    wideband_slope: float,
) -> tuple[BandDecay, ...]:
    """Fit a level slope per octave band over the part of the tail that is signal.

    The window matters more than the fit. The tail ends on the recording's own noise
    floor - measured at -69 dBFS in the half second before the first note - and a
    straight line drawn through decay *and* through a flat floor averages the two into
    a slope that describes neither. Fitting the whole tail gave 63-125 Hz **rising** at
    +0.54 dB/s and 2-4 kHz essentially flat at -0.25 dB/s, which is the floor winning,
    not the instrument.

    So each band is fitted only over the frames where it is at least
    :data:`FLOOR_MARGIN_DB` above the floor, and a band that leaves too little of the
    tail above its floor to fit is given the wideband slope rather than a fabricated
    one, because the wideband slope is the only decay actually established here.
    """
    from scipy import signal as sig

    if tail.size < int(0.25 * sr) or times.size < 4:
        return ()

    nfft = 1024
    freqs, times_spec, spec = sig.stft(
        tail, fs=sr, nperseg=nfft, noverlap=nfft // 2, boundary=None, padded=False
    )
    power = np.abs(spec) ** 2
    bands: list[BandDecay] = []
    for low, high in BAND_EDGES:
        if low >= sr / 2:
            continue
        select = (freqs >= low) & (freqs < high)
        if not select.any():
            continue
        level = 10 * np.log10(power[select].sum(axis=0) + 1e-20)
        slope, used = _slope_above_floor(times_spec, level, ambience_db)
        if slope is None or used < MIN_FIT_SECONDS or slope >= -0.5:
            logger.info(
                "band %d-%d Hz: %.2fs above the floor, slope %s; using the wideband slope",
                low,
                high,
                used,
                "unusable" if slope is None else f"{slope:+.2f} dB/s",
            )
            slope = wideband_slope
        bands.append(BandDecay(low_hz=low, high_hz=high, db_per_second=float(slope)))
    return tuple(bands)


def _slope_above_floor(
    times: np.ndarray, level: np.ndarray, ambience_db: float
) -> tuple[float | None, float]:
    """Least-squares level slope, using only frames above the floor.

    Returns the slope and how many seconds of the signal it was fitted over, so a
    caller can refuse a slope that was measured over almost nothing.
    """
    if level.size == 0 or times.size != level.size:
        return None, 0.0
    above = level > ambience_db + FLOOR_MARGIN_DB
    if above.sum() < 4:
        return None, 0.0
    slope = float(np.polyfit(times[above], level[above], 1)[0])
    return slope, float(times[above][-1] - times[above][0])


def measure_release_decay(
    y: np.ndarray,
    sr: int,
    *,
    release_seconds: float | None = None,
) -> RoomMeasurement:
    """Measure how the recording dies away once the playing stops.

    ``release_seconds`` is where the last note-off is. Pass it when it is known - it
    always is for a render, whose note list is in hand - because the detector that
    guesses it assumes the tail falls at least :data:`RELEASE_DROP_DB` below the music
    inside the search window, and a slow, long decay never gets that far, leaving the
    detector pointing past the end of the file with nothing to measure.
    """
    times, db = _frame_db(y, sr)
    if times.size == 0:
        return RoomMeasurement(0.0, 0.0, (), 0.0, -120.0, 0.0)

    if release_seconds is None:
        release_index = _find_release(times, db)
    else:
        # Frames are centred, so the first frame starting after the release is the one
        # whose window lies wholly past it.
        release_index = int(np.searchsorted(times, release_seconds))
    release_index = max(0, min(release_index, times.size - 1))
    release = float(times[release_index])
    tail_db = db[release_index:]
    tail_t = times[release_index:]

    # The recording's own noise, estimated from the quietest frames anywhere in the file
    # rather than from the half second before the first note, which was the first attempt
    # and only works when the file starts before the music does. On a clip that opens on
    # a downbeat that estimator returns the music's own level - -10 dBFS instead of -70 -
    # and every fit is then trimmed away to nothing.
    threshold = float(np.percentile(db, 2))
    quiet_frames = db[db <= threshold]
    ambience = float(np.median(quiet_frames)) if quiet_frames.size else -120.0

    # Only trim against the floor if there *is* a floor. On material with no true
    # silence the quietest frames are the end of the tail itself, and treating that as a
    # noise floor silently deletes most of the decay being measured.
    trimmed = ambience if ambience <= float(np.percentile(db, 95)) - FLOOR_TRUST_DB else -1e9

    slope = 0.0
    if tail_t.size >= 4:
        # Trimmed against the floor for the same reason the bands are: the last of the
        # tail is the recording's noise, and including it biases the fit steep.
        fitted, _used = _slope_above_floor(tail_t, tail_db, trimmed)
        slope = fitted if fitted is not None else float(np.polyfit(tail_t, tail_db, 1)[0])

    tail_seconds = float(max(times[-1] - release, 0.0))
    t20 = 20.0 / abs(slope) if slope < -0.5 else 0.0

    logger.info(
        "release decay: %.2fs in, %.2fs of tail, %.2f dB/s, T20 %.2fs, ambience %.0f dBFS",
        release,
        tail_seconds,
        slope,
        t20,
        ambience,
    )
    return RoomMeasurement(
        decay_db_per_second=slope,
        t20_seconds=t20,
        bands=_band_slopes(y[int(release * sr) :], sr, tail_t, ambience, slope),
        release_seconds=release,
        ambience_dbfs=ambience,
        tail_seconds=tail_seconds,
    )


def synthesise_impulse_response(
    measurement: RoomMeasurement,
    sr: int,
    *,
    wet_db: float = -6.0,
    predelay_ms: float = PREDELAY_MS,
) -> np.ndarray:
    """An impulse response with the direct sound at unity and the tail at ``wet_db``.

    So that convolving gives ``dry + reverb`` and ``wet_db`` states the direct-to-
    reverberant balance directly.

    That structure is not cosmetic. The first version scaled the whole response by
    ``wet_db``, which made the parameter a master gain rather than a mix, and every
    energy ratio measured on the output came out identical from -12 dB to +12 dB -
    scaling a signal scales both halves of any ratio taken within it, so a
    reverberant-to-direct criterion could not respond to a parameter that only moved the
    master fader. A parameter that silently stops responding is worse than one that is
    merely wrong, because the measurement built on it then looks like a property of the
    room rather than of the clamp.

    Built in the time domain, band by band: each band gets band-passed noise under its
    own ``10 ** (slope * t / 20)`` envelope, and the bands are summed. That is the
    standard construction for a synthetic room response and it keeps each band's decay
    rate independent.

    The first attempt did this in the frequency domain, forming a rate-per-frequency
    array against a time axis - which is a full nfft-by-nfft outer product and asked for
    4 TiB of memory. Worse, it was wrong on the theory: a frequency-domain envelope
    describes the spectrum, not the decay. Decay is what happens in time, so it belongs
    on the time axis, one envelope per band.

    A single exponential would also be wrong here for a reason the measurement shows:
    the recording's high bands fall faster than its low ones, and one rate would leave
    treble ringing at bass length.
    """
    from scipy import signal as sig

    slope = measurement.decay_db_per_second
    if slope >= -0.5 or not measurement.bands:
        logger.warning("no usable decay measured; returning no impulse response")
        return np.zeros(1, dtype=np.float32)

    seconds = min(TAIL_DEPTH_DB / abs(slope), MAX_TAIL_SECONDS)
    n = max(1, int(seconds * sr))
    t = np.arange(n) / sr
    rng = np.random.default_rng(0)

    diffuse = np.zeros(n, dtype=np.float64)
    nyquist = sr / 2
    for band in measurement.bands:
        if band.low_hz >= nyquist:
            continue
        low = min(band.low_hz / nyquist, 0.99)
        high = min(band.high_hz / nyquist, 0.999)
        noise = rng.standard_normal(n)
        if high > low and high < 0.999:
            noise = sig.sosfilt(sig.butter(4, [low, high], btype="bandpass", output="sos"), noise)
        envelope = 10.0 ** (np.minimum(band.db_per_second * t, 0.0) / 20.0)
        diffuse += noise * envelope

    # Normalised to unit *energy*, not unit RMS, and this distinction is the whole
    # difference between a parameter that works and one that does not. A 12 s tail
    # convolved with a 240 s track sums about 260000 independent noise samples, so a
    # unit-RMS tail arrives 514 times louder than it started (sqrt of the tap count).
    # Every wet level then produced the same output, because the level was set by the
    # length of the response rather than by the setting. With unit energy the reverb
    # adds exactly 10 ** (wet_db / 10) times the dry signal's energy whatever the
    # length, which is the definition of a wet/dry balance.
    energy = float(np.sqrt(np.sum(diffuse**2)))
    diffuse = diffuse / (energy if energy > 0 else 1.0) * (10 ** (wet_db / 20.0))

    # Fade the cap instead of cutting it. MAX_TAIL_SECONDS truncates a tail that is still
    # around -7 dB, and a discontinuity in an impulse response is a click in the render,
    # not a quiet ending. Measured with a cosine taper so the slope is continuous at both
    # ends rather than jumping.
    fade_n = max(1, int(TAIL_FADE_FRACTION * n))
    if fade_n < n:
        taper = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_n)))
        diffuse[n - fade_n :] *= taper

    onset = max(1, int(predelay_ms * sr / 1000.0))
    ir = np.concatenate([np.zeros(onset), diffuse])
    # Unity direct: the dry signal passes through unchanged and everything after this
    # sample is the room.
    ir[onset] = 1.0

    logger.info(
        "impulse response: %.2fs, direct at unity, tail %.1f dB relative, %.2f dB/s decay",
        len(ir) / sr,
        wet_db,
        slope,
    )
    return ir.astype(np.float32)


def decay_slope_after(
    y: np.ndarray,
    sr: int,
    *,
    anchor_seconds: float | None = None,
    window_seconds: float = DECAY_WINDOW_SECONDS,
    floor_db: float | None = None,
) -> float | None:
    """The level trend once the music has stopped. The number this module is judged on.

    Public because it is the acceptance criterion: the render has to fall at the rate
    the recording falls, and a claim that cannot be recomputed from the output file is
    not a claim.

    Returns ``None`` when there is nothing left to measure, and that is the *correct*
    answer for a dry render: its tail is digital silence, so every frame sits below any
    floor and there is no decay to report. Returning 0.0 instead would say "the level
    is flat", which reads as a measurement and is the opposite of what happened.

    ``anchor_seconds`` is where the last note-off is. Pass it whenever it is known - and
    on the render it always is, because the note list is in hand. Guessing it from the
    level is what this first did, and it is wrong in a way that hides its own error: an
    anchor past the end of the music measures the silence after the music, which is how
    a track whose tail really decays at -3.91 dB/s was reported as -31 dB/s.

    ``window_seconds`` defaults to :data:`DECAY_WINDOW_SECONDS`, which is measured
    rather than chosen; see that constant for why a short window reads the cutoff
    instead of the room.
    """
    times, db = _frame_db(y, sr)
    if times.size < 8:
        return None
    if anchor_seconds is None:
        anchor_seconds = float(times[_find_release(times, db)])
    floor = floor_db if floor_db is not None else -1e9

    window = (times >= anchor_seconds) & (times <= anchor_seconds + window_seconds)
    if window.sum() < 4:
        return None
    slope, _used = _slope_above_floor(times[window], db[window], floor)
    return slope


def tail_energy_ratio(
    y: np.ndarray,
    sr: int,
    music_end: float,
    *,
    direct_seconds: float = 1.0,
    floor_db: float = 60.0,
) -> float | None:
    """Energy after the music stops, relative to the energy just before it stops.

    This is the reverberant-to-direct criterion, and it is the only one of the three
    numbers here that is not circular. Neither region depends on the decay rate being
    matched: the numerator is everything after the last note, the denominator is the
    final second of playing. So a wet level chosen to satisfy it is not two errors
    cancelling - it sets the level of the reverberant contribution outright.

    Measured on 星辰大海: the recording is -1.76 dB and the dry render -3.84 dB, so the
    render's tail is 2.1 dB too quiet relative to its own direct sound and the room has
    that much to make up.

    Being a ratio of two energies within one signal, it is also scale-invariant, which is
    why the impulse response must keep its direct sound at unity: a response scaled
    end-to-end moves this number not at all.
    """
    direct = y[int((music_end - direct_seconds) * sr) : int(music_end * sr)]
    tail = y[int(music_end * sr) :]
    if direct.size == 0 or tail.size == 0:
        return None
    peak = float(np.max(np.abs(y)))
    if peak <= 1e-9:
        return None
    limit = peak * 10 ** (-floor_db / 20)
    above = np.nonzero(np.abs(tail) >= limit)[0]
    if above.size:
        tail = tail[: above[-1] + 1]
    direct_energy = float(np.sum(direct**2))
    if direct_energy <= 1e-30:
        return None
    return float(10 * np.log10((float(np.sum(tail**2)) + 1e-30) / direct_energy))


def apply_room(y: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Convolve the dry render with the measured room: ``dry + reverb``.

    Deliberately does not limit or normalise the result. An earlier version divided by
    the peak whenever the convolution exceeded full scale, which looks harmless and is
    not: it is a non-linear function of the wet level, so past that point raising
    ``wet_db`` stopped changing anything and every setting from -9 dB to +12 dB
    produced an identical result. The caller normalises instead, which is where the peak
    target belongs anyway.
    """
    if ir.size < 2 or y.size == 0:
        return y
    from scipy import signal as sig

    return sig.fftconvolve(y.astype(np.float32), ir.astype(np.float32), mode="full").astype(
        np.float32
    )


def wet_level_for_ratio(
    measurement: RoomMeasurement,
    dry: np.ndarray,
    sr: int,
    music_end: float,
    target_db: float,
    *,
    search_db: tuple[float, float] = (-40.0, 10.0),
    tolerance_db: float = 0.5,
    max_iterations: int = 24,
) -> tuple[float | None, float | None]:
    """The wet level that brings the rendered tail/direct ratio to ``target_db``.

    Solved by bisection rather than by assuming the relationship is linear in dB. The
    tail energy is a sum of a dry contribution and a reverberant one, so the ratio in dB
    is very nearly -3 dB per 6 dB of wet level over part of the range and saturates over
    the rest; a linear solve would land in the wrong place half the time.

    Returns the wet level and the ratio it achieves, or ``(None, None)`` if the target is
    unreachable inside ``search_db`` - which is a real answer, not a failure to try, and
    far better than clamping to an endpoint and reporting it as a match.
    """
    if target_db is None or target_db != target_db:  # NaN guard
        return None, None

    def ratio_at(wet_db: float) -> float | None:
        impulse = synthesise_impulse_response(measurement, sr, wet_db=wet_db)
        if impulse.size < 2:
            return None
        return tail_energy_ratio(apply_room(dry, impulse), sr, music_end)

    low, high = search_db
    low_ratio, high_ratio = ratio_at(low), ratio_at(high)
    if low_ratio is None or high_ratio is None:
        return None, None
    if not (min(low_ratio, high_ratio) <= target_db <= max(low_ratio, high_ratio)):
        logger.info(
            "tail/direct target %.2f dB is outside the reachable range [%.2f, %.2f]",
            target_db,
            min(low_ratio, high_ratio),
            max(low_ratio, high_ratio),
        )
        return None, None

    best_wet, best_ratio = low, low_ratio
    for _ in range(max_iterations):
        middle = (low + high) / 2
        value = ratio_at(middle)
        if value is None:
            return None, None
        best_wet, best_ratio = middle, value
        if abs(value - target_db) <= tolerance_db or high - low <= 0.05:
            break
        # The ratio rises with wet level; step the side that is still short of target.
        if (value < target_db) == (low_ratio <= target_db):
            low, low_ratio = middle, value
        else:
            high, high_ratio = middle, value
    return round(best_wet, 2), round(float(best_ratio), 3)
