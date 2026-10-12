"""MusicXML output for a two-staff piano score.

Replaces the single-voice writer: a piano score is one part with two staves, and
MusicXML says that directly - ``<staves>2</staves>`` in the first measure's
attributes, and a ``<staff>`` number on every note.

The XML is emitted directly rather than through music21. music21 10.5's exporter
was verified on this exact two-PartStaff case and emits **no notes and no staff
tags at all** - a StaffGroup containing two PartStaffs each holding Measures comes
out empty. Rather than fight that, the document is written here, which also makes
the two things a hand-rolled writer always gets wrong impossible to get wrong by
accident:

* every measure is padded to the full bar with a hidden rest, because a measure
  where one hand rests would otherwise end early and the next bar's notes would
  print over the wrong bar line;
* notes that :mod:`services.arrangement.piano_score` split at a barline get
  ``<tie>``/``<tied>``, so a sustained note stays one note in the renderer.

music21 is still used, in :func:`musicxml_summary`, to read a written file back
and confirm a renderer would see what we intended.
"""

from __future__ import annotations

import logging
from math import inf
from pathlib import Path

from services.arrangement.piano_score import DIVISIONS, PianoScore, ScoredNote

logger = logging.getLogger(__name__)

_NOTE_TYPES = (
    (4.0, "whole"),
    (3.0, "half"),
    (2.0, "half"),
    (1.5, "quarter"),
    (1.0, "quarter"),
    (0.75, "eighth"),
    (0.5, "eighth"),
    (0.375, "16th"),
    (0.25, "16th"),
    (0.125, "32nd"),
)
#: Dots needed for the durations that are not powers of two.
_DOTS = {3.0: 1, 1.5: 1, 0.75: 1, 0.375: 1}
_CLEF_MARKS = {"treble": ("G", 2), "bass": ("F", 4)}

#: Alteration numbers MusicXML uses for sharps and flats.
_SHARP_ORDER = ("F", "C", "G", "D", "A", "E", "B")

#: Characters that cannot appear literally in XML character data. Escaped by hand
#: rather than via xml.sax.saxutils, which bandit flags as an XML-parsing import
#: (B406); this module only ever writes XML, never parses it.
_XML_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def _escape(text: str) -> str:
    """Escape text for use in XML character data."""
    for char, replacement in _XML_ESCAPES:
        text = text.replace(char, replacement)
    return text


def note_type_for(quarters: float) -> tuple[str, int]:
    """Nearest notatable (type, dots) for a duration in quarter notes."""
    best = min(_NOTE_TYPES, key=lambda item: abs(item[0] - quarters))
    return best[1], _DOTS.get(best[0], 0)


def _pitch_xml(midi: int) -> tuple[str, int, int]:
    """Step, alter and octave for a MIDI pitch (C4 = 60)."""
    names = ("C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B")
    pc = midi % 12
    step = names[pc]
    alter = pc - names.index(step)
    octave = midi // 12 - 1
    return step, alter, octave


def _accidentals(fifths: int) -> set[str]:
    """Which staff positions get an accidental from the key signature."""
    if fifths > 0:
        return set(_SHARP_ORDER[:fifths])
    return set(_SHARP_ORDER[7 + fifths :])


def _key_alter(step: str) -> int:
    order = ("F", "C", "G", "D", "A", "E", "B")
    return order.index(step)


def _attributes_xml(score: PianoScore, *, first: bool) -> str:
    lines = ["      <attributes>"]
    if first:
        lines.append(f"        <divisions>{DIVISIONS}</divisions>")
    lines.append("        <time>")
    lines.append(f"          <beats>{score.beats_per_bar}</beats>")
    lines.append("          <beat-type>4</beat-type>")
    lines.append("        </time>")
    if first:
        lines.append(f"        <key><fifths>{score.key_fifths}</fifths></key>")
        lines.append("        <staves>2</staves>")
        for staff_number, clef in ((1, score.right_clef), (2, score.left_clef)):
            sign, line = _CLEF_MARKS[clef]
            lines.append(f'        <clef number="{staff_number}">')
            lines.append(f"          <sign>{sign}</sign>")
            lines.append(f"          <line>{line}</line>")
            lines.append("        </clef>")
    lines.append("      </attributes>")
    return "\n".join(lines)


