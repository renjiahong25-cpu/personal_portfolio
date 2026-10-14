"""Song section detection: intro / verse / chorus / interlude / bridge / outro.

Why this exists
---------------
The arrange module rebuilds the left hand, and one figure for the whole song is
musically wrong: a chorus wants a fuller texture than an intro, and a bridge
should not get the verse's rhythm. This module splits a song into its sections
and labels them, so each section can carry its own texture
(:func:`build_section_texture_plan`).

How the labels are decided
--------------------------
A fixed rule chain over deterministic features. Same song, same machine or not,
same sections: no learning, no random seed, no run-order dependence. Every
threshold is a named constant, and every section ships its ``evidence`` dict so
a wrong label can be inspected ("this was called a verse because its vocal
coverage was 0.42 and it clusters with the other verse").

1. **Per-bar features** - energy (RMS), spectral centroid, chord-change count,
   the top voice's 12-bin pitch-class profile and pitch level. Everything except
   the centroid is already computed by the pipeline; the centroid is one FFT per
   bar.
2. **Boundaries** - a novelty score at every bar (the distance between the
   feature vectors on either side), thresholded at ``mean + k*MAD``. The
   threshold adapts to the song: a quiet ballad and a loud rock song both
   segment, without a hand-tuned absolute level.
3. **Fingerprints** - each section's pitch-class profile, chord sequence,
   energy and pitch level.
4. **Parts** - sections whose fingerprints are close enough (>= 0.75) are the
   *same part*: A A B B, whatever the order. This is the generalisation the
   user asked for: the detector never assumes a form. AABA, ABAB, ABABCAB all
   first come out as "which sections repeat", and only then get names.
5. **Labels** - positional and vocal rules first (the user's definitions),
   part scoring second:

   * **intro** - the first section, and nobody is singing in it.
   * **outro** - the last section, and nobody is singing in it.
   * **interlude** - a mid-song section, nobody singing, singing before and after.
   * **chorus** - among the singing parts, the one that is both the loudest and
     the highest (energy z + pitch z, averaged over the part's sections).
   * **verse** - the singing parts that are not the chorus.
   * **bridge** - a singing section that belongs to no repeated part and sits
     between two sections of the same part (the C in ABAB**C**AB).

   "Nobody is singing" comes from Whisper phrase coverage per bar, not from F0:
   on a full mix an F0 detector locks onto instruments, so a piano intro would
   read as voiced and the intro would be mislabelled verse. Whisper only hears
   speech, which is exactly the definition of "有 vocals".

When Whisper is unavailable (no ML environment, or the run fails) the detector
degrades instead of failing: intro/interlude/outro fall back to energy valleys,
chorus/verse to energy alone, and every confidence comes down with a ``mode``
field saying the evidence was missing. A piano recording has no vocals, which
is a *fact the detector reports*, not an error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from services.arrangement.grid import BarGrid
from services.arrangement.textures import TextureName, TexturePlan, TextureSection
from services.arrangement.vocal_melody import DEFAULT_TIMEOUT_SECONDS, WORKER, _worker_python
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

#: The label vocabulary, in the order the UI renders the strip.
SECTION_LABELS: tuple[str, ...] = (
    "intro",
    "verse",
    "chorus",
    "interlude",
    "bridge",
    "outro",
)

#: What each label gets when the caller did not pick a texture for it. The intro
#: is the one conditional default: with a high-register motif it needs the flowing
#: line to sit under the tune, without one the bare bass is the honest choice.
DEFAULT_SECTION_TEXTURES: Mapping[str, str] = {
    "intro": "arplong",
    "verse": "half",
    "chorus": "arpfull",
    "interlude": "arplong",
    "bridge": "half",
    "outro": "arplong",
}
INTRO_TEXTURE_NO_MOTIF = "bass"

#: A high-register motif: the top voice's median sits at or above this MIDI pitch
#: (C5) with enough notes to be a line, not a stray.
HIGH_MOTIF_MIDI = 72
HIGH_MOTIF_MIN_NOTES = 8

# ----------------------------------------------------------------- the thresholds

#: Shorter than this and a "section" is a bar, not a form.
MIN_SECTION_BARS = 4
#: No boundary within the first/last two bars: an intro that starts on bar 1 is
#: the rule, not the exception, and the edge is where novelty is most spiky.
EDGE_GUARD_BARS = 2
#: Feature bars each side of a candidate boundary.
NOVELTY_WINDOW = 2
#: Boundary = a novelty peak above mean + k*MAD (robust: MAD, not std, so one
#: explosive drop-out does not raise the bar for every other boundary).
NOVELTY_K = 3.0
#: Two sections are the same part at or above this similarity.
PART_SIMILARITY = 0.75
#: Fingerprint weights: the tune (pitch classes) and the harmony carry the
#: repetition; energy only tells chorus apart from verse.
W_PITCH_CLASS = 0.4
W_CHORD = 0.4
W_ENERGY = 0.2
#: Below this fraction of a bar covered by sung phrases, the section has no vocals.
VOCAL_COVERAGE_NO_SINGER = 0.10
#: In the energy fallback, a section below this fraction of the song's mean RMS
#: counts as a valley (intro/interlude/outro candidate).
ENERGY_VALLEY_FRACTION = 0.75
#: A one-off part is called the chorus only when it beats the best repeated part
#: by more than this many score units (z of energy plus z of pitch). Below the
#: margin, repetition is the stronger chorus evidence.
SINGLETON_CHORUS_MARGIN = 0.75


def default_texture_for(label: str, *, high_motif: bool = False) -> str:
    """The texture a label gets when the caller did not override it."""
    if label not in DEFAULT_SECTION_TEXTURES:
        raise ValueError(f"unknown section label: {label}")
    if label == "intro" and not high_motif:
        return INTRO_TEXTURE_NO_MOTIF
    return DEFAULT_SECTION_TEXTURES[label]


# ----------------------------------------------------------------- the data model


@dataclass(frozen=True)
class VocalEvidence:
    """Per-bar sung-phrase coverage from Whisper, or its absence."""

    coverage: tuple[float, ...]  # per bar, 0..1
    available: bool
    language: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "language": self.language,
            "coverage": [round(c, 3) for c in self.coverage],
        }


@dataclass(frozen=True)
class Section:
    """One named bar range with the evidence behind its label."""

    start_bar: int
    end_bar: int  # exclusive
    label: str
    confidence: float  # 0..1
    high_motif: bool = False
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def bar_count(self) -> int:
        return self.end_bar - self.start_bar

    def to_dict(self) -> dict[str, object]:
        return {
            "startBar": self.start_bar,
            "endBar": self.end_bar,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "highMotif": self.high_motif,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class SectionAnalysis:
    """The detector's full answer: sections, the vocal evidence, the song length."""

    sections: tuple[Section, ...]
    vocal: VocalEvidence
    duration_seconds: float
    transcriber: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "vocal": self.vocal.to_dict(),
            "durationSeconds": round(self.duration_seconds, 3),
            "transcriber": self.transcriber,
        }


