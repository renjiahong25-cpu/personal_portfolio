"""Per-bar quality scoring: the evidence behind as-played vs recomposed.

Every bar gets five measurable checks. Each produces a flag when it fails, and
the flags decide whether the bar keeps the transcription as played or gets a
fresh left-hand part written from the detected chord.

The checks, and why each one means "a pianist could not play this":

``grid``      off-grid onsets. Measured on the test piece, 100 % of onsets were
              within 60 ms of a 16th, so this normally passes - it exists to
              catch a bad tempo estimate rather than to grade playing.
``density``   notes per beat. Above ~6 the bar is a roll nobody can read; below
              ~0.5 the left hand has nothing to do.
``crossing``  a left-hand note above a simultaneous right-hand note.
``overlap``   the same pitch sounding twice in one hand, which is physically
              impossible on a piano and always a transcription artefact.
``hole``      a beat with no left hand at all in a bar that has a right hand.

Anti-flicker: a bar is only recomposed when it fails badly, and hysteresis
requires ``consecutive`` failing bars in a row before a run starts, so a single
weak bar between good ones is kept as played. Without that, adjacent bars can
jump styles and the result sounds like two different pianists.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from services.arrangement.grid import BarGrid
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: Onset must sit within this of a grid position to count as on-grid.
GRID_TOLERANCE = 0.06
#: Notes per beat outside this band is a flag.
MIN_NOTES_PER_BEAT = 0.5
MAX_NOTES_PER_BEAT = 6.0
#: Fraction of onsets that must be on-grid for the bar to pass.
MIN_GRID_FIT = 0.9
#: Score at or below which a bar is considered for recomposition.
RECOMPOSE_SCORE = 55.0
#: Consecutive failing bars required before a recomposed run starts.
HYSTERESIS_BARS = 2
#: Flags that mean the bar cannot be played as transcribed. These bypass the
#: score threshold entirely: a single same-pitch overlap is physically
#: impossible, so no amount of good density elsewhere makes it worth keeping.
HARD_FLAGS = ("overlap", "crossing")
#: Score penalty per flag.
PENALTIES = {"overlap": 30.0, "crossing": 25.0, "density": 20.0, "grid": 15.0, "hole": 15.0}


@dataclass(frozen=True)
class QualityThresholds:
    grid_tolerance: float = GRID_TOLERANCE
    min_grid_fit: float = MIN_GRID_FIT
    min_notes_per_beat: float = MIN_NOTES_PER_BEAT
    max_notes_per_beat: float = MAX_NOTES_PER_BEAT
    recompose_score: float = RECOMPOSE_SCORE
    hysteresis_bars: int = HYSTERESIS_BARS


@dataclass(frozen=True)
class BarQuality:
    index: int
    score: float
    notes: int
    notes_per_beat: float
    grid_fit: float
    crossings: int
    overlaps: int
    left_hand_holes: int
    flags: tuple[str, ...] = field(default=())

    @property
    def failed(self) -> bool:
        return bool(self.flags)

    @property
    def hard_fault(self) -> bool:
        """True when the bar cannot be played as transcribed."""
        return any(flag in HARD_FLAGS for flag in self.flags)

    def to_dict(self) -> dict[str, object]:
        return {
            "bar": self.index,
            "score": round(self.score, 1),
            "notes": self.notes,
            "notesPerBeat": round(self.notes_per_beat, 2),
            "gridFit": round(self.grid_fit, 3),
            "crossings": self.crossings,
            "overlaps": self.overlaps,
            "leftHandHoles": self.left_hand_holes,
            "flags": list(self.flags),
        }


@dataclass(frozen=True)
class QualityReport:
    bars: tuple[BarQuality, ...]
    recomposed_bars: tuple[int, ...]

    @property
    def mean_score(self) -> float:
        if not self.bars:
            return 0.0
        return sum(b.score for b in self.bars) / len(self.bars)

    def to_dict(self) -> dict[str, object]:
        return {
            "barCount": len(self.bars),
            "meanScore": round(self.mean_score, 1),
            "recomposedBars": list(self.recomposed_bars),
            "bars": [b.to_dict() for b in self.bars],
        }


def _same_pitch_overlap(notes: Sequence[NoteEvent]) -> int:
    """Count same-pitch overlaps inside one hand (unplayable)."""
    ordered = sorted(notes, key=lambda n: (n.midi, n.start))
    overlaps = 0
    for position, note in enumerate(ordered):
        for other in ordered[position + 1 :]:
            if other.midi != note.midi:
                break
            if other.start >= note.end - 1e-6:
                break
            overlaps += 1
    return overlaps


def _count_crossings(right: Sequence[NoteEvent], left: Sequence[NoteEvent]) -> int:
    """Left-hand notes above a simultaneous right-hand note."""
    crossings = 0
    for left_note in left:
        for right_note in right:
            if right_note.start >= left_note.end - 1e-6:
                break
            if left_note.midi > right_note.midi:
                crossings += 1
    return crossings


def _left_hand_holes(left: Sequence[NoteEvent], grid: BarGrid, bar: int) -> int:
    """Beats inside the bar where the left hand is silent."""
    start = grid.bar_start(bar)
    beats = grid.beats_per_bar
    holes = 0
    for beat in range(beats):
        beat_start = start + beat * grid.beat_seconds
        beat_end = beat_start + grid.beat_seconds
        sounding = any(
            note.start < beat_end - 1e-6 and note.end > beat_start + 1e-6 for note in left
        )
        if not sounding:
            holes += 1
    return holes


def assess_bar(
    right: Sequence[NoteEvent],
    left: Sequence[NoteEvent],
    grid: BarGrid,
    bar: int,
    *,
    thresholds: QualityThresholds | None = None,
) -> BarQuality:
    """Score a single bar."""
    limits = thresholds or QualityThresholds()
    notes = [*right, *left]
    beats = max(grid.beats_per_bar, 1)
    notes_per_beat = len(notes) / beats

    flags: list[str] = []
    if notes:
        on_grid = sum(1 for note in notes if grid.quantize(note.start)[3] <= limits.grid_tolerance)
        grid_fit = on_grid / len(notes)
    else:
        grid_fit = 0.0
    if notes and grid_fit < limits.min_grid_fit:
        flags.append("grid")

    if notes and (
        notes_per_beat > limits.max_notes_per_beat or notes_per_beat < limits.min_notes_per_beat
    ):
        flags.append("density")

    crossings = _count_crossings(right, left)
    if crossings:
        flags.append("crossing")

    overlaps = _same_pitch_overlap(right) + _same_pitch_overlap(left)
    if overlaps:
        flags.append("overlap")

    holes = _left_hand_holes(left, grid, bar) if right else 0
    if right and holes == beats:
        flags.append("hole")

    penalty = sum(PENALTIES.get(flag, 10.0) for flag in flags)
    score = max(0.0, 100.0 - penalty)

    return BarQuality(
        index=bar,
        score=score,
        notes=len(notes),
        notes_per_beat=notes_per_beat,
        grid_fit=grid_fit,
        crossings=crossings,
        overlaps=overlaps,
        left_hand_holes=holes,
        flags=tuple(flags),
    )


def assess_bars(
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]],
    grid: BarGrid,
    *,
    thresholds: QualityThresholds | None = None,
) -> QualityReport:
    """Score every bar, then decide which bars get recomposed."""
    limits = thresholds or QualityThresholds()
    qualities = tuple(
        assess_bar(
            buckets.get(index, ([], []))[0],
            buckets.get(index, ([], []))[1],
            grid,
            index,
            thresholds=limits,
        )
        for index in range(grid.bar_count())
    )

    recomposed: list[int] = []
    run: list[int] = []
    for quality in qualities:
        # A hard fault bypasses the score threshold; the hysteresis still
        # applies, so one bad bar between good ones is kept as played.
        bad = quality.hard_fault or quality.score <= limits.recompose_score
        if bad:
            run.append(quality.index)
            continue
        if run:
            if len(run) >= limits.hysteresis_bars:
                recomposed.extend(run)
            run = []
    if len(run) >= limits.hysteresis_bars:
        recomposed.extend(run)

    report = QualityReport(bars=qualities, recomposed_bars=tuple(recomposed))
    logger.info(
        "bar quality: %d bars, mean %.1f, recomposing %d",
        len(qualities),
        report.mean_score,
        len(recomposed),
    )
    return report


__all__ = [
    "GRID_TOLERANCE",
    "HYSTERESIS_BARS",
    "RECOMPOSE_SCORE",
    "BarQuality",
    "QualityReport",
    "QualityThresholds",
    "assess_bar",
    "assess_bars",
]
