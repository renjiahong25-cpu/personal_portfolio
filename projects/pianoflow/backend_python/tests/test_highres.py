"""Tests for the high-resolution transcriber.

The decoders are tested against synthetic heads rather than the 154 MB graph, because what
can go wrong here is arithmetic, not the model: an off-by-one in the frame arithmetic
produces plausible-looking notes that are quietly a frame late. The one thing that needs
real weights is the pedal *name*, so :class:`TestPedalHeadSemantics` pins that separately.

Where this module's results came from, and what they are not good for, is written down in
``highres.py``'s module docstring. The tests below encode the same findings so a later
change that reintroduces them fails here instead of shipping.
"""

from __future__ import annotations

import itertools
import re

import numpy as np
import pytest

from services.audio_analysis import highres as H
from services.audio_analysis.highres import (
    MIDI_LOW,
    HighResTranscriber,
    _decode_notes,
    _decode_pedal,
    _dedupe,
    _local_max,
    _merge_pedals,
    _split_close,
)

FRAMES = 1000
PITCHES = 88


def _heads() -> dict[str, np.ndarray]:
    """Silent heads: nothing above any threshold anywhere."""
    return {
        "onset": np.zeros((FRAMES, PITCHES), np.float32),
        "offset": np.zeros((FRAMES, PITCHES), np.float32),
        "frame": np.zeros((FRAMES, PITCHES), np.float32),
        "velocity": np.zeros((FRAMES, PITCHES), np.float32),
    }


def _note(heads: dict[str, np.ndarray], pitch: int, start: int, end: int, vel: float = 0.7):
    heads["onset"][start, pitch] = 1.0
    heads["frame"][start:end, pitch] = 0.9
    heads["velocity"][start, pitch] = vel
    return heads


class TestLocalMax:
    def test_peak_is_a_candidate_but_its_neighbours_are_not(self):
        values = np.zeros((5, 1), np.float32)
        values[2, 0] = 1.0
        found = np.nonzero(_local_max(values)[:, 0])[0]
        assert found.tolist() == [2]

    def test_rising_signal_at_the_first_frame_still_counts(self):
        # Padding is zero, so a local max is not lost at the boundary. This is the case
        # that catches a padded[:-2]/padded[2:] mix-up, which silently drops it.
        values = np.array([[0.9], [0.5], [0.1]], np.float32)
        assert np.nonzero(_local_max(values)[:, 0])[0].tolist() == [0]

    def test_a_flat_run_yields_one_onset_not_one_per_frame(self):
        # The interior of a plateau is not a peak. Its edges are, because a plateau next
        # to zero padding genuinely rises on one side - and for onset detection the first
        # frame of a run is the right answer anyway. What must not happen is a candidate
        # per frame, which is what the >= -on-both-sides comparison produced.
        values = np.full((5, 1), 0.8, np.float32)
        found = np.nonzero(_local_max(values)[:, 0])[0].tolist()
        assert _split_close(found, 5) == [found[0]]
        assert len(found) <= 2

    def test_columns_are_compared_independently(self):
        values = np.zeros((5, 3), np.float32)
        values[3, 0] = 1.0
        found = np.nonzero(_local_max(values))
        assert set(zip(found[0].tolist(), found[1].tolist(), strict=True)) == {(3, 0)}


class TestSplitClose:
    def test_events_nearer_than_the_gap_collapse(self):
        assert _split_close([10, 12, 40, 41, 90], 5) == [10, 40, 90]

    def test_exactly_the_gap_apart_are_kept(self):
        assert _split_close([10, 15], 5) == [10, 15]

    def test_empty(self):
        assert _split_close([], 5) == []


