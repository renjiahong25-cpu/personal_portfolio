"""Tests for section detection: intro/verse/chorus/interlude/bridge/outro.

Two layers. The rule tests hand the label function crafted fingerprints and
check the decision rules directly, so a rule change is visible in the diff. The
synthetic-song test builds a real waveform + chords + top voice + vocal evidence
for a known AABA' form and checks that the full five-stage pipeline recovers the
labels, which is what a regression in feature or boundary code would break.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from services.arrangement.grid import BarGrid
from services.arrangement.sections import (
    MIN_SECTION_BARS,
    Section,
    SectionAnalysis,
    VocalEvidence,
    _Fingerprint,
    _spans,
    assign_labels,
    audio_identity,
    bar_features,
    build_section_texture_plan,
    cluster_parts,
    default_texture_for,
    detect_sections,
    find_boundaries,
    load_cached_analysis,
    promote_section_melodies,
    run_vocal_evidence,
    store_analysis,
)
from services.arrangement.textures import TextureName
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

SR = 22050


def grid_at(bars: int, tempo_bpm: float = 120.0) -> BarGrid:
    # 120 bpm, 4/4: one bar every 2 s.
    return BarGrid(tempo_bpm=tempo_bpm, beats_per_bar=4, phase_seconds=0.0, duration=bars * 2.0)


def _fp(pc: tuple[int, ...], chords: tuple[str, ...], energy: float, pitch: float, count: int) -> _Fingerprint:
    return _Fingerprint(
        pc=np.array(list(pc), dtype=float),
        chords=chords,
        energy=energy,
        pitch_level=pitch,
        top_count=count,
        chord_changes=len(chords),
    )


# ------------------------------------------------------------------- rule tests


def test_single_sung_section_is_a_verse() -> None:
    spans = [(0, 8)]
    fps = (_fp((1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0), ("C", "G"), 0.2, 62.0, 30),)
    parts = (0,)
    sections = assign_labels(spans, fps, parts, (0.5,), vocal_available=True)
    assert len(sections) == 1
    assert sections[0].label == "verse"
    assert sections[0].confidence < 0.6  # nothing to compare it with


def test_single_instrumental_section_is_an_intro() -> None:
    spans = [(0, 8)]
    fps = (_fp((0,) * 12, ("C",), 0.1, 0.0, 0),)
    parts = (0,)
    sections = assign_labels(spans, fps, parts, (0.0,), vocal_available=True)
    assert sections[0].label == "intro"


def test_no_vocal_evidence_segments_by_energy() -> None:
    """Quiet-loud-quiet with no Whisper: valley intro, the main part, valley outro.

    With only one singing part there is no verse/chorus contrast, so it gets the
    conservative verse label - a chorus is a contrast, and there is nothing to
    contrast it with.
    """
    spans = [(0, 8), (8, 16), (16, 24)]
    fps = (
        _fp((1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), ("C",), 0.05, 50.0, 8),
        _fp((1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0), ("C", "G", "Am"), 0.3, 64.0, 40),
        _fp((1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), ("C",), 0.05, 50.0, 8),
    )
    parts = (0, 1, 2)
    sections = assign_labels(spans, fps, parts, (0.0, 0.0, 0.0), vocal_available=False)
    assert [s.label for s in sections] == ["intro", "verse", "outro"]
    assert sections[1].evidence["mode"] == "energy"


def test_chorus_is_the_loudest_repeated_part() -> None:
    """A B A B: B is louder and higher, so B is the chorus, A the verse."""
    spans = [(0, 8), (8, 16), (16, 24), (24, 32)]
    a = _fp((1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0), ("C", "G", "Am", "F"), 0.15, 62.0, 30)
    b = _fp((1, 0, 1, 0, 1, 1, 0, 0, 0, 0, 0, 1), ("Dm", "G", "C", "F"), 0.35, 74.0, 34)
    fps = (a, b, a, b)
    parts = cluster_parts(fps)
    assert len(set(parts)) == 2  # the two A's and two B's must not merge
    sections = assign_labels(spans, fps, parts, (0.5, 0.6, 0.5, 0.6), vocal_available=True)
    assert [s.label for s in sections] == ["verse", "chorus", "verse", "chorus"]
    assert sections[1].confidence >= 0.8


def test_bridge_is_the_one_off_between_the_same_part() -> None:
    """A B A C A B A: C is sung, unique, and sandwiched between two A's."""
    spans = [(i * 8, (i + 1) * 8) for i in range(7)]
    a = _fp((1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0), ("C", "G", "Am", "F"), 0.15, 62.0, 30)
    b = _fp((1, 0, 1, 0, 1, 1, 0, 0, 0, 0, 0, 1), ("Dm", "G", "C", "F"), 0.35, 74.0, 34)
    c = _fp((0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0), ("E", "A", "B", "E"), 0.18, 68.0, 30)
    fps = (a, b, a, c, a, b, a)
    parts = cluster_parts(fps)
    assert parts[0] == parts[2] == parts[4] == parts[6]
    assert parts[1] == parts[5]
    assert parts[3] not in (parts[0], parts[1])
    sections = assign_labels(spans, fps, parts, (0.5,) * 7, vocal_available=True)
    assert [s.label for s in sections] == [
        "verse",
        "chorus",
        "verse",
        "bridge",
        "verse",
        "chorus",
        "verse",
    ]


