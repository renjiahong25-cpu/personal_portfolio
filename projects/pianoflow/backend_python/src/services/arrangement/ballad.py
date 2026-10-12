"""Ballad arrangement: an extracted vocal melody plus chord-driven left and right hands.

This is the "make it flow and colour it" step that sits on top of
:mod:`services.arrangement.vocal_melody`:

* **Left hand** - a per-section texture (:mod:`services.arrangement.textures`), voiced
  with the bass in E2..E3 and the chord tones within one octave of it, so it stays
  under the right hand instead of competing with the melody.
* **Right hand** - the extracted melody on top. Each note gets one colour, never both:
   a peak at or above the doubling threshold is octave-doubled (the singer's high note,
   shining) wherever it occurs, any other held note is filled with chord tones below
   (:func:`services.arrangement.harmony.harmonize_melody`), the level per section.
   Doubling an already-harmonised note would make a chord wider than a hand, and the
   notator's span fold would fix that by burying the tune under its own filling. The
   bars the voice does not sing at all get a broken-chord arpeggio fill, the way the
   reference score's continuous eighth-note layer does.
* **Sections** - each section owns a texture and a velocity band (intro/outro quiet,
   chorus loud), but the labels come from an energy detector that is coarse: a long
   "bridge" often holds the repeated choruses. So a section whose *measured* melody
   density reaches the chorus level is escalated to the chorus style - the content
   decides, not the label.
* **Dynamics** - each section owns a velocity band (intro/outro quiet, chorus loud);
   the fixed playback policy (PR-3/4/5) is applied on top by the writers, so the
   per-section band sets the centre and the policy keeps it sane.

The result is a two-staff :class:`~services.arrangement.piano_score.PianoScore`, which
the existing MIDI writer and the fluidsynth renderer consume unchanged - pedal, room
and the vocal mix all come for free from that path.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from services.arrangement.arranger import ArrangedBar, Arrangement, chord_for_bar
from services.arrangement.expression import ExpressionProfile
from services.arrangement.grid import BarGrid
from services.arrangement.harmony import SPAN_CAP, harmonize_melody
from services.arrangement.patterns import PlacedNote, Role, chord_intervals, place_bass, voice_chord
from services.arrangement.piano_score import PianoScore, build_score
from services.arrangement.textures import TEXTURES, TextureName
from services.arrangement.vocal_melody import MelodyNote
from services.audio_analysis.models import ChordEvent
from services.audio_analysis.polyphony import NoteEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionSpan:
    """A named bar range, as the analysis pipeline reports sections."""

    start_bar: int
    end_bar: int  # exclusive
    name: str

    def to_dict(self) -> dict[str, object]:
        return {"startBar": self.start_bar, "endBar": self.end_bar, "name": self.name}


@dataclass(frozen=True)
class SectionStyle:
    """How one section is played: the left-hand figure, the right-hand density, the
    loudness band, and which flourishes the section allows."""

    texture: TextureName
    harmony_level: int  # same-hand budget, 1..4 (see harmony.MAX_HAND_CHORD)
    dynamic: tuple[int, int]  # (velocity floor, velocity ceiling) for the section


#: The tuned default: quiet flowing intro, half-broken verse, a full-arp loud chorus,
#: an arpeggiated bridge, and a fading outro. A section whose *measured* melody
#: density reaches the chorus level is escalated to this chorus style at arrange time
#: (the energy detector's labels are coarse: a long "bridge" often holds the repeated
#: choruses, which is where the tune actually needs the full treatment).
DEFAULT_STYLES: Mapping[str, SectionStyle] = {
    "intro": SectionStyle(TextureName.ARLONG, 2, (46, 64)),
    "verse": SectionStyle(TextureName.HALF, 3, (54, 74)),
    "chorus": SectionStyle(TextureName.ARPFULL, 4, (66, 98)),
    "bridge": SectionStyle(TextureName.ARLONG, 3, (58, 82)),
    "outro": SectionStyle(TextureName.ARLONG, 2, (46, 68)),
}

#: What a bar without a section of its own gets.
DEFAULT_STYLE = SectionStyle(TextureName.HALF, 3, (54, 74))

#: How hard each left-hand role strikes, relative to the section's centre.
_ROLE_FACTOR: Mapping[Role, float] = {
    Role.SKELETON: 1.0,
    Role.CHORD: 0.85,
    Role.COLOR: 0.70,
    Role.ORNAMENT: 0.60,
}


@dataclass(frozen=True)
class BalladConfig:
    # field(default=...) would be the obvious form, but dataclasses reject mutable
    # defaults; the mapping is module-level and never mutated, so the factory just
    # hands out the shared constant.
    styles: Mapping[str, SectionStyle] = field(default_factory=lambda: DEFAULT_STYLES)
    default_style: SectionStyle = DEFAULT_STYLE
    #: Chord tones stay within this many semitones above the bass, so the left hand
    #: (bass 40..52) never climbs into the melody's register.
    left_span: int = 12
    #: Hard ceiling for any left-hand note. A texture's open voicing (half's +12
    #: colour tone) can reach 74, which sits inside the melody - anything above the
    #: ceiling comes down an octave, which keeps the figure and its pitch classes.
    left_ceiling: int = 64
    #: Notes at or above this MIDI pitch are octave-doubled, in every section: the
    #: singer's high notes are the song's peaks, and they shine wherever the melody
    #: reaches them (a "bridge" that is really a repeated chorus has the same peaks).
    octave_double_above: int = 78
    #: The double sits below the original note in loudness.
    octave_double_gain: float = 0.75
    #: A section whose mean melody notes per voiced bar reach this level is escalated
    #: to the chorus style (full texture, loud band, rich harmony). Tuned against the
    #: reference: choruses run ~4 notes/bar, verses and intros stay below.
    escalate_density: float = 4.0
    #: The register the arpeggio fills live in - the right hand's, not the left's.
    fill_low: int = 55
    fill_high: int = 83
    #: Where inside the section band the fill sits (0 = floor, 1 = ceiling).
    fill_position: float = 0.45
    expression: ExpressionProfile = field(default_factory=ExpressionProfile)


@dataclass(frozen=True)
class BalladResult:
    score: PianoScore
    stats: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "noteCount": self.score.note_count,
            "pitchRange": list(self.score.pitch_range()),
            "tiedNotes": self.score.tied_count,
            "stats": dict(self.stats),
        }


def _amp_for_velocity(velocity: float, profile: ExpressionProfile) -> float:
    """The 0..1 loudness the writers map back to ``velocity``.

    The writers run every amplitude through ``to_velocity`` with the profile's floor
    and ceiling, so a note is authored in velocity space and converted back rather
    than guessing a 0..1 value that ends up somewhere else after the mapping.
    """
    span = profile.velocity_ceiling - profile.velocity_floor
    if span <= 0:
        return 0.5
    return min(max((velocity - profile.velocity_floor) / span, 0.0), 1.0)


def _section_density(
    melody: Sequence[NoteEvent], sections: Sequence[SectionSpan], grid: BarGrid
) -> dict[str, float]:
    """The mean melody notes per *voiced* bar, per section name.

    Averaging over voiced bars only, so an instrumental break inside a section does
    not drag its level down; a section with no singing at all reads as zero.
    """
    out: dict[str, float] = {}
    for section in sections:
        counts: list[int] = []
        for bar in range(section.start_bar, section.end_bar):
            start = grid.bar_start(bar)
            counts.append(
                sum(1 for note in melody if start <= note.start < start + grid.bar_seconds)
            )
        voiced = [count for count in counts if count > 0]
        out[section.name] = sum(voiced) / len(voiced) if voiced else 0.0
    return out


def _style_for_bar(
    bar: int,
    sections: Sequence[SectionSpan],
    config: BalladConfig,
    density: Mapping[str, float],
) -> SectionStyle:
    section = next((s for s in sections if s.start_bar <= bar < s.end_bar), None)
    style = (
        config.styles.get(section.name, config.default_style) if section else config.default_style
    )
    chorus = config.styles.get("chorus")
    if (
        section is not None
        and chorus is not None
        and style is not chorus
        and density.get(section.name, 0.0) >= config.escalate_density
    ):
        return chorus
    return style


def _cap_to_left_ceiling(note: PlacedNote, ceiling: int) -> PlacedNote:
    """Bring a left-hand note down whole octaves until it is at or below the ceiling.

    An octave lower keeps the pitch class and the figure; only the register moves,
    which is what the ceiling is for.
    """
    if note.midi <= ceiling:
        return note
    octaves = (note.midi - ceiling + 11) // 12
    return dataclasses.replace(note, midi=note.midi - 12 * octaves)


def _left_hand_bar(
    chord: ChordEvent | None,
    style: SectionStyle,
    grid: BarGrid,
    bar: int,
    config: BalladConfig,
) -> list[NoteEvent]:
    """One bar of the section's texture, in absolute seconds, voiced under the RH."""
    if chord is None:
        return []
    bass = place_bass(chord.root % 12)
    tones = voice_chord(chord_intervals(chord.quality), bass, span=config.left_span)
    placed = TEXTURES[style.texture](bass, tones, grid.beats_per_bar)
    placed = [_cap_to_left_ceiling(note, config.left_ceiling) for note in placed]
    beat_seconds = grid.beat_seconds
    bar_start = grid.bar_start(bar)
    bar_end = grid.bar_end(bar)
    floor, ceiling = style.dynamic
    centre = (floor + ceiling) * 0.5
    notes: list[NoteEvent] = []
    for note in placed:
        start = bar_start + note.beat * beat_seconds
        duration = min(note.duration_beats * beat_seconds, bar_end - start)
        if start >= bar_end - 1e-6 or duration < 0.05:
            continue
        amplitude = _amp_for_velocity(centre * _ROLE_FACTOR[note.role], config.expression)
        notes.append(
            NoteEvent(
                start=round(start, 4),
                duration=round(duration, 4),
                midi=note.midi,
                amplitude=amplitude,
            )
        )
    return notes


