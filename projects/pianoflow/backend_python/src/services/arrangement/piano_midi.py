"""MIDI output for a two-staff piano score.

Replaces the single-voice writer with two playable tracks plus the usual tempo
map, so the result opens as a piano piece in any DAW or notation editor:

* track 0 meta: tempo, time signature, key
* track 1: right hand, program 0 (piano)
* track 2: left hand, program 0

``mido`` needs absolute delta times, so both tracks are built from sorted
``(tick, order, message)`` events. ``order`` breaks the tie between a note-off and
a note-on of the same pitch at the same tick, so a repeated note does not get cut
short: note-offs sort before note-ons.
"""

from __future__ import annotations

import logging
from pathlib import Path

import mido

from services.arrangement.expression import ExpressionProfile, velocities
from services.arrangement.piano_score import PianoScore, ScoredNote

logger = logging.getLogger(__name__)

TICKS_PER_BEAT = 480
#: General MIDI program 0 is acoustic grand piano.
#: Used only when a note cannot be matched to the score timeline, which should not
#: happen: it is the same note list. Loud enough to hear if it ever does.
MIDI_VELOCITY_FALLBACK = 64

PIANO_PROGRAM = 0
RIGHT_TRACK_NAME = "Right hand"
LEFT_TRACK_NAME = "Left hand"
META_TRACK_NAME = "PianoFlow"


def seconds_to_ticks(seconds: float, tempo_bpm: float, ticks_per_beat: int = TICKS_PER_BEAT) -> int:
    """Convert seconds to ticks. Kept for callers that work in seconds."""
    beats = seconds * (tempo_bpm / 60.0)
    return round(beats * ticks_per_beat)


def beats_to_ticks(beats: float, ticks_per_beat: int = TICKS_PER_BEAT) -> int:
    """Convert quarter-note beats to ticks, which is what the score carries."""
    return round(beats * ticks_per_beat)


def _merge_ties(
    score: PianoScore, notes: tuple[ScoredNote, ...]
) -> list[tuple[ScoredNote, float, float]]:
    """Join the halves of a split note into one span, in beats from zero.

    MusicXML shows a sustained note as two tied notes across the barline. MIDI has
    no ties, so emitting both halves re-articulates the note with a tiny gap -
    audible as a stutter at the barline. Merging is what makes the playback match
    the notation.

    The pairing is by (bar, beat, pitch) rather than by list position, and that is the
    whole point. A sustained note crossing a barline sits alongside the other notes of
    the next chord, so in a list sorted by (bar, beat, midi) the two halves of one tied
    note are *not* adjacent - the intervening chord tones are. Only ever comparing against
    ``merged[-1]`` therefore missed most of them: on the reference recording 21 tied
    notes in the right hand yielded only 6 merged notes, and 30 in the left hand only 4,
    so 35 of 45 sustained notes were being re-struck. That is the stutter this function
    exists to prevent.
    """
    beats_per_bar = score.beats_per_bar
    #: pitch -> indices of pieces whose note still expects a tied continuation, with the
    #: beat that continuation has to start on.
    open_ties: dict[int, list[tuple[int, float]]] = {}
    pieces: list[tuple[ScoredNote, float, float]] = []

    def beat_of(scored: ScoredNote) -> tuple[float, float]:
        offset = scored.bar * beats_per_bar
        return offset + scored.beat, offset + scored.end_beat

    for scored in sorted(notes, key=lambda n: (n.bar, n.beat, n.midi)):
        start, end = beat_of(scored)

        target = None
        if scored.tie_start:
            for index, expected_start in reversed(open_ties.get(scored.midi, ())):
                # Only join when the halves actually touch: the continuation has to begin
                # exactly where the first half ended. A tie flag without continuity is a
                # marking error, and re-striking is the safer reading of that.
                if abs(expected_start - start) < 1e-6:
                    target = index
                    break

        if target is None:
            pieces.append((scored, start, end))
            if scored.tied_to_next:
                open_ties.setdefault(scored.midi, []).append((len(pieces) - 1, end))
            # A note that is not a continuation does not cancel the pending ties of its
            # pitch. Those belong to other sustained notes on the same key, and dropping
            # them here would silently re-strike them.
            continue

        previous, prev_start, _previous_end = pieces[target]
        pieces[target] = (previous, prev_start, end)
        remaining = [(i, s) for i, s in open_ties[scored.midi] if i != target]
        if scored.tied_to_next:
            # The joined note runs longer and may tie on again.
            remaining.append((target, end))
        if remaining:
            open_ties[scored.midi] = remaining
        else:
            del open_ties[scored.midi]

    return pieces


def _note_events(
    score: PianoScore,
    notes: tuple[ScoredNote, ...],
    velocities_by_key: dict[tuple[int, float, int], int],
) -> list[tuple[int, int, mido.Message]]:
    """Absolute-tick note_on/note_off pairs for one staff."""
    beat_ticks = TICKS_PER_BEAT
    events: list[tuple[int, int, mido.Message]] = []
    for scored, start_beat, end_beat in _merge_ties(score, notes):
        start = round(start_beat * beat_ticks)
        end = round(end_beat * beat_ticks)
        if end <= start:
            end = start + 1
        velocity = velocities_by_key.get(
            (scored.bar + 1, round(scored.beat, 4), scored.midi), MIDI_VELOCITY_FALLBACK
        )
        events.append((start, 1, mido.Message("note_on", note=scored.midi, velocity=velocity)))
        events.append((end, 0, mido.Message("note_off", note=scored.midi, velocity=0)))
    return events