def test_interlude_is_the_no_vocal_gap_between_vocal_sections() -> None:
    """Intro, sung, instrumental mid-gap, sung, outro."""
    spans = [(0, 8), (8, 16), (16, 24), (24, 32), (32, 40)]
    instrumental = _fp((0,) * 12, ("C",), 0.08, 0.0, 0)
    sung = _fp((1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0), ("C", "G"), 0.2, 62.0, 30)
    fps = (instrumental, sung, instrumental, sung, instrumental)
    # The two sung sections are the same part; the instrumental ones are not.
    parts = cluster_parts(fps)
    assert parts[1] == parts[3]
    assert parts[0] != parts[1] and parts[2] != parts[1] and parts[4] != parts[1]
    coverage = (0.0, 0.5, 0.0, 0.5, 0.0)
    sections = assign_labels(spans, fps, parts, coverage, vocal_available=True)
    assert [s.label for s in sections] == [
        "intro",
        "verse",
        "interlude",
        "verse",
        "outro",
    ]
    # The interlude's confidence reflects the flanked-by-singing rule.
    assert sections[2].confidence >= 0.8


def test_a_sung_first_section_is_not_an_intro() -> None:
    """The user's definition: an intro has no vocals. A sung opening is a verse."""
    spans = [(0, 8), (8, 16), (16, 24)]
    a = _fp((1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0), ("C", "G"), 0.2, 62.0, 30)
    b = _fp((1, 0, 1, 0, 1, 1, 0, 0, 0, 0, 0, 1), ("Dm", "G"), 0.3, 74.0, 30)
    fps = (a, a, b)
    parts = (0, 0, 1)
    sections = assign_labels(spans, fps, parts, (0.5, 0.5, 0.5), vocal_available=True)
    assert sections[0].label == "verse"
    assert sections[2].label == "chorus"


# ------------------------------------------------------------- boundary tests


def test_find_boundaries_recovers_six_transitions() -> None:
    y = _synthetic_audio()
    grid = grid_at(64)
    features = bar_features(y, SR, grid, _synthetic_chords(), _synthetic_top_voice())
    boundaries = find_boundaries(features)
    expected = [8, 16, 24, 32, 40, 48, 56]
    for want in expected:
        assert any(abs(b - want) <= 1 for b in boundaries), f"no boundary near bar {want}: {boundaries}"
    assert len(boundaries) == len(expected)


def test_a_flat_song_has_no_boundaries() -> None:
    """No change anywhere, so no change should be invented."""
    grid = grid_at(16)
    y = np.zeros(grid.bar_count() * 2 * SR, dtype=np.float32)
    feats = bar_features(y, SR, grid, [], [[] for _ in range(16)])
    assert find_boundaries(feats) == []


def test_sections_are_at_least_the_minimum_length() -> None:
    grid = grid_at(16)
    y = _synthetic_audio()[: 16 * 2 * SR]
    features = bar_features(y, SR, grid, _synthetic_chords(), _synthetic_top_voice()[:16])
    boundaries = find_boundaries(features)
    spans = _spans(16, boundaries)
    for start, end in spans:
        assert end - start >= MIN_SECTION_BARS


# --------------------------------------------------------- synthetic song tests