class TestPedalHeadSemantics:
    """`reg_pedal_offset` is the release head, not a sub-frame refinement.

    Reading it as a refinement - or ignoring it and ending a segment at the next press -
    merged a whole phrase into one 10.9 s pedal on the reference recording, which in turn
    pinned every note to the offset search cap.
    """

    def _curves(self, presses: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        down = np.zeros((FRAMES, 1), np.float32)
        up = np.zeros((FRAMES, 1), np.float32)
        frame = np.zeros((FRAMES, 1), np.float32)
        for start, end in presses:
            down[start, 0] = 1.0
            up[end, 0] = 1.0
            frame[start:end, 0] = 1.0
        return down, up, frame

    def test_release_head_ends_the_segment(self):
        down, up, frame = self._curves([(100, 300)])
        assert _decode_pedal(down, up, frame) == [(1.0, 3.0)]

    def test_two_presses_close_together_stay_separate(self):
        down, up, frame = self._curves([(100, 150), (160, 300)])
        segments = _decode_pedal(down, up, frame)
        assert len(segments) == 2
        assert segments[0] == (1.0, 1.5)
        assert segments[1] == (1.6, 3.0)

    def test_a_segment_never_runs_past_the_next_press(self):
        # The old behaviour: end at the next press, so a 7 s "pedal" appeared.
        down, up, frame = self._curves([(100, 700), (800, 900)])
        first = _decode_pedal(down, up, frame)[0]
        assert first[1] == 7.0

    def test_missing_release_falls_back_to_the_frame_head(self):
        down = np.zeros((FRAMES, 1), np.float32)
        down[100, 0] = 1.0
        up = np.zeros((FRAMES, 1), np.float32)
        frame = np.zeros((FRAMES, 1), np.float32)
        frame[100:250, 0] = 1.0
        assert _decode_pedal(down, up, frame) == [(1.0, 2.5)]

    def test_release_without_a_press_is_ignored(self):
        down = np.zeros((FRAMES, 1), np.float32)
        up = np.zeros((FRAMES, 1), np.float32)
        up[300, 0] = 1.0
        frame = np.zeros((FRAMES, 1), np.float32)
        frame[100:300, 0] = 1.0
        assert _decode_pedal(down, up, frame) == []


class TestMergePedals:
    def test_a_seam_duplicate_is_absorbed(self):
        # Overlapping windows decode one press twice, a frame apart.
        assert _merge_pedals([(23.69, 27.20), (23.70, 27.19)]) == [(23.69, 27.20)]

    def test_separate_presses_survive(self):
        assert _merge_pedals([(0.0, 1.0), (2.0, 3.0)]) == [(0.0, 1.0), (2.0, 3.0)]

    def test_touching_presses_merge(self):
        assert _merge_pedals([(0.0, 1.0), (1.05, 2.0)]) == [(0.0, 2.0)]

    def test_input_is_sorted(self):
        assert _merge_pedals([(5.0, 6.0), (0.0, 1.0)]) == [(0.0, 1.0), (5.0, 6.0)]


def _decode(heads: dict[str, np.ndarray], pedal: list[tuple[float, float]] | None = None):
    return _decode_notes(heads["onset"], heads["frame"], heads["velocity"], pedal or [])


class TestDecodeNotes:
    def test_a_note_ends_where_its_frame_drops(self):
        heads = _heads()
        _note(heads, 40, 100, 140)
        notes = _decode(heads)
        assert len(notes) == 1
        start, midi, end, velocity, model_velocity = notes[0]
        assert (start, midi, end) == (100, MIDI_LOW + 40, 140)
        assert velocity == 89  # 0.7 * 127
        assert model_velocity == pytest.approx(0.7, abs=1e-6)

    def test_onset_without_sustain_is_dropped(self):
        heads = _heads()
        heads["onset"][100, 40] = 1.0  # onset but no frame behind it
        assert _decode(heads) == []

    def test_a_held_pedal_extends_a_decaying_note(self):
        # The mechanism the model is here for, stated precisely: the note is still
        # sounding, but quietly (0.1), and with the damper up the amplified frame stays
        # above threshold. The tail stops well inside OFFSET_SEARCH_FRAMES so the end is
        # the pedal's doing and not the search cap's.
        heads = _heads()
        heads["onset"][100, 40] = 1.0
        heads["frame"][100:140, 40] = 0.9
        heads["frame"][140:290, 40] = 0.1  # audible but below threshold
        assert _decode(heads)[0][2] == 140
        assert _decode(heads, [(1.0, 5.0)])[0][2] == 290
        assert 100 + H.OFFSET_SEARCH_FRAMES > 290

    def test_saturated_frames_run_to_the_search_cap(self):
        # The measured failure on the reference recording: pedal_frame median 1.000 and
        # note frames rising to 0.997, so every note ended at OFFSET_SEARCH_FRAMES. This
        # test exists to keep that behaviour visible, not to endorse it - see the module
        # docstring for why the caller's answer is to ignore these offsets.
        heads = _heads()
        heads["onset"][100, 40] = 1.0
        heads["frame"][100:, 40] = 0.99  # never decays
        notes = _decode(heads, [(1.0, 10.0)])
        assert notes[0][2] == min(100 + H.OFFSET_SEARCH_FRAMES, FRAMES)

    def test_onsets_are_not_shifted_by_the_reg_heads(self):
        # `reg_onset` and `reg_offset` are sigmoid-gated, so they are probabilities rather
        # than signed offsets; rounding one to a frame shift introduced an unconditional
        # +1 frame bias (a head value of 1.0 pushed a note at 100 to 101).
        heads = _heads()
        _note(heads, 40, 100, 140)
        heads["offset"][100, 40] = 1.0
        start, _midi, end, *_rest = _decode(heads)[0]
        assert (start, end) == (100, 140)

    def test_a_note_is_never_shorter_than_one_frame(self):
        heads = _heads()
        _note(heads, 40, 100, 101)
        notes = _decode(heads)
        assert notes[0][2] > notes[0][0]

    def test_pitches_above_the_model_range_are_impossible(self):
        heads = _heads()
        _note(heads, PITCHES - 1, 10, 60)
        assert _decode(heads)[0][1] == MIDI_LOW + PITCHES - 1


class TestDedupe:
    def test_seam_copies_collapse_and_keep_the_longer_note(self):
        notes = [(100, 60, 200, 80, 0.6), (100, 60, 203, 80, 0.6)]
        assert _dedupe(notes) == [(100, 60, 203, 80, 0.6)]

    def test_the_shorter_copy_is_dropped(self):
        assert _dedupe([(100, 60, 200, 80, 0.6), (100, 60, 150, 80, 0.6)]) == [
            (100, 60, 200, 80, 0.6)
        ]

    def test_different_pitches_at_one_frame_are_not_merged(self):
        assert len(_dedupe([(100, 60, 200, 80, 0.6), (100, 64, 200, 80, 0.6)])) == 2

    def test_output_keeps_the_five_field_shape(self):
        # A 4-tuple here silently unpacked as midi downstream, so the arity is pinned.
        for note in _dedupe([(100, 60, 200, 80, 0.6)]):
            assert len(note) == 5


class TestSegmentStarts:
    def _stubs(self):
        stub = HighResTranscriber.__new__(HighResTranscriber)
        return stub

    def test_short_audio_is_one_window(self):
        assert self._stubs()._segment_starts(1000) == [0]

    def test_exactly_one_window(self):
        assert self._stubs()._segment_starts(H.WINDOW_SAMPLES) == [0]

    def test_every_window_is_full_length(self):
        starts = self._stubs()._segment_starts(H.WINDOW_SAMPLES * 3)
        assert starts == sorted(set(starts))
        assert all(s + H.WINDOW_SAMPLES <= H.WINDOW_SAMPLES * 3 for s in starts)

    def test_the_tail_is_covered_by_a_final_window(self):
        total = H.WINDOW_SAMPLES * 3 + 12345
        starts = self._stubs()._segment_starts(total)
        assert starts[-1] == total - H.WINDOW_SAMPLES

    def test_no_gap_larger_than_the_stride(self):
        total = H.WINDOW_SAMPLES * 4 + 777
        starts = self._stubs()._segment_starts(total)
        stride = H.WINDOW_SAMPLES - int(H.SEGMENT_MARGIN_SECONDS * H.TARGET_SR)
        assert all(b - a <= stride for a, b in itertools.pairwise(starts))


class TestMissingWeights:
    def test_the_error_says_how_to_install_it(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=re.escape("fetch_models.py --only bytedance")):
            HighResTranscriber(model_path=tmp_path / "absent.onnx")

    def test_the_default_path_is_outside_the_repository(self):
        # These weights are not committed; the default has to point at the gitignored
        # directory that fetch_models.py writes to.
        assert H.MODEL_PATH.parent.name == "bytedance"
        assert "models" in H.MODEL_PATH.parts
