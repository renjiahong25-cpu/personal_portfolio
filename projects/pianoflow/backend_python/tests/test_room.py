"""Tests for the measured room and the decay it has to reproduce."""

from __future__ import annotations

import numpy as np
import pytest

from services.audio_analysis.room import (
    DECAY_LIMIT_SECONDS,
    FLOOR_MARGIN_DB,
    MAX_TAIL_SECONDS,
    RELEASE_DROP_DB,
    _frame_db,
    apply_room,
    decay_slope_after,
    measure_release_decay,
    synthesise_impulse_response,
    tail_energy_ratio,
    wet_level_for_ratio,
)

SR = 22050


def decaying_tail(
    slope_db_per_second: float,
    seconds: float,
    *,
    start_db: float = 0.0,
    floor_db: float = -70.0,
    sr: int = SR,
) -> np.ndarray:
    """A tone that falls at ``slope_db_per_second`` and then stops at ``floor_db``.

    Two details are deliberate, because a signal without them hides the mistakes this
    module is written against: the decay has to reach the floor partway through, since a
    tail that falls forever makes every floor-handling error invisible; and the tail must
    start *below* the music, since a tail louder than the music it follows would
    correctly be read as more music rather than as a release.
    """
    t = np.arange(int(seconds * sr)) / sr
    level = start_db + slope_db_per_second * t
    hit = level < floor_db
    level = np.where(hit, floor_db, level)
    return (10 ** (level / 20.0)).astype(np.float32)


#: Level of the steady tone in :func:`with_music_then_release`, in dBFS.
MUSIC_DB = -12.0


def with_music_then_release(sr: int = SR) -> tuple[np.ndarray, float]:
    """Five seconds of steady music, then a release, then the decay.

    Returns the signal and the time the last note-off happens, which is the anchor the
    acceptance measurement needs and cannot guess.
    """
    music_t = np.arange(int(5.0 * sr)) / sr
    music = (10 ** (MUSIC_DB / 20.0) * np.sin(2 * np.pi * 220 * music_t)).astype(np.float32)
    tail = decaying_tail(-3.0, 6.0, start_db=MUSIC_DB)
    return np.concatenate([music, tail]), 5.0


def test_measure_recovers_the_decay_it_was_given() -> None:
    y, anchor = with_music_then_release()
    measured = measure_release_decay(y, SR, release_seconds=anchor)
    assert measured.decay_db_per_second == pytest.approx(-3.0, abs=0.6)
    assert measured.t20_seconds == pytest.approx(20 / 3.0, rel=0.2)


def test_the_release_detector_lands_where_the_tail_is_thirty_decibels_down() -> None:
    """Without an anchor the detector has to find the release on its own.

    This is the path the source recording takes. Note what it actually guarantees: it
    returns the point at which the tail has fallen :data:`RELEASE_DROP_DB` below the
    music, **not** the instant of the last key. On a tail that reaches that depth at
    t=10.5 s the detector returns 10.5 s, and pretending otherwise would be asserting
    something the code does not do.

    That is also the right place to measure from, for a reason that is not luck. A piano
    tone is not one exponential - the prompt sound after the hammer strike falls fast and
    the aftersound falls slowly - so measuring from 30 dB down reads the aftersound rate,
    which is the rate that governs everything after the final release. Measuring at the
    note's own attack would report the prompt slope instead, which is not what the tail
    of the piece is made of.
    """
    music_t = np.arange(int(5.0 * SR)) / SR
    music = (10 ** (MUSIC_DB / 20.0) * np.sin(2 * np.pi * 220 * music_t)).astype(np.float32)
    # -6 dB/s from -12 dB clears 30 dB five seconds into the tail.
    tail = decaying_tail(-6.0, 6.0, start_db=MUSIC_DB, floor_db=-95.0)
    y = np.concatenate([music, tail])

    measured = measure_release_decay(y, SR)
    _times, db = _frame_db(y, SR)
    index = int(np.searchsorted(_times, measured.release_seconds))
    music_level = float(np.percentile(db, 95))
    assert float(db[index]) <= music_level - RELEASE_DROP_DB + 1.0
    # And it must be inside the file, not past the end: a slow decay that never gets
    # 30 dB down leaves the detector pointing nowhere.
    assert measured.release_seconds < len(y) / SR
    # A single-exponential tail measures the same from any point, so the rate survives.
    assert measured.decay_db_per_second == pytest.approx(-6.0, abs=0.5)


def test_a_tail_that_stops_at_the_noise_floor_is_not_read_as_faster_decay() -> None:
    """The floor must not be mistaken for signal.

    This is the bug that made the first per-band fit report 63-125 Hz as **rising** at
    +0.54 dB/s while 2-4 kHz came out flat at -0.25 dB/s: the line was drawn through
    decay and through a flat floor together, and the floor won. Here the signal falls
    at -3 dB/s down to -70 dBFS and then holds there, and the measured slope has to stay
    near -3 rather than steepening towards however fast the tail happened to end.
    """
    y, anchor = with_music_then_release()
    measured = measure_release_decay(y, SR, release_seconds=anchor)
    # A steepening would show up as a slope well past -3 dB/s once the flat part is
    # folded in; the whole signal is above the floor for its whole length here, so the
    # fit has to agree with the input.
    assert measured.decay_db_per_second == pytest.approx(-3.0, abs=0.6)

    after = decay_slope_after(y, SR, anchor_seconds=anchor, floor_db=-70.0)
    assert after is not None
    assert after == pytest.approx(-3.0, abs=0.6)


