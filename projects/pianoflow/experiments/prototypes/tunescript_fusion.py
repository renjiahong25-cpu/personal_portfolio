"""TuneScript-style piano fusion engine.

Ports the core fusion logic from TuneScript AI (MIT license) into
PianoFlow, adapting to our NoteEvent model and render pipeline.

Key principles (from TuneScript, proven on 5+ real songs):
  - Vocals -> right hand melody (clean, via Demucs separation)
  - Accompaniment stems -> left hand harmony (sparse, 1-2 notes)
  - R1: At least one octave gap between hands
  - R2: Instrumental sections get enhanced accompaniment
  - Velocity: right 80-120, left 60-90 (25% lower)
  - Melody smoothing: fix octave jumps, remove impossible leaps
  - Fragment merging: same pitch, small gap -> merge
  - Pedal: left hand only, notes >= 0.3s, value 75
"""

from __future__ import annotations

import logging
import os

import numpy as np

from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

Note = tuple[float, float, int, int]  # (start, end, pitch, velocity)


def _to_notes(events: list[NoteEvent]) -> list[Note]:
    return [(e.start, e.end, e.midi, int(round(e.amplitude * 127))) for e in events]


def _to_events(notes: list[Note]) -> list[NoteEvent]:
    return [
        NoteEvent(
            start=s,
            duration=max(e - s, 0.02),
            midi=p,
            amplitude=max(1, min(127, v)) / 127.0,
        )
        for s, e, p, v in notes
    ]


# ---------------------------------------------------------------------------
# Melody cleaning
# ---------------------------------------------------------------------------


def smooth_melody(notes: list[Note], window=0.4, max_jump=9) -> list[Note]:
    """Fix octave jumps and remove impossible leaps.

    Basic Pitch on vocals often mistakes the 2nd harmonic for the fundamental:
    individual notes suddenly one octave above/below neighbors.
    Uses median of neighbors within ±window as reference.
    """
    if len(notes) < 3:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out: list[Note] = []
    for i, (s, e, p, v) in enumerate(notes):
        lo = i
        while lo > 0 and s - notes[lo - 1][0] <= window:
            lo -= 1
        hi = i
        while hi < len(notes) - 1 and notes[hi + 1][0] - s <= window:
            hi += 1
        med = float(np.median([notes[k][2] for k in range(lo, hi + 1)]))
        d = p - med
        if 10.0 <= abs(d) <= 14.0:
            folded = p - 12 if d > 0 else p + 12
            if abs(folded - med) <= 4.0:
                p = folded
                d = p - med
        if abs(d) > max_jump and (hi - lo) >= 2:
            continue
        if not (21 <= p <= 108):
            continue
        out.append((s, e, p, v))
    return out


def merge_melody_dups(notes: list[Note]) -> list[Note]:
    """Remove completely nested duplicates (AI re-detection).

    Only merges when a new note is fully inside an old one (same pitch).
    Preserves all genuine re-articulations.
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], x[2]))
    out: list[Note] = []
    last_same: dict[int, int] = {}
    for s, e, p, v in notes:
        idx = last_same.get(p)
        if idx is not None:
            ps, pe, pp, pv = out[idx]
            if s >= ps and e <= pe:
                continue
            if s <= ps and e >= pe:
                out[idx] = (s, e, p, max(v, pv))
                continue
        last_same[p] = len(out)
        out.append((s, e, p, v))
    return out


def dejitter_melody(notes: list[Note], onset_gap=0.22, min_span=1.0) -> list[Note]:
    """Merge vocal jitter fragments (VOCALOID/synthesized voices).

    Same pitch with onset gap < onset_gap -> merge into one long note.
    Real multi-syllable re-articulation intervals are usually >= 0.25s.
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], x[2]))
    by_pitch: dict[int, list[Note]] = {}
    for n in notes:
        by_pitch.setdefault(n[2], []).append(n)
    out: list[Note] = []
    for p, grp in by_pitch.items():
        grp.sort(key=lambda x: x[0])
        merged: list[Note] = []
        cur_s: float | None = None
        cur_e: float | None = None
        prev_s: float | None = None
        cur_v = 0
        for s, e, _p, v in grp:
            if cur_s is None:
                cur_s, cur_e, cur_v, prev_s = s, e, v, s
            elif s - prev_s <= onset_gap:
                cur_e = max(cur_e, e)
                cur_v = max(cur_v, v)
                prev_s = s
            else:
                merged.append((cur_s, cur_e, p, cur_v))
                cur_s, cur_e, cur_v, prev_s = s, e, v, s
        if cur_s is not None:
            merged.append((cur_s, cur_e, p, cur_v))
        out.extend(merged)
    out.sort(key=lambda x: (x[0], x[2]))
    return out