# --------------------------------------------------------------- stage 1: features


@dataclass(frozen=True)
class BarFeatures:
    """Everything the detector knows about one bar."""

    rms: float
    centroid: float
    chord_changes: int
    pc_profile: tuple[int, ...]  # 12-bin histogram of the top voice
    top_notes: tuple[int, ...]  # the top voice's MIDI notes, in order


def bar_features(
    y: np.ndarray,
    sr: int,
    grid: BarGrid,
    chords: Sequence[ChordEvent],
    top_voice_by_bar: Sequence[Sequence[int]],
) -> list[BarFeatures]:
    """Stage 1: one feature vector per bar. Pure numpy, deterministic."""
    n = grid.bar_count()
    duration = len(y) / sr
    feats: list[BarFeatures] = []
    for bar in range(n):
        start_s = grid.bar_start(bar)
        end_s = min(grid.bar_end(bar), duration)
        i0, i1 = int(start_s * sr), int(end_s * sr)
        segment = y[i0:i1]
        rms = float(np.sqrt(np.mean(np.square(segment)))) if segment.size else 0.0
        if segment.size >= 256:
            windowed = segment * np.hanning(segment.size)
            spectrum = np.abs(np.fft.rfft(windowed))
            freqs = np.fft.rfftfreq(segment.size, 1.0 / sr)
            total = float(spectrum.sum())
            centroid = float((freqs * spectrum).sum() / total) if total > 0 else 0.0
        else:
            centroid = 0.0
        changes = sum(1 for c in chords if start_s <= c.start < end_s)
        tops = tuple(int(m) for m in top_voice_by_bar[bar]) if bar < len(top_voice_by_bar) else ()
        pc = [0] * 12
        for m in tops:
            pc[m % 12] += 1
        feats.append(BarFeatures(rms, centroid, changes, tuple(pc), tops))
    return feats


# --------------------------------------------------------------- stage 2: boundaries