def _fill_bar(
    chord: ChordEvent | None, grid: BarGrid, bar: int, style: SectionStyle, config: BalladConfig
) -> list[NoteEvent]:
    """A broken-chord arpeggio for a bar with no melody: one note per eighth.

    The chord's tones are placed around octave 4 and cycled ascending, which is the
    reference score's own way of carrying a section the voice is not singing yet.
    """
    if chord is None:
        return []
    beat_seconds = grid.beat_seconds
    root4 = 60 + (chord.root % 12)
    tones = sorted(
        {root4, root4 + 12} | {root4 + interval for interval in chord_intervals(chord.quality)}
    )
    tones = [t for t in tones if config.fill_low <= t <= config.fill_high]
    if not tones:
        return []
    floor, ceiling = style.dynamic
    velocity = floor + (ceiling - floor) * config.fill_position
    amplitude = _amp_for_velocity(velocity, config.expression)
    bar_start = grid.bar_start(bar)
    eighths = grid.beats_per_bar * 2
    return [
        NoteEvent(
            start=round(bar_start + i * beat_seconds / 2, 4),
            duration=round(beat_seconds / 2, 4),
            midi=tones[i % len(tones)],
            amplitude=amplitude,
        )
        for i in range(eighths)
    ]


def _reband(
    notes: Sequence[NoteEvent], style: SectionStyle, profile: ExpressionProfile
) -> list[NoteEvent]:
    """Map the notes' relative loudness into the section's velocity band.

    The extraction gives per-note loudness relative to the whole song; a section band
    then places that phrasing inside the section's own dynamics (a soft verse keeps
    its swells but stays quiet, a loud chorus keeps them and gets loud).
    """
    floor, ceiling = style.dynamic
    return [
        dataclasses.replace(
            note,
            amplitude=_amp_for_velocity(floor + (ceiling - floor) * float(note.amplitude), profile),
        )
        for note in notes
    ]


