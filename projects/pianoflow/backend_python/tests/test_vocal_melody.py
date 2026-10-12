"""The generalised vocal melody pipeline, pure core only (no torch required)."""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import pytest

from core.errors import ValidationError
from services.arrangement.vocal_melody import (
    MelodyNote,
    VocalMelody,
    VocalMelodyConfig,
    build_melody,
    cell_octave_lift,
    fit_to_scale,
    fold_low_outliers,
    form_notes,
    scale_pcs,
    spectral_level,
)


def test_fit_to_scale_keeps_the_key() -> None:
    scale = scale_pcs(4, "major")  # E major: {1, 3, 4, 6, 8, 9, 11}
    assert fit_to_scale(69.2, 4, "major") == 69  # A5, already a scale tone
    assert fit_to_scale(62.4, 4, "major") == 63  # D#5, not the out-of-key D5
    assert fit_to_scale(60.1, 4, "major") == 61  # C#5, not the out-of-key C5
    for pitch in (54.2, 61.7, 66.3, 71.9, 78.4, 84.6):
        fitted = fit_to_scale(pitch, 4, "major")
        assert fitted % 12 in scale, f"{pitch} left the key at {fitted}"


def test_form_notes_merges_same_pitch_across_words() -> None:
    """The default rule: only a pitch change splits, so a sustained note across two
    syllables stays one note (the v7 word-split read as staccato, '一拍一个音')."""
    cell_note = {0: 64, 1: 64, 2: 64, 3: 67}
    words = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]
    notes = form_notes(cell_note, phase_seconds=0.0, cell=0.5, words=words)
    assert [(n["ks"], n["ke"], n["p"]) for n in notes] == [(0, 3, 64), (3, 4, 67)]


def test_form_notes_splits_on_word_when_merge_disabled() -> None:
    cell_note = {0: 64, 1: 64, 2: 64, 3: 67}
    words = [(0.0, 1.1), (1.0, 2.0)]
    notes = form_notes(cell_note, phase_seconds=0.0, cell=0.5, words=words, merge_same_pitch=False)
    assert [(n["ks"], n["ke"], n["p"]) for n in notes] == [(0, 2, 64), (2, 3, 64), (3, 4, 67)]


def test_fold_low_outliers_folds_up_never_down() -> None:
    out, folded = fold_low_outliers({0: 64, 1: 64, 2: 52, 3: 64}, fold_cap=85)
    assert out[2] == 64 and folded == 1  # a subharmonic goes up an octave
    out, folded = fold_low_outliers({0: 64, 1: 76, 2: 64}, fold_cap=85)
    assert folded == 0 and out[1] == 76  # a genuine peak is never folded down
    out, folded = fold_low_outliers({0: 75, 1: 75, 2: 63, 3: 75}, fold_cap=70)
    assert folded == 0 and out[2] == 63  # the fold respects the cap


def _place(
    stft: np.ndarray, sr: int, n_fft: int, freq: float, value: float, frames: np.ndarray
) -> None:
    """Set the STFT so that spectral_level reads exactly ``value`` at ``freq``."""
    bins = stft.shape[0]
    position = float(np.clip(freq * n_fft / sr, 0, bins - 1.001))
    lo = int(position)
    hi = min(lo + 1, bins - 1)
    stft[lo, frames] = value
    stft[hi, frames] = value


def _octave_case(m1: float, m2: float, f0_median: float) -> bool:
    sr, n_fft = 22050, 8192
    stft = np.zeros((256, 16))
    frames = np.arange(10)
    _place(stft, sr, n_fft, f0_median, m1, frames)
    _place(stft, sr, n_fft, 2 * f0_median, m2, frames)
    _place(stft, sr, n_fft, 3 * f0_median, 1.0, frames)
    _place(stft, sr, n_fft, 4 * f0_median, 1.5, frames)
    _place(stft, sr, n_fft, 6 * f0_median, 1.0, frames)
    return cell_octave_lift(stft, sr, n_fft, f0_median, frames, VocalMelodyConfig())


def test_cell_octave_lift_when_the_double_wins() -> None:
    # m1 weak, 2*f0 series strong: B = 4.5 > A*0.5 = 2.5, fundamental absent, under cap.
    assert _octave_case(m1=0.5, m2=2.0, f0_median=110.0) is True


def test_cell_octave_lift_refuses_a_strong_fundamental() -> None:
    # A strong f0 means the detector locked correctly, whatever the upper series says.
    assert _octave_case(m1=2.0, m2=2.0, f0_median=110.0) is False


def test_cell_octave_lift_respects_the_cap() -> None:
    # Same spectrum shape, but the lift target would exceed C#6 territory.
    assert _octave_case(m1=0.5, m2=2.0, f0_median=700.0) is False


def _set_level(stft: np.ndarray, sr: int, n_fft: int, freq: float, value: float) -> None:
    frames = np.arange(stft.shape[1])
    _place(stft, sr, n_fft, freq, value, frames)
    assert spectral_level(stft, sr, n_fft, freq, frames) == pytest.approx(value, abs=1e-6)