def _synthetic_audio() -> np.ndarray:
    """64 bars: intro(8) verse(8) chorus(8) verse(8) chorus(8) bridge(8) chorus(8) outro(8).

    Each section has its own loudness, brightness, melody range and chords, with
    a per-bar contour so sections are internally alive (real songs are, and a
    flat feature vector would make the novelty floor degenerate).
    """
    plan = [
        (0.05, 250.0),  # intro: quiet, dark
        (0.15, 320.0),  # verse: medium
        (0.30, 520.0),  # chorus: loud, bright
        (0.15, 320.0),  # verse
        (0.30, 520.0),  # chorus
        (0.18, 420.0),  # bridge: its own thing
        (0.30, 520.0),  # chorus
        (0.05, 220.0),  # outro: quiet, darker
    ]
    bars = 64
    bar_seconds = 2.0
    total = bars * bar_seconds * SR
    t = np.arange(int(total), dtype=np.float32) / SR
    y = np.zeros_like(t)
    for bar in range(bars):
        amp, freq = plan[bar // 8]
        # A per-bar contour: the pitch wobbles by a third, the level by 8 %.
        freq_bar = freq * (1.0 + 0.03 * (bar % 3 - 1))
        amp_bar = amp * (1.0 + 0.08 * ((bar % 4) - 1.5) / 1.5)
        i0 = int(bar * bar_seconds * SR)
        i1 = int((bar + 1) * bar_seconds * SR)
        segment = t[i0:i1]
        y[i0:i1] = amp_bar * np.sin(2 * np.pi * freq_bar * segment)
    return y


def _synthetic_chords() -> list[ChordEvent]:
    verse = [(0, "major"), (7, "major"), (9, "minor"), (5, "major")]
    chorus = [(2, "minor"), (7, "major"), (0, "major"), (5, "major")]
    bridge = [(4, "major"), (9, "major"), (11, "major"), (4, "major")]
    chords: list[ChordEvent] = []
    for bar in range(64):
        section = bar // 8
        if section == 0:
            if bar == 0:
                chords.append(ChordEvent(start=0.0, duration=16.0, root=0, quality="major"))
            continue
        if section == 7:
            if bar == 56:
                chords.append(ChordEvent(start=112.0, duration=16.0, root=0, quality="major"))
            continue
        table = [verse, chorus, verse, chorus, bridge, chorus][section - 1]
        root, quality = table[bar % 4]
        chords.append(ChordEvent(start=bar * 2.0, duration=2.0, root=root, quality=quality))
    return chords


def _synthetic_top_voice() -> list[list[int]]:
    verse = [60, 62, 64, 62, 60, 62, 64, 67]
    chorus = [72, 74, 76, 74, 72, 74, 76, 79]
    bridge = [66, 68, 70, 68, 66, 68, 70, 72]
    voice: list[list[int]] = []
    for bar in range(64):
        section = bar // 8
        if section == 0 or section == 7:
            voice.append([])  # intro and outro are instrumental
        elif section in (1, 3):
            voice.append([verse[bar % 8], verse[bar % 8] + 12])
        elif section == 5:
            voice.append([bridge[bar % 8], bridge[bar % 8] + 12])
        else:
            voice.append([chorus[bar % 8], chorus[bar % 8] + 12])
    return voice


def _synthetic_vocal() -> VocalEvidence:
    coverage = []
    for bar in range(64):
        section = bar // 8
        coverage.append(0.0 if section in (0, 7) else 0.5)
    return VocalEvidence(coverage=tuple(coverage), available=True, language="zh")


def test_the_synthetic_song_recoveres_its_form() -> None:
    """The full pipeline on a known AABA' song: every label, every boundary."""
    y = _synthetic_audio()
    grid = grid_at(64)
    sections = detect_sections(y, SR, grid, _synthetic_chords(), _synthetic_top_voice(), _synthetic_vocal())
    expected = ["intro", "verse", "chorus", "verse", "chorus", "bridge", "chorus", "outro"]
    assert [s.label for s in sections] == expected
    # The boundaries land on the true transitions (one bar of tolerance).
    starts = [s.start_bar for s in sections]
    for found, want in zip(starts, [0, 8, 16, 24, 32, 40, 48, 56], strict=True):
        assert abs(found - want) <= 1, f"boundary at {found}, wanted {want}"
    # The choruses carry the high-register motif; the intro (instrumental) does not.
    assert sections[2].high_motif is True
    assert sections[0].high_motif is False
    # Evidence is reported so a label can be inspected.
    for s in sections:
        assert s.evidence["part"]
        assert 0 < s.confidence <= 0.95


def test_the_intro_with_a_high_motif_is_flagged() -> None:
    """A sung-looking instrumental intro with a high line: flagged, and given
    the flowing texture rather than the bare bass by default."""
    voice = _synthetic_top_voice()
    for bar in range(8):
        voice[bar] = [78, 80, 78]  # a high motif in the intro
    y = _synthetic_audio()
    grid = grid_at(64)
    vocal = VocalEvidence(coverage=(0.0,) * 64, available=True)  # still no singing
    sections = detect_sections(y, SR, grid, _synthetic_chords(), voice, vocal)
    intro = sections[0]
    assert intro.label == "intro"
    assert intro.high_motif is True
    assert default_texture_for(intro.label, high_motif=intro.high_motif) == "arplong"
    assert default_texture_for(intro.label, high_motif=False) == "bass"


def test_the_full_audio_without_vocal_evidence_still_segments() -> None:
    """No Whisper: the energy fallback still finds intro and outro."""
    y = _synthetic_audio()
    grid = grid_at(64)
    sections = detect_sections(
        y, SR, grid, _synthetic_chords(), _synthetic_top_voice(),
        VocalEvidence(coverage=(0.0,) * 64, available=False),
    )
    assert sections[0].label == "intro"
    assert sections[-1].label == "outro"
    # Without vocal evidence the middle cannot be called an interlude on that
    # basis alone; the form still segments into >= 5 pieces.
    assert len(sections) >= 5


# --------------------------------------------------------------- texture plan


def test_build_section_texture_plan_honours_overrides() -> None:
    sections = (
        Section(0, 8, "intro", 0.9, high_motif=True),
        Section(8, 16, "verse", 0.75),
        Section(16, 24, "chorus", 0.85),
    )
    plan = build_section_texture_plan(sections, {"chorus": "block"})
    assert plan is not None
    assert plan.texture_for_bar(0) == TextureName.ARLONG  # intro with motif
    assert plan.texture_for_bar(8) == TextureName.HALF  # verse default
    assert plan.texture_for_bar(16) == TextureName.BLOCK  # override


def test_build_section_texture_plan_without_motif_uses_bass_for_the_intro() -> None:
    sections = (Section(0, 8, "intro", 0.9, high_motif=False),)
    plan = build_section_texture_plan(sections, {})
    assert plan is not None
    assert plan.texture_for_bar(0) == TextureName.BASS


def test_build_section_texture_plan_of_nothing_is_none() -> None:
    assert build_section_texture_plan((), {}) is None


def test_default_texture_for_rejects_unknown_labels() -> None:
    with pytest.raises(ValueError):
        default_texture_for("solo")


# -------------------------------------------------------------------- caching


def test_analysis_cache_round_trip(tmp_path: Path) -> None:
    sections = (
        Section(0, 8, "intro", 0.9, high_motif=True, evidence={"part": "A"}),
        Section(8, 16, "verse", 0.7, evidence={"part": "B"}),
    )
    analysis = SectionAnalysis(
        sections=sections,
        vocal=VocalEvidence(coverage=(0.0, 0.5), available=True, language="en"),
        duration_seconds=32.0,
        transcriber="basic_pitch",
    )
    identity = "a" * 20
    store_analysis(tmp_path, identity, analysis, bar_count=16)

    loaded = load_cached_analysis(tmp_path, identity, 16)
    assert loaded is not None
    assert [s.label for s in loaded.sections] == ["intro", "verse"]
    assert loaded.sections[0].high_motif is True
    assert loaded.vocal.available is True
    assert loaded.vocal.language == "en"

    # A different grid (another preview length) invalidates the cache.
    assert load_cached_analysis(tmp_path, identity, 17) is None
    # A different file never reads this cache.
    assert load_cached_analysis(tmp_path, "b" * 20, 16) is None


def test_a_corrupt_cache_file_is_a_miss(tmp_path: Path) -> None:
    from services.arrangement.sections import _cache_file

    identity = "c" * 20
    _cache_file(tmp_path, identity).write_text("{not json", encoding="utf-8")
    assert load_cached_analysis(tmp_path, identity, 16) is None


def test_audio_identity_changes_with_mtime(tmp_path: Path) -> None:
    path = tmp_path / "song.wav"
    path.write_bytes(b"0" * 100)
    first = audio_identity(path)
    import os
    import time

    os.utime(path, (time.time() + 10, time.time() + 10))
    assert audio_identity(path) != first


def test_run_vocal_evidence_degrades_without_an_ml_environment() -> None:
    """No torch environment: 'no evidence', never an exception."""
    grid = grid_at(8)

    from services.arrangement import sections as sections_module

    def _missing(_explicit: str | None) -> Path:
        raise RuntimeError("no ml env")

    monkeypatched = sections_module._worker_python
    sections_module._worker_python = _missing
    try:
        evidence = run_vocal_evidence(Path("nonexistent.wav"), grid)
    finally:
        sections_module._worker_python = monkeypatched
    assert evidence.available is False
    assert evidence.coverage == (0.0,) * 8


# ------------------------------------------------- instrumental melody promotion


def _event(start: float, duration: float, midi: int, amplitude: float = 0.5) -> NoteEvent:
    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def _intro_then_verse(bars: int = 8) -> tuple[Section, ...]:
    return (
        Section(0, 4, "intro", 0.9, evidence={"vocalCoverage": 0.0}),
        Section(4, bars, "verse", 0.8, evidence={"vocalCoverage": 0.6}),
    )


class TestSectionMelodyPromotion:
    def test_the_intro_top_voice_moves_to_the_right_hand(self) -> None:
        buckets = {
            bar: ([], [_event(bar * 2.0, 0.5, 45), _event(bar * 2.0 + 1.0, 0.5, 72)])
            for bar in range(8)
        }
        promoted, report = promote_section_melodies(buckets, _intro_then_verse())
        for bar in range(4):
            right, left = promoted[bar]
            assert [n.midi for n in right] == [72]
            assert [n.midi for n in left] == [45]
        # The sung verse is untouched.
        for bar in range(4, 8):
            right, left = promoted[bar]
            assert right == []
            assert [n.midi for n in left] == [45, 72]
        assert report["promotedNotes"] == 4
        assert report["promotedBars"] == [0, 1, 2, 3]
        assert report["labels"] == ["intro"]

    def test_vocal_sections_are_left_alone(self) -> None:
        # An intro-shaped bar that belongs to a sung section is not promoted.
        buckets = {
            bar: ([], [_event(bar * 2.0, 0.5, 45), _event(bar * 2.0 + 1.0, 0.5, 72)])
            for bar in range(8)
        }
        sections = (
            Section(0, 8, "verse", 0.8, evidence={"vocalCoverage": 0.6}),
        )
        promoted, report = promote_section_melodies(buckets, sections)
        for bar in range(8):
            assert promoted[bar][0] == []
            assert [n.midi for n in promoted[bar][1]] == [45, 72]
        assert report["promotedNotes"] == 0
        assert report["labels"] == []

    def test_a_low_top_voice_is_not_promoted(self) -> None:
        # The "highest" note is still a bass: lifting it would put it in the treble
        # staff as if it were the tune.
        buckets = {bar: ([], [_event(bar * 2.0, 0.5, 36), _event(bar * 2.0 + 1.0, 0.5, 50)]) for bar in range(4)}
        promoted, report = promote_section_melodies(buckets, _intro_then_verse()[:1])
        for bar in range(4):
            assert promoted[bar][0] == []
            assert [n.midi for n in promoted[bar][1]] == [36, 50]
        assert report["promotedNotes"] == 0

    def test_a_right_hand_that_reaches_higher_is_untouched(self) -> None:
        # The tune already sits where it belongs.
        buckets = {
            bar: ([_event(bar * 2.0, 0.5, 74)], [_event(bar * 2.0 + 1.0, 0.5, 72), _event(bar * 2.0 + 1.5, 0.5, 45)])
            for bar in range(4)
        }
        promoted, report = promote_section_melodies(buckets, _intro_then_verse()[:1])
        for bar in range(4):
            right, left = promoted[bar]
            assert [n.midi for n in right] == [74]
            assert sorted(n.midi for n in left) == [45, 72]
        assert report["promotedNotes"] == 0

    def test_the_callers_buckets_are_not_mutated(self) -> None:
        left = [_event(0.0, 0.5, 45), _event(1.0, 0.5, 72)]
        buckets = {0: ([], left)}
        promote_section_melodies(buckets, _intro_then_verse()[:1])
        assert [n.midi for n in left] == [45, 72]
        assert buckets[0][0] == []

    def test_bars_outside_the_preview_are_ignored(self) -> None:
        # A section that reaches beyond the buckets the caller has (a truncated
        # preview) must not invent bars.
        buckets = {0: ([], [_event(0.0, 0.5, 45), _event(1.0, 0.5, 72)])}
        sections = (Section(0, 4, "intro", 0.9, evidence={"vocalCoverage": 0.0}),)
        promoted, report = promote_section_melodies(buckets, sections)
        assert [n.midi for n in promoted[0][0]] == [72]
        assert report["promotedBars"] == [0]
