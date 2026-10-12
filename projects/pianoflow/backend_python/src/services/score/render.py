"""Render a playable audio track from the score.

Why this exists
---------------
Follow-along mode used to play the *original recording* while the notation came
from a quantised grid. Those are two independent estimates of the same music, so
they drift apart: measured on the test piece the quantisation error reaches 52 ms
per note, and a 1 % tempo difference accumulates 300 ms over 30 seconds. A player
following the keys was therefore never quite right.

Here the relationship is inverted. The audio is generated *from* ``score.notes``,
and the notation, the cursor and the keyboard all read that same list. The three
are consistent by construction rather than by luck.

That also removes the non-piano content. The input to the pipeline is a mixed
recording, so playing it back meant hearing guitar, drums and vocals while the
keyboard showed piano notes. A synthesised track contains only what is on the
staff.

The synth is fluidsynth with a SoundFont. Its pitch accuracy was measured across
four octaves (A2 110 Hz, A3 220 Hz, C6 1046.5 Hz, A6 2349.3 Hz) with every error
under 10 Hz, which is what makes the round-trip test meaningful.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.errors import ValidationError
from services.arrangement.expression import ExpressionProfile, velocities
from services.arrangement.piano_score import PianoScore
from services.audio_analysis.dynamics import VELOCITY_FLOOR
from services.audio_analysis.room import (
    apply_room,
    decay_slope_after,
    measure_release_decay,
    synthesise_impulse_response,
    tail_energy_ratio,
)

logger = logging.getLogger(__name__)

#: Analysis sample rate. The rendered track matches it so the transcription model
#: can read it back for the round-trip test.
SAMPLE_RATE = 22050
#: fluidsynth's own rate. Anything else makes the pitch shift.
SYNTH_RATE = 44100
#: Rendered track peak in dBFS. The raw fluidsynth output measured -28.8 dBFS,
#: far too quiet to follow; the track is normalised to just under full scale.
TARGET_PEAK_DBFS = -1.0
#: Give up on the synth rather than hang a request.
RENDER_TIMEOUT_SECONDS = 300

#: How much to darken the render, in decibels per octave above TONE_FREQ. Zero by
#: default, and that default came from a measurement that overturned an assumption.
#:
#: Comparing 星辰大海 with its own render suggested the synth was far too bright:
#:
#:     spectral centroid    598 Hz  ->  1110 Hz   (1.86x)
#:     harmonic roll-off   -2.42     ->  -0.07 dB per partial
#:
#: Both numbers were an artefact of comparing different material - sixty seconds of
#: dense, bass-heavy music against a single held chord. Measured on the *same* note,
#: the render's roll-off is -4.78 dB per partial against the recording's -2.42: the
#: synth is already darker than the source, and a -3 dB/octave tilt pushes it further
#: from the target rather than towards it.
#:
#: So no correction is applied. The knob stays because a different SoundFont may want
#: one, and `spectral_centroid` stays because the claim needs to stay checkable - but
#: the honest answer to "make it sound like the recording" is a closer instrument and
#: a transcription model that reproduces the player's timing and dynamics, not EQ.
TONE_TILT_DB_PER_OCTAVE = 0.0
#: Corner of the tilt, should one ever be wanted. Below this the response is flat, so
#: the fundamental and the lower partials - which carry the pitch - are untouched.
TONE_FREQ = 320.0
#: Shelf slope. 0.7 is the Butterworth-ish default and keeps the transition gentle
#: enough not to sound like a filter sweeping.
TONE_Q = 0.7

#: MIDI controller 64 is the sustain pedal, and it is the whole difference between
#: a piano and a sequence of beeps. Without it the render is dry and discontinuous,
#: because every note is a hard gate and the gap between two notes is true silence.
SUSTAIN_CC = 64
PEDAL_DOWN = 127
PEDAL_UP = 0
#: How long before the barline the pedal lifts, so the release is heard but the
#: next chord does not start smeared into the old harmony.
PEDAL_RELEASE_SECONDS = 0.04
#: How much of the bar the pedal is released *early*, as a fraction of the bar. Lifting
#: only 0.04 s before the barline is effectively "off at the barline", which still leaves
#: the whole bar smearing into the next one; releasing a real slice of the bar is what
#: lets each harmony clear before the next one arrives.
#:
#: Chosen by ear over a five-way comparison of the wet level and this fraction on the
#: reference recording, and 0.18 with the wet level at -4.5 dB was clearly the most
#: comfortable of them. The measured differences are small - attack-to-sustain ran from
#: +5.81 dB down to +5.38 dB and the tail barely moved - so this is a preference rather
#: than a measurement pointing the way, and it is recorded as such.
PEDAL_RELEASE_FRACTION = 0.18

#: When the texture system is active, the left hand carries the harmonic/rhythmic
#: identity of the arrangement. A drier mix makes the rhythmic differences between
#: textures audible; the default room and pedal blur them together.
TEXTURE_ROOM_WET_DB = -8.0
TEXTURE_PEDAL_RELEASE_FRACTION = 0.08

#: Wet level of the measured room: the reverberant energy as a fraction of the dry
#: energy, so -3 dB adds about half as much energy again as the direct sound already has.
#:
#: **Re-measured, and the old justification was stale.** It used to be 0 dB, on the
#: strength of a table saying -3 dB was 11 % out on decay and 0 dB was 3 % out. That table
#: predates the current impulse response - the one built band by band from filtered noise,
#: after the frequency-domain attempt was discarded for asking for 4 TiB - so it was
#: measuring an implementation that no longer exists. Re-measured on 星辰大海, 40 s,
#: against the source's own -2.68 dB/s tail:
#:
#:     wet      0.0    -3.0    -6.0    -9.0     dB
#:     decay   -2.55   -2.68   -2.88   -3.14    dB/s
#:     error     5 %     0 %     7 %    17 %
#:
#: So -3 dB is now the best match on both counts, where 0 dB was worse on both. It also
#: fixes the complaint that sent us to a room at all. "muddy, every note unclear" is the
#: transient-to-sustain ratio, and at 0 dB it measured **-0.382 dB** - the sustain after the
#: attack was louder than the attack, which is the arithmetic definition of a note whose
#: start cannot be heard. At -3 dB it is -0.113, and turning the room off entirely recovers
#: +1.384. The room is what was burying the attacks; the pedal is not, and a pedal that
#: follows the notes rather than the barline was measured and does not help (duty stays at
#: 95 %, and the tail length changes nothing, because the notes fill the bar anyway).
#:
#: **Now -4.5 dB, chosen by ear rather than by that table.** Once the tied notes stopped
#: being re-struck, the complaint became that it was still slightly muddy, and a comparison
#: of -3/-4.5/-6 dB crossed with the pedal fraction preferred -4.5 dB with an 18 % early
#: release. The decay match is the worse of the two by a little: at -4.5 dB the tail decays
#: about 3 % slower than the source's, against 0 % at -3 dB. That is a real cost and it was
#: accepted, because the attack-to-sustain ratio - which is what "muddy" actually means -
#: is what the ear is judging, and it improves as the room comes down. Re-measure both if
#: the playback starts sounding thin or detached instead.
#:
#: The tail is still what the room is for - it is the one thing a recording never stops -
#: and at -3 dB that tail matches the source exactly. -4.5 dB trades that exact match for
#: a clearer attack; see the note above.
ROOM_WET_DB = -4.5

#: The decay is only fitted over frames within this many decibels of the render's own
#: peak. Absolute floors do not work here: the render is normalised, so an absolute
#: threshold measures the normalisation rather than the tail, and a dry render's
#: "decay" comes out as a fit through digital silence - which is how an earlier version
#: reported -31 dB/s for a track whose real tail decays at -3.91 dB/s.
DECAY_FLOOR_DB = 60.0


@dataclass(frozen=True)
class RenderResult:
    """A rendered track plus the events it was built from."""

    path: Path
    sample_rate: int
    duration_seconds: float
    peak: float
    #: (onset_seconds, duration_seconds, midi, velocity) in render order.
    events: tuple[tuple[float, float, int, int], ...]
    #: The measured decay of the source recording, and the decay of what was rendered.
    #: Both in dB/s, and ``None`` where there was nothing to measure - which is the
    #: honest answer for a dry render, since its tail is digital silence.
    room: Mapping[str, object] = dataclasses.field(default_factory=dict)

    @property
    def note_count(self) -> int:
        return len(self.events)

    def to_dict(self) -> dict[str, object]:
        return {
            "noteCount": self.note_count,
            "durationSeconds": round(self.duration_seconds, 3),
            "peak": round(self.peak, 4),
            "sampleRate": self.sample_rate,
        }


def _merge_tied_events(
    events: list[tuple[float, float, int, int, int, int, int]],
) -> list[tuple[float, float, int, int]]:
    """Join the two halves of a tied note into one note-on, holding through both.

    MusicXML has to spell a note that overruns a barline as two tied note-heads, because
    there is no other way to write it. MIDI has no ties, so turning those two heads back
    into two note_ons re-strikes the string at the barline - audible as a click in the
    middle of a sustained note. The note-off then also lands early, so the gap the tie was
    there to bridge reopens. One press is what the notation means.

    Pairing is by staff and pitch, and the halves have to actually touch. Tied halves are
    written underneath the chord that follows, so in a list ordered by time the notes of
    that chord sit between them; matching only against the immediately preceding entry
    therefore finds almost none of them. A tie flag without continuity is a marking error,
    and re-striking is the safer reading of one.
    """
    # (staff, midi) -> (index into merged, time the continuation must start on)
    pending: dict[tuple[int, int], list[tuple[int, float]]] = {}
    merged: list[tuple[float, float, int, int]] = []

    for time, duration, midi, velocity, staff, tie_start, tied_to_next in events:
        key = (staff, midi)
        target = None
        if tie_start:
            for index, expected in reversed(pending.get(key, ())):
                # note_timeline rounds times to 4 decimals, so two halves that meet
                # exactly can land 0.1 ms apart. 0.5 ms absorbs that and is still far
                # below any gap that could be deliberate.
                if abs(expected - time) < 5e-4:
                    target = index
                    break

        if target is None:
            merged.append((time, duration, midi, velocity))
            if tied_to_next:
                pending.setdefault(key, []).append((len(merged) - 1, time + duration))
            # A plain note on this key leaves the pending ties of other notes on it alone;
            # dropping them would re-strike those notes.
            continue

        onset, _held, pitch, strike = merged[target]
        merged[target] = (onset, time + duration - onset, pitch, strike)
        remaining = [(i, t) for i, t in pending[key] if i != target]
        if tied_to_next:
            # The joined note holds longer, so it may tie on again.
            remaining.append((target, time + duration))
        if remaining:
            pending[key] = remaining
        else:
            del pending[key]

    return merged


def note_events(
    score: PianoScore, *, profile: ExpressionProfile | None = None
) -> list[tuple[float, float, int, int]]:
    """Every note as (onset, duration, midi, velocity), in time order.

    This is the single source of truth the notation, the cursor and the keyboard
    all read, and the exact list :func:`render` feeds to the synth.

    The velocity is the **measured** loudness put through the fixed playback rules in
    :mod:`services.arrangement.expression`, not ``amplitude * 127``. That multiplication
    was doing two things nobody chose: it ignored ``VELOCITY_FLOOR``/``VELOCITY_CEIL``
    entirely - they were dead constants - and it gave every note the full 0..127 range,
    which measured p1 = 3 and p99 = 127 with a tenth of the notes at maximum hammer
    force. It also skipped the hand balance and the slew limit, which is where
    "右手比左手大声一点点" and "不要有突然响度增大的情况" live.
    """
    timeline = score.note_timeline()
    rules = profile or ExpressionProfile()
    values = [
        velocities(
            [float(note["amplitude"]) for note in timeline],
            [str(note["hand"]) for note in timeline],
            profile=rules,
            times=[float(note["time"]) for note in timeline],
        )
    ][0]

    events: list[tuple[float, float, int, int, int, int, int]] = []
    for note, velocity in zip(timeline, values, strict=True):
        # A silent note would be a note the keyboard lights but the ear cannot
        # hear, so a floor is applied.
        events.append(
            (
                float(note["time"]),
                float(note["duration"]),
                int(note["midi"]),
                max(int(velocity), VELOCITY_FLOOR),
                int(note["staff"]),
                bool(note["tieStart"]),
                bool(note["tiedToNext"]),
            )
        )
    events.sort(key=lambda item: (item[0], item[4], item[2]))
    return _merge_tied_events(events)


def _pedal_segments(
    score: PianoScore, release_fraction: float = PEDAL_RELEASE_FRACTION
) -> list[tuple[float, float]]:
    """Where the sustain pedal goes down and comes up, in seconds.

    Without this the render is bone dry, and the user named it immediately: "很干
    不连续". The cause is that the MIDI contained nothing but note_on and note_off,
    so every note was a hard gate and the gap between two consecutive notes was
    true silence. A real piano keeps the strings ringing after the keys are
    released, which is what makes successive notes blur into a line instead of
    arriving as separate clicks.

    Block pedalling, one press per bar: down as the harmony arrives, up just before
    the next one so the old chord has cleared. Lifting *after* the next downbeat
    would smear the new chord into the old harmony, which sounds like a mistake
    rather than like pedalling.
    """
    segments: list[tuple[float, float]] = []
    beat_seconds = 60.0 / max(score.tempo_bpm, 1e-6)
    bar_seconds = score.beats_per_bar * beat_seconds
    for measure in score.measures:
        # The same offset note_timeline() uses, so the pedal lands on the barline
        # the ear hears rather than on a notated zero.
        start = score.phase_seconds + measure.index * bar_seconds
        # A hair before the barline, so the release is heard but the next chord
        # starts clean. Lifting after the downbeat would smear the new chord into
        # the old harmony, which sounds like a mistake rather than like pedalling.
        end = start + bar_seconds - PEDAL_RELEASE_SECONDS
        if release_fraction > 0.0:
            # Release a real slice of the bar early, so the previous harmony has
            # actually cleared by the time the next one arrives.
            end -= release_fraction * bar_seconds
        if end > start + 0.01:
            segments.append((start, end))
    return segments


def _midi_bytes(
    events: list[tuple[float, float, int, int]],
    tempo_bpm: float,
    pedal: Sequence[tuple[float, float]] = (),
) -> bytes:
    """Build the MIDI byte stream for the synth.

    Written directly rather than through mido because the synth is handed raw
    bytes on stdin and a temporary file is one more thing to clean up.
    """
    import mido

    midi = mido.MidiFile(ticks_per_beat=480)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("track_name", name="PianoFlow", time=0))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0))
    meta.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(meta)

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Piano", time=0))
    track.append(mido.Message("program_change", program=0, channel=0, time=0))

    ticks_per_second = 480 * (tempo_bpm / 60.0)

    # Ordering within one tick: pedal first, then note_on, then note_off. The pedal
    # has to be down *before* the note is struck or the attack is heard dry, and the
    # release has to be heard *before* the next note or the new chord is smeared into
    # the old one. note_off before note_on at the same tick re-articulates a
    # repeated pitch instead of cutting it short.
    messages: list[tuple[int, int, mido.Message]] = []
    for start_s, end_s in pedal:
        messages.append(
            (
                round(start_s * ticks_per_second),
                0,
                mido.Message("control_change", control=SUSTAIN_CC, value=PEDAL_DOWN, time=0),
            )
        )
        messages.append(
            (
                round(end_s * ticks_per_second),
                0,
                mido.Message("control_change", control=SUSTAIN_CC, value=PEDAL_UP, time=0),
            )
        )
    for onset, duration, pitch, velocity in events:
        start = round(onset * ticks_per_second)
        end = max(start + 1, round((onset + duration) * ticks_per_second))
        messages.append((start, 1, mido.Message("note_on", note=pitch, velocity=velocity, time=0)))
        messages.append((end, 2, mido.Message("note_off", note=pitch, velocity=0, time=0)))

    previous = 0
    for tick, _order, message in sorted(messages, key=lambda item: (item[0], item[1])):
        message.time = tick - previous
        previous = tick
        track.append(message)
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(track)

    import io

    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def _fluidsynth_binary(config: object) -> Path:
    exe = config.fluidsynth_exe()  # type: ignore[attr-defined]
    if exe is None:
        raise ValidationError(
            "fluidsynth is not installed, so the piano track cannot be rendered",
            details={"hint": "unpack the fluidsynth release into tools/fluidsynth"},
        )
    return exe


def _run_synth(
    midi_path: Path,
    wav_path: Path,
    *,
    config: object,
    gain: float = 1.0,
) -> None:
    """Point fluidsynth at a MIDI file and write a WAV.

    The SoundFont is a *positional* argument: ``-f`` means ``--load-config`` and
    will happily try to parse the .sf2 as a shell script, which renders silence.
    """
    exe = _fluidsynth_binary(config)
    soundfont = config.soundfont()  # type: ignore[attr-defined]
    if soundfont is None:
        raise ValidationError(
            "the SoundFont is missing, so the piano track cannot be rendered",
            details={"expected": str(config.soundfont_path)},  # type: ignore[attr-defined]
        )

    command = [
        str(exe),
        "-n",  # no MIDI input
        "-i",  # no audio device
        "-F",
        str(wav_path),
        "-r",
        str(SYNTH_RATE),
        "-g",
        f"{gain:.3f}",
        str(soundfont),
        # fluidsynth only reads a MIDI file named on the command line; there is no
        # "read the stream" mode, so stdin cannot carry it and a temporary file
        # has to be written.
        str(midi_path),
    ]
    # The DLLs sit beside the executable and are not on PATH.
    environment = {**os.environ, "PATH": f"{exe.parent};{os.environ.get('PATH', '')}"}

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        wav_path.unlink(missing_ok=True)
        raise ValidationError("the piano track could not be rendered in time") from exc
    except OSError as exc:
        raise ValidationError(f"fluidsynth could not be run: {exc}") from exc

    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        wav_path.unlink(missing_ok=True)
        raise ValidationError(
            "the piano track could not be rendered",
            details={"reason": _stderr_text(completed)},
        )


def _stderr_text(completed: subprocess.CompletedProcess[bytes]) -> str:
    """The synth's diagnostics as text, for the error detail."""
    stderr = completed.stderr
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", "replace")[:300]
    return (stderr or "")[:300]