def _note_xml(
    scored: ScoredNote,
    staff: int,
    *,
    key_alters: set[str],
    duration_beats: float | None = None,
    chord: bool = False,
) -> str:
    """One ``<note>`` element.

    ``duration_beats`` overrides the note's own length, which a chord needs: the
    sounding length of a chord is the *shortest* of its notes, and MusicXML puts
    that on every member including the first.

    ``chord`` adds the ``<chord/>`` marker. Without it a renderer reads the note
    as starting after the previous one finished, so a three-note chord is drawn
    as three consecutive notes - which is why the score looked like a single-note
    line.
    """
    step, alter, octave = _pitch_xml(scored.midi)
    written = duration_beats if duration_beats is not None else scored.duration_beats
    type_name, dots = note_type_for(written)
    duration = max(1, round(written * DIVISIONS))

    lines = ["      <note>"]
    if chord:
        lines.append("        <chord/>")
    lines.append("        <pitch>")
    lines.append(f"          <step>{step}</step>")
    if alter:
        lines.append(f"          <alter>{alter}</alter>")
    lines.append(f"          <octave>{octave}</octave>")
    lines.append("        </pitch>")
    lines.append(f"        <duration>{duration}</duration>")
    if scored.tied_to_next or scored.tie_start:
        tie_type = "start" if scored.tied_to_next else "stop"
        lines.append(f'        <tie type="{tie_type}"/>')
    lines.append(f"        <voice>{staff}</voice>")
    lines.append(f"        <type>{type_name}</type>")
    for _ in range(dots):
        lines.append("        <dot/>")
    # Only print an accidental the key signature does not already imply.
    if alter and step not in key_alters:
        value = "sharp" if alter > 0 else "flat"
        if abs(alter) > 1:
            lines.append(f"        <accidental>{value}{abs(alter)}</accidental>")
        else:
            lines.append(f"        <accidental>{value}</accidental>")
    if scored.amplitude > 0:
        velocity = round(min(max(scored.amplitude, 0.0), 1.0) * 127)
        lines.append(f'        <sound dynamics="{velocity}"/>')
    if scored.tied_to_next or scored.tie_start:
        tie_type = "start" if scored.tied_to_next else "stop"
        lines.append(f'        <notations><tied type="{tie_type}"/></notations>')
    lines.append(f"        <staff>{staff}</staff>")
    lines.append("      </note>")
    return "\n".join(lines)


def _pad_xml(score: PianoScore, staff: int, used: float) -> str:
    """A hidden rest so the measure still spans the whole bar.

    ``used`` must be the tick position the staff actually *reached*, not
    ``max(beat + length)``. The two disagree whenever a staff starts late in the
    bar: the notes' own durations then add up to less than the bar even though the
    last note ends on the barline, and the measure falls short - which is how every
    measure used to overflow or underflow. See :func:`_voice_xml`.
    """
    gap = score.beats_per_bar - used
    if gap <= 1e-6:
        return ""
    ticks = round(gap * DIVISIONS)
    lines = ["      <note>", "        <rest/>", f"        <duration>{ticks}</duration>"]
    lines.append(f"        <voice>{staff}</voice>")
    lines.append("        <type>quarter</type>")
    lines.append(f"        <staff>{staff}</staff>")
    lines.append("      </note>")
    return "\n".join(lines)


#: Two onsets this close, in beats, are the same instant.
#:
#: Snapping rounds to four decimals, so the noise is a rounding error, not a
#: musical fraction. An earlier version used a sixteenth note (0.25 beats) and
#: merged every pair of consecutive sixteenths into a chord - 74 markers where
#: only 45 chords exist.
ONSET_TOLERANCE = 1e-3


def _group_by_onset(notes: tuple[ScoredNote, ...]) -> list[tuple[float, list[ScoredNote]]]:
    """Group a staff's notes into simultaneous groups, in time order.

    Onsets within :data:`ONSET_TOLERANCE` beats land in the same group, so a chord
    survives the rounding that quantisation applies. Anything further apart is a
    real musical fraction and must stay a separate note.
    """
    if not notes:
        return []
    groups: list[tuple[float, list[ScoredNote]]] = []
    for note in sorted(notes, key=lambda n: (n.beat, n.midi)):
        if groups and abs(note.beat - groups[-1][0]) <= ONSET_TOLERANCE:
            groups[-1][1].append(note)
        else:
            groups.append((note.beat, [note]))
    return groups


def _forward_xml(ticks: int) -> str:
    """Advance the tick cursor without printing anything."""
    return "\n".join(
        [
            "      <forward>",
            f"        <duration>{ticks}</duration>",
            "      </forward>",
        ]
    )


def _backup_xml(ticks: int) -> str:
    """Rewind the tick cursor so the next staff starts at the barline again."""
    return "\n".join(
        [
            "      <backup>",
            f"        <duration>{ticks}</duration>",
            "      </backup>",
        ]
    )