def test_spectral_level_reads_a_placed_peak() -> None:
    stft = np.zeros((256, 32))
    _set_level(stft, 22050, 8192, 440.0, 0.7)
    _set_level(stft, 22050, 8192, 880.0, 0.3)


def test_build_melody_synthetic_song() -> None:
    """A two-note hum through the whole pipeline: A4 for three cells then E5, E major.

    A pure sine carries only its fundamental, so the octave rule must not fire, the
    key fit must land on the exact scale tones, and the cells of A4 must merge into
    one sustained note. The hum stops before the last cell, so the silence is gated
    out rather than becoming a note.
    """
    sr = 22050
    hop = 0.01
    duration = 0.5
    t = np.arange(int(duration * sr)) / sr
    freqs = np.where(t < 0.30, 440.0, np.where(t < 0.35, 659.255, 0.0))
    y = np.where(freqs > 0, 0.8 * np.sin(2 * np.pi * np.cumsum(freqs) / sr), 0.0).astype(np.float32)
    f0 = np.zeros(int(duration / hop))
    f0[:30] = 440.0  # t = 0.00 .. 0.29
    f0[30:35] = 659.255  # t = 0.30 .. 0.34; the rest is unvoiced

    config = VocalMelodyConfig(
        tempo_bpm=300.0,  # cell = 60/300/2 = 0.1 s, five cells in the window
        cell_division=2,
        tonic_pc=4,
        key_mode="major",
        duration=duration,
    )
    melody = build_melody(y, sr, f0, hop, config, phase_seconds=0.0)

    assert [(n.start, n.end, n.midi) for n in melody.notes] == [(0.0, 0.3, 69), (0.3, 0.4, 76)]
    assert all(52 <= n.velocity <= 106 for n in melody.notes)
    assert melody.stats["notes"] == 2
    assert melody.stats["octaveLifted"] == 0
    assert melody.stats["outOfKey"] == 0


def test_build_melody_gate_rejects_quiet_cells() -> None:
    """A silent stretch inside the audio is not melody: the amplitude gate drops it."""
    sr = 22050
    hop = 0.01
    duration = 0.5
    t = np.arange(int(duration * sr)) / sr
    freqs = np.where(t < 0.35, 440.0, 440.0)
    y = (0.8 * np.sin(2 * np.pi * np.cumsum(freqs) / sr)).astype(np.float32)
    y[int(0.20 * sr) :] = 0.0  # the second half is silence
    n_frames = int(duration / hop)
    f0 = np.zeros(n_frames)
    f0[: int(0.20 / hop)] = 440.0

    config = VocalMelodyConfig(tempo_bpm=600.0, cell_division=2, tonic_pc=4, duration=duration)
    melody = build_melody(y, sr, f0, hop, config, phase_seconds=0.0)
    assert melody.notes
    assert all(n.end <= 0.25 for n in melody.notes)  # nothing survives the silence


def test_vocal_melody_to_midi_and_events(tmp_path: object) -> None:
    melody = VocalMelody(
        notes=(
            MelodyNote(start=0.0, end=0.3, midi=69, velocity=80),
            MelodyNote(start=0.3, end=0.5, midi=76, velocity=60),
        ),
        tempo_bpm=84.0,
        phase_seconds=0.0,
        cell_seconds=60.0 / 84.0 / 2,
        tonic_pc=4,
        key_mode="major",
        stats={},
    )
    events = melody.to_note_events()
    assert [e.midi for e in events] == [69, 76]
    assert events[0].amplitude == pytest.approx(80 / 127.0)

    path = melody.to_midi(Path(tmp_path) / "m.mid")  # type: ignore[arg-type]
    mid = mido.MidiFile(str(path))
    notes = [
        (msg.note, msg.velocity)
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and msg.velocity > 0
    ]
    assert notes == [(69, 80), (76, 60)]


def test_extractor_refuses_a_missing_interpreter() -> None:
    from services.arrangement.vocal_melody import VocalMelodyExtractor

    with pytest.raises(ValidationError):
        VocalMelodyExtractor(python_executable="C:\\definitely\\not\\a\\python.exe")
    assert VocalMelodyExtractor.is_available("C:\\definitely\\not\\a\\python.exe") is False


def test_grid_phase_from_reference_midi(tmp_path: object) -> None:
    from services.arrangement.vocal_melody import grid_phase

    target = Path(tmp_path) / "ref.mid"  # type: ignore[arg-type]
    # Onsets at 0.1964 + k*0.357 s: the reference part written on the 84 BPM eighth grid.
    tps = 480 * 84.0 / 60.0
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=int(60_000_000 / 84.0), time=0))
    previous = 0
    for k in range(12):
        on_tick = round((0.1964 + k * 0.357) * tps)
        track.append(
            mido.Message("note_on", note=60 + (k % 7), velocity=80, time=on_tick - previous)
        )
        previous = on_tick
        # A real note duration, so the note-off delta does not shift later onsets.
        track.append(mido.Message("note_off", note=60 + (k % 7), velocity=0, time=120))
        previous += 120
    mid.save(str(target))

    phase = grid_phase(target, 0.357, 84.0)
    assert phase == pytest.approx(0.1964, abs=0.01)