def _novelty_matrix(features: Sequence[BarFeatures]) -> np.ndarray:
    """The bar feature vectors for boundary detection, z-normalised.

    Deliberately only the section-stable dimensions: energy, brightness and the
    top voice's pitch level. The per-bar detail - the pitch-class profile and the
    chord onsets - is left out because it is periodic *inside* a section (a melody
    moves, a progression cycles), so it reads as constant motion rather than
    change and raises the novelty floor above the real boundaries. That detail
    earns its keep in the fingerprint stage, where it separates sections that sit
    at the same energy and pitch.
    """
    n = len(features)
    matrix = np.zeros((n, 3), dtype=float)
    for i, f in enumerate(features):
        matrix[i, 0] = f.rms
        matrix[i, 1] = f.centroid
        matrix[i, 2] = float(np.median(f.top_notes)) if f.top_notes else 0.0
    std = matrix.std(axis=0)
    std[std <= 0] = 1.0
    return (matrix - matrix.mean(axis=0)) / std


def find_boundaries(features: Sequence[BarFeatures]) -> list[int]:
    """Stage 2: the bar indices where the music changes, nothing else.

    A boundary is a novelty peak above the song's own noise floor
    (mean + k*MAD), with a minimum distance to its neighbours and to the edges,
    so the output is a set of sections each at least ``MIN_SECTION_BARS`` long.
    """
    n = len(features)
    if n < 2 * MIN_SECTION_BARS:
        return []
    matrix = _novelty_matrix(features)
    w = NOVELTY_WINDOW
    novelty = np.zeros(n)
    for b in range(w, n - w + 1):
        left = matrix[b - w : b].mean(axis=0)
        right = matrix[b : b + w].mean(axis=0)
        novelty[b] = float(np.linalg.norm(left - right))

    median = float(np.median(novelty))
    mad = float(np.median(np.abs(novelty - median))) * 1.4826
    if mad <= 0:
        # Most bars exactly identical (a piecewise-flat song): MAD is zero and
        # would reject every boundary, so fall back to the plain standard
        # deviation of the curve.
        mad = float(novelty.std())
    if mad <= 0:
        return []
    threshold = median + NOVELTY_K * mad

    peaks = [
        b
        for b in range(1, n - 1)
        if novelty[b] > threshold and novelty[b] >= novelty[b - 1] and novelty[b] > novelty[b + 1]
    ]
    # Strongest peaks first: a contested region keeps its loudest boundary.
    peaks.sort(key=lambda b: -novelty[b])
    accepted: list[int] = []
    for b in peaks:
        if b < MIN_SECTION_BARS or n - b < MIN_SECTION_BARS:
            continue
        if b < EDGE_GUARD_BARS:
            continue
        if all(abs(b - a) >= MIN_SECTION_BARS for a in accepted):
            accepted.append(b)
    return sorted(accepted)


def _spans(n: int, boundaries: Sequence[int]) -> list[tuple[int, int]]:
    edges = [0, *boundaries, n]
    return list(zip(edges[:-1], edges[1:], strict=True))


# --------------------------------------------------------------- stage 3: fingerprints


@dataclass(frozen=True)
class _Fingerprint:
    """How a section sounds, condensed to what repetition detection needs."""

    pc: np.ndarray  # 12-bin histogram of the top voice
    chords: tuple[str, ...]  # the chord sequence, consecutive duplicates collapsed
    energy: float  # mean RMS
    pitch_level: float  # median top-voice MIDI (0.0 when there is none)
    top_count: int
    chord_changes: int


def _chord_names_per_bar(
    grid: BarGrid, chords: Sequence[ChordEvent], start: int, end: int
) -> list[str]:
    """The chord at each bar's middle; empty string where the detector heard none."""
    names: list[str] = []
    for bar in range(start, end):
        midpoint = (grid.bar_start(bar) + grid.bar_end(bar)) / 2
        covering = [c for c in chords if c.start <= midpoint < c.end]
        names.append(max(covering, key=lambda c: c.end - c.start).name if covering else "")
    return names


def section_fingerprints(
    features: Sequence[BarFeatures],
    spans: Sequence[tuple[int, int]],
    grid: BarGrid,
    chords: Sequence[ChordEvent],
) -> tuple[_Fingerprint, ...]:
    """Stage 3: one fingerprint per section."""
    fps: list[_Fingerprint] = []
    for start, end in spans:
        chunk = features[start:end]
        pc = np.array([sum(f.pc_profile[j] for f in chunk) for j in range(12)], dtype=float)
        raw = _chord_names_per_bar(grid, chords, start, end)
        collapsed: list[str] = []
        for name in raw:
            if name and (not collapsed or collapsed[-1] != name):
                collapsed.append(name)
        tops = [m for f in chunk for m in f.top_notes]
        fps.append(
            _Fingerprint(
                pc=pc,
                chords=tuple(collapsed),
                energy=float(np.mean([f.rms for f in chunk])) if chunk else 0.0,
                pitch_level=float(np.median(tops)) if tops else 0.0,
                top_count=len(tops),
                chord_changes=sum(f.chord_changes for f in chunk),
            )
        )
    return tuple(fps)