def _normalise(y: np.ndarray, target_dbfs: float = TARGET_PEAK_DBFS) -> tuple[np.ndarray, float]:
    """Scale to a target peak and report the gain applied."""
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak <= 1e-9:
        return y, 1.0
    target = 10 ** (target_dbfs / 20.0)
    gain = target / peak
    return np.clip(y * gain, -1.0, 1.0), gain


def _resample(y: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return y
    import librosa

    return librosa.resample(y, orig_sr=source_rate, target_sr=target_rate, res_type="soxr_hq")


def _high_shelf(y: np.ndarray, sr: int, gain_db: float, freq: float, q: float) -> np.ndarray:
    """A high-shelf filter: flat below ``freq``, ``gain_db`` above it.

    The textbook audio-EQ cookbook coefficients (Robert Bristow-Johnson), bilinear
    transformed. Used rather than a low-pass because the mismatch is a *slope*, not a
    cut-off: the render's harmonics are uniformly too strong relative to the
    fundamental, so attenuating only the top would hollow out the middle instead of
    matching the recording.

    Returned un-normalised on purpose. Normalising would put the peak back where it
    was and hide the correction from the caller's own normalisation, which would then
    undo it.
    """
    from scipy import signal

    amplitude = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * freq / sr
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    two_sqrt_a_alpha = 2.0 * np.sqrt(amplitude) * alpha

    b0 = amplitude * ((amplitude + 1) + (amplitude - 1) * cos_w0 + two_sqrt_a_alpha)
    b1 = -2.0 * amplitude * ((amplitude - 1) + (amplitude + 1) * cos_w0)
    b2 = amplitude * ((amplitude + 1) + (amplitude - 1) * cos_w0 - two_sqrt_a_alpha)
    a0 = (amplitude + 1) - (amplitude - 1) * cos_w0 + two_sqrt_a_alpha
    a1 = 2.0 * ((amplitude - 1) - (amplitude + 1) * cos_w0)
    a2 = (amplitude + 1) - (amplitude - 1) * cos_w0 - two_sqrt_a_alpha

    return signal.lfilter([b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0], y).astype(
        np.float32
    )


def spectral_centroid(y: np.ndarray, sr: int) -> float:
    """Where the spectrum's weight sits, in Hz. Lower is darker.

    Reported for every render so a timbre change cannot pass unnoticed: this is the
    number that caught the SoundFont being 1.86x too bright.
    """
    if y.size < 2048:
        return 0.0
    n = min(y.size, sr * 4)
    spectrum = np.abs(np.fft.rfft(y[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    total = float(spectrum.sum())
    return float((freqs * spectrum).sum() / total) if total > 0 else 0.0


def render(
    score: PianoScore,
    wav_path: Path,
    *,
    config: object,
    gain: float = 1.0,
    normalise: bool = True,
    pedal: bool = True,
    tone_db_per_octave: float = TONE_TILT_DB_PER_OCTAVE,
    room: bool = True,
    source_audio: Path | None = None,
    expression: ExpressionProfile | None = None,
    texture_mode: bool = False,
) -> RenderResult:
    """Render the score to a mono WAV at the analysis sample rate.

    ``pedal`` adds the sustain pedal one press per bar. It is on by default because
    its absence is audible as a dry, disconnected line; it stays a parameter so the
    round-trip tests can compare the two, and so a user who wants the transcription
    played literally can ask for it.

    ``room`` convolves the render with an impulse response measured from
    ``source_audio``. Without it the track stops dead at the last note-off, which is the
    one thing a recording of a piano never does: the source's tail falls at -2.96 dB/s
    and the dry render has no tail at all. Without ``source_audio`` there is nothing to
    measure a room from, so the render stays dry and says so, rather than inventing one.

    ``texture_mode`` uses a drier mix (less room, less pedal) so the rhythmic
    differences between textures are audible. The default room and pedal blur them
    together, making eight textures sound identical.
    """
    events = note_events(score, profile=expression)
    if not events:
        raise ValidationError(
            "the score has no notes, so there is nothing to play",
            details={"measureCount": len(score.measures)},
        )

    target = Path(wav_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # with_suffix() replaces the last part, so "song.piano.wav" would become
    # "song.piano.mid" and collide across render kinds. Stems are explicit.
    midi = target.with_name(f"{target.stem}.render.mid")
    raw = target.with_name(f"{target.stem}.raw.wav")
    pedal_fraction = TEXTURE_PEDAL_RELEASE_FRACTION if texture_mode else PEDAL_RELEASE_FRACTION
    segments = _pedal_segments(score, release_fraction=pedal_fraction) if pedal else []
    midi.write_bytes(_midi_bytes(events, score.tempo_bpm, segments))
    raw.unlink(missing_ok=True)

    try:
        _run_synth(midi, raw, config=config, gain=gain)
    finally:
        # The intermediate MIDI is scaffolding; it must not survive a failure.
        midi.unlink(missing_ok=True)

    import soundfile as sf

    audio, source_rate = sf.read(str(raw), dtype="float32", always_2d=False)
    raw.unlink(missing_ok=True)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    mono = _resample(mono, source_rate, SAMPLE_RATE)

    # Darken to match the instrument, then normalise. The order matters: normalising
    # first and filtering after would leave the peak below target, since the filter
    # removes energy.
    before = spectral_centroid(mono, SAMPLE_RATE)
    if tone_db_per_octave != 0.0:
        # Applied twice, because -3 dB/octave over the 40 dB that separate a note's
        # fundamental from its tenth partial adds up to a lot at the top.
        mono = _high_shelf(mono, SAMPLE_RATE, tone_db_per_octave, TONE_FREQ, TONE_Q)
        mono = _high_shelf(mono, SAMPLE_RATE, tone_db_per_octave, TONE_FREQ, TONE_Q)
    after = spectral_centroid(mono, SAMPLE_RATE)

    # The room, applied before normalisation so the caller's peak target still means
    # something after the convolution has summed the whole tail in.
    room_report: dict[str, object] = {"applied": False}
    music_end = max(onset + duration for onset, duration, _midi, _vel in events)
    if room and source_audio is not None:
        source, source_rate = _load_source(source_audio)
        measurement = measure_release_decay(source, source_rate)
        if source_rate != SAMPLE_RATE:
            import librosa

            source = librosa.resample(
                source, orig_sr=source_rate, target_sr=SAMPLE_RATE, res_type="soxr_hq"
            )
        wet_db = TEXTURE_ROOM_WET_DB if texture_mode else ROOM_WET_DB
        impulse = synthesise_impulse_response(measurement, SAMPLE_RATE, wet_db=wet_db)
        if impulse.size > 1:
            # Measured on the dry signal, so the two numbers describe the same thing:
            # what the instrument alone did, and what the instrument plus room did.
            dry_decay = _decay_of(mono, music_end)
            source_release = _source_release(source, SAMPLE_RATE)
            mono = apply_room(mono, impulse)
            after = spectral_centroid(mono, SAMPLE_RATE)
            room_report = {
                "applied": True,
                "wetDb": wet_db,
                "sourceDecayDbPerSecond": round(measurement.decay_db_per_second, 3),
                "sourceT20Seconds": round(measurement.t20_seconds, 3),
                "ambienceDbfs": round(measurement.ambience_dbfs, 1),
                "impulseSeconds": round(impulse.size / SAMPLE_RATE, 3),
                "musicEndsSeconds": round(music_end, 3),
                # Whether the two decays can be compared at all. The source's decay is
                # measured from the recording's own ending, while a preview render ends
                # wherever the preview was cut - so the render's "final release" is an
                # artificial fade, not a performance. Comparing the two would report a
                # number that looks like fidelity and is not: measured on a 30 s preview
                # the two came out 21 % apart, which is inside the bound by coincidence.
                "comparableToSource": music_end >= 0.5 * len(source) / SAMPLE_RATE,
                "dryDecayDbPerSecond": _rounded(dry_decay),
                "renderDecayDbPerSecond": _rounded(_decay_of(mono, music_end)),
                # The two criteria that disagree about the wet level, both reported so
                # the trade is visible. `tailOverDirectDb` is the one that is not
                # circular; the decay rate is the one the ear complained about.
                "sourceTailOverDirectDb": _rounded(
                    tail_energy_ratio(source, SAMPLE_RATE, source_release)
                    if source_release is not None
                    else None
                ),
                "renderTailOverDirectDb": _rounded(tail_energy_ratio(mono, SAMPLE_RATE, music_end)),
                "bands": [
                    {
                        "lowHz": b.low_hz,
                        "highHz": b.high_hz,
                        "dbPerSecond": round(b.db_per_second, 3),
                    }
                    for b in measurement.bands
                ],
            }
        else:
            room_report = {"applied": False, "reason": "no usable decay in the source"}

    applied = 1.0
    if normalise:
        mono, applied = _normalise(mono)
        logger.info("piano track normalised, gain x%.2f", applied)

    sf.write(str(target), mono, SAMPLE_RATE, subtype="PCM_16")
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    duration = len(mono) / SAMPLE_RATE if mono.size else 0.0
    logger.info(
        "piano track: %d notes, %.2fs, peak %.4f (gain x%.2f, pedal %s, room %s), "
        "brightness %.0f -> %.0f Hz, wrote %s",
        len(events),
        duration,
        peak,
        applied,
        "on" if pedal else "off",
        room_report.get("applied"),
        before,
        after,
        target,
    )
    return RenderResult(
        path=target,
        sample_rate=SAMPLE_RATE,
        duration_seconds=duration,
        peak=peak,
        events=tuple(events),
        room=room_report,
    )


def _load_source(path: Path) -> tuple[np.ndarray, int]:
    """Read the recording whose decay is to be matched."""
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32", always_2d=False)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    return mono, int(rate)


def _source_release(source: np.ndarray, sr: int) -> float | None:
    """Where the recording itself stops sounding, for the energy ratio to be anchored to.

    Measured with the same detector the decay uses, so both ends of the ratio come from
    one definition rather than from two that could disagree.
    """
    from services.audio_analysis.room import _find_release, _frame_db

    times, db = _frame_db(source, sr)
    if times.size == 0:
        return None
    return float(times[_find_release(times, db)])


def _decay_of(y: np.ndarray, music_end: float) -> float | None:
    """How fast the track falls once the last note is off.

    The floor is relative to this signal's own peak rather than absolute. The track gets
    normalised, so a fixed dBFS threshold would make the measurement a function of the
    output level instead of the tail, and a dry render - whose tail is digital silence -
    would report a slope fitted through numerical noise instead of admitting there is
    nothing to fit.
    """
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak <= 1e-9:
        return None
    peak_db = 20.0 * np.log10(peak)
    return decay_slope_after(
        y, SAMPLE_RATE, anchor_seconds=music_end, floor_db=peak_db - DECAY_FLOOR_DB
    )


def _rounded(value: float | None) -> float | None:
    """Round an optional measurement, keeping ``None`` as ``None``."""
    return None if value is None else round(value, 3)


def mix_tracks(
    primary: np.ndarray,
    secondary: np.ndarray | None,
    *,
    secondary_gain: float = 1.0,
) -> np.ndarray:
    """Sum a vocal track under a piano track, length-aligned and limited.

    The vocal stem comes from the original recording while the piano is
    synthesised, so the two have different lengths and the vocal is placed by its
    own first significant sample rather than assumed to start at zero.
    """
    if secondary is None:
        return primary
    length = max(len(primary), len(secondary))
    base = np.zeros(length, dtype=np.float32)
    base[: len(primary)] += primary
    if secondary_gain <= 0:
        return base
    start = _first_significant(secondary)
    if start is None:
        return base
    # The leading silence of the stem is discarded so its first real sound lands
    # on the piano, rather than starting the stem at its own offset and adding a
    # block of silence on top of the music.
    content = secondary[start:]
    usable = min(len(content), length)
    base[:usable] += content[:usable] * secondary_gain
    peak = float(np.max(np.abs(base)))
    if peak > 1.0:
        base = base / peak
    return base


def _first_significant(y: np.ndarray, threshold: float = 1e-3) -> int | None:
    """Index of the first sample above the noise floor."""
    if y.size == 0:
        return None
    loud = np.flatnonzero(np.abs(y) >= threshold * float(np.max(np.abs(y)) + 1e-12))
    if loud.size == 0:
        return None
    return int(loud[0])


__all__ = [
    "RENDER_TIMEOUT_SECONDS",
    "SAMPLE_RATE",
    "SYNTH_RATE",
    "TARGET_PEAK_DBFS",
    "RenderResult",
    "mix_tracks",
    "note_events",
    "render",
]