def _to_events(melody: Sequence[MelodyNote | NoteEvent]) -> list[NoteEvent]:
    out: list[NoteEvent] = []
    for note in melody:
        if isinstance(note, NoteEvent):
            out.append(note)
        else:
            out.append(
                NoteEvent(
                    start=note.start,
                    duration=note.duration,
                    midi=note.midi,
                    amplitude=note.velocity / 127.0,
                )
            )
    return out


def arrange(
    melody: Sequence[MelodyNote | NoteEvent],
    chords: Sequence[ChordEvent],
    grid: BarGrid,
    sections: Sequence[SectionSpan] = (),
    *,
    config: BalladConfig | None = None,
    title: str = "",
    key_tonic: int | None = None,
    key_mode: str | None = None,
) -> BalladResult:
    """One extracted melody + the song's chords -> a two-hand piano score."""
    cfg = config or BalladConfig()
    melody_events = _to_events(melody)
    beat_seconds = grid.beat_seconds
    bar_count = grid.bar_count()
    if bar_count <= 0:
        raise ValueError("the grid has no bars to arrange")

    # What the voice actually does, per section: it decides whether a labelled
    # section gets the full chorus treatment.
    density = _section_density(melody_events, sections, grid)

    # Group consecutive bars into runs of equal style, so each run is harmonised and
    # re-banded under one level and one dynamic.
    runs: list[tuple[int, int, SectionStyle]] = []
    for bar in range(bar_count):
        style = _style_for_bar(bar, sections, cfg, density)
        if runs and runs[-1][1] == bar and runs[-1][2] is style:
            runs[-1] = (runs[-1][0], bar + 1, style)
        else:
            runs.append((bar, bar + 1, style))

    left_by_bar: dict[int, list[NoteEvent]] = {}
    for bar in range(bar_count):
        style = _style_for_bar(bar, sections, cfg, density)
        left_by_bar[bar] = _left_hand_bar(chord_for_bar(chords, grid, bar), style, grid, bar, cfg)

    right: list[NoteEvent] = []
    doubled = 0
    filled = 0
    for start_bar, end_bar, style in runs:
        run_start = grid.bar_start(start_bar)
        run_end = grid.bar_start(end_bar - 1) + grid.bar_seconds
        slice_ = [n for n in melody_events if run_start <= n.start < run_end]
        if slice_:
            banded = _reband(slice_, style, cfg.expression)
            peaks: list[NoteEvent] = []
            ordinary: list[NoteEvent] = []
            for note in banded:
                if note.midi >= cfg.octave_double_above and note.midi + 12 <= 108:
                    peaks.append(note)
                else:
                    ordinary.append(note)
            if ordinary:
                right.extend(
                    harmonize_melody(
                        ordinary,
                        chords,
                        difficulty=style.harmony_level,
                        beat_seconds=beat_seconds,
                        span_cap=SPAN_CAP.get(style.harmony_level, 12),
                    )
                )
            for note in peaks:
                right.append(note)
                right.append(
                    NoteEvent(
                        start=note.start,
                        duration=note.duration,
                        midi=note.midi + 12,
                        amplitude=note.amplitude * cfg.octave_double_gain,
                    )
                )
                doubled += 1

    # Any bar the voice does not sing gets a broken-chord arpeggio, so the right hand
    # never goes silent (intro, instrumental breaks, fading outro alike). The bar's own
    # style sets the fill's loudness band, so quiet sections fill quietly.
    for bar in range(bar_count):
        bar_start = grid.bar_start(bar)
        if any(bar_start <= n.start < bar_start + grid.bar_seconds for n in melody_events):
            continue
        style = _style_for_bar(bar, sections, cfg, density)
        bar_notes = _fill_bar(chord_for_bar(chords, grid, bar), grid, bar, style, cfg)
        right.extend(bar_notes)
        filled += len(bar_notes)

    right_by_bar: dict[int, list[NoteEvent]] = {bar: [] for bar in range(bar_count)}
    for note in right:
        bar, _beat, _sixteenth, _error = grid.quantize(note.start)
        if 0 <= bar < bar_count:
            right_by_bar[bar].append(note)

    bars: list[ArrangedBar] = []
    for bar in range(bar_count):
        chord = chord_for_bar(chords, grid, bar)
        bars.append(
            ArrangedBar(
                index=bar,
                right=tuple(sorted(right_by_bar[bar], key=lambda n: (n.start, n.midi))),
                left=tuple(sorted(left_by_bar[bar], key=lambda n: (n.start, n.midi))),
                source="recomposed",
                chord=None if chord is None else chord.name,
            )
        )

    # Difficulty 4 so the notator's span fold (build_score) uses the widest budget:
    # the arrangement already kept each hand inside its own span.
    arrangement = Arrangement(bars=bars, fidelity="arranged", difficulty=4)
    score = build_score(arrangement, grid, title=title, key_tonic=key_tonic, key_mode=key_mode)

    left_count = sum(len(notes) for notes in left_by_bar.values())
    stats: dict[str, object] = {
        "barCount": bar_count,
        "sections": [s.to_dict() for s in sections],
        "sectionDensity": {name: round(value, 2) for name, value in density.items()},
        "melodyNotes": len(melody_events),
        "rightNotes": len(right),
        "leftNotes": left_count,
        "octaveDoubled": doubled,
        "fillNotes": filled,
        "pitchRange": list(score.pitch_range()),
        "tiedNotes": score.tied_count,
    }
    logger.info(
        "ballad: %d bars, %d melody -> %d right + %d left notes (%d doubled, %d fill)",
        bar_count,
        len(melody_events),
        len(right),
        left_count,
        doubled,
        filled,
    )
    return BalladResult(score=score, stats=stats)


__all__ = [
    "DEFAULT_STYLE",
    "DEFAULT_STYLES",
    "BalladConfig",
    "BalladResult",
    "SectionSpan",
    "SectionStyle",
    "arrange",
]