# ---------------------------------------------------------------------------
# Accompaniment cleaning
# ---------------------------------------------------------------------------


def merge_fragments(notes: list[Note], gap=0.15) -> list[Note]:
    """Merge same-pitch fragments with small gaps into one long note."""
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], x[2]))
    out: list[Note] = []
    last_same: dict[int, int] = {}
    for s, e, p, v in notes:
        idx = last_same.get(p)
        if idx is not None:
            ps, pe, pp, pv = out[idx]
            if s - pe <= gap:
                out[idx] = (ps, max(pe, e), p, max(pv, v))
                continue
        last_same[p] = len(out)
        out.append((s, e, p, v))
    return out


def drop_tiny(notes: list[Note], min_len=0.08) -> list[Note]:
    """Filter ultra-short fragments (identification noise)."""
    return [(s, e, p, v) for s, e, p, v in notes if e - s >= min_len]


def sparsify_harmony(notes: list[Note], min_gap=0.35) -> list[Note]:
    """Sparsify: keep only the loudest note per min_gap window.

    Accompaniment stems have many notes per window. Keeping all makes
    the left hand messy. Sparsified left hand has sparse harmony points.
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out: list[Note] = []
    last_time = -1.0
    for n in notes:
        if n[0] - last_time >= min_gap:
            out.append(n)
            last_time = n[0]
    return out


def filter_high_hallucination(notes: list[Note], high_pitch=79, neighbor=0.22) -> list[Note]:
    """Remove isolated high notes >= high_pitch (synth lead / hallucination).

    A note is "isolated" if no other note within ±neighbor seconds.
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out: list[Note] = []
    for i, (s, e, p, v) in enumerate(notes):
        if p >= high_pitch:
            has_neighbor = any(
                abs(notes[j][0] - s) <= neighbor
                for j in range(max(0, i - 3), min(len(notes), i + 4))
                if j != i
            )
            if not has_neighbor:
                continue
        out.append((s, e, p, v))
    return out


def suppress_pad_notes(notes: list[Note], max_len=0.7) -> list[Note]:
    """Suppress long sustained pad notes (synth pads, etc.)."""
    return [(s, e, p, v) for s, e, p, v in notes if (e - s) <= max_len]


# ---------------------------------------------------------------------------
# Hand processing
# ---------------------------------------------------------------------------


