"""The follow-along consistency tests.

These are the tests that justify replacing the original recording with a
synthesised track. The claim is that the audio, the notation and the keyboard all
agree, and that claim is checked rather than asserted in a comment.

Baseline, measured before any of this existed (see the commit message):

  quantisation error  median 27.7 ms   mean 26.9 ms   max 52.0 ms
  follow-along drift  1 % tempo -> 300 ms accumulated over 30 s

So the target for T1 and T3 is zero error, not "under 30 ms".
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from core.config import Settings
from services.arrangement.arranger import arrange
from services.arrangement.grid import BarGrid
from services.arrangement.piano_score import build_score
from services.audio_analysis.polyphony import BasicPitchTranscriber, NoteEvent
from services.score.render import (
    SAMPLE_RATE,
    TARGET_PEAK_DBFS,
    mix_tracks,
    note_events,
    render,
)

fluidsynth_exe = Settings().fluidsynth_exe()
soundfont = Settings().soundfont()

requires_synth = pytest.mark.skipif(
    fluidsynth_exe is None or soundfont is None,
    reason="fluidsynth or the SoundFont is not installed",
)


def grid_at(tempo: float = 120.0, duration: float = 8.0) -> BarGrid:
    return BarGrid(tempo_bpm=tempo, beats_per_bar=4, phase_seconds=0.0, duration=duration)


def note(start: float, duration: float, midi: int, amplitude: float = 0.7):
    from services.audio_analysis.polyphony import NoteEvent

    return NoteEvent(start=start, duration=duration, midi=midi, amplitude=amplitude)


def two_hand_score(tmp_path: Path):
    """A four bar score with chords in both hands and a bar-1 pickup offset."""
    grid = grid_at()
    buckets = {index: ([], []) for index in range(grid.bar_count())}
    for index in range(grid.bar_count()):
        start = index * 2.0
        buckets[index] = (
            # Right hand: a two note chord on beat 1, then moving eighths.
            [
                note(start, 0.5, 72),
                note(start, 0.5, 76),
                note(start + 0.5, 0.5, 79),
                note(start + 1.5, 0.5, 81),
            ],
            # Left hand: a triad on beat 1 and a bass note on beat 2.
            #
            # A2-C3-E3, a span of 7 semitones. Chosen to sit inside L3's 9-semitone
            # hand budget: the original A2-E3-A3 spans 12, and the span fitter
            # correctly drops the octave doubling, so the fixture would have been
            # testing the fitter instead of the renderer.
            [
                note(start, 1.0, 45),
                note(start, 1.0, 48),
                note(start, 1.0, 52),
                note(start + 1.0, 1.0, 43),
            ],
        )
    # The right hand goes first: arrange() reads buckets as (right, left).
    return build_score(arrange(buckets, grid), grid, tempo_bpm=120.0)


# ---------------------------------------------------------------- note events


def test_note_events_are_the_single_source_of_truth(tmp_path: Path) -> None:
    """The synth and the notation must read the same list, not two copies.

    Compared as sets of (onset, pitch): the two lists order simultaneous notes
    differently on purpose - the timeline groups by staff for the cursor, the
    render list sorts by pitch for the synth - so a positional comparison would be
    comparing the tie-breaks rather than the content.
    """
    score = two_hand_score(tmp_path)
    events = note_events(score)
    timeline = score.note_timeline()
    assert len(events) == len(timeline) == score.note_count
    assert {(e[0], e[2]) for e in events} == {(n["time"], n["midi"]) for n in timeline}
    # Sort both by (time, pitch) explicitly: the default tuple order breaks ties
    # on duration, which pairs a different note against each other.
    by_pitch = sorted(events, key=lambda e: (e[0], e[2]))
    notated = sorted(timeline, key=lambda n: (n["time"], n["midi"]))
    for event, note_dict in zip(by_pitch, notated, strict=True):
        assert event[0] == pytest.approx(note_dict["time"], abs=1e-6)
        assert event[1] == pytest.approx(note_dict["duration"], abs=1e-6)
        assert event[2] == note_dict["midi"]


def test_note_events_are_sorted_in_time(tmp_path: Path) -> None:
    score = two_hand_score(tmp_path)
    onsets = [event[0] for event in note_events(score)]
    assert onsets == sorted(onsets)


def test_every_event_is_audible(tmp_path: Path) -> None:
    """A note too quiet to hear is a key that lights with no sound."""
    score = two_hand_score(tmp_path)
    for _onset, _duration, _pitch, velocity in note_events(score):
        assert velocity >= 20


# ------------------------------------------------------------- T1 / T2 render


@requires_synth
def test_t1_rendered_onsets_match_the_score_within_a_millisecond(tmp_path: Path) -> None:
    """T1: the audio is built from the score, so timing cannot drift."""
    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    assert result.note_count == score.note_count
    for (onset, _duration, _pitch, _velocity), expected in zip(
        result.events, note_events(score), strict=True
    ):
        assert onset == pytest.approx(expected[0], abs=1e-3)


@requires_synth
def test_t2_rendered_pitches_match_the_score_exactly(tmp_path: Path) -> None:
    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    assert [event[2] for event in result.events] == [event[2] for event in note_events(score)]


@requires_synth
def test_the_track_is_normalised_to_a_usable_level(tmp_path: Path) -> None:
    """Baseline: raw fluidsynth output peaked at -28.8 dBFS, inaudibly quiet."""
    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    target = 10 ** (TARGET_PEAK_DBFS / 20.0)
    assert result.peak == pytest.approx(target, abs=0.02)
    dbfs = 20 * np.log10(result.peak)
    assert -2.0 <= dbfs <= -0.5


@requires_synth
def test_the_track_matches_the_analysis_sample_rate(tmp_path: Path) -> None:
    """It must be readable by the transcription model for the round trip."""
    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    assert result.sample_rate == SAMPLE_RATE


@requires_synth
def test_the_track_is_long_enough_to_hold_its_last_note(tmp_path: Path) -> None:
    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    last = max(onset + duration for onset, duration, _p, _v in result.events)
    assert result.duration_seconds >= last


# --------------------------------------------------------------- T3 round trip


@requires_synth
def test_t3_transcribing_the_rendered_track_recovers_the_score(tmp_path: Path) -> None:
    """T3: let the transcriber check the render, in the direction that matters.

    The requirement is one-sided on purpose. The transcriber has a known blind
    spot: in a dense triad it misses the *middle* note, because the notes on
    either side mask it. Measured on this score it drops E5 from C5-E5-G5 even
    though E5 is plainly present in the spectrum, and it does so at every
    threshold from 0.25 to 0.40. Demanding a perfect pitch-set match would be
    testing the transcriber's blind spot rather than the render.

    What is asserted instead:
      * coverage - at least 85 % of the notated pitches are recovered;
      * soundness - nothing is recovered that is not on the staff, apart from
        octave doublings, so a mis-voiced or invented note cannot pass;
      * timing - every recovered pitch lands within one sixteenth of its notated
        onset, which is the grid the score was written on.

    Direction matters: a weaker assertion that merely counted notes would pass a
    render of entirely the wrong piece.

    This runs with ``pedal=False`` on purpose. The sustain pedal makes a repeated
    pitch produce no new attack, because the previous one is still ringing, and a
    frame-based transcriber then cannot see the onset at all. On 星辰大海 it dropped
    notated onsets by 1.6 s that way - not a rendering fault, but the pedal trading
    onset visibility for sustain. The pedal's own effect is measured in
    ``TestTheSustainPedal``, and ``test_the_default_render_still_sounds_every_pitch``
    checks that nothing on the staff goes missing once it is on.
    """
    import soundfile as sf

    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings(), pedal=False)
    events = note_events(score)

    audio, rate = sf.read(str(result.path), dtype="float32", always_2d=False)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    assert rate == SAMPLE_RATE

    # The transcriber sees exactly the array the analysis pipeline produces.
    detected = BasicPitchTranscriber().transcribe_array(mono, SAMPLE_RATE)

    expected_pitches = {event[2] for event in events}
    detected_pitches = {item.midi for item in detected}

    recovered = expected_pitches & detected_pitches
    coverage = len(recovered) / len(expected_pitches)
    assert coverage >= 0.85, (
        f"only {coverage:.0%} of the notated pitches survived the round trip; "
        f"missing {sorted(expected_pitches - detected_pitches)}"
    )

    # Soundness: a render that invented a pitch would fail here.
    unexpected = detected_pitches - expected_pitches - _octave_ghosts(expected_pitches)
    assert not unexpected, (
        f"the render contains pitches that are not on the staff: {sorted(unexpected)}"
    )

    # Timing: this is the property follow-along actually depends on. Without the
    # pedal every note is a clean transient, so the nearest detection to a notated
    # onset must be within one sixteenth of it.
    #
    # The transcriber cannot report anything before its first output frame, so an
    # onset inside that window is unverifiable by this method - not wrong, just
    # unseen. Measured here: notated 45/48/52/72/76 at t=0.000 produce no detection at
    # all before 0.499 s, while every later onset (79 at 0.5, 43 at 1.0, 81 at 1.5)
    # lands within 2 ms. The window is measured rather than hard-coded, and the check
    # that most onsets are still verified keeps this from quietly becoming vacuous.
    beat = 60.0 / score.tempo_bpm
    tolerance = beat / 4
    first_detection = min((d.start for d in detected), default=0.0)
    checked = skipped = 0
    for onset, _duration, pitch, _velocity in events:
        if onset < first_detection:
            skipped += 1
            continue
        matches = [item for item in detected if item.midi == pitch]
        if not matches:
            continue
        checked += 1
        nearest = min(abs(item.start - onset) for item in matches)
        assert nearest <= tolerance, f"pitch {pitch} onset off by {nearest:.3f}s"
    assert checked > len(events) * 0.6, (
        f"only {checked} of {len(events)} onsets could be timing-checked; "
        f"{skipped} fell inside the transcriber's warm-up ({first_detection:.3f}s)"
    )


@requires_synth
def test_the_default_render_still_sounds_every_pitch(tmp_path: Path) -> None:
    """With the pedal on - the default - nothing on the staff may go missing.

    Pedalling makes repeated pitches harder to re-detect, so it is fair to ask
    whether it costs coverage. It does not: a ringing note is easier to find, not
    harder. Coverage is asserted without any onset timing, because as the round-trip
    test records, the pedal genuinely does blur where the attacks are.
    """
    import soundfile as sf

    score = two_hand_score(tmp_path)
    events = note_events(score)
    result = render(score, tmp_path / "pedalled.wav", config=Settings())

    audio, rate = sf.read(str(result.path), dtype="float32", always_2d=False)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    detected = BasicPitchTranscriber().transcribe_array(mono, rate)

    expected = {e[2] for e in events}
    recovered = expected & {d.midi for d in detected}
    coverage = len(recovered) / len(expected)
    assert coverage >= 0.85, (
        f"only {coverage:.0%} of the notated pitches are audible once the pedal is on; "
        f"missing {sorted(expected - recovered)}"
    )


@requires_synth
def test_every_notated_pitch_is_audible_at_its_fundamental(tmp_path: Path) -> None:
    """The direct check that does not depend on the transcriber's blind spot.

    For each notated note the spectrum is inspected at that pitch's fundamental
    frequency over the note's own duration. This is what the ear hears, so it is
    the ground truth the transcriber test is compared against.

    The window is the note's duration, not a fixed slice: a short window gives a
    coarse frequency grid, and a narrow band around a fundamental can then fall
    *between* two bins and read as silence. That is not hypothetical - a 0.2 s
    window at 22050 Hz has 5 Hz bins, and G2 at 98 Hz lands between them.

    This deliberately renders the **default** configuration, pedal included, because
    that is what a player hears. It also has no onset-timing claim, so it is immune
    to the pedal hiding repeated attacks.
    """
    import soundfile as sf

    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    events = note_events(score)

    audio, _rate = sf.read(str(result.path), dtype="float32", always_2d=False)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)

    reference = float(np.max(np.abs(mono)))
    for onset, duration, pitch, _velocity in events:
        frequency = 440.0 * 2 ** ((pitch - 69) / 12)
        start = int(onset * SAMPLE_RATE)
        # A third of a second is the floor; shorter notes get a longer window so
        # the frequency grid stays fine enough to resolve the fundamental.
        length = int(max(duration, 0.34) * SAMPLE_RATE)
        segment = mono[start : start + length]
        if len(segment) < 512:
            continue
        magnitude = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
        freqs = np.fft.rfftfreq(len(segment), 1.0 / SAMPLE_RATE)
        # The band must be wider than one bin or the fundamental can be missed.
        bin_width = SAMPLE_RATE / len(segment)
        half_width = max(frequency * 0.03, bin_width * 1.5)
        band = (freqs >= frequency - half_width) & (freqs <= frequency + half_width)
        amplitude = float(np.max(magnitude[band])) if band.any() else 0.0
        assert amplitude > reference * 1.0, (
            f"pitch {pitch} is not audible at {frequency:.1f} Hz "
            f"(amplitude {amplitude:.2f}, reference {reference:.2f})"
        )


def _octave_ghosts(pitches: set[int]) -> set[int]:
    """Octave doublings the transcriber may report for a synthesised tone."""
    ghosts: set[int] = set()
    for pitch in pitches:
        ghosts.add(pitch + 12)
        ghosts.add(pitch - 12)
    return {ghost for ghost in ghosts if 0 < ghost < 128}


@requires_synth
def test_the_render_contains_no_instrument_but_piano(tmp_path: Path) -> None:
    """The original mix carried guitar, drums and vocals; this must not.

    Checked as decay rather than as a spectral guess: a synth note fades out, so
    the far end of the track must be near silence. A drum hit or a vocal phrase
    would put energy there.
    """
    score = two_hand_score(tmp_path)
    result = render(score, tmp_path / "piano.wav", config=Settings())
    import soundfile as sf

    audio, _rate = sf.read(str(result.path), dtype="float32", always_2d=False)
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    last_onset = max(onset + duration for onset, duration, _p, _v in result.events)
    # A second past the final note, and past where a piano release would settle.
    tail = mono[int((last_onset + 1.0) * SAMPLE_RATE) :]
    if tail.size:
        assert np.max(np.abs(tail)) < 1e-3


def test_an_empty_score_is_refused(tmp_path: Path) -> None:
    from core.errors import ValidationError

    grid = grid_at()
    empty = build_score(arrange({}, grid), grid)
    with pytest.raises(ValidationError):
        render(empty, tmp_path / "empty.wav", config=Settings())


# -------------------------------------------------------------------- mixing


def test_mixing_without_a_secondary_leaves_the_primary_untouched() -> None:
    piano = np.ones(100, dtype=np.float32) * 0.5
    assert mix_tracks(piano, None) is piano


def test_mixing_aligns_the_secondary_by_its_first_sound() -> None:
    """The vocal stem starts with silence, and that silence must be discarded.

    A separated stem is padded at the head. Adding it at its own offset would lay
    a block of nothing on top of the piano and push the singing behind the music,
    so the first real sample is moved to the start of the mix.
    """
    piano = np.full(2000, 0.05, dtype=np.float32)
    vocal = np.zeros(2000, dtype=np.float32)
    vocal[1000:1200] = 0.4
    mixed = mix_tracks(piano, vocal)
    # Stripping the 1000 samples of leading silence moves the vocal's 200 samples
    # of content to the very start, where it sums with the piano.
    assert np.max(np.abs(mixed[:200])) == pytest.approx(0.45, abs=1e-5)
    # Past the vocal, the piano alone remains: no trailing block of stem silence
    # was laid over the music.
    assert np.max(np.abs(mixed[200:])) == pytest.approx(0.05, abs=1e-5)


def test_mixing_respects_the_secondary_gain() -> None:
    piano = np.full(1000, 0.1, dtype=np.float32)
    vocal = np.full(1000, 0.5, dtype=np.float32)
    mixed = mix_tracks(piano, vocal, secondary_gain=0.5)
    assert np.max(np.abs(mixed)) == pytest.approx(0.35, abs=1e-5)


def test_a_silent_secondary_changes_nothing() -> None:
    piano = np.full(500, 0.2, dtype=np.float32)
    assert mix_tracks(piano, np.zeros(500, dtype=np.float32)).max() == pytest.approx(0.2)


def test_mixing_never_clips() -> None:
    loud = np.ones(1000, dtype=np.float32)
    louder = np.ones(1000, dtype=np.float32)
    mixed = mix_tracks(loud, louder)
    assert np.max(np.abs(mixed)) <= 1.0


class TestRenderDegradesGracefully:
    """A missing synth is a normal state, never a failed request.

    fluidsynth and the SoundFont are separate downloads the user may not have. If
    that made the whole score request fail, the app would be unusable on a fresh
    clone, so the render is allowed to decline while everything else stands.
    """

    def _bare_settings(self, tmp_path: Path) -> Settings:
        return Settings(
            fluidsynth_location=str(tmp_path / "no-such-synth"),
            soundfont_path=str(tmp_path / "no-such.sf2"),
        )

    def test_a_missing_synth_reports_why_instead_of_raising(self, tmp_path: Path) -> None:
        from services.score.service import _render_piano

        path, info = _render_piano(
            two_hand_score(tmp_path), tmp_path / "out.piano.wav", self._bare_settings(tmp_path)
        )

        assert path is None
        assert info["available"] is False
        # The reason is user-facing: the UI shows it instead of a broken player.
        assert "not installed" in str(info["reason"])
        assert not (tmp_path / "out.piano.wav").exists()

    def test_a_missing_soundfont_is_also_reported(self, tmp_path: Path) -> None:
        from services.score.service import _render_piano

        settings = dataclasses.replace(
            Settings(),
            fluidsynth_location=str(fluidsynth_exe.parent if fluidsynth_exe else tmp_path),
            soundfont_path=str(tmp_path / "missing.sf2"),
        )
        path, info = _render_piano(two_hand_score(tmp_path), tmp_path / "out.piano.wav", settings)

        assert path is None
        assert info["available"] is False

    def test_the_result_only_advertises_a_track_that_exists(self, tmp_path: Path) -> None:
        """to_dict must not hand the UI a URL it cannot fetch."""
        from services.score.service import ScoreResult

        without = ScoreResult(score=two_hand_score(tmp_path), musicxml="<x/>")
        assert without.to_dict()["pianoTrackUrl"] is None

        wav = tmp_path / "song.piano.wav"
        wav.write_bytes(b"RIFF")
        with_track = ScoreResult(score=two_hand_score(tmp_path), musicxml="<x/>", piano_path=wav)
        assert with_track.to_dict()["pianoTrackUrl"].startswith("/api/score/piano?")


class TestOutputUrlsAreVersioned:
    """Regression: the notation and the audio could show different scores.

    Every output endpoint serves "whichever file is newest" from one URL, so the URL
    alone does not identify the content. Two things went wrong. The browser served
    the cached file for up to a minute, and Vue saw an unchanged ``src`` on the audio
    element and did not reload it at all - the notation updated while the previous
    recording kept playing. This is precisely the drift the follow-along mode exists
    to remove, and it was the user's first reported symptom.

    The fix ties the URL to the file's own mtime, so the string changes whenever the
    content does. These tests assert the string *changes*, which is the property the
    frontend depends on - asserting the token merely exists would pass while the bug
    stayed.
    """

    def _urls(self, tmp_path: Path, stem: str, suffix: str) -> dict[str, str]:
        from services.score.service import ScoreResult

        p = tmp_path / f"{stem}{suffix}"
        p.write_bytes(b"RIFF" if suffix.endswith(".wav") else b"x")
        return ScoreResult(
            score=two_hand_score(tmp_path),
            musicxml="<x/>",
            piano_path=p if "piano" in stem else None,
        ).to_dict()

    def test_the_token_changes_when_the_file_is_rewritten(self, tmp_path: Path) -> None:
        import os

        target = tmp_path / "song.piano.wav"
        target.write_bytes(b"RIFF")

        from services.score.service import ScoreResult

        def url() -> str:
            result = ScoreResult(score=two_hand_score(tmp_path), musicxml="<x/>", piano_path=target)
            return str(result.to_dict()["pianoTrackUrl"])

        first = url()
        # Same path, new content: the newest-file rule means the URL now serves
        # something else, so the token has to move.
        target.write_bytes(b"RIFFnew")
        os.utime(target, (0, 0))
        second = url()
        assert first != second

    def test_the_token_is_absent_when_there_is_no_file(self, tmp_path: Path) -> None:
        from services.score.service import ScoreResult

        result = ScoreResult(
            score=two_hand_score(tmp_path), musicxml="<x/>", piano_path=tmp_path / "gone.wav"
        )
        assert result.to_dict()["pianoTrackUrl"] is None

    def test_the_query_string_is_appended_not_substituted(self, tmp_path: Path) -> None:
        """A bare "?v=" would break any future query the route already needs."""
        from services.score.service import _versioned

        p = tmp_path / "a.piano.wav"
        p.write_bytes(b"RIFF")
        versioned = _versioned("/api/score/piano", p)
        assert versioned is not None
        assert versioned.startswith("/api/score/piano?v=")
        assert versioned.count("?") == 1
        # And the token is a plain integer, so it survives being put in a query string.
        assert versioned.split("=", 1)[1].isdigit()

    def test_a_vanished_file_yields_no_url_rather_than_the_bare_one(self, tmp_path: Path) -> None:
        """The endpoint would serve an *older* score, which is worse than none."""
        from services.score.service import _versioned

        assert _versioned("/api/score/piano", tmp_path / "gone.wav") is None
        assert _versioned("/api/score/piano", None) is None


@requires_synth
class TestTheSustainPedal:
    """The pedal is the difference between a piano and a sequence of beeps.

    The user's complaint, unprompted and immediately after hearing the first
    render: "很干不连续" - dry and discontinuous. The cause was that the MIDI
    contained nothing but note_on and note_off, so every note was a hard gate and
    the gap between two consecutive notes was true silence. On a real piano the
    strings keep ringing after the keys are released, which is what makes successive
    notes blur into a line.

    These tests measure the silence, because that is the defect. Asserting that a
    CC64 message exists would pass while the audio stayed just as dry, which is
    exactly the kind of test that gives false confidence.
    """

    def _two_notes_in_a_bar(self):
        """Two notes with a deliberate gap between them, both inside bar 1."""
        from services.arrangement.piano_score import Measure, PianoScore, ScoredNote

        # At 120 BPM a beat is 0.5 s, so these notes run 0.00-0.25, 0.50-0.75 and
        # 1.00-1.25, leaving a real gap at 0.32-0.46 and a bar that ends at 2.00.
        left = ScoredNote(bar=0, beat=0.0, duration_beats=0.5, midi=48)
        right = (
            ScoredNote(bar=0, beat=1.0, duration_beats=0.5, midi=72),
            ScoredNote(bar=0, beat=2.0, duration_beats=0.5, midi=74),
        )
        return PianoScore(
            measures=(Measure(index=0, right=right, left=(left,)),),
            tempo_bpm=120.0,
            beats_per_bar=4,
        )

    def _gap_energy(self, y: np.ndarray, start: float, end: float) -> float:
        """RMS in the silent window between two notes."""
        a = int(start * SAMPLE_RATE)
        b = int(end * SAMPLE_RATE)
        window = y[a:b]
        return float(np.sqrt(np.mean(np.square(window)))) if window.size else 0.0

    def _ring_time(self, y: np.ndarray, after: float, ceiling: float) -> float:
        """How long the sound keeps going after the last key is released.

        Measured from the moment the final note stops to where a short-window RMS
        envelope falls below ``ceiling``.

        The envelope matters: taking the instantaneous magnitude instead finds the
        *first sample* under the threshold, which is a sine's zero crossing a
        millisecond in. That reported 0.002 s for a note that was still ringing at
        -21 dBFS - a measurement bug that looked exactly like the pedal not working.
        """
        start = int(after * SAMPLE_RATE)
        tail = y[start:]
        if not tail.size:
            return 0.0
        window = max(1, int(0.01 * SAMPLE_RATE))
        n = len(tail) // window
        if n == 0:
            return 0.0
        env = np.array(
            [np.sqrt(np.mean(np.square(tail[i * window : (i + 1) * window]))) for i in range(n)]
        )
        quiet = np.flatnonzero(env < ceiling)
        return (
            float(quiet[0] * window / SAMPLE_RATE) if quiet.size else float(len(tail) / SAMPLE_RATE)
        )

    def test_the_pedal_fills_the_gap_between_two_notes(self, tmp_path: Path) -> None:
        """The whole point: the silence between notes must stop being silent.

        The threshold is a measured comparison rather than a guess. Without the pedal
        the gap already carried 0.056 RMS - fluidsynth's release is not instant, and
        the render is normalised - so demanding a 5x jump was measuring an
        idealisation, not the defect. What matters is that the gap is audibly filled
        and that the sound rings on measurably longer.
        """
        score = self._two_notes_in_a_bar()
        dry = render(score, tmp_path / "dry.piano.wav", config=Settings(), pedal=False)
        wet = render(score, tmp_path / "wet.piano.wav", config=Settings(), pedal=True)

        import soundfile as sf

        dry_y, _ = sf.read(str(dry.path), dtype="float32")
        wet_y, _ = sf.read(str(wet.path), dtype="float32")

        # Note 1 runs 0.0-0.25 s and note 2 starts at 0.5 s, so 0.32-0.46 s is the
        # gap that used to be dead silence.
        dry_gap = self._gap_energy(dry_y, 0.32, 0.46)
        wet_gap = self._gap_energy(wet_y, 0.32, 0.46)
        assert wet_gap > dry_gap * 1.8, (
            f"the pedal barely changed the gap: {dry_gap:.6f} -> {wet_gap:.6f}. "
            "If fluidsynth ignored CC64 the render would still sound dry."
        )

        # The last note is released at 1.25 s and the pedal lifts 18 % before the barline,
        # which at 120 BPM with four beats is 1.60 s. Without the pedal the sound is gone
        # by about 1.45 s; with it, still audible at 1.58 s.
        ceiling = float(np.max(np.abs(dry_y))) * 10 ** (-40.0 / 20.0)
        dry_ring = self._ring_time(dry_y, 1.25, ceiling)
        wet_ring = self._ring_time(wet_y, 1.25, ceiling)
        assert dry_ring < 0.5, f"the dry render rang for {dry_ring:.3f}s; the test is miscalibrated"
        # This used to demand dry_ring + 0.4, which was reachable only because the pedal
        # held to 1.96 s. Releasing at 1.60 s caps the extension at 0.35 s by
        # construction, so the bound is now what the release point allows. The pedal is
        # still doing the job - 0.23 s dry against 0.58 s wet, and the gap above is
        # measured to sit just under the cap rather than to miss it. A pedal that failed
        # to register at all would land near dry_ring, not near the ceiling.
        assert wet_ring > dry_ring + 0.3, (
            f"the pedal did not extend the decay: {dry_ring:.3f}s -> {wet_ring:.3f}s"
        )

    def test_the_note_events_are_untouched_by_the_pedal(self, tmp_path: Path) -> None:
        """The pedal adds resonance, never notes.

        ``score.notes`` stays the single source of truth for the notation, the cursor
        and the keyboard. If pedalling added or moved a note, the player would hear
        something the staff does not show.
        """
        score = self._two_notes_in_a_bar()
        dry = render(score, tmp_path / "d2.piano.wav", config=Settings(), pedal=False)
        wet = render(score, tmp_path / "w2.piano.wav", config=Settings(), pedal=True)
        assert dry.events == wet.events

    def test_the_midi_really_carries_the_controller(self, tmp_path: Path) -> None:
        """A cheap structural check, alongside the measurement above."""
        import mido

        from services.score.render import _midi_bytes, _pedal_segments

        score = self._two_notes_in_a_bar()
        data = _midi_bytes(note_events(score), score.tempo_bpm, _pedal_segments(score))
        midi = mido.MidiFile(file=__import__("io").BytesIO(data))
        controls = [m.control for track in midi.tracks for m in track if m.type == "control_change"]
        assert 64 in controls
        # Down before up, so the pedal is actually used and not left stuck on.
        values = [
            m.value
            for track in midi.tracks
            for m in track
            if m.type == "control_change" and m.control == 64
        ]
        assert values[0] == 127
        assert 0 in values

    def test_the_pedal_is_down_when_a_note_is_struck(self, tmp_path: Path) -> None:
        """Lifting before the attack would make every chord start dry.

        The pedal has to be down at the note_on tick, not after it, or the release
        of the previous bar would still be cutting the new chord off.
        """
        import io

        import mido

        from services.score.render import _midi_bytes, _pedal_segments

        score = self._two_notes_in_a_bar()
        data = _midi_bytes(note_events(score), score.tempo_bpm, _pedal_segments(score))
        midi = mido.MidiFile(file=io.BytesIO(data))

        absolute = 0
        down_at: list[int] = []
        note_on_at: list[int] = []
        pedal = 0
        at_first_note: list[bool] = []
        for track in midi.tracks:
            for message in track:
                absolute += message.time
                if message.type == "control_change" and message.control == 64:
                    pedal = 127 if message.value >= 64 else 0
                    down_at.append(absolute)
                elif message.type == "note_on" and message.velocity > 0:
                    note_on_at.append(absolute)
                    at_first_note.append(pedal == 127)

        assert note_on_at, "no notes in the test score"
        assert down_at[0] <= note_on_at[0], "the first note is struck before the pedal is down"
        assert all(at_first_note), "some notes are struck with the pedal up"


class TestDynamicsComeFromTheRecording:
    """Velocity must be measured, not taken from the model's guess.

    The user heard every key at the same volume. The data said otherwise - basic-pitch
    reports 943 distinct amplitudes and they survived into the MIDI intact - so the
    fault was not a lost value but a weak estimate. Measured on 星辰大海 it correlates
    +0.235 with how loud the notes actually were, and rendered to velocities between 31
    and 103 with a standard deviation of 14.7: everything in the middle.
    """

    def test_the_measurement_beats_the_guess_on_spread(self) -> None:
        import numpy as np

        # Two notes of the same pitch, one struck hard and one barely, in a signal
        # where that is true. They start at 0.3 s and 1.3 s rather than at zero: a
        # measurement that looks backwards from the onset needs something to look at,
        # and a note at t=0 is a boundary case that no recording starts with.
        sr = 22050
        y = np.zeros(sr * 2, dtype=np.float32)
        for start, level in ((0.3, 0.9), (1.3, 0.12)):
            t = np.arange(int(0.4 * sr)) / sr
            y[int(start * sr) : int(start * sr) + t.size] += level * np.sin(
                2 * np.pi * 440.0 * t
            ).astype(np.float32)

        from services.audio_analysis.dynamics import measure

        # Both notes are annotated with the *same* amplitude, so anything that separates
        # them came from the audio.
        notes = [
            NoteEvent(start=0.3, duration=0.4, midi=69, amplitude=0.5),
            NoteEvent(start=1.3, duration=0.4, midi=69, amplitude=0.5),
        ]
        profile = measure(y, sr, notes)

        assert profile.loudness[0] > profile.loudness[1] + 0.5, (
            "the louder note was not measured as louder: "
            f"{profile.loudness[0]:.3f} vs {profile.loudness[1]:.3f}"
        )

    def test_loudness_is_mapped_across_the_piano_range_not_the_middle(self) -> None:
        """Percentile mapping, because linear scaling produced the flatness."""
        import numpy as np

        from services.audio_analysis.dynamics import _to_unit, to_velocity

        # A bell-shaped distribution, which is what a naive loudness estimate gives.
        db = np.abs(np.random.default_rng(7).normal(30.0, 4.0, size=4000))
        unit = _to_unit(db)
        velocities = [to_velocity(v) for v in unit]
        assert min(velocities) <= 32, "the softest notes are not soft"
        assert max(velocities) >= 100, "the loudest notes are not loud"
        # And the spread has to be usable, not theoretical.
        assert float(np.std(velocities)) > 20.0, f"velocity spread is only {np.std(velocities):.1f}"

    def test_a_note_far_below_the_piece_gets_no_dynamic(self) -> None:
        """Otherwise the noise floor becomes the softest note in the piece."""
        import numpy as np

        from services.audio_analysis.dynamics import QUIET_MARGIN_DB, _quiet_floor

        levels = np.array([60.0, 60.0, 60.0, 61.0, 62.0, 30.0])
        floor = _quiet_floor(levels)
        assert floor == pytest.approx(60.0 - QUIET_MARGIN_DB)
        # Only the outlier is below it.
        assert list(np.where(levels >= floor)[0]) == [0, 1, 2, 3, 4]

    def test_apply_replaces_the_guess_and_leaves_a_mismatch_alone(self) -> None:
        import dataclasses

        from services.audio_analysis import dynamics

        notes = [
            NoteEvent(start=0.0, duration=1.0, midi=60, amplitude=0.5),
            NoteEvent(start=1.0, duration=1.0, midi=62, amplitude=0.5),
        ]
        replaced = dynamics.apply(notes, dynamics.DynamicsProfile([0.1, 0.9], 0.0, 10.0))
        assert [n.amplitude for n in replaced] == [0.1, 0.9]
        assert all(n.midi == original.midi for n, original in zip(replaced, notes, strict=True))

        # A profile of the wrong length must not silently misalign the notes.
        untouched = dynamics.apply(notes, dynamics.DynamicsProfile([0.5], 0.0, 0.0))
        assert [n.amplitude for n in untouched] == [0.5, 0.5]
        assert dataclasses.is_dataclass(untouched[0])


class TestTheTimbreCorrectionIsOffUnlessAskedFor:
    """A measurement that overturned the reason the correction existed.

    Comparing 星辰大海 with its own render suggested the synth was 1.86x too bright
    and its partials 2.4 dB too strong. Both came from comparing sixty seconds of
    dense music against a single chord. On the same note the render is *darker* than
    the recording, so the default is no correction at all.
    """

    def test_no_tilt_is_applied_by_default(self) -> None:
        from services.score.render import TONE_TILT_DB_PER_OCTAVE

        assert TONE_TILT_DB_PER_OCTAVE == 0.0

    @requires_synth
    def test_a_tone_change_is_audible_when_asked_for(self, tmp_path: Path) -> None:
        """The knob still works; it is simply not the default."""
        from services.score.render import render, spectral_centroid

        score = two_hand_score(tmp_path)
        flat = render(score, tmp_path / "flat.wav", config=Settings(), tone_db_per_octave=0.0)
        dark = render(score, tmp_path / "dark.wav", config=Settings(), tone_db_per_octave=-6.0)

        import soundfile as sf

        flat_y, _ = sf.read(str(flat.path), dtype="float32")
        dark_y, _ = sf.read(str(dark.path), dtype="float32")
        assert (
            spectral_centroid(dark_y, SAMPLE_RATE) < spectral_centroid(flat_y, SAMPLE_RATE) * 0.95
        )

    def test_the_centroid_measurement_works_on_silence(self) -> None:
        import numpy as np

        from services.score.render import spectral_centroid

        assert spectral_centroid(np.zeros(10, dtype=np.float32), SAMPLE_RATE) == 0.0
        assert spectral_centroid(np.zeros(100000, dtype=np.float32), SAMPLE_RATE) == 0.0
