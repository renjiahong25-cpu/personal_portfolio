"""Tests for the bar grid: tempo folding, phase search and quantisation."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from services.arrangement.grid import BarGrid, build_bar_grid, estimate_phase, estimate_tempo


def test_beat_and_bar_geometry() -> None:
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=8.0)
    assert grid.beat_seconds == pytest.approx(0.5)
    assert grid.bar_seconds == pytest.approx(2.0)
    assert grid.sixteenth_seconds == pytest.approx(0.125)
    assert grid.sixteenths_per_bar == 16
    assert grid.bar_count() == 4


def test_quantize_snaps_to_grid_and_reports_error() -> None:
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=8.0)
    bar, beat, sixteenth, error = grid.quantize(2.06)
    assert (bar, beat, sixteenth) == (1, 0, 0)
    assert error == pytest.approx(0.06, abs=1e-6)


def test_quantize_handles_notes_before_the_first_beat() -> None:
    """A pickup before the phase must clamp into bar 0, never a negative bar."""
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=1.0, duration=8.0)
    bar, beat, sixteenth, error = grid.quantize(0.2)
    assert (bar, beat, sixteenth) == (0, 0, 0)
    # The error still reports the true distance to the nearest grid position,
    # so a pickup is not mistaken for an off-grid note.
    assert error == pytest.approx(0.05)


def test_quantize_maps_the_last_sixteenth_of_a_bar() -> None:
    grid = BarGrid(tempo_bpm=120.0, beats_per_bar=4, phase_seconds=0.0, duration=8.0)
    assert grid.quantize(1.90)[:3] == (0, 3, 3)
    # Just past the barline must roll over to the next bar.
    assert grid.quantize(1.95)[:3] == (1, 0, 0)


def test_bar_spans_are_contiguous() -> None:
    grid = BarGrid(tempo_bpm=143.6, beats_per_bar=4, phase_seconds=0.14, duration=10.0)
    spans = list(grid.bars())
    assert len(spans) == 6
    for current, following in itertools.pairwise(spans):
        assert current.end == pytest.approx(following.start)


def test_estimate_phase_prefers_the_offset_with_onset_energy() -> None:
    """Energy sits on 0.0, 0.5, 1.0 ... so phase 0.25 must lose to phase 0.0."""
    period = 0.5
    envelope = np.zeros(2000)
    for index in range(0, envelope.size, int(0.5 * 512)):
        envelope[index] = 1.0
    phase = estimate_phase(envelope, period, total_seconds=20.0)
    assert phase == pytest.approx(0.0, abs=period / 200 * 2)


def test_estimate_phase_reports_a_sub_beat_offset() -> None:
    period = 0.5
    offset = 0.25
    envelope = np.zeros(2000)
    step = int(0.5 * 512)
    for index in range(int(offset * 512), envelope.size, step):
        envelope[index] = 1.0
    assert estimate_phase(envelope, period, total_seconds=20.0) == pytest.approx(
        offset, abs=period / 100
    )


def test_estimate_tempo_rejects_empty_audio() -> None:
    with pytest.raises(ValueError):
        estimate_tempo(np.zeros(100, dtype=np.float32), 22050)


def test_estimate_tempo_rejects_silence() -> None:
    with pytest.raises(ValueError):
        estimate_tempo(np.zeros(22050 * 4, dtype=np.float32), 22050)


def test_build_bar_grid_on_a_click_track() -> None:
    """A synthetic click train must yield a usable grid near the true tempo."""
    sr = 22050
    tempo = 120.0
    period = int(sr * 60 / tempo)
    y = np.zeros(sr * 8, dtype=np.float32)
    for start in range(0, y.size - 400, period):
        y[start : start + 400] = 1.0
    grid = build_bar_grid(y, sr)
    assert grid.tempo_bpm == pytest.approx(tempo, rel=0.05)
    assert 0.0 <= grid.phase_seconds < grid.beat_seconds
    assert grid.bar_count() >= 3


def test_build_bar_grid_rejects_a_bad_time_signature() -> None:
    y = np.zeros(22050 * 2, dtype=np.float32)
    y[::1000] = 1.0
    with pytest.raises(ValueError):
        build_bar_grid(y, 22050, beats_per_bar=0)


def test_build_bar_grid_accepts_an_explicit_tempo() -> None:
    """Passing the tempo skips estimation, which keeps callers reproducible."""
    y = np.zeros(22050 * 4, dtype=np.float32)
    y[::1000] = 1.0
    grid = build_bar_grid(y, 22050, tempo=90.0)
    assert grid.tempo_bpm == 90.0


def test_grid_serialises_for_the_api() -> None:
    grid = BarGrid(tempo_bpm=143.6, beats_per_bar=4, phase_seconds=0.14, duration=30.0)
    payload = grid.to_dict()
    assert payload["tempoBpm"] == 143.6
    assert payload["barCount"] == grid.bar_count()