def fix_hand(
    notes: list[Note],
    max_span=14,
    window=0.08,
    max_notes=3,
    mode="mix",
) -> list[Note]:
    """Limit notes per time window to physically playable set.

    max_span: max semitone range within one hand (9th ≈ 14 semitones)
    max_notes: max simultaneous notes
    mode: "mix" (keep loudest), "accomp" (keep lowest), "melody" (keep highest)
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], -x[3]))
    out: list[Note] = []
    i = 0
    n = len(notes)
    while i < n:
        t0 = notes[i][0]
        j = i
        cluster: list[Note] = []
        while j < n and notes[j][0] <= t0 + window:
            cluster.append(notes[j])
            j += 1
        i = j
        if len(cluster) <= max_notes:
            out.extend(cluster)
            continue
        if mode == "accomp":
            cluster.sort(key=lambda x: x[2])
        elif mode == "melody":
            cluster.sort(key=lambda x: -x[2])
        else:
            cluster.sort(key=lambda x: -x[3])
        chosen = cluster[:max_notes]
        lo = min(n[2] for n in chosen)
        hi = max(n[2] for n in chosen)
        if hi - lo > max_span:
            if mode == "accomp":
                kept = [n for n in chosen if n[2] - lo <= max_span]
            elif mode == "melody":
                kept = [n for n in chosen if hi - n[2] <= max_span]
            else:
                mid = (lo + hi) // 2
                kept = [n for n in chosen if abs(n[2] - mid) <= max_span // 2]
                if not kept:
                    kept = [chosen[0]]
            out.extend(kept)
        else:
            out.extend(chosen)
    return out


def separate_hands(left: list[Note], right_min=60) -> list[Note]:
    """Drop left hand notes >= right_min by one octave."""
    out = []
    for s, e, p, v in left:
        if p >= right_min + 12:
            out.append((s, e, p - 24, v))
        elif p >= right_min:
            out.append((s, e, p - 12, v))
        else:
            out.append((s, e, p, v))
    return out


def enforce_octave_gap(
    left: list[Note],
    right: list[Note],
    min_gap=12,
    floor=21,
    grid=0.02,
) -> tuple[list[Note], list[Note], dict]:
    """R1: Ensure >= min_gap semitones between hands at overlap times.

    Pass 1: push left hand down by octaves.
    Pass 2: if left hits floor, raise right hand up.
    Only changes pitch, not timing or velocity.
    """
    stats: dict = {
        "on": True,
        "moved": 0,
        "octaves": 0,
        "blocked": 0,
        "raised": 0,
        "gap_before": None,
        "gap_after": None,
    }
    if not left or not right:
        return left, right, stats

    hi_t = max(max(e for _s, e, _p, _v in left), max(e for _s, e, _p, _v in right))
    n_cells = int(hi_t / grid) + 2

    r_min_cells: list[int | None] = [None] * n_cells
    for s, e, p, _v in right:
        i0 = max(0, int(s / grid))
        i1 = min(n_cells - 1, int(e / grid))
        for i in range(i0, i1 + 1):
            if r_min_cells[i] is None or p < r_min_cells[i]:
                r_min_cells[i] = p

    gap_b, _ = _hand_gap_min(left, right, grid)
    stats["gap_before"] = gap_b

    occ: dict[int, list[tuple[float, float]]] = {}
    for _s, _e, _p, _v in left:
        occ.setdefault(_p, []).append((_s, _e))

    def _occupied(pitch: int, s: float, e: float, self_s: float, self_e: float) -> bool:
        for a, b in occ.get(pitch, ()):
            if abs(a - self_s) < 1e-9 and abs(b - self_e) < 1e-9:
                continue
            if a < e - 1e-6 and b > s + 1e-6:
                return True
        return False

    out_left: list[Note] = []
    for s, e, p, v in left:
        rgm: int | None = None
        for i in range(max(0, int(s / grid)), min(n_cells - 1, int(e / grid)) + 1):
            c = r_min_cells[i]
            if c is not None and (rgm is None or c < rgm):
                rgm = c
        if rgm is None:
            out_left.append((s, e, p, v))
            continue
        limit = rgm - min_gap
        np_ = p
        k = 0
        while np_ > limit and np_ - 12 >= floor:
            np_ -= 12
            k += 1
        while k and np_ - 12 >= floor and _occupied(np_, s, e, s, e):
            np_ -= 12
            k += 1
        if k:
            stats["moved"] += 1
            stats["octaves"] += k
        if np_ > limit:
            stats["blocked"] += 1
        occ.setdefault(np_, []).append((s, e))
        out_left.append((s, e, np_, v))

    new_right = right
    if stats["blocked"]:
        hi2 = max(max(e for _s, e, _p, _v in out_left), max(e for _s, e, _p, _v in right))
        n2 = int(hi2 / grid) + 2
        l_max_cells: list[int | None] = [None] * n2
        for s, e, p, _v in out_left:
            i0 = max(0, int(s / grid))
            i1 = min(n2 - 1, int(e / grid))
            for i in range(i0, i1 + 1):
                if l_max_cells[i] is None or p > l_max_cells[i]:
                    l_max_cells[i] = p
        new_right = []
        for s, e, p, v in right:
            lm: int | None = None
            for i in range(max(0, int(s / grid)), min(n2 - 1, int(e / grid)) + 1):
                c = l_max_cells[i]
                if c is not None and (lm is None or c > lm):
                    lm = c
            if lm is None:
                new_right.append((s, e, p, v))
                continue
            req = lm + min_gap
            np_ = p
            k = 0
            while np_ < req and np_ + 12 <= 96:
                np_ += 12
                k += 1
            if k:
                stats["raised"] += 1
            new_right.append((s, e, np_, v))

    gap_a, _ = _hand_gap_min(out_left, new_right, grid)
    stats["gap_after"] = gap_a
    return out_left, new_right, stats


def _hand_gap_min(left: list[Note], right: list[Note], grid=0.02) -> tuple[float | None, int]:
    """Minimum (right_min - left_max) at overlap times."""
    if not left or not right:
        return None, 0
    hi_t = max(max(e for _s, e, _p, _v in left), max(e for _s, e, _p, _v in right))
    n = int(hi_t / grid) + 2
    r_min: list[int | None] = [None] * n
    for s, e, p, _v in right:
        i0 = max(0, int(s / grid))
        i1 = min(n - 1, int(e / grid))
        for i in range(i0, i1 + 1):
            if r_min[i] is None or p < r_min[i]:
                r_min[i] = p
    l_max: list[int | None] = [None] * n
    for s, e, p, _v in left:
        i0 = max(0, int(s / grid))
        i1 = min(n - 1, int(e / grid))
        for i in range(i0, i1 + 1):
            if l_max[i] is None or p > l_max[i]:
                l_max[i] = p
    best: float | None = None
    cnt = 0
    for i in range(n):
        if r_min[i] is not None and l_max[i] is not None:
            g = r_min[i] - l_max[i]
            cnt += 1
            if best is None or g < best:
                best = g
    return best, cnt


def soft_velocity(notes: list[Note], lo=80, hi=120) -> list[Note]:
    """Map velocities to [lo, hi] range, preserving relative dynamics."""
    if not notes:
        return notes
    vels = [v for _, _, _, v in notes]
    vmin, vmax = min(vels), max(vels)
    if vmax <= vmin:
        mid = (lo + hi) // 2
        return [(s, e, p, mid) for s, e, p, v in notes]
    span = vmax - vmin
    out = []
    for s, e, p, v in notes:
        ratio = (v - vmin) / span
        new_v = int(lo + ratio * (hi - lo))
        out.append((s, e, p, max(lo, min(hi, new_v))))
    return out


def fix_same_pitch_overlap(notes: list[Note]) -> list[Note]:
    """Truncate earlier same-pitch note when later one starts earlier."""
    if len(notes) < 2:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out: list[Note] = []
    last: dict[int, int] = {}
    for s, e, p, v in notes:
        idx = last.get(p)
        if idx is not None:
            ps, pe, pp, pv = out[idx]
            if s < pe:
                out[idx] = (ps, s, p, max(pv, v))
        last[p] = len(out)
        out.append((s, e, p, v))
    return out


def dedupe_exact(notes: list[Note]) -> list[Note]:
    """Remove exact duplicates (same start, end, pitch)."""
    seen: set[tuple] = set()
    out: list[Note] = []
    for s, e, p, v in notes:
        key = (round(s, 6), round(e, 6), p)
        if key in seen:
            continue
        seen.add(key)
        out.append((s, e, p, v))
    return out


def collapse_octave_doubling(hand: list[Note], window=0.08) -> list[Note]:
    """Remove self octave doubling (note + note+12 at same time)."""
    if not hand:
        return hand
    hand = sorted(hand, key=lambda n: (n[0], n[2]))
    n = len(hand)
    drop = [False] * n
    i = 0
    while i < n:
        t0 = hand[i][0]
        j, win = i, []
        while j < n and hand[j][0] <= t0 + window:
            if hand[j][1] >= t0:
                win.append(j)
            j += 1
        i = j
        if len(win) > 1:
            low = min(hand[k][2] for k in win)
            for k in win:
                p = hand[k][2]
                if p > low and (p - low) % 12 == 0:
                    drop[k] = True
    return [hand[k] for k in range(n) if not drop[k]]


def shape_durations(
    notes: list[Note],
    trim_at_onset=False,
    legato_gap=0.12,
) -> list[Note]:
    """Shape note durations: fill small gaps (legato), optionally trim at onset."""
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out: list[Note] = []
    for s, e, p, v in notes:
        if out and abs(out[-1][2] - p) < 12 and s - out[-1][1] < legato_gap:
            out[-1] = (out[-1][0], max(out[-1][1], e), p, max(out[-1][3], v))
            continue
        if trim_at_onset and out and abs(out[-1][2] - p) < 12:
            out[-1] = (out[-1][0], s, out[-1][2], out[-1][3])
        out.append((s, e, p, v))
    return out


def denoise_left(notes: list[Note], vel_floor_pct=30, max_len=0.22) -> list[Note]:
    """Remove noisy short low-velocity notes from left hand."""
    if not notes:
        return notes
    vels = [v for _, _, _, v in notes]
    floor = int(min(vels) * vel_floor_pct / 100) if vels else 30
    out = []
    for s, e, p, v in notes:
        if (e - s) < max_len and v < floor:
            continue
        out.append((s, e, p, v))
    return out


# ---------------------------------------------------------------------------
# Vocal gap detection & melody fill
# ---------------------------------------------------------------------------


def find_vocal_gaps(
    vocal_notes: list[Note], other_notes: list[Note], min_gap=2.0
) -> list[tuple[float, float]]:
    """Find time intervals where vocals are absent but accompaniment exists."""
    if not vocal_notes or not other_notes:
        return []
    v_starts = sorted(n[0] for n in vocal_notes)
    other_starts = sorted(n[0] for n in other_notes)
    t_min = min(v_starts[0], other_starts[0])
    t_max = max(
        max(e for _, e, _, _ in vocal_notes),
        max(e for _, e, _, _ in other_notes),
    )
    gaps: list[tuple[float, float]] = []
    cur_start: float | None = None
    for t in np.arange(t_min, t_max, 0.1):
        has_vocal = any(s <= t < e for s, e, _, _ in vocal_notes if e - s > 0.3)
        if not has_vocal and cur_start is None:
            cur_start = float(t)
        elif has_vocal and cur_start is not None:
            if float(t) - cur_start >= min_gap:
                gaps.append((cur_start, float(t)))
            cur_start = None
    if cur_start is not None and t_max - cur_start >= min_gap:
        gaps.append((cur_start, t_max))
    return gaps


def fill_melody_gaps(
    vocal_notes: list[Note],
    other_notes: list[Note],
    gaps: list[tuple[float, float]] | None = None,
) -> list[Note]:
    """Fill vocal gaps with the highest note line from accompaniment."""
    if gaps is None:
        gaps = find_vocal_gaps(vocal_notes, other_notes)
    if not gaps:
        return vocal_notes
    filled = list(vocal_notes)
    for gs, ge in gaps:
        seg = [n for n in other_notes if gs <= n[0] < ge]
        if not seg:
            continue
        seg.sort(key=lambda x: -x[3])
        top_notes = [n for n in seg if n[3] >= seg[0][3] * 0.7]
        if top_notes:
            top_notes.sort(key=lambda x: x[0])
            for s, e, p, v in top_notes:
                filled.append((s, e, p, min(110, int(v * 1.1))))
    filled.sort(key=lambda x: (x[0], x[2]))
    return filled


# ---------------------------------------------------------------------------
# Accompaniment stem merging
# ---------------------------------------------------------------------------


def merge_accomp_stems(stems: dict[str, str], progress=None) -> tuple[int, str | None]:
    """Merge accompaniment stems (piano+guitar+other) by loudness.

    Returns (total_notes_estimate, best_stem_path).
    Priority: piano > guitar > other (R3).
    """
    if progress is None:
        progress = lambda msg: None
    priority = ["piano", "guitar", "other"]
    best_path: str | None = None
    best_loudness = -1.0
    for name in priority:
        path = stems.get(name)
        if not path or not os.path.isfile(path):
            continue
        try:
            import soundfile as sf

            data, _ = sf.read(path, dtype="float32")
            rms = float(np.sqrt(np.mean(data**2)))
            if rms > best_loudness:
                best_loudness = rms
                best_path = path
        except Exception:
            continue
    if best_path is None:
        for name in priority:
            path = stems.get(name)
            if path and os.path.isfile(path):
                best_path = path
                break
    return (0, best_path)


# ---------------------------------------------------------------------------
# Main fusion entry point
# ---------------------------------------------------------------------------


def fuse_to_piano(
    melody_notes: list[Note],
    accomp_notes: list[Note],
    max_span=14,
    window=0.08,
    arrangement="studio",
) -> tuple[list[Note], list[Note]]:
    """Fusion and modification: melody(right) + accompaniment(left) -> playable piano.

    Returns (left_notes, right_notes) as list of (start, end, pitch, velocity).
    """
    melody_notes = smooth_melody(melody_notes)

    left = drop_tiny(merge_fragments(accomp_notes, gap=0.25), min_len=0.12)
    right = drop_tiny(merge_melody_dups(melody_notes), min_len=0.06)
    left = denoise_left(left, vel_floor_pct=30, max_len=0.22)

    left_limit = 2 if arrangement == "studio" else 1
    left = fix_hand(left, max_span=max_span, window=window, max_notes=left_limit, mode="accomp")
    right = fix_hand(right, max_span=max_span, window=window, max_notes=3, mode="melody")

    left = shape_durations(left, trim_at_onset=(arrangement == "studio"))
    right = shape_durations(right, trim_at_onset=False, legato_gap=0.06)

    left = fix_same_pitch_overlap(left)
    right = fix_same_pitch_overlap(right)
    if arrangement == "studio":
        left = collapse_octave_doubling(left)

    left = soft_velocity(left, lo=80, hi=120)
    right = soft_velocity(right, lo=80, hi=120)
    left = [(s, e, p, max(1, int(v * 0.75))) for s, e, p, v in left]

    left = dedupe_exact(left)
    right = dedupe_exact(right)

    left = separate_hands(left, right_min=60)
    left, right, gap_stats = enforce_octave_gap(left, right)

    logger.info(
        "fuse_to_piano: L=%d R=%d gap=%s->%s moved=%d raised=%d",
        len(left),
        len(right),
        gap_stats.get("gap_before"),
        gap_stats.get("gap_after"),
        gap_stats.get("moved", 0),
        gap_stats.get("raised", 0),
    )
    return left, right


# ---------------------------------------------------------------------------
# Convenience: full pipeline from stems
# ---------------------------------------------------------------------------


def build_from_stems(
    stems: dict[str, str],
    transcribe_fn,
    progress=None,
    arrangement="studio",
) -> tuple[list[NoteEvent], list[NoteEvent]]:
    """Full pipeline: separated stems -> transcribed notes -> fused piano.

    Args:
        stems: dict mapping stem name to wav path (from Demucs)
        transcribe_fn: callable(wav_path, label) -> list[NoteEvent]
        progress: optional progress callback
        arrangement: "studio" (2-note left) or "legacy" (1-note left)

    Returns:
        (left_hand_events, right_hand_events)
    """
    if progress is None:
        progress = lambda msg: None

    progress("Transcribing vocals...")
    vocal_events = transcribe_fn(stems["vocals"], "vocals")
    if len(vocal_events) < 10:
        progress("Too few vocal notes, fallback to full mix")
        all_events = transcribe_fn(_find_best_stem(stems), "full_mix")
        return _simple_piano_from_events(all_events, arrangement)

    vocal_notes = _to_notes(vocal_events)

    progress("Merging accompaniment stems...")
    _, best_accomp = merge_accomp_stems(stems, progress)
    if best_accomp:
        accomp_events = transcribe_fn(best_accomp, "accompaniment")
    else:
        accomp_events = []

    other_extra: list[Note] = []
    other_path = stems.get("other")
    if other_path and other_path != best_accomp and os.path.isfile(other_path):
        try:
            other_events = transcribe_fn(other_path, "other")
            other_extra = _to_notes(other_events)
        except Exception:
            other_extra = []

    progress("Building accompaniment...")
    accomp_notes = _to_notes(accomp_events)
    gaps = find_vocal_gaps(vocal_notes, accomp_notes)

    def in_gap(t: float) -> bool:
        return any(gs <= t < ge for gs, ge in gaps)

    sung = [n for n in accomp_notes if not in_gap(n[0])]
    instr = [n for n in accomp_notes if in_gap(n[0])]

    sung_clean = sparsify_harmony(suppress_pad_notes(filter_high_hallucination(sung)), min_gap=0.7)
    instr_clean = sparsify_harmony(suppress_pad_notes(instr, max_len=1.6), min_gap=0.4)

    merged_accomp = sung_clean + instr_clean
    if other_extra:
        other_instr = [n for n in other_extra if in_gap(n[0])]
        merged_accomp += sparsify_harmony(other_instr, min_gap=0.4)

    merged_accomp.sort(key=lambda x: x[0])

    progress("Filling melody gaps...")
    melody = fill_melody_gaps(vocal_notes, merged_accomp, gaps)
    melody = dejitter_melody(melody)

    progress("Fusing to piano...")
    left, right = fuse_to_piano(melody, merged_accomp, arrangement=arrangement)

    return _to_events(left), _to_events(right)


def _find_best_stem(stems: dict[str, str]) -> str:
    for name in ("piano", "guitar", "other", "vocals"):
        path = stems.get(name)
        if path and os.path.isfile(path):
            return path
    return next(iter(stems.values()), "")


def _simple_piano_from_events(
    events: list[NoteEvent], arrangement="studio"
) -> tuple[list[NoteEvent], list[NoteEvent]]:
    """Fallback: split by pitch at C4, no separation."""
    notes = dejitter_melody(_to_notes(events))
    left_raw = [n for n in notes if n[2] < 60]
    right_raw = [n for n in notes if n[2] >= 60]
    left, right = fuse_to_piano(right_raw, left_raw, arrangement=arrangement)
    return _to_events(left), _to_events(right)