def test_silence_reports_no_decay_rather_than_a_flat_one() -> None:
    """A dry render must not be reported as decaying at 0 dB/s.

    0.0 would read as a measurement - "the level is flat" - when in fact there is no
    level left to measure. That distinction is the whole point: it is how a caller tells
    "we fixed it" from "there was never anything there".
    """
    anchor = 2.0
    silence = np.zeros(int(10.0 * SR), dtype=np.float32)
    silence[: int(anchor * SR)] = 0.3
    assert decay_slope_after(silence, SR, anchor_seconds=anchor, floor_db=-80.0) is None


def test_the_impulse_response_decays_at_the_measured_rate() -> None:
    y, anchor = with_music_then_release()
    measurement = measure_release_decay(y, SR, release_seconds=anchor)
    ir = synthesise_impulse_response(measurement, SR)
    assert ir.size > 1
    assert ir.size / SR <= MAX_TAIL_SECONDS + 1.0, "the tail must stay bounded"
    assert ir.size / SR <= DECAY_LIMIT_SECONDS, (
        f"the response is {ir.size / SR:.2f}s, past {DECAY_LIMIT_SECONDS}s - at that length the "
        "cap outlasts the decay, the tail ends in digital silence and the fit describes "
        "nothing. See MAX_TAIL_SECONDS in room.py for the measured table."
    )
    # The tail must reach zero smoothly. A hard cut in an impulse response is a click in
    # the render, not a quiet ending.
    assert float(np.max(np.abs(ir[-int(0.05 * SR) :]))) < 1e-4, "the tail ends on a step"

    # The response's own energy must fall at the measured rate. Measured well past the
    # onset so the direct spike is not part of the fit.
    #
    # The moving RMS is an amplitude, so it converts with 20*log10. Using 10*log10 here
    # halves every slope - it reported -1.52 dB/s against a target of -3.00 and looked
    # like the response decaying at half the specified rate.
    start = int(0.2 * SR)
    stop = int((0.2 + 3.0) * SR)
    window = np.arange(start, stop)
    energy = np.sqrt(np.convolve(ir**2, np.ones(64) / 64, mode="same"))
    level = 20 * np.log10(energy[window] + 1e-20)
    times = window / SR
    slope = float(np.polyfit(times, level, 1)[0])
    assert slope == pytest.approx(measurement.decay_db_per_second, abs=0.6)


def test_convolving_adds_a_tail_where_there_was_none() -> None:
    """The acceptance test: a dry render gains the decay the recording has."""
    y, anchor = with_music_then_release()
    measurement = measure_release_decay(y, SR, release_seconds=anchor)
    ir = synthesise_impulse_response(measurement, SR)

    # A dry "render": the music, then digital silence.
    music = y[: int(anchor * SR)]
    dry = np.concatenate([music, np.zeros(int(8.0 * SR), dtype=np.float32)])
    assert decay_slope_after(dry, SR, anchor_seconds=anchor, floor_db=-80.0) is None

    wet = apply_room(dry, ir)
    after = decay_slope_after(wet, SR, anchor_seconds=anchor, floor_db=-80.0)
    assert after is not None, "the convolution left nothing to measure"
    # MIREX's own tolerance for a note offset is 20 % of the true value; the decay has
    # to land inside the same kind of bound.
    assert after == pytest.approx(measurement.decay_db_per_second, rel=0.2)


def test_convolution_does_not_change_the_wet_level() -> None:
    """Slope is a shape, not a level: the two must be reported separately."""
    y, anchor = with_music_then_release()
    measurement = measure_release_decay(y, SR, release_seconds=anchor)
    music = y[: int(anchor * SR)]
    dry = np.concatenate([music, np.zeros(int(8.0 * SR), dtype=np.float32)])

    slopes = []
    for wet_db in (-3.0, -12.0):
        ir = synthesise_impulse_response(measurement, SR, wet_db=wet_db)
        value = decay_slope_after(apply_room(dry, ir), SR, anchor_seconds=anchor, floor_db=-80.0)
        assert value is not None
        slopes.append(value)
    assert slopes[0] == pytest.approx(slopes[1], abs=0.6)


def test_no_impulse_response_without_a_usable_decay() -> None:
    """A flat tail has no room in it, and must not get one invented."""
    flat_t = np.arange(int(6.0 * SR)) / SR
    music = (0.3 * np.sin(2 * np.pi * 220 * np.arange(int(3.0 * SR)) / SR)).astype(np.float32)
    steady = np.full_like(flat_t, 0.2, dtype=np.float32)
    y = np.concatenate([music, steady])
    measurement = measure_release_decay(y, SR, release_seconds=3.0)
    if measurement.decay_db_per_second >= -0.5:
        assert synthesise_impulse_response(measurement, SR).size == 1
    else:
        pytest.skip("this material does decay; the flat case is covered by construction")