def section_similarity(a: _Fingerprint, b: _Fingerprint) -> float:
    """How much two sections sound like the same part of a song.

    The pitch-class profile ignores *when* a note happens (a chorus that revoices
    the same melody a bar later still matches), the chord sequence keeps the
    order, and the energy closeness is a ratio so loud and quiet songs compare.
    """
    norm_a = float(np.linalg.norm(a.pc))
    norm_b = float(np.linalg.norm(b.pc))
    if norm_a <= 0 or norm_b <= 0:
        pc_score = 0.5  # no top voice: uninformative, neither similar nor different
    else:
        pc_score = float(np.dot(a.pc, b.pc) / (norm_a * norm_b))
    if a.chords and b.chords:
        length = min(len(a.chords), len(b.chords))
        chord_score = sum(1 for x, y in zip(a.chords[:length], b.chords[:length], strict=False) if x == y) / length
    else:
        chord_score = 0.5
    energy_max = max(a.energy, b.energy)
    energy_score = 1.0 - abs(a.energy - b.energy) / energy_max if energy_max > 0 else 1.0
    return W_PITCH_CLASS * pc_score + W_CHORD * chord_score + W_ENERGY * energy_score


# --------------------------------------------------------------- stage 4: parts


def cluster_parts(fingerprints: Sequence[_Fingerprint]) -> tuple[int, ...]:
    """Stage 4: which sections are the same part (union of pairwise similarity).

    Connected components rather than a fitted model: if A~B and B~C, they are one
    part, and the labels the UI reads are the part letters A, B, C in order of
    first appearance.
    """
    n = len(fingerprints)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if section_similarity(fingerprints[i], fingerprints[j]) >= PART_SIMILARITY:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)

    roots = sorted({find(i) for i in range(n)})
    rename = {root: letter for letter, root in enumerate(roots)}
    return tuple(rename[find(i)] for i in range(n))


