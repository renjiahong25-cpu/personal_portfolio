"""Polyphonic transcription tests.

The graph contract is asserted against the real ONNX file, and the note-grouping
functions are tested with hand-built activation matrices so the expected result
is known exactly. One end-to-end test uses a synthetic triad, because "the model
finds the three notes we put in" is the only assertion that actually proves the
wiring.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.errors import NotFoundError, ValidationError
from services.audio_analysis.polyphony import (
    ANNOT_N_FRAMES,
    AUDIO_N_SAMPLES,
    AUDIO_SAMPLE_RATE,
    FFT_HOP,
    BasicPitchTranscriber,
    NoteEvent,
    extract_polyphonic_notes,
    frame_index_to_seconds,
    infer_onsets_from_frames,
    mask_frequency_range,
    suppress_octave_ghosts,
)

SR = 22050
C4, E4, G4 = 60, 64, 67


# --- graph contract -------------------------------------------------------


def test_window_constants_match_the_onnx_graph():
    # 2 second window at 22050 Hz minus one hop, and (22050 // 256) * 2 frames.
    assert AUDIO_N_SAMPLES == 43844
    assert ANNOT_N_FRAMES == 172
    assert FFT_HOP == 256
    assert AUDIO_SAMPLE_RATE == 22050


def test_onnx_input_is_a_43844_sample_waveform():
    onnxruntime = pytest.importorskip("onnxruntime")
    from services.audio_analysis.polyphony import MODEL_PATH

    if not MODEL_PATH.is_file():
        pytest.skip("model weights missing; run fetch_models.py")
    session = onnxruntime.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
    shape = session.get_inputs()[0].shape
    assert shape[1] == AUDIO_N_SAMPLES
    assert shape[2] == 1

    output_shapes = sorted(tuple(o.shape[1:]) for o in session.get_outputs())
    assert (ANNOT_N_FRAMES, 88) in output_shapes
    assert (ANNOT_N_FRAMES, 264) in output_shapes  # contour head, unused


def test_frame_to_time_is_monotonic_and_starts_near_zero():
    times = frame_index_to_seconds(ANNOT_N_FRAMES)
    assert times[0] == pytest.approx(0.0, abs=0.02)
    assert np.all(np.diff(times) > 0)
    assert times[-1] == pytest.approx((ANNOT_N_FRAMES - 1) * FFT_HOP / SR, rel=0.02)


# --- vendored note grouping ----------------------------------------------


def make_activations(pitches: dict[int, tuple[int, int]], n_frames: int = 200, n_bins: int = 88):
    """frames/onsets with a rectangular activation per pitch."""
    frames = np.zeros((n_frames, n_bins), dtype=np.float32)
    onsets = np.zeros((n_frames, n_bins), dtype=np.float32)
    for midi, (start, end) in pitches.items():
        index = midi - 21
        frames[start:end, index] = 0.9
        onsets[start, index] = 1.0
    return frames, onsets


def test_groups_a_single_held_note():
    frames, onsets = make_activations({C4: (10, 80)})
    notes = extract_polyphonic_notes(frames, onsets, onset_threshold=0.5, frame_threshold=0.4)
    assert len(notes) == 1
    begin, end, midi, amplitude = notes[0]
    assert midi == C4
    assert 9 <= begin <= 11
    assert end == pytest.approx(80, abs=2)
    assert amplitude == pytest.approx(0.9, abs=0.05)


def test_groups_a_three_note_chord():
    frames, onsets = make_activations({C4: (10, 90), E4: (10, 90), G4: (10, 90)})
    notes = extract_polyphonic_notes(frames, onsets, onset_threshold=0.5, frame_threshold=0.4)
    assert sorted(midi for _b, _e, midi, _a in notes) == [C4, E4, G4]


def test_notes_shorter_than_min_note_len_are_dropped():
    frames, onsets = make_activations({C4: (10, 15)})  # 5 frames
    assert (
        extract_polyphonic_notes(
            frames, onsets, onset_threshold=0.5, frame_threshold=0.4, min_note_len=11
        )
        == []
    )
    assert extract_polyphonic_notes(
        frames, onsets, onset_threshold=0.5, frame_threshold=0.4, min_note_len=3
    )


def test_weak_activation_below_frame_threshold_is_ignored():
    frames, onsets = make_activations({C4: (10, 90)})
    frames[10:90, C4 - 21] = 0.2  # below the threshold
    assert extract_polyphonic_notes(frames, onsets, frame_threshold=0.4) == []


def test_melodia_trick_recovers_an_onsetless_note_only_when_enabled():
    frames, onsets = make_activations({C4: (10, 90)})
    # a second pitch that is active but has no onset at all. Onset inference is
    # disabled so this isolates the melodia trick from that other path.
    frames[10:90, E4 - 21] = 0.8
    without = extract_polyphonic_notes(
        frames, onsets, do_infer_onsets=False, use_melodia_trick=False
    )
    with_trick = extract_polyphonic_notes(
        frames, onsets, do_infer_onsets=False, use_melodia_trick=True
    )
    assert [n[2] for n in without] == [C4]
    assert sorted(n[2] for n in with_trick) == [C4, E4]


def test_neighbouring_bins_do_not_both_become_notes():
    """A harmonic leaking into bin+1 must not double the voice.

    Which of the two bins wins depends on the export order, so the assertion is
    about the count, not the pitch.
    """
    frames, onsets = make_activations({C4: (10, 90)})
    frames[10:90, C4 - 21 + 1] = 0.85
    notes = extract_polyphonic_notes(frames, onsets, use_melodia_trick=True)
    assert len(notes) == 1
    assert notes[0][2] in {C4, C4 + 1}


def test_frequency_mask_removes_out_of_range_bins():
    frames, onsets = make_activations({C4: (10, 90)})
    masked_onsets, masked_frames = mask_frequency_range(
        onsets.copy(), frames.copy(), min_freq=500.0, max_freq=None
    )
    assert masked_frames[C4 - 21].sum() == 0
    assert masked_onsets[C4 - 21].sum() == 0


def test_frequency_mask_keeps_the_band():
    frames, onsets = make_activations({C4: (10, 90)})
    masked_frames, _ = mask_frequency_range(frames.copy(), onsets.copy(), 60.0, 2000.0)
    assert masked_frames[C4 - 21].sum() > 0


def test_onset_inference_fires_on_a_step():
    frames = np.zeros((100, 4), dtype=np.float32)
    frames[50:, :] = 0.9  # sudden appearance of energy
    onsets = np.zeros((100, 4), dtype=np.float32)
    inferred = infer_onsets_from_frames(onsets, frames)
    assert inferred[50].max() > 0
    assert inferred[10:40].max() == 0


def test_onset_inference_survives_an_all_zero_onset_head():
    """Regression: rescaling by onsets.max() used to erase every onset."""
    frames = np.zeros((100, 4), dtype=np.float32)
    frames[60:, :] = 0.7
    onsets = np.zeros((100, 4), dtype=np.float32)
    inferred = infer_onsets_from_frames(onsets, frames)
    assert inferred[60].max() > 0


# --- octave ghost suppression --------------------------------------------


def test_octave_suppression_drops_the_quiet_side():
    events = [
        NoteEvent(start=0.0, duration=1.0, midi=48, amplitude=0.35),  # harmonic leak
        NoteEvent(start=0.0, duration=1.0, midi=60, amplitude=0.85),
    ]
    survivors, removed = suppress_octave_ghosts(events)
    assert removed == 1
    assert [note.midi for note in survivors] == [60]


def test_octave_suppression_keeps_notes_above_the_ratio():
    """0.7 against 0.9 is above 0.6 * 0.9, so both stay."""
    events = [
        NoteEvent(start=0.0, duration=1.0, midi=48, amplitude=0.7),
        NoteEvent(start=0.0, duration=1.0, midi=60, amplitude=0.9),
    ]
    _survivors, removed = suppress_octave_ghosts(events)
    assert removed == 0


def test_octave_suppression_keeps_a_real_doubling():
    events = [
        NoteEvent(start=0.0, duration=1.0, midi=48, amplitude=0.7),
        NoteEvent(start=0.0, duration=1.0, midi=60, amplitude=0.75),
    ]
    survivors, removed = suppress_octave_ghosts(events)
    assert removed == 0
    assert len(survivors) == 2


def test_octave_suppression_ignores_non_overlapping_notes():
    events = [
        NoteEvent(start=0.0, duration=0.4, midi=48, amplitude=0.7),
        NoteEvent(start=2.0, duration=0.4, midi=60, amplitude=0.9),
    ]
    _survivors, removed = suppress_octave_ghosts(events)
    assert removed == 0


def test_octave_suppression_handles_two_octaves_apart():
    events = [
        NoteEvent(start=0.0, duration=1.0, midi=48, amplitude=0.9),
        NoteEvent(start=0.0, duration=1.0, midi=72, amplitude=0.4),
    ]
    survivors, removed = suppress_octave_ghosts(events)
    assert removed == 1
    assert survivors[0].midi == 48


def test_octave_suppression_can_be_disabled():
    events = [
        NoteEvent(start=0.0, duration=1.0, midi=48, amplitude=0.4),
        NoteEvent(start=0.0, duration=1.0, midi=60, amplitude=0.9),
    ]
    survivors, removed = suppress_octave_ghosts(events, ratio=0.0)
    assert removed == 0
    assert len(survivors) == 2


# --- end to end on synthetic audio ----------------------------------------


def tone(freq: float, seconds: float, *, harmonics: int = 3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    wave = np.zeros_like(t)
    for index in range(1, harmonics + 1):
        wave += np.sin(2 * np.pi * freq * index * t) / index
    return wave * 0.2


def chord(midis: tuple[int, ...], seconds: float) -> np.ndarray:
    total = np.zeros(int(seconds * SR))
    for midi in midis:
        total += tone(440.0 * 2 ** ((midi - 69) / 12), seconds)
    return total


def test_finds_the_three_notes_we_played():
    """The assertion that actually proves the whole wiring."""
    signal = np.concatenate(
        [chord((C4, E4, G4), 1.0), chord((C4, E4, G4), 1.0), chord((C4, E4, G4), 1.0)]
    )
    events = BasicPitchTranscriber().transcribe_array(signal, SR)
    pitches = {note.midi for note in events}
    assert {C4, E4, G4} <= pitches, f"missing pitches, got {sorted(pitches)}"


def test_does_not_invent_loud_octave_ghosts_for_a_simple_triad():
    signal = np.concatenate(
        [chord((C4, E4, G4), 1.2), chord((C4, E4, G4), 1.2), chord((C4, E4, G4), 1.2)]
    )
    events = BasicPitchTranscriber().transcribe_array(signal, SR)
    pitches = {note.midi for note in events}
    assert {C4, E4, G4} <= pitches, f"lost real pitches: {sorted(pitches)}"

    # Nothing may survive an octave away from a louder overlapping note.
    for note in events:
        for other in events:
            if other is note or abs(other.midi - note.midi) != 12:
                continue
            if other.start >= note.end:
                continue
            assert not (note.amplitude < 0.6 * other.amplitude), (
                f"ghost {note.midi}({note.amplitude:.2f}) vs {other.midi}({other.amplitude:.2f})"
            )


def test_events_are_sorted_and_within_bounds():
    signal = np.concatenate([chord((60, 64), 1.0), chord((62, 67), 1.0)])
    events = BasicPitchTranscriber().transcribe_array(signal, SR)
    assert events == sorted(events, key=lambda n: (n.start, n.midi))
    assert all(n.duration > 0 for n in events)
    assert all(n.amplitude > 0 for n in events)


def test_longer_audio_is_split_across_windows():
    """Regression: an early version stopped after the first batch."""
    signal = np.concatenate([chord((60, 64, 67), 1.5)] * 8)  # 12 s -> 8+ windows
    events = BasicPitchTranscriber(batch_size=2).transcribe_array(signal, SR)
    assert events
    assert max(note.start for note in events) > 8.0, "audio past the first batches was dropped"


def test_resampling_from_another_rate_still_works():
    """Same material as the passing test, presented at 44100 Hz instead."""
    signal = np.concatenate(
        [chord((C4, E4, G4), 1.0), chord((C4, E4, G4), 1.0), chord((C4, E4, G4), 1.0)]
    )
    upsampled = np.repeat(signal, 2)  # pretend the file was 44100 Hz
    events = BasicPitchTranscriber().transcribe_array(upsampled, SR * 2)
    assert {C4, E4, G4} <= {note.midi for note in events}


def test_audio_shorter_than_one_window_is_rejected():
    with pytest.raises(ValidationError):
        BasicPitchTranscriber().transcribe_array(np.zeros(1000), SR)


def test_missing_model_gives_an_actionable_error(tmp_path):
    transcriber = BasicPitchTranscriber(tmp_path / "absent.onnx")
    with pytest.raises(NotFoundError) as err:
        transcriber.load()
    assert "fetch_models.py" in str(err.value)


def test_wrong_window_size_is_rejected(tmp_path, monkeypatch):
    """A model with a different input length must be refused, not silently fed."""
    import onnxruntime as ort

    class FakeInput:
        name = "x"
        shape: tuple = ("batch", 12345, 1)

    class FakeSession:
        def get_inputs(self):
            return [FakeInput()]

    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: FakeSession())
    transcriber = BasicPitchTranscriber(tmp_path / "whatever.onnx")
    transcriber._path.write_bytes(b"not really a model")  # existence check must pass
    with pytest.raises(ValidationError) as err:
        transcriber.load()
    assert "window size" in str(err.value)