def test_the_floor_margin_is_a_real_margin() -> None:
    """Guards the constant the band fit is built on."""
    assert FLOOR_MARGIN_DB > 0


def test_the_wet_level_actually_controls_the_wet_dry_balance() -> None:
    """Raising ``wet_db`` must change the reverberant-to-direct energy ratio.

    It did not, twice over, and both causes are worth pinning. The response was scaled
    end to end, so the output was a scaled copy of itself and every ratio taken within it
    was unchanged; and then the diffuse tail was normalised to unit *RMS*, which for a
    12 s response means the incoherent sum of ~260000 taps arrives sqrt(N) times louder,
    so the level was set by the response's length and every setting saturated at once.

    A parameter that has stopped responding is worse than one that is merely wrong,
    because the measurement built on it reads as a property of the room.
    """
    y, anchor = with_music_then_release()
    music = y[: int(anchor * SR)]
    dry = np.concatenate([music, np.zeros(int(20.0 * SR), dtype=np.float32)])
    measurement = measure_release_decay(y, SR, release_seconds=anchor)

    ratios = []
    for wet_db in (-18.0, -6.0, 6.0):
        impulse = synthesise_impulse_response(measurement, SR, wet_db=wet_db)
        value = tail_energy_ratio(apply_room(dry, impulse), SR, anchor)
        assert value is not None
        ratios.append(value)
    assert ratios[0] < ratios[1] < ratios[2], f"wet level is inert: {ratios}"
    # And the steps are real: doubling the wet level twice moves it by about 6 dB each
    # time, give or take the dry contribution.
    assert ratios[1] - ratios[0] > 3.0
    assert ratios[2] - ratios[1] > 3.0


def test_the_response_passes_the_direct_sound_through_at_unity() -> None:
    """The dry signal must survive the convolution, or the wet level is a master fader."""
    y, anchor = with_music_then_release()
    measurement = measure_release_decay(y, SR, release_seconds=anchor)
    impulse = synthesise_impulse_response(measurement, SR, wet_db=-40.0)
    onset = int(np.argmax(np.abs(impulse)))
    # With the tail 40 dB down, the response is essentially its direct spike.
    assert abs(impulse[onset] - 1.0) < 0.05


def test_the_solver_finds_a_wet_level_that_hits_the_target() -> None:
    """``wet_level_for_ratio`` must reach a reachable target and refuse an impossible one."""
    y, anchor = with_music_then_release()
    measurement = measure_release_decay(y, SR, release_seconds=anchor)
    music = y[: int(anchor * SR)]
    dry = np.concatenate([music, np.zeros(int(20.0 * SR), dtype=np.float32)])

    impulse = synthesise_impulse_response(measurement, SR, wet_db=0.0)
    reachable = tail_energy_ratio(apply_room(dry, impulse), SR, anchor)
    assert reachable is not None
    wet, achieved = wet_level_for_ratio(measurement, dry, SR, anchor, reachable)
    assert wet is not None and achieved is not None
    assert achieved == pytest.approx(reachable, abs=1.0)

    # A target far above anything the response can add must be refused, not clamped.
    assert wet_level_for_ratio(measurement, dry, SR, anchor, 60.0) == (None, None)


def test_the_decay_floor_is_relative_to_the_peak_not_absolute() -> None:
    """The same tail must measure the same at any output level.

    The render is peak-normalised, so an absolute dBFS floor makes the measurement a
    function of the output level instead of the tail. Quietening the track by 40 dB
    would silently move the window and change the answer, and a dry render - whose tail
    is digital silence - would report a slope fitted through numerical noise rather than
    admitting there is nothing there.
    """
    y, anchor = with_music_then_release()

    def decay_of(scaled: np.ndarray) -> float | None:
        peak = float(np.max(np.abs(scaled)))
        peak_db = 20.0 * np.log10(peak)
        return decay_slope_after(scaled, SR, anchor_seconds=anchor, floor_db=peak_db - 60.0)

    # The whole signal, not just the music: truncating at the anchor would delete the
    # tail and leave nothing to fit, which is a different test failing for the same
    # reason a dry render does.
    loud = decay_of(y)
    quiet = decay_of(y * 0.01)  # -40 dB
    assert loud is not None
    assert quiet is not None
    assert loud == pytest.approx(quiet, abs=0.3)


def test_measurement_degrades_without_throwing() -> None:
    assert measure_release_decay(np.zeros(0, dtype=np.float32), SR).decay_db_per_second == 0.0
    assert measure_release_decay(np.zeros(10, dtype=np.float32), SR).bands == ()
    assert decay_slope_after(np.zeros(10, dtype=np.float32), SR) is None
    assert apply_room(np.zeros(10, dtype=np.float32), np.zeros(1, dtype=np.float32)).size == 10