def _track_from(
    name: str,
    events: list[tuple[int, int, mido.Message]],
    total_ticks: int,
) -> mido.MidiTrack:
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))
    track.append(mido.Message("program_change", program=PIANO_PROGRAM, channel=0, time=0))
    previous = 0
    for tick, _order, message in sorted(events, key=lambda item: (item[0], item[1])):
        message.time = tick - previous
        previous = tick
        track.append(message)
    tail = max(total_ticks - previous, 0)
    track.append(mido.MetaMessage("end_of_track", time=tail))
    return track


def velocity_lookup(
    score: PianoScore, *, profile: ExpressionProfile | None = None
) -> dict[tuple[int, float, int], int]:
    """Every note's final velocity, keyed by (bar, beat, MIDI).

    The exported MIDI used to compute its own velocity as ``amplitude * 127``, which is
    the same dead end the audio renderer had: it skipped the measured velocity range, the
    hand balance and the slew limit, and produced a file whose dynamics contradicted the
    audio made from the same score. Both now read this one function, so "what the score
    sounds like" and "what the score plays back as" cannot drift apart.

    Keyed by notated position rather than by seconds, deliberately. The tick stream here
    works in beats from bar 0 while ``note_timeline`` works in seconds including
    ``phase_seconds``, and a key that spans the two time bases silently misses every
    note - which is what happened first, and it filled the whole file with the fallback
    velocity of 64.
    """
    timeline = score.note_timeline()
    if not timeline:
        return {}
    values = velocities(
        [float(note["amplitude"]) for note in timeline],
        [str(note["hand"]) for note in timeline],
        profile=profile,
        times=[float(note["time"]) for note in timeline],
    )
    return {
        (int(n["bar"]), round(float(n["beat"]), 4), int(n["midi"])): int(v)
        for n, v in zip(timeline, values, strict=True)
    }


def write_midi(score: PianoScore, path: Path, *, profile: ExpressionProfile | None = None) -> Path:
    """Write the score as a two-track piano MIDI file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    midi = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name=META_TRACK_NAME, time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(score.tempo_bpm), time=0))
    meta.append(
        # This mido build names them numerator/denominator, not numer/denom.
        mido.MetaMessage(
            "time_signature",
            numerator=score.beats_per_bar,
            denominator=4,
            time=0,
        )
    )
    key_name = key_name_for(score.key_fifths)
    if key_name:
        meta.append(mido.MetaMessage("key_signature", key=key_name, time=0))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(meta)

    right: list[ScoredNote] = [n for m in score.measures for n in m.right]
    left: list[ScoredNote] = [n for m in score.measures for n in m.left]
    lookup = velocity_lookup(score, profile=profile)
    right_events = _note_events(score, tuple(right), lookup)
    left_events = _note_events(score, tuple(left), lookup)

    total_ticks = max((tick for tick, _order, _message in [*right_events, *left_events]), default=0)
    midi.tracks.append(_track_from(RIGHT_TRACK_NAME, right_events, total_ticks))
    midi.tracks.append(_track_from(LEFT_TRACK_NAME, left_events, total_ticks))

    midi.save(str(target))
    logger.info("wrote midi: %s (%d right, %d left)", target, len(right), len(left))
    return target


#: Circle-of-fifths position -> the key name mido accepts for key_signature.
#
# mido does not take a fifths count: its key_signature message wants a key *name*,
# and passing an integer raises "invalid key 4". Only these fifteen names exist, so
# a fifths value outside -7..7 has no representation and is skipped.
FIFTHS_TO_KEY_NAME = {
    -7: "Cb",
    -6: "Gb",
    -5: "Db",
    -4: "Ab",
    -3: "Eb",
    -2: "Bb",
    -1: "F",
    0: "C",
    1: "G",
    2: "D",
    3: "A",
    4: "E",
    5: "B",
    6: "F#",
    7: "C#",
}


def key_name_for(fifths: int) -> str | None:
    """The key name for a fifths count, or None when it has no representation."""
    return FIFTHS_TO_KEY_NAME.get(fifths)


def midi_summary(path: Path) -> dict[str, int | list[str] | list[float]]:
    """Report what a player will see."""
    midi = mido.MidiFile(str(path))
    names: list[str] = []
    note_ons = 0
    tempos: list[float] = []
    for track in midi.tracks:
        for message in track:
            if message.type == "track_name" and message.name:
                names.append(message.name)
            elif message.type == "note_on" and message.velocity > 0:
                note_ons += 1
            elif message.type == "set_tempo":
                tempos.append(round(mido.tempo2bpm(message.tempo), 2))
    return {
        "tracks": len(midi.tracks),
        "trackNames": names,
        "noteOns": note_ons,
        "tempos": tempos,
    }


__all__ = [
    "FIFTHS_TO_KEY_NAME",
    "LEFT_TRACK_NAME",
    "META_TRACK_NAME",
    "PIANO_PROGRAM",
    "RIGHT_TRACK_NAME",
    "TICKS_PER_BEAT",
    "beats_to_ticks",
    "key_name_for",
    "midi_summary",
    "seconds_to_ticks",
    "write_midi",
]