def _part_letters(parts: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(chr(ord("A") + p) for p in parts)


# --------------------------------------------------------------- stage 5: labels


def _z(values: Sequence[float]) -> list[float]:
    arr = np.asarray(values, dtype=float)
    std = float(arr.std())
    if std <= 0:
        return [0.0] * len(values)
    return ((arr - arr.mean()) / std).tolist()


def _mean(coverage: Sequence[float], span: tuple[int, int]) -> float:
    a, b = span
    if b <= a:
        return 0.0
    return float(np.mean(coverage[a:b])) if coverage else 0.0


def _energy_valley_flags(fingerprints: Sequence[_Fingerprint]) -> list[bool]:
    """The fallback's notion of 'nobody singing': an energy valley.

    Against the mean, not the median: in a song that is mostly quiet with one loud
    part, the median *is* the quiet level, so no section reads as a valley. The
    mean sits in between and separates the two.
    """
    energies = [f.energy for f in fingerprints]
    mean = float(np.mean(energies)) if energies else 0.0
    return [e < ENERGY_VALLEY_FRACTION * mean for e in energies]


def _sandwiched(index: int, parts: Sequence[int], labels: Sequence[str | None]) -> bool:
    """True when both neighbours are already labelled with the same part."""
    if index <= 0 or index >= len(parts) - 1:
        return False
    prev, nxt = index - 1, index + 1
    return (
        parts[prev] == parts[nxt]
        and labels[prev] is not None
        and labels[nxt] is not None
        and labels[prev] == labels[nxt]
    )


def assign_labels(
    spans: Sequence[tuple[int, int]],
    fingerprints: Sequence[_Fingerprint],
    parts: Sequence[int],
    mean_coverage: Sequence[float],
    vocal_available: bool,
) -> list[Section]:
    """Stage 5: names for the sections, with the confidence behind each name.

    The order of the rules is the answer to "how does it generalise": positional
    and vocal rules first (intro/outro/interlude are defined by position and by
    the absence of singing, full stop), then the repetition parts get verse and
    chorus by energy and pitch height, and whatever singing section is left over
    is a bridge when it is sandwiched between the same part, a verse when it is
    not. A single-section song gets a verse with a low confidence and a note,
    because there is genuinely nothing to compare it with.
    """
    n = len(spans)
    letters = _part_letters(tuple(parts))

    def make(i: int, label: str, confidence: float, mode: str) -> Section:
        f = fingerprints[i]
        cov = mean_coverage[i] if vocal_available else None
        evidence: dict[str, object] = {
            "part": letters[i],
            "vocalCoverage": round(cov, 3) if cov is not None else None,
            "rms": round(f.energy, 5),
            "topMedianMidi": int(f.pitch_level) if f.top_count else None,
            "chordChanges": f.chord_changes,
            "chords": "-".join(f.chords[:8]),
            "highMotif": _is_high_motif(f),
            "mode": mode,
        }
        return Section(
            start_bar=spans[i][0],
            end_bar=spans[i][1],
            label=label,
            confidence=round(max(0.05, min(0.95, confidence)), 3),
            high_motif=_is_high_motif(f),
            evidence=evidence,
        )

    if n == 1:
        # One section: there is nothing to compare it with. A sung whole gets a
        # verse (no chorus to contrast against); an instrumental whole gets an
        # intro, whose flowing/sparse texture suits a through-composed piece.
        mode = "vocal" if vocal_available else "energy"
        has_voice = vocal_available and mean_coverage[0] >= VOCAL_COVERAGE_NO_SINGER
        return [make(0, "verse" if has_voice else "intro", 0.4, mode)]

    valley = _energy_valley_flags(fingerprints)
    # "Known to have no singing": only a fact when the evidence exists. Without it
    # no section may be called vocal or not-vocal - the energy valleys take over.
    no_vocal = [
        vocal_available and mean_coverage[i] < VOCAL_COVERAGE_NO_SINGER for i in range(n)
    ]
    labels: list[str | None] = [None] * n
    conf: list[float] = [0.0] * n
    modes: list[str] = ["vocal" if vocal_available else "energy"] * n

    # -- intro: first section, no singing (or an energy valley in fallback)
    if no_vocal[0]:
        labels[0] = "intro"
        conf[0] = 0.8 + 0.2 * (1 - (mean_coverage[0] / VOCAL_COVERAGE_NO_SINGER))
    elif not vocal_available and valley[0]:
        labels[0] = "intro"
        conf[0] = 0.55

    # -- outro: last section, no singing
    if no_vocal[-1]:
        labels[-1] = "outro"
        conf[-1] = 0.8 + 0.2 * (1 - (mean_coverage[-1] / VOCAL_COVERAGE_NO_SINGER))
    elif not vocal_available and valley[-1]:
        labels[-1] = "outro"
        conf[-1] = 0.55

    # -- interlude: a mid-song section nobody is singing in; a valley in fallback
    for i in range(1, n - 1):
        if labels[i] is not None:
            continue
        if no_vocal[i]:
            sung_before = any(mean_coverage[j] >= VOCAL_COVERAGE_NO_SINGER for j in range(i))
            sung_after = any(mean_coverage[j] >= VOCAL_COVERAGE_NO_SINGER for j in range(i + 1, n))
            labels[i] = "interlude"
            base = 0.8 + 0.1 * (1 - (mean_coverage[i] / VOCAL_COVERAGE_NO_SINGER))
            conf[i] = base + (0.1 if (sung_before and sung_after) else -0.1)
        elif not vocal_available and valley[i] and not valley[i - 1] and not valley[i + 1]:
            labels[i] = "interlude"
            conf[i] = 0.5

    # -- an instrumental section with no vocal context (only reachable in odd
    #    layouts) is an interlude rather than a verse
    for i in range(n):
        if labels[i] is None and no_vocal[i]:
            labels[i] = "interlude"
            conf[i] = 0.6

    # -- verse / chorus / bridge over the sections that remain
    remaining = [i for i in range(n) if labels[i] is None]
    if remaining:
        _label_vocal_sections(remaining, fingerprints, parts, labels, conf)

    return [make(i, labels[i] or "verse", conf[i] or 0.5, modes[i]) for i in range(n)]


def _is_high_motif(f: _Fingerprint) -> bool:
    return f.top_count >= HIGH_MOTIF_MIN_NOTES and f.pitch_level >= HIGH_MOTIF_MIDI


def _label_vocal_sections(
    remaining: list[int],
    fingerprints: Sequence[_Fingerprint],
    parts: Sequence[int],
    labels: list[str | None],
    conf: list[float],
) -> None:
    """Chorus/verse/bridge for the sections the positional rules left unnamed.

    The chorus is the singing part that scores highest on energy plus pitch
    height (z-scores, averaged over the part's sections) - which is exactly what
    makes a chorus *feel* like a chorus. When the top-scoring part is a one-off,
    repetition wins unless the one-off is clearly above it: a hook sung once can
    out-energy the verses, but a verse/chorus song has its chorus twice.
    """
    by_part: dict[int, list[int]] = {}
    for i in remaining:
        by_part.setdefault(int(parts[i]), []).append(i)

    energies = _z([fingerprints[i].energy for i in remaining])
    pitches = _z([fingerprints[i].pitch_level for i in remaining])
    score = {i: energies[k] + pitches[k] for k, i in enumerate(remaining)}
    part_score = {
        part: float(np.mean([score[i] for i in members])) for part, members in by_part.items()
    }
    ranked = sorted(part_score, key=lambda p: -part_score[p])
    best = ranked[0]

    if len(ranked) == 1:
        # One singing part: there is no verse/chorus contrast in this song, so the
        # conservative label is verse. Calling it a chorus would hand it the loud
        # texture with nothing to stand against.
        for i in by_part[best]:
            labels[i] = "verse"
            conf[i] = 0.5
        return

    best_members = by_part[best]
    runner = next((p for p in ranked[1:] if len(by_part[p]) >= 2), None)
    if len(best_members) >= 2:
        for i in best_members:
            labels[i] = "chorus"
            conf[i] = 0.8 + min(0.1, 0.05 * (part_score[best] - part_score[ranked[1]]))
    elif runner is not None and part_score[best] - part_score[runner] < SINGLETON_CHORUS_MARGIN:
        # A one-off only marginally above a repeated part: the repetition is the
        # stronger chorus evidence, and the one-off is a bridge when it sits
        # between the chorus, a verse when it is at the edge.
        for i in by_part[runner]:
            labels[i] = "chorus"
            conf[i] = 0.7
        for i in best_members:
            if _sandwiched(i, parts, labels):
                labels[i] = "bridge"
                conf[i] = 0.8
            else:
                labels[i] = "verse"
                conf[i] = 0.55
    else:
        # The top part is a one-off and clearly the loudest, or nothing repeats:
        # it is the chorus, with the confidence a one-off deserves.
        for i in best_members:
            labels[i] = "chorus"
            conf[i] = 0.65 if runner is not None else 0.6

    # Repeated parts first, so a one-off's sandwich test sees finished labels.
    for i in remaining:
        if labels[i] is None and len(by_part[int(parts[i])]) >= 2:
            labels[i] = "verse"
            conf[i] = 0.75
    for i in remaining:
        if labels[i] is None:
            if _sandwiched(i, parts, labels):
                labels[i] = "bridge"
                conf[i] = 0.8
            else:
                labels[i] = "verse"
                conf[i] = 0.5


# --------------------------------------------------------------- the entry point


def detect_sections(
    y: np.ndarray,
    sr: int,
    grid: BarGrid,
    chords: Sequence[ChordEvent],
    top_voice_by_bar: Sequence[Sequence[int]],
    vocal: VocalEvidence,
) -> list[Section]:
    """The five stages, in order, on already-loaded data.

    This is the pure core the tests pin down: give it a waveform, a grid, the
    detected chords, the transcribed top voice per bar and the vocal evidence,
    and it returns the labelled sections - no disk, no subprocess, no settings.
    """
    if len(y) <= 0:
        raise ValueError("no audio to segment")
    features = bar_features(y, sr, grid, chords, top_voice_by_bar)
    boundaries = find_boundaries(features)
    spans = _spans(len(features), boundaries)
    fps = section_fingerprints(features, spans, grid, chords)
    parts = cluster_parts(fps)
    mean_coverage = [_mean(vocal.coverage, s) for s in spans]
    return assign_labels(spans, fps, parts, mean_coverage, vocal.available)


# --------------------------------------------------------------- vocal evidence


def _bar_coverage(spans: Sequence[Sequence[float]], grid: BarGrid) -> tuple[float, ...]:
    """Fraction of each bar covered by sung phrases."""
    n = grid.bar_count()
    coverage: list[float] = []
    for bar in range(n):
        bar_start = grid.bar_start(bar)
        bar_end = grid.bar_end(bar)
        length = max(bar_end - bar_start, 1e-6)
        total = sum(
            max(0.0, min(end, bar_end) - max(start, bar_start)) for start, end in spans
        )
        coverage.append(min(1.0, total / length))
    return tuple(coverage)


def run_vocal_evidence(
    audio_path: Path,
    grid: BarGrid,
    *,
    python_executable: str | None = None,
    whisper_model: str = "small",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> VocalEvidence:
    """Whisper phrase spans -> per-bar coverage, degrading to "no evidence".

    The worker runs with ``--skip-rmvpe --language auto``: the section detector
    needs no F0 (instruments fool an F0 detector) and the song may not be in
    Chinese. Any failure returns ``available=False`` with a warning, because a
    song with unknown vocal evidence is still segmentable by energy - failing
    the whole arrangement over a missing ML environment is not.
    """
    n = grid.bar_count()
    none = VocalEvidence(coverage=(0.0,) * n, available=False)
    try:
        python = _worker_python(python_executable)
    except Exception as exc:  # noqa: BLE001 - degrade, do not fail the arrangement
        logger.warning("no ML environment for vocal evidence: %s", exc)
        return none

    with tempfile.TemporaryDirectory(prefix="pianoflow-sections-") as tmp:
        out = Path(tmp) / "evidence.json"
        command = [
            str(python),
            str(WORKER),
            str(audio_path),
            str(out),
            "--skip-rmvpe",
            "--language",
            "auto",
            "--whisper-model",
            whisper_model,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            logger.error("vocal evidence worker timed out after %.0fs", timeout)
            return none
        if completed.returncode != 0 or not out.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()
            logger.error("vocal evidence worker failed (%s): %s", completed.returncode, detail)
            return none
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
            spans = [
                (float(s[0]), float(s[1]))
                for s in (payload.get("merged") or [])
                if isinstance(s, (list, tuple)) and len(s) == 2
            ]
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            logger.error("vocal evidence output unreadable: %s", exc)
            return none

    language = str(payload.get("language", ""))
    coverage = _bar_coverage(spans, grid)
    singing_bars = sum(1 for c in coverage if c >= VOCAL_COVERAGE_NO_SINGER)
    logger.info(
        "vocal evidence: %s, %d phrases, %d/%d bars singing",
        language,
        len(spans),
        singing_bars,
        n,
    )
    return VocalEvidence(coverage=coverage, available=True, language=language)


# --------------------------------------------------------------- the cache


def audio_identity(audio_path: Path) -> str:
    """A content-ish identity: same file on this machine, same key.

    Name + size + mtime is enough for a cache invalidation key: a re-download of
    the same song gets a new mtime and is re-detected, and a different song never
    collides on all three.
    """
    stat = audio_path.stat()
    raw = f"{audio_path.name}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _cache_file(cache_dir: Path, identity: str) -> Path:
    return cache_dir / f"{identity}.json"


def load_cached_analysis(
    cache_dir: Path, identity: str, bar_count: int
) -> SectionAnalysis | None:
    """A cached analysis, or None when the cache misses or the grid changed."""
    path = _cache_file(cache_dir, identity)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("barCount", -1)) != bar_count:
            return None
        vocal = VocalEvidence(
            coverage=tuple(float(c) for c in payload["vocal"]["coverage"]),
            available=bool(payload["vocal"]["available"]),
            language=str(payload["vocal"].get("language", "")),
        )
        sections = tuple(
            Section(
                start_bar=int(s["startBar"]),
                end_bar=int(s["endBar"]),
                label=str(s["label"]),
                confidence=float(s["confidence"]),
                high_motif=bool(s.get("highMotif", False)),
                evidence={str(k): v for k, v in (s.get("evidence") or {}).items()},
            )
            for s in payload["sections"]
        )
        return SectionAnalysis(
            sections=sections,
            vocal=vocal,
            duration_seconds=float(payload["durationSeconds"]),
            transcriber=str(payload.get("transcriber", "")),
        )
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as exc:
        logger.warning("section cache %s is unreadable (%s); re-detecting", path.name, exc)
        return None


def store_analysis(cache_dir: Path, identity: str, analysis: SectionAnalysis, bar_count: int) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "barCount": bar_count,
            "durationSeconds": analysis.duration_seconds,
            "transcriber": analysis.transcriber,
            "vocal": analysis.vocal.to_dict(),
            "sections": [s.to_dict() for s in analysis.sections],
        }
        _cache_file(cache_dir, identity).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        # The cache is an optimisation; a read-only disk is not a reason to fail
        # the request that just paid for the detection.
        logger.warning("could not write section cache: %s", exc)


# --------------------------------------------------------------- textures


def build_section_texture_plan(
    sections: Sequence[Section],
    textures: Mapping[str, str],
) -> TexturePlan | None:
    """The detected sections + the caller's per-label textures -> a TexturePlan.

    ``textures`` maps labels the caller overrode; everything else gets the
    default for its label (with the intro's high-motif rule). Bars no section
    covers keep the plan's default texture, the last section's.
    """
    resolved: list[TextureSection] = []
    for section in sections:
        name = textures.get(section.label) or default_texture_for(
            section.label, high_motif=section.high_motif
        )
        resolved.append(
            TextureSection(section.start_bar, section.end_bar, TextureName(name))
        )
    if not resolved:
        return None
    return TexturePlan(sections=tuple(resolved), default_texture=resolved[-1].texture)


#: The labels with no singing: in them, the tune is whatever the top voice does.
INSTRUMENTAL_SECTION_LABELS: tuple[str, ...] = ("intro", "interlude", "outro")

#: A promoted top voice must sit at or above middle C. Below it the "highest
#: note" is still accompaniment, and lifting it would put a bass in the treble
#: staff as if it were the tune.
MELODY_PROMOTE_MIN_MIDI = 60


def promote_section_melodies(
    buckets: Mapping[int, tuple[Sequence[NoteEvent], Sequence[NoteEvent]]],
    sections: Sequence[Section],
) -> tuple[
    dict[int, tuple[list[NoteEvent], list[NoteEvent]]],
    dict[str, object],
]:
    """Lift the top voice of a no-vocal section into the right hand.

    The hand split gives a chord to one hand whole, by its centre: a mid-register
    instrumental line - exactly what an intro or interlude usually is - lands in
    the left bucket, and the arranged left hand is then rewritten around it, so
    the section's tune simply disappears. The rule here is the user's: 没有人声的
    要把伴奏最高音作为旋律. For every bar of an intro/interlude/outro the
    highest note of the left bucket becomes the melody, moved (not copied) to the
    right bucket, under two guards:

    * it must be a melody-register pitch (:data:`MELODY_PROMOTE_MIN_MIDI`), and
    * the right hand must not already reach at least as high, because then the
      tune is where it belongs and there is nothing to fix.

    Bars of the preview that the full-song sections do not cover - and vice
    versa, section bars beyond a truncated preview - are left alone: the caller
    only ever sees bars that exist in ``buckets``.

    Returns the new buckets (the caller's lists are not touched) and a report:
    ``{"promotedNotes", "promotedBars", "labels"}``.
    """
    promoted: dict[int, tuple[list[object], list[object]]] = {
        bar: (list(right), list(left)) for bar, (right, left) in buckets.items()
    }
    promoted_bars: list[int] = []
    labels: list[str] = []

    for section in sections:
        if section.label not in INSTRUMENTAL_SECTION_LABELS:
            continue
        touched = False
        for bar in range(section.start_bar, section.end_bar):
            if bar not in promoted:
                continue
            right, left = promoted[bar]
            if not left:
                continue
            top = max(left, key=lambda n: n.midi)
            if top.midi < MELODY_PROMOTE_MIN_MIDI:
                continue
            if right and top.midi <= max(n.midi for n in right):
                continue
            left.remove(top)
            right.append(top)
            promoted[bar] = (right, left)
            touched = True
            promoted_bars.append(bar)
        if touched and section.label not in labels:
            labels.append(section.label)

    report = {
        "promotedNotes": len(promoted_bars),
        "promotedBars": sorted(promoted_bars),
        "labels": labels,
    }
    return promoted, report


__all__ = [
    "DEFAULT_SECTION_TEXTURES",
    "ENERGY_VALLEY_FRACTION",
    "HIGH_MOTIF_MIN_NOTES",
    "HIGH_MOTIF_MIDI",
    "INTRO_TEXTURE_NO_MOTIF",
    "MIN_SECTION_BARS",
    "NOVELTY_K",
    "INSTRUMENTAL_SECTION_LABELS",
    "MELODY_PROMOTE_MIN_MIDI",
    "PART_SIMILARITY",
    "SECTION_LABELS",
    "SINGLETON_CHORUS_MARGIN",
    "VOCAL_COVERAGE_NO_SINGER",
    "BarFeatures",
    "Section",
    "SectionAnalysis",
    "VocalEvidence",
    "assign_labels",
    "audio_identity",
    "bar_features",
    "build_section_texture_plan",
    "cluster_parts",
    "default_texture_for",
    "detect_sections",
    "find_boundaries",
    "load_cached_analysis",
    "promote_section_melodies",
    "run_vocal_evidence",
    "section_fingerprints",
    "section_similarity",
    "store_analysis",
]
