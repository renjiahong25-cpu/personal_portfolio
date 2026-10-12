"""Extract clean melody from mixed audio using HPSS + F0 tracking.

This module implements the first step of professional arrangement:
1. Separate harmonic (melody) from percussive (rhythm) using HPSS
2. Extract F0 contour from the harmonic part
3. Generate clean melody notes from F0 + basic-pitch transcription
4. Output: pure melody MIDI + phrase boundaries + motif markers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from services.audio_analysis.polyphony import BasicPitchTranscriber, NoteEvent

logger = logging.getLogger(__name__)


@dataclass
class MelodyExtraction:
    """Result of melody extraction."""

    notes: list[NoteEvent]
    f0_contour: np.ndarray
    phrase_boundaries: list[float]
    motif_markers: list[tuple[float, float]]


def extract_melody_hpss(
    y: np.ndarray,
    sr: int,
    *,
    fmin: float = 150.0,  # Filter out bass accompaniment
    fmax: float = 1200.0,  # C6
) -> MelodyExtraction:
    """Extract melody using HPSS + F0 tracking.

    Professional arrangement principle:
    - HPSS separates harmonic (melody + chords) from percussive (drums)
    - F0 tracking finds the fundamental frequency of the melody
    - Basic-pitch transcribes the harmonic part for note onsets
    - Combine F0 + transcription for clean melody notes
    """
    # 1. HPSS separation
    harmonic, percussive = librosa.effects.hpss(y)
    logger.info(
        f"HPSS: harmonic RMS={np.sqrt(np.mean(harmonic**2)):.4f}, "
        f"percussive RMS={np.sqrt(np.mean(percussive**2)):.4f}"
    )

    # 2. F0 tracking on harmonic part
    f0, voiced_flag, voiced_probs = librosa.pyin(
        harmonic,
        fmin=fmin,
        fmax=fmax,
        sr=sr,
        frame_length=2048,
        hop_length=512,
    )
    logger.info(f"F0: {len(f0)} frames, voiced={np.sum(voiced_flag)}/{len(f0)}")

    # 3. Transcribe harmonic part with basic-pitch
    transcriber = BasicPitchTranscriber()
    events = transcriber.transcribe_array(harmonic, sr)
    logger.info(f"Transcribed {len(events)} events from harmonic part")

    # 4. Extract melody notes from F0 contour
    melody_notes = _f0_to_notes(f0, voiced_flag, sr)

    # 5. Detect phrase boundaries (energy valleys)
    phrase_boundaries = _detect_phrases(y, sr)

    # 6. Detect motifs (repeated patterns)
    motif_markers = _detect_motifs(melody_notes)

    return MelodyExtraction(
        notes=melody_notes,
        f0_contour=f0,
        phrase_boundaries=phrase_boundaries,
        motif_markers=motif_markers,
    )


def _f0_to_notes(f0: np.ndarray, voiced_flag: np.ndarray, sr: int) -> list[NoteEvent]:
    """Convert F0 contour to melody notes.

    Group consecutive voiced frames with similar F0 into notes.
    """
    notes: list[NoteEvent] = []
    hop_length = 512
    frame_duration = hop_length / sr

    current_note: NoteEvent | None = None
    current_f0: float | None = None

    for i, (freq, voiced) in enumerate(zip(f0, voiced_flag)):
        if not voiced or np.isnan(freq):
            # End current note
            if current_note is not None:
                notes.append(current_note)
                current_note = None
                current_f0 = None
            continue

        # Convert F0 to MIDI
        midi = int(round(librosa.hz_to_midi(freq)))

        if current_note is None:
            # Start new note
            current_note = NoteEvent(
                start=i * frame_duration,
                duration=frame_duration,
                midi=midi,
                amplitude=0.8,
            )
            current_f0 = freq
        else:
            # Continue or end note based on pitch change
            if abs(midi - current_note.midi) >= 2:  # 2 semitones = new note
                notes.append(current_note)
                current_note = NoteEvent(
                    start=i * frame_duration,
                    duration=frame_duration,
                    midi=midi,
                    amplitude=0.8,
                )
                current_f0 = freq
            else:
                # Extend current note
                current_note = NoteEvent(
                    start=current_note.start,
                    duration=(i * frame_duration - current_note.start) + frame_duration,
                    midi=current_note.midi,
                    amplitude=current_note.amplitude,
                )

    # Don't forget the last note
    if current_note is not None:
        notes.append(current_note)

    # Filter out very short notes (< 0.1s)
    notes = [n for n in notes if n.duration >= 0.1]

    logger.info(f"Extracted {len(notes)} melody notes from F0")
    return notes


def _detect_phrases(y: np.ndarray, sr: int) -> list[float]:
    """Detect phrase boundaries using energy valleys.

    Phrases are separated by brief silences or energy drops.
    """
    # Compute RMS energy
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    hop_duration = 512 / sr

    # Find energy valleys (local minima below threshold)
    threshold = np.percentile(rms, 20)
    valleys: list[float] = []

    for i in range(1, len(rms) - 1):
        if rms[i] < threshold and rms[i] <= rms[i - 1] and rms[i] <= rms[i + 1]:
            valleys.append(i * hop_duration)

    # Merge nearby valleys (within 1 second)
    merged: list[float] = []
    for v in valleys:
        if not merged or v - merged[-1] > 1.0:
            merged.append(v)

    logger.info(f"Detected {len(merged)} phrase boundaries")
    return merged


def _detect_motifs(notes: list[NoteEvent]) -> list[tuple[float, float]]:
    """Detect repeated melodic motifs.

    A motif is a sequence of 3-5 notes that repeats.
    """
    if len(notes) < 6:
        return []

    motifs: list[tuple[float, float]] = []

    # Simple motif detection: look for repeated interval patterns
    for i in range(len(notes) - 5):
        # Get interval pattern of 4 notes
        pattern = tuple(notes[i + j + 1].midi - notes[i + j].midi for j in range(4))

        # Look for same pattern later
        for j in range(i + 4, len(notes) - 4):
            other_pattern = tuple(notes[j + k + 1].midi - notes[j + k].midi for k in range(4))
            if pattern == other_pattern:
                motifs.append((notes[i].start, notes[j].start))
                break

    logger.info(f"Detected {len(motifs)} motif repetitions")
    return motifs


def main() -> None:
    """Test melody extraction on the source audio."""
    logging.basicConfig(level=logging.INFO)

    repo_root = Path(__file__).resolve().parents[2]
    source = repo_root / "cache" / "audio" / "b6cdcdb386a4b3b271d6be75fc874902a81642d8.wav"
    y, sr = librosa.load(str(source), sr=22050, mono=True, duration=30)

    result = extract_melody_hpss(y, sr)

    print("\n=== 旋律提取结果 ===")
    print(f"旋律音符: {len(result.notes)} 个")
    print(f"乐句边界: {len(result.phrase_boundaries)} 个")
    print(f"动机标记: {len(result.motif_markers)} 个")

    if result.notes:
        pitches = [n.midi for n in result.notes]
        print(f"音高范围: {min(pitches)}-{max(pitches)}")
        print(f"音高均值: {np.mean(pitches):.1f}")

        # 显示前10个音符
        print("\n前10个音符:")
        for i, note in enumerate(result.notes[:10]):
            print(
                f"  {i + 1}. {librosa.midi_to_note(note.midi)} @ {note.start:.2f}s, 时长 {note.duration:.2f}s"
            )


if __name__ == "__main__":
    main()