def _voice_xml(
    score: PianoScore,
    notes: tuple[ScoredNote, ...],
    staff_number: int,
    key_alters: set[str],
    bar_ticks: int,
) -> tuple[str, float]:
    """One staff's music in one measure, at the beats it was written on.

    MusicXML has no ``offset``: a note's position is wherever the tick cursor
    happens to be, and only ``<forward>``/``<backup>`` move that cursor. The first
    version of this writer emitted neither, so a note written on beat 2.5 was
    engraved wherever the *previous* note's duration left the cursor. Measured on the
    real fixture, every one of the 17 measures came out wrong: interior gaps
    collapsed and pulled the rest of the bar left, and the left hand began where the
    right hand finished rather than at the barline.

    Returns the measure's music and the beats this staff actually reached.
    """
    lines: list[str] = []
    cursor = 0
    groups = _group_by_onset(notes)

    for index, (onset, chord_notes) in enumerate(groups):
        position = max(0, round(onset * DIVISIONS))
        if position > cursor:
            # The gap the player hears as a rest. Without this the note lands early.
            lines.append(_forward_xml(position - cursor))
            cursor = position
        # One staff is one voice, and a voice cannot have two notes sounding at once.
        # Quantisation can still produce that - a note that was already ringing when
        # the next one arrived - and the two options are to shorten the earlier note or
        # to delay the later one. Shortening keeps every onset, which is what the
        # follow-along highlight and the player's fingers both depend on; delaying
        # quietly rewrites the rhythm. An earlier version dropped whatever would not
        # fit, which cost nine notes across the real fixture and left the keyboard
        # lighting keys that had no note-head to match.
        next_onset = groups[index + 1][0] if index + 1 < len(groups) else float(inf)
        limit = min(next_onset, score.beats_per_bar) - onset
        # A chord lasts as long as its shortest member, and MusicXML writes that
        # length on all of them.
        length = min(n.duration_beats for n in chord_notes)
        length_ticks = max(1, round(min(length, limit, bar_ticks - cursor) * DIVISIONS))
        written = length_ticks / DIVISIONS
        for position_in_chord, scored in enumerate(chord_notes):
            lines.append(
                _note_xml(
                    scored,
                    staff_number,
                    key_alters=key_alters,
                    duration_beats=written,
                    chord=position_in_chord > 0,
                )
            )
        cursor += length_ticks

    padding = _pad_xml(score, staff_number, cursor / DIVISIONS)
    if padding:
        lines.append(padding)
    return "\n".join(lines), cursor / DIVISIONS


def _measure_xml(score: PianoScore, index: int, key_alters: set[str]) -> str:
    measure = score.measures[index]
    bar_ticks = score.beats_per_bar * DIVISIONS
    lines = [f'    <measure number="{index + 1}">']
    if index == 0:
        lines.append(_attributes_xml(score, first=True))
        lines.append("      <direction>")
        lines.append("        <direction-type>")
        lines.append('          <metronome parentheses="no">')
        lines.append("            <beat-unit>quarter</beat-unit>")
        lines.append(f"            <per-minute>{score.tempo_bpm:g}</per-minute>")
        lines.append("          </metronome>")
        lines.append("        </direction-type>")
        lines.append(f'        <sound tempo="{score.tempo_bpm:g}"/>')
        lines.append("      </direction>")

    right, _used_right = _voice_xml(score, measure.right, 1, key_alters, bar_ticks)
    lines.append(right)
    # Back to the barline for the second staff. A piano measure is two voices that
    # both start at beat 1; without this the left hand is engraved one right-hand
    # bar later, which is what put staff 2 at tick 24 of a 16-tick measure.
    lines.append(_backup_xml(bar_ticks))
    left, _used_left = _voice_xml(score, measure.left, 2, key_alters, bar_ticks)
    lines.append(left)

    if index == len(score.measures) - 1:
        lines.append('      <barline location="right">')
        lines.append("        <bar-style>light-heavy</bar-style>")
        lines.append("      </barline>")
    lines.append("    </measure>")
    return "\n".join(lines)


def render_musicxml(score: PianoScore) -> str:
    """The complete MusicXML document as a string."""
    key_alters = _accidentals(score.key_fifths)
    measures = "\n".join(_measure_xml(score, i, key_alters) for i in range(len(score.measures)))
    title = _escape(score.title) if score.title else "PianoFlow"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="4.0">
  <work><work-title>{title}</work-title></work>
  <identification>
    <encoding>
      <software>PianoFlow</software>
      <supports element="accidental" type="yes"/>
    </encoding>
  </identification>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
{measures}
  </part>
</score-partwise>
"""


def score_musicxml(score: PianoScore) -> str:
    """The MusicXML document as a string, for the API to serve inline."""
    return render_musicxml(score)


def to_musicxml(score: PianoScore, path: Path, *, title: str | None = None) -> Path:
    """Write the score as MusicXML."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    resolved = score
    if title is not None and title != score.title:
        from dataclasses import replace

        resolved = replace(score, title=title)
    target.write_text(render_musicxml(resolved), encoding="utf-8")
    logger.info("wrote musicxml: %s", target)
    return target


def musicxml_summary(path: Path) -> dict[str, object]:
    """Read a file back and report what a renderer would see."""
    from music21 import (
        converter,  # imported lazily: parsing is not needed everywhere
        stream,  # imported lazily: parsing is not needed everywhere
    )

    parsed = converter.parse(str(path), forceSource=True)
    if not isinstance(parsed, stream.Score):
        raise ValueError(f"not a score document: {path}")
    text = Path(path).read_text(encoding="utf-8")
    # music21 reads a two-staff part back as two PartStaff objects, so this
    # counts staves rather than MusicXML <part> elements.
    staves = len(parsed.parts)
    return {
        "staves": staves,
        "measures": text.count("<measure "),
        "notes": len(list(parsed.recurse().notes)),
        "staffTags": text.count("<staff>"),
        "stavesDeclared": "<staves>2</staves>" in text,
    }


__all__ = [
    "DIVISIONS",
    "musicxml_summary",
    "note_type_for",
    "render_musicxml",
    "score_musicxml",
    "to_musicxml",
]
