"""Adversarial edges: final attacks, partial model windows and harmonic budgets."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from services.arrangement.arranger import arrange
from services.arrangement.grid import BarGrid
from services.arrangement.hand_split import HandAssignment, hand_events_by_bar
from services.arrangement.piano_score import build_score
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import (
    AUDIO_N_SAMPLES,
    AUDIO_SAMPLE_RATE,
    BasicPitchTranscriber,
    NoteEvent,
    extract_polyphonic_notes,
)


@pytest.mark.parametrize("fidelity", ["transcribed", "as-played"])
@pytest.mark.parametrize("difficulty", [1, 2, 3, 4])
def test_final_attack_survives_the_second_faithful_bucket(fidelity, difficulty):
    grid = BarGrid(120, 4, 0.173, 2.173)
    notes = [NoteEvent(0.173, 0.3, 60, 0.8), NoteEvent(2.15, 0.07, 73, 0.7)]
    buckets = hand_events_by_bar(HandAssignment(right_hand=notes), grid)
    score = build_score(arrange(buckets, grid, fidelity=fidelity, difficulty=difficulty), grid)
    assert [(n["time"], n["midi"], n["duration"]) for n in score.note_timeline()] == [
        (n.start, n.midi, n.duration) for n in notes
    ]


@pytest.mark.parametrize("seconds", [2.0, 2.2, 2.5, 3.7, 5.1])
def test_padded_windows_cover_every_input_sample(seconds):
    transcriber = BasicPitchTranscriber()
    signal = np.arange(round(seconds * AUDIO_SAMPLE_RATE), dtype=np.float32)
    windows = transcriber._windows(signal)
    stride = (172 - transcriber._overlapping_frames) * 256
    covered = np.zeros(signal.size, dtype=bool)
    for i, window in enumerate(windows):
        begin = i * stride
        real = min(AUDIO_N_SAMPLES, signal.size - begin)
        assert np.array_equal(window[:real, 0], signal[begin : begin + real])
        covered[begin : begin + real] = True
    assert covered.all()


@pytest.mark.parametrize("difficulty", [1, 2, 3, 4])
def test_sparse_melody_harmony_budget_covers_both_chords(difficulty):
    grid = BarGrid(120, 4, 0.173, 2.173)
    melody = [NoteEvent(0.173, 1.8, 72, 0.8)]
    result = arrange(
        {0: (melody, [])},
        grid,
        chords=[ChordEvent(0.173, 1, 0, "major"), ChordEvent(1.173, 1, 7, "major")],
        difficulty=difficulty,
    )
    left = result.bars[0].left
    assert any(n.start < 1.173 and n.midi % 12 == 0 for n in left)
    assert any(n.start >= 1.173 and n.midi % 12 == 7 for n in left)
    assert all(n.end <= 1.173 + 1e-6 for n in left if n.start < 1.173 - 1e-6)


def test_overbudget_original_melody_silences_support_and_reports_limit():
    grid = BarGrid(120, 4, 0, 4)
    melody = [NoteEvent(i * 0.5, 0.4, 72 + i % 4, 0.8) for i in range(8)]
    buckets = hand_events_by_bar(HandAssignment(right_hand=melody), grid)
    result = arrange(buckets, grid, chords=[ChordEvent(0, 4, 0, "major")], difficulty=1)
    assert sum(len(b.melody) for b in result.bars) == len(melody)
    assert not any(b.left for b in result.bars)
    assert all(b.right == b.melody for b in result.bars)
    assert result.density_report["melodyExceedsBudget"] is True
    assert result.density_report["warning"]
    assert result.density_report["outputNotesPerSecond"] == 2


def test_real_onnx_detects_a_note_in_the_last_partial_window():
    model = Path(os.environ.get("PIANOFLOW_BASIC_PITCH_MODEL_PATH", ""))
    if not model.is_file():
        pytest.skip("set PIANOFLOW_BASIC_PITCH_MODEL_PATH for offline real-model validation")
    sr = AUDIO_SAMPLE_RATE
    y = np.zeros(round(2.5 * sr), dtype=np.float32)
    t = np.arange(round(0.35 * sr)) / sr
    tone = sum(np.sin(2 * np.pi * 329.6276 * k * t) / k for k in range(1, 4))
    envelope = np.minimum(t / 0.01, 1) * np.exp(-t * 2)
    begin = round(2.1 * sr)
    y[begin : begin + len(t)] = tone * envelope * 0.2
    events = BasicPitchTranscriber(model).transcribe_array(y, sr)
    assert any(n.midi == 64 and abs(n.start - 2.1) < 0.1 for n in events), events
    assert all(n.end <= 2.5 + 1e-8 for n in events)


def test_flat_onset_peak_is_one_real_attack_not_zero():
    frames = np.zeros((50, 88))
    frames[10:35, 43] = 0.8
    onsets = np.zeros_like(frames)
    onsets[10:12, 43] = 0.9
    notes = extract_polyphonic_notes(frames, onsets, min_note_len=4)
    assert len(notes) == 1
    assert notes[0][2] == 64
