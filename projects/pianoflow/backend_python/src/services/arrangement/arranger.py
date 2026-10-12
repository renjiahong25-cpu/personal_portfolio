"""Arrangement: decide, per bar, whether to keep the transcription or rewrite it.

Two independent inputs, kept independent on purpose:

*texture* (``patterns``) decides what a generated left hand looks like.
*Difficulty* decides what gets cut from it. They never talk to each other.

Three fidelity modes:

``as-played``  keep every bar exactly as transcribed.
``hybrid``     keep the bars that scored well, rewrite the ones that failed.
               The default, and the reason the product exists.
``arranged``   rewrite every bar's left hand, keep the right hand as transcribed.

The right hand is never rewritten. Melody is the part a listener recognises, and
a generated melody is worse than a transcribed one even when the transcription is
imperfect.

Style consistency: ``hybrid`` may rewrite a run of bars, and neighbouring bars
that were kept must not sound like a different piece. So the pattern is chosen
**once for the whole piece** by measuring the transcribed left hand, never per
bar, and a rewrite reuses that same pattern.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from services.arrangement.grid import BarGrid
from services.arrangement.hand_split import HandAssignment, _simultaneous_groups
from services.arrangement.harmony import (
    ACCOMPANIMENT_GAIN,
    DENSITY_CAP,
    SPAN_CAP,
    TEXTURE_ACCOMPANIMENT_GAIN,
    fit_span,
    harmonize_melody,
)
from services.arrangement.patterns import DEFAULT_PATTERN, PATTERNS, PlacedNote, build_pattern
from services.arrangement.quality import BarQuality, QualityReport
from services.arrangement.textures import (
    DEFAULT_TEXTURE,
    TextureName,
    TexturePlan,
    build_texture,
)
from services.arrangement.voicing import revoice
from services.audio_analysis.models import ChordEvent, midi_to_pitch
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)

Fidelity = Literal["as-played", "hybrid", "arranged", "transcribed"]

FIDELITIES: tuple[Fidelity, ...] = ("as-played", "hybrid", "arranged", "transcribed")
DIFFICULTIES: tuple[int, ...] = (1, 2, 3, 4)

BarSource = Literal["as-played", "recomposed"]


@dataclass(frozen=True)
class ArrangedBar:
    index: int
    right: tuple[NoteEvent, ...]
    left: tuple[NoteEvent, ...]
    source: BarSource
    chord: str | None = None
    score: float | None = None
    flags: tuple[str, ...] = ()
    reason: str = ""

    @property
    def note_count(self) -> int:
        return len(self.right) + len(self.left)

    def to_dict(self) -> dict[str, object]:
        return {
            "bar": self.index,
            "source": self.source,
            "chord": self.chord,
            "score": None if self.score is None else round(self.score, 1),
            "flags": list(self.flags),
            "reason": self.reason,
            "rightHandCount": len(self.right),
            "leftHandCount": len(self.left),
        }


@dataclass
class Arrangement:
    bars: list[ArrangedBar] = field(default_factory=list)
    fidelity: Fidelity = "arranged"
    difficulty: int = 3
    pattern: str = DEFAULT_PATTERN
    pattern_reason: str = ""
    #: What the faithful re-voicing did. Empty for every other mode, so a caller can
    #: tell "nothing was re-voiced" from "re-voicing found nothing to do".
    voicing: Mapping[str, object] = field(default_factory=dict)

    @property
    def recomposed(self) -> list[ArrangedBar]:
        return [b for b in self.bars if b.source == "recomposed"]

    @property
    def as_played(self) -> list[ArrangedBar]:
        return [b for b in self.bars if b.source == "as-played"]

    def to_dict(self) -> dict[str, object]:
        return {
            "fidelity": self.fidelity,
            "difficulty": self.difficulty,
            "pattern": self.pattern,
            "patternReason": self.pattern_reason,
            "barCount": len(self.bars),
            "asPlayedBars": len(self.as_played),
            "recomposedBars": [b.index for b in self.recomposed],
            "bars": [b.to_dict() for b in self.bars],
        }


#: Which texture each difficulty plays, from the agreed table. Difficulty chooses
#: the figure; measuring the audio only explains the choice in the UI.
DIFFICULTY_PATTERN: dict[int, str] = {
    1: "root_only",
    2: "block_chord",
    3: "broken_chord",
    4: "alberti_bass",
}


def choose_pattern(
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]],
    grid: BarGrid,
    *,
    difficulty: int = 3,
) -> tuple[str, str]:
    """Pick the accompaniment figure for the whole piece.

    The figure is chosen by **difficulty**, not by measurement. The earlier version
    measured the transcribed left hand and ignored its own ``difficulty`` argument
    entirely, so L4 could never select ``alberti_bass`` and every level produced the
    same texture - measured on 星辰大海, L1 through L4 all chose ``block_chord``. The
    user asked for the texture to follow the level, so it does.

    The measurement is still taken, and still reported, because it is what the UI
    shows as the reason: "左手同拍最多 4 个音" tells the player something true about
    the recording even though it no longer overrides the choice.

    ``root_octave`` and ``alberti_bass`` remain reachable by name. Measured textures
    are not auto-selected because after transcription noise neither is reliably
    distinguishable from a plain bass line.
    """
    if difficulty not in DIFFICULTY_PATTERN:
        raise ValueError(f"unsupported difficulty: {difficulty}")

    off_beat = 0
    on_beat = 0
    widest = 0
    for index in range(grid.bar_count()):
        left = buckets.get(index, ([], []))[1]
        for note in left:
            # quantize returns the subdivision *within the beat* (0 on the beat,
            # 2 on the eighth, 1/3 on sixteenths), which is exactly the
            # on-beat test.
            _bar, _beat, subdivision, _error = grid.quantize(note.start)
            if subdivision == 0:
                on_beat += 1
            else:
                off_beat += 1
        # Max notes sounding at once, not the number of groups.
        widest = max(widest, max((len(g) for g in _simultaneous_groups(left)), default=0))

    off_ratio = off_beat / (on_beat + off_beat) if (on_beat + off_beat) else 0.0
    if widest >= 3:
        detected = f"左手同拍最多 {widest} 个音（柱式）"
    elif off_ratio >= 0.30:
        detected = f"左手 {off_ratio:.0%} 的音落在反拍（分解）"
    else:
        detected = f"左手 {on_beat} 个音几乎全在拍上（单音）"

    name = DIFFICULTY_PATTERN[difficulty]
    why = f"L{difficulty} -> {name}；转录实测{detected}"
    logger.info("pattern %s: %s", name, why)
    return name, why


def chord_for_bar(chords: Sequence[ChordEvent], grid: BarGrid, bar: int) -> ChordEvent | None:
    """The chord sounding at the middle of the bar, else the nearest one."""
    if not chords:
        return None
    midpoint = (grid.bar_start(bar) + grid.bar_end(bar)) / 2
    covering = [c for c in chords if c.start <= midpoint < c.end]
    if covering:
        return max(covering, key=lambda c: c.end - c.start)
    return min(chords, key=lambda c: min(abs(c.start - midpoint), abs(c.end - midpoint)))


def simplify_melody(notes: Sequence[NoteEvent], grid: BarGrid, difficulty: int) -> list[NoteEvent]:
    """Thin the right hand for the easier difficulties.

    Difficulty has to reach the melody, not just the generated left hand:
    without this, selecting L1 changes almost nothing, because most bars are
    kept as played. A beginner cannot play a melody that moves in sixteenths,
    so the easier levels keep fewer, longer notes.

    * L3 and L4 - untouched; the transcription is already the melody.
    * L2 - one note per beat, the longest one sounding in that beat.
    * L1 - only beats 1 and 3, and only the longest note in each.
    """
    if difficulty >= 3 or not notes:
        return list(notes)

    allowed_beats = (0, 2) if difficulty == 1 else (0, 1, 2, 3)
    best: dict[tuple[int, int], NoteEvent] = {}
    for note in notes:
        bar, beat, _sixteenth, _error = grid.quantize(note.start)
        if beat not in allowed_beats:
            continue
        key = (bar, beat)
        current = best.get(key)
        # Longest wins; on a tie the higher note, which is usually the melody.
        if current is None or (note.duration, note.midi) > (current.duration, current.midi):
            best[key] = note
    kept = sorted(best.values(), key=lambda n: n.start)
    return kept or list(notes)


def _placed_to_events(
    placed: Sequence[PlacedNote],
    grid: BarGrid,
    bar: int,
    *,
    amplitude: float,
) -> list[NoteEvent]:
    """Turn beat-positioned notes into real note events on the audio timeline."""
    beat_seconds = grid.beat_seconds
    start_of_bar = grid.bar_start(bar)
    events: list[NoteEvent] = []
    for note in placed:
        start = start_of_bar + note.beat * beat_seconds
        duration = max(note.duration_beats * beat_seconds, 0.05)
        events.append(
            NoteEvent(
                start=round(start, 4),
                duration=round(duration, 4),
                midi=note.midi,
                amplitude=amplitude,
            )
        )
    return events


def _clean(events: Sequence[NoteEvent]) -> list[NoteEvent]:
    """Sort and drop duplicates: the same key twice at the same instant is unplayable."""
    seen: set[tuple[float, int]] = set()
    kept: list[NoteEvent] = []
    for note in sorted(events, key=lambda n: (n.start, n.midi)):
        key = (round(note.start, 3), note.midi)
        if key in seen:
            continue
        seen.add(key)
        kept.append(note)
    return kept


def _arrange_transcribed(
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]],
    grid: BarGrid,
    *,
    chords: Sequence[ChordEvent],
    difficulty: int,
    fill_chords: bool = False,
) -> Arrangement:
    """The performance as played, with its harmony voiced correctly.

    Deliberately shares nothing with the arranging path below. Every stage that
    rewrites music is skipped: no density thinning, no melody simplification, no
    chord-tone harmonisation, no accompaniment pattern. The pianist's own 11.7 notes
    per second is above every difficulty budget, and thinning it is what made the
    arranged version lose its character.

    The hand span no longer reaches here either. Difficulty used to be the one thing
    that still applied, on the grounds that a chord a hand cannot hold is unplayable at
    any level - but it was applied by moving notes, and the user was explicit: "不要有
    八度折叠，要严格忠于原来的音频". So difficulty changes nothing in this mode, and a
    chord wider than a hand is *reported* as ``unreachableGroups`` rather than rewritten.
    The player can see and redistribute an impossible chord; they cannot hear a note that
    was quietly moved an octave somewhere else.
    """
    events = [
        note
        for index in range(grid.bar_count())
        for hand in (0, 1)
        for note in buckets.get(index, ([], []))[hand]
    ]
    # Which hand played what. Keyed by onset *and* pitch, because an onset alone does
    # not identify a hand: both hands play at the same time, so a time-only key let
    # the left hand overwrite the right and the whole lower staff came out empty -
    # 224 notes arranged, 224 of them in the right hand and none in the left.
    #
    # The same mistake appeared twice in one session, which is why it is worth naming:
    # in the notation highlight, where bar and beat keyed both staves into one bucket,
    # and here.
    hand_at: dict[tuple[float, int], int] = {}
    onset_hand: dict[float, int] = {}
    for index in range(grid.bar_count()):
        for hand in (0, 1):
            for note in buckets.get(index, ([], []))[hand]:
                hand_at.setdefault((round(note.start, 3), note.midi), hand)
                onset_hand.setdefault(round(note.start, 3), hand)

    revoiced, report = revoice(
        events,
        chords,
        span_cap=SPAN_CAP[difficulty],
        fill_chords=fill_chords,
        # This *is* the faithful mode, so nothing the player played is moved or removed.
        faithful=True,
        bar_of=lambda t: grid.quantize(t)[0],
    )

    by_hand: dict[tuple[int, int], list[NoteEvent]] = {}
    for note in revoiced:
        bar, _beat, _sixteenth, _error = grid.quantize(note.start)
        key = (round(note.start, 3), note.midi)
        # A note added by the fill option has no entry of its own; it belongs to the
        # hand of the group it was added to.
        hand = hand_at.get(key, onset_hand.get(round(note.start, 3), 0))
        by_hand.setdefault((bar, hand), []).append(note)

    bars: list[ArrangedBar] = []
    for index in range(grid.bar_count()):
        chord = chord_for_bar(chords, grid, index)
        bars.append(
            ArrangedBar(
                index=index,
                right=tuple(_clean(by_hand.get((index, 0), []))),
                left=tuple(_clean(by_hand.get((index, 1), []))),
                source="as-played",
                chord=chord.name if chord else None,
                reason="faithful transcription, re-voiced",
            )
        )

    return Arrangement(
        bars=bars,
        fidelity="transcribed",
        difficulty=difficulty,
        pattern="none",
        pattern_reason="忠实转谱，不生成伴奏织体",
        voicing=report.to_dict(),
    )


def arrange(
    buckets: dict[int, tuple[list[NoteEvent], list[NoteEvent]]],
    grid: BarGrid,
    *,
    chords: Sequence[ChordEvent] = (),
    quality: QualityReport | None = None,
    fidelity: Fidelity = "arranged",
    difficulty: int = 3,
    pattern: str | None = None,
    assignment: HandAssignment | None = None,
    fill_chords: bool = False,
    texture: TextureName | str | None = None,
    texture_plan: TexturePlan | None = None,
) -> Arrangement:
    """Build the arranged score, one bar at a time.

    When ``texture`` or ``texture_plan`` is given, the left hand is built from
    the eight accompaniment textures in :mod:`services.arrangement.textures`
    instead of the five patterns in :mod:`services.arrangement.patterns`.
    ``texture`` selects one texture for the whole piece; ``texture_plan`` maps
    bar ranges to textures for a varying left hand.
    """
    if fidelity not in FIDELITIES:
        raise ValueError(f"unknown fidelity: {fidelity}")
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"unsupported difficulty: {difficulty}")
    if pattern is not None and pattern not in PATTERNS:
        raise ValueError(f"unknown pattern: {pattern}")
    if texture is not None and texture_plan is not None:
        raise ValueError("texture and texture_plan are mutually exclusive")

    if fidelity == "transcribed":
        return _arrange_transcribed(
            buckets, grid, chords=chords, difficulty=difficulty, fill_chords=fill_chords
        )

    use_texture = texture is not None or texture_plan is not None
    texture_for_bar: Callable[[int], TextureName] | None = None
    if use_texture:
        if texture_plan is not None:
            texture_for_bar = texture_plan.texture_for_bar
            chosen, reason = "texture_plan", "texture mode"
        elif texture is not None:
            # One texture for the whole piece. Resolving the string here rather than
            # per bar means an unknown name is rejected before any bar is built.
            if isinstance(texture, str):
                try:
                    texture = TextureName(texture)
                except ValueError as exc:
                    raise ValueError(f"unknown texture: {texture}") from exc
            single = texture

            def texture_for_bar(_index: int, _single: TextureName = single) -> TextureName:
                return _single

            chosen, reason = single.value, "texture mode"
        else:
            raise ValueError("texture mode needs texture or texture_plan")
    else:
        chosen, reason = (
            (pattern, "caller supplied")
            if pattern
            else choose_pattern(buckets, grid, difficulty=difficulty)
        )
    result = Arrangement(
        fidelity=fidelity, difficulty=difficulty, pattern=chosen, pattern_reason=reason
    )

    recomposed = set(quality.recomposed_bars) if quality is not None else set()
    qualities = {b.index: b for b in quality.bars} if quality is not None else {}

    for index in range(grid.bar_count()):
        right, left = buckets.get(index, ([], []))
        chord = chord_for_bar(chords, grid, index)
        bar_quality: BarQuality | None = qualities.get(index)

        rewrite = fidelity == "arranged"
        why = "fidelity=arranged"
        if fidelity == "hybrid" and index in recomposed:
            rewrite = True
            why = (
                f"bar quality {bar_quality.score:.0f} ({', '.join(bar_quality.flags)})"
                if bar_quality
                else "bar failed quality"
            )
        elif fidelity == "as-played":
            why = "fidelity=as-played"

        # The left hand is decided first, because the melody is harmonised against
        # whatever the left hand actually plays. Harmonising before would allow both
        # hands to sound the same pitch, which reads as one thick wrong note.
        if rewrite and chord is not None:
            if use_texture:
                placed = build_texture(
                    texture_for_bar(index) if texture_for_bar is not None else DEFAULT_TEXTURE,
                    chord.root,
                    chord.quality,
                    beats_per_bar=grid.beats_per_bar,
                )
            else:
                placed = build_pattern(
                    chosen,
                    chord.root,
                    chord.quality,
                    difficulty=difficulty,
                    beats_per_bar=grid.beats_per_bar,
                )
            generated = _placed_to_events(placed, grid, index, amplitude=0.6)
            source: BarSource = "recomposed"
            bar_reason = f"{why} -> {chosen}/{difficulty}"
        else:
            # No chord means nothing to rewrite from. Keeping the transcription
            # beats inventing a harmony, so the bar stays as played and says why.
            generated = list(left)
            source = "as-played"
            bar_reason = "no chord detected, kept as played" if rewrite else why

        # The accompaniment is support and must never out-shout the tune. Measured
        # on 星辰大海 the transcribed amplitudes put it at 0.600 against a melody of
        # 0.428, which is why the melody was not clear.
        # When the texture system is active, the left hand carries the harmonic/rhythmic
        # identity of the arrangement, so it needs to be clearly audible.
        gain = TEXTURE_ACCOMPANIMENT_GAIN if use_texture else ACCOMPANIMENT_GAIN
        final_left = [
            dataclasses.replace(n, amplitude=n.amplitude * gain) for n in _clean(generated)
        ]

        # The transcribed tune, kept aside so the span fitter knows which pitches
        # must not move: the filling is folded to fit the melody, never the reverse.
        skeleton = simplify_melody(right, grid, difficulty)
        melody = harmonize_melody(
            skeleton,
            chords,
            difficulty=difficulty,
            beat_seconds=grid.beat_seconds,
            occupied=[n.midi for n in final_left],
            span_cap=SPAN_CAP[difficulty],
        )

        # Fold each chord into the stretch the difficulty allows. The transcribed
        # melody is protected, so the filling is moved rather than the tune.
        melody = fit_span(melody, SPAN_CAP[difficulty], protect=[n.midi for n in skeleton])
        final_left = fit_span(final_left, SPAN_CAP[difficulty])

        result.bars.append(
            ArrangedBar(
                index=index,
                right=tuple(melody),
                left=tuple(final_left),
                source=source,
                chord=chord.name if chord else None,
                score=bar_quality.score if bar_quality else None,
                flags=bar_quality.flags if bar_quality else (),
                reason=bar_reason,
            )
        )

    _enforce_density(result, grid, difficulty)

    logger.info(
        "arranged %d bars: %d as played, %d recomposed (pattern %s, difficulty L%d)",
        len(result.bars),
        len(result.as_played),
        len(result.recomposed),
        chosen,
        difficulty,
    )
    return result


def _enforce_density(result: Arrangement, grid: BarGrid, difficulty: int) -> None:
    """Thin the whole piece to the difficulty's note budget.

    Applied after every bar is built, because the budget is a property of the piece:
    four bars that are each within the limit can still add up to a passage no hand
    can play. The melody's structural notes and the left hand's downbeats are what
    survive; see ``thin_to_density`` for the order.
    """
    seconds = grid.phase_seconds + len(result.bars) * grid.bar_seconds
    if seconds <= 0:
        return
    cap = DENSITY_CAP[difficulty]
    before = sum(bar.note_count for bar in result.bars)
    if before <= cap * seconds:
        return

    # Bars are thinned evenly rather than bar by bar, so the piece does not open
    # with a dense passage and end sparse. ArrangedBar is frozen, so the bars are
    # rebuilt rather than edited.
    #
    # The budget is handed out with a fractional remainder rather than truncated per
    # bar. Truncating each bar's share to an integer lost the fraction every time, so
    # the piece came in under at the bar level and still measured over at the piece
    # level - L4 read 8.06 against a cap of 8.00 no matter how the quota was rounded.
    exact = cap * seconds / max(len(result.bars), 1)
    allowance = 0.0
    thinned: list[ArrangedBar] = []
    for bar in result.bars:
        allowance += exact
        allowed_total = int(allowance + 1e-9)
        allowance -= allowed_total
        if bar.note_count <= allowed_total:
            thinned.append(bar)
            continue
        # The melody has already been shaped for this difficulty by simplify_melody
        # and by the chord budget, so it is only thinned here if it would blow the bar
        # on its own. Splitting the bar 55/45 unconditionally cut L1's melody to a
        # single note per bar, which is thinner than L1 is supposed to be.
        allowed_right = min(len(bar.right), max(1, round(allowed_total * 0.6)))
        allowed_left = min(len(bar.left), max(0, allowed_total - allowed_right))
        thinned.append(
            dataclasses.replace(
                bar,
                right=tuple(_prioritise(bar.right)[:allowed_right]),
                left=tuple(_prioritise_left(bar.left)[:allowed_left]),
            )
        )
    result.bars = thinned
    logger.info(
        "density cap %.1f/s: %d -> %d notes over %.1fs at L%d",
        cap,
        before,
        sum(bar.note_count for bar in result.bars),
        seconds,
        difficulty,
    )


def _prioritise(notes: tuple[NoteEvent, ...]) -> list[NoteEvent]:
    """Melody order: long notes first, because those are the tune."""
    return sorted(notes, key=lambda n: (-round(n.duration, 3), n.start))


def _prioritise_left(notes: tuple[NoteEvent, ...]) -> list[NoteEvent]:
    """Accompaniment order: downbeats and long notes first."""
    return sorted(
        notes,
        key=lambda n: (1 if n.duration < 0.2 else 0, -round(n.duration, 3), n.start),
    )


def describe_left_hand(bar: ArrangedBar) -> str:
    """One-line human summary, used by the debug CLI and the tests."""
    if not bar.left:
        return "left: (silent)"
    notes = " ".join(
        f"{midi_to_pitch(n.midi)}@{n.start:.2f}" for n in sorted(bar.left, key=lambda z: z.start)
    )
    return f"left: {notes}"


__all__ = [
    "DIFFICULTIES",
    "DIFFICULTY_PATTERN",
    "FIDELITIES",
    "ArrangedBar",
    "Arrangement",
    "BarSource",
    "Fidelity",
    "arrange",
    "choose_pattern",
    "chord_for_bar",
    "describe_left_hand",
    "simplify_melody",
]
