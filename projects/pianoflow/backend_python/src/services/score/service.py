"""Score generation: one call from an audio file to a playable two-staff score.

This is the seam that ties phase 2c (transcription, grid, hand split, quality,
arrangement) to phase 2d (score model, writers). Everything expensive happens
here and the HTTP layer only translates.

Two input paths, one pipeline:

* a **cache key** for something already downloaded by phase 1
* a **local upload**, which is normalised into the same cache so the pipeline
  never has to care where the audio came from

A third mode, ``preview``, truncates the audio. Turning a 4 minute song into a
score takes a few seconds of CPU; that is fine for a background job but painful
in a request, so the API exposes a length cap and says plainly in the response
whether it truncated.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from core.config import Settings
from core.errors import NotFoundError, ValidationError
from services.arrangement.arranger import DIFFICULTIES, FIDELITIES, Fidelity, arrange
from services.arrangement.grid import build_bar_grid
from services.arrangement.hand_split import HandSplitConfig, hand_events_by_bar, split_hands
from services.arrangement.harmony import SPAN_CAP
from services.arrangement.piano_midi import midi_summary, write_midi
from services.arrangement.piano_musicxml import score_musicxml, to_musicxml
from services.arrangement.piano_score import PianoScore, build_score
from services.arrangement.quality import QualityReport, assess_bars
from services.audio_analysis.chords import detect_chords
from services.audio_analysis.dynamics import apply as apply_dynamics
from services.audio_analysis.dynamics import measure as measure_dynamics
from services.audio_analysis.pitch import estimate_key
from services.audio_analysis.polyphony import BasicPitchTranscriber, Transcriber

logger = logging.getLogger(__name__)

#: Patterns the API accepts. Mirrors patterns.PATTERNS keys.
PATTERN_NAMES = ("root_only", "root_octave", "block_chord", "broken_chord", "alberti_bass")
DEFAULT_PATTERN = "broken_chord"

#: Which model turns audio into notes. ``basic_pitch`` is the default because it is
#: nearly forty times faster (2.3 s against 87.8 s for 239.5 s of audio, measured on
#: the reference recording); ``transkun`` is offered because basic-pitch has no offset
#: head at all and guesses every duration from frame decay, so its note lengths are an
#: artefact. See :mod:`services.audio_analysis.transkun` for the three-way measurement.
TRANSCRIBER_NAMES = ("basic_pitch", "transkun")
DEFAULT_TRANSCRIBER = "basic_pitch"

#: What the UI is told about each model, so the choice is explainable rather than a
#: bare dropdown.
TRANSCRIBER_HINTS: dict[str, str] = {
    "basic_pitch": "最快；音符时值由包络衰减推测，长短可能不准",
    "transkun": "较慢（约为 1/40）；时值由回归头给出，不按踏板撑长",
}

#: What each difficulty actually does, so the UI can explain the choice instead of
#: only offering "L1..L4". The numbers are the agreed budget table: same-hand chord
#: size, and the accompaniment figure the level maps to.
DIFFICULTY_HINTS: dict[int, str] = {
    1: "骨架低音，半速；右手旋律每手最多 1 音，不加和弦",
    2: "柱式和弦伴奏；右手旋律每手最多 2 音",
    3: "分解和弦伴奏；右手旋律补成三和弦（最多 3 音）",
    4: "阿尔贝蒂低音伴奏；右手旋律补成带七音的和弦（最多 4 音）",
}

#: Analysis sample rate. Must match what the ONNX graph expects.
SAMPLE_RATE = 22050
#: Longest slice analysed in one request. A full song is allowed but the caller
#: should pass previewSeconds when they only want to look at the result.
DEFAULT_PREVIEW_SECONDS = 0  # 0 = whole file
MAX_PREVIEW_SECONDS = 600

#: Extensions accepted for a local upload. Anything else is refused rather than
#: handed to ffmpeg, which would happily transcode an arbitrary file.
UPLOAD_EXTENSIONS = ("wav", "mp3", "m4a", "flac", "ogg", "opus", "aac", "wma")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class ScoreRequest:
    """What the caller asked for. Validated once, here."""

    fidelity: Fidelity = "arranged"
    difficulty: int = 3
    #: Empty means "let the difficulty choose". The agreed rule is that the texture
    #: follows the level - L1 root_only through L4 alberti_bass - so a non-empty
    #: default here silently overrode that: building a ScoreRequest directly gave
    #: L4 broken_chord, and the reason line read "caller supplied" when nobody had
    #: asked for a pattern.
    pattern: str = ""
    #: Faithful mode only: complete a lone chord tone into a triad. Off by default,
    #: because doing it to every lone note inflated a 1057-note transcription by 40 %.
    fill_chords: bool = False
    title: str = ""
    preview_seconds: float = 0.0
    write_files: bool = True
    #: Which model does the transcription. Kept on the request rather than only in
    #: settings so one caller can ask for Transkun's durations without changing what the
    #: next request does.
    transcriber: str = DEFAULT_TRANSCRIBER
    #: Accompaniment texture. Empty means "use the pattern system". When set, the
    #: left hand is built from the eight textures in textures.py instead of the
    #: five patterns in patterns.py.
    texture: str = ""
    #: Texture plan for varying left-hand texture across sections. Mutually
    #: exclusive with texture.
    texture_plan: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "fidelity": self.fidelity,
            "difficulty": self.difficulty,
            "pattern": self.pattern,
            "fillChords": self.fill_chords,
            "title": self.title,
            "previewSeconds": self.preview_seconds,
            "transcriber": self.transcriber,
            "texture": self.texture,
            "texturePlan": self.texture_plan,
        }


@dataclass
class ScoreResult:
    """Everything the API needs to answer, plus the files on disk."""

    score: PianoScore
    musicxml: str
    #: Each stage is stored as a read-only mapping so a payload of any concrete
    #: mapping type can go in without an invariance complaint. The contents are
    #: what the tests and the API contract check.
    arrangement: Mapping[str, object] = field(default_factory=dict)
    grid: Mapping[str, object] = field(default_factory=dict)
    quality: Mapping[str, object] = field(default_factory=dict)
    key: Mapping[str, object] = field(default_factory=dict)
    chords: Sequence[Mapping[str, object]] = field(default_factory=list)
    hand_split: Mapping[str, object] = field(default_factory=dict)
    musicxml_path: Path | None = None
    midi_path: Path | None = None
    #: What the faithful re-voicing did. Empty for the arranging modes.
    fidelity_report: Mapping[str, object] = field(default_factory=dict)
    #: How the dynamics were derived, and how well the model had done on its own.
    dynamics: Mapping[str, object] = field(default_factory=dict)
    #: The synthesised piano track, generated from ``score.notes`` so the audio
    #: and the notation share one timeline. Absent when the synth is unavailable.
    piano_path: Path | None = None
    piano: Mapping[str, object] = field(default_factory=dict)
    truncated: bool = False
    analysed_seconds: float = 0.0
    #: Which model produced the notes. Reported so a score can be traced back to the
    #: transcription that made it: the two models disagree about note duration by more
    #: than a factor of three on the same audio, so this is not a detail to hide.
    transcriber: str = DEFAULT_TRANSCRIBER

    def to_dict(self) -> dict[str, object]:
        return {
            "request": None,
            "grid": self.grid,
            "handSplit": self.hand_split,
            "quality": self.quality,
            "arrangement": self.arrangement,
            "fidelityReport": self.fidelity_report,
            "dynamics": self.dynamics,
            "transcriber": self.transcriber,
            "key": self.key,
            "chords": self.chords,
            "score": self.score.to_dict(),
            "piano": self.piano,
            "truncated": self.truncated,
            "analysedSeconds": round(self.analysed_seconds, 3),
            "musicxmlUrl": _versioned("/api/score/musicxml", self.musicxml_path),
            "midiUrl": _versioned("/api/score/midi", self.midi_path),
            # Only offered when a track was actually produced, so the UI does not
            # offer a player with nothing behind it.
            "pianoTrackUrl": _versioned("/api/score/piano", self.piano_path),
        }


def validate_request(
    payload: dict[str, object],
    *,
    default_title: str = "",
) -> ScoreRequest:
    """Turn a JSON body into a ScoreRequest, refusing anything unsupported."""
    fidelity = str(payload.get("fidelity") or "arranged").strip().lower()
    if fidelity not in FIDELITIES:
        raise ValidationError(
            f"unsupported fidelity {fidelity!r}", details={"supported": list(FIDELITIES)}
        )

    raw_difficulty: object = payload.get("difficulty", 3)
    if isinstance(raw_difficulty, bool):
        raise ValidationError("field 'difficulty' must be an integer 1-4")
    if isinstance(raw_difficulty, str) and raw_difficulty.strip().upper().startswith("L"):
        raw_difficulty = raw_difficulty.strip().upper()[1:]
    if not isinstance(raw_difficulty, (int, str)):
        raise ValidationError("field 'difficulty' must be an integer 1-4")
    try:
        difficulty = int(raw_difficulty)
    except ValueError as exc:
        raise ValidationError("field 'difficulty' must be an integer 1-4") from exc
    if difficulty not in DIFFICULTIES:
        raise ValidationError(
            f"unsupported difficulty {difficulty}", details={"supported": list(DIFFICULTIES)}
        )

    pattern = str(payload.get("pattern") or "").strip()
    if not pattern:
        pattern = ""  # empty means "detect from the transcription"
    elif pattern not in PATTERN_NAMES:
        raise ValidationError(
            f"unsupported pattern {pattern!r}", details={"supported": list(PATTERN_NAMES)}
        )

    preview: object = payload.get("previewSeconds", 0) or 0
    if not isinstance(preview, (int, float, str)):
        raise ValidationError("field 'previewSeconds' must be a number")
    try:
        preview_seconds = float(preview)
    except ValueError as exc:
        raise ValidationError("field 'previewSeconds' must be a number") from exc
    if not 0 <= preview_seconds <= MAX_PREVIEW_SECONDS:
        raise ValidationError(f"field 'previewSeconds' must be between 0 and {MAX_PREVIEW_SECONDS}")

    title = payload.get("title")
    if title is not None and not isinstance(title, str):
        raise ValidationError("field 'title' must be a string")

    fill_chords = payload.get("fillChords", False)
    if not isinstance(fill_chords, bool):
        raise ValidationError("field 'fillChords' must be true or false")

    transcriber = str(payload.get("transcriber") or DEFAULT_TRANSCRIBER).strip().lower()
    if transcriber not in TRANSCRIBER_NAMES:
        raise ValidationError(
            f"unsupported transcriber {transcriber!r}",
            details={"supported": list(TRANSCRIBER_NAMES)},
        )

    texture = str(payload.get("texture") or "").strip()
    if texture:
        from services.arrangement.textures import TextureName

        try:
            TextureName(texture)
        except ValueError as exc:
            raise ValidationError(
                f"unsupported texture {texture!r}",
                details={"supported": [t.value for t in TextureName]},
            ) from exc

    texture_plan = str(payload.get("texturePlan") or "").strip()
    if texture and texture_plan:
        raise ValidationError("texture and texturePlan are mutually exclusive")

    return ScoreRequest(
        fidelity=fidelity,
        difficulty=difficulty,
        pattern=pattern,
        title=(title or default_title).strip()[:200],
        preview_seconds=preview_seconds,
        fill_chords=fill_chords,
        transcriber=transcriber,
        texture=texture,
        texture_plan=texture_plan,
    )


def _versioned(url: str, path: Path | None) -> str | None:
    """Add a cache-busting token to an output URL, or None if there is nothing.

    Every output endpoint serves "whichever file is newest", so the URL alone does
    not identify the content: generating a second score leaves the first one's URL
    valid but pointing at different audio. Two things then go wrong. The browser
    serves the cached file for up to a minute, and Vue sees an unchanged ``src`` on
    the audio element and does not reload it at all - so the notation updates while
    the previous recording keeps playing. Tying the URL to the file's own mtime
    fixes both, because the string now changes whenever the content does.

    A path that is missing yields ``None`` rather than the bare URL. The endpoint
    would serve some *older* file in that case, which is worse than nothing: the
    player would play a score the notation on screen is not showing.
    """
    if path is None or not path.is_file():
        return None
    return f"{url}?v={path.stat().st_mtime_ns}"


def _render_piano(
    score: PianoScore,
    wav_path: Path,
    settings: Settings,
    source_audio: Path | None = None,
) -> tuple[Path | None, Mapping[str, object]]:
    """Render the piano track, reporting unavailability instead of failing.

    The synth and the SoundFont are external files a user may not have, so a
    missing tool is a normal state, not an error: the score, MusicXML and MIDI are
    all still valid, and follow-along simply falls back to the source recording.
    Anything else that goes wrong *is* logged, because a silent fallback would
    hide a real regression in the render path.

    ``source_audio`` is the recording the score came from. It is handed to the renderer
    because that is the only place the decay after the final note can be measured - the
    synth has no tail of its own, and without the recording there is nothing to match
    one against, so the track stays dry rather than getting an invented room.
    """
    if settings.fluidsynth_exe() is None or settings.soundfont() is None:
        logger.info("piano track skipped: fluidsynth or the SoundFont is not installed")
        return None, {"available": False, "reason": "the synth or SoundFont is not installed"}

    from services.score.render import render

    try:
        rendered = render(score, wav_path, config=settings, source_audio=source_audio)
    except Exception as exc:
        logger.exception("piano track failed, keeping the score")
        return None, {"available": False, "reason": str(exc)[:200]}

    return rendered.path, {
        "available": True,
        "durationSeconds": round(rendered.duration_seconds, 3),
        "peak": round(rendered.peak, 5),
        "sampleRate": rendered.sample_rate,
        "noteCount": len(rendered.events),
        # Stated plainly so the UI can show where the audio came from.
        "synth": "fluidsynth",
        "source": "generated-from-score",
        "room": dict(rendered.room),
    }


def _load_audio(path: Path) -> tuple[NDArray[Any], int]:
    """Load mono audio at the analysis rate.

    Both failure modes below used to escape as a raw library error, which the API
    turned into a 500: a file that is not there (librosa raises FileNotFoundError
    through audioread) and one with no musical content (the grid raises ValueError
    about onset energy). Both are the caller's problem, so both are reported as
    validation errors with an explanation.
    """
    import librosa

    if not path.is_file():
        raise ValidationError(f"audio file not found: {path.name}")

    try:
        y, sr = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    except ValidationError:
        raise
    except FileNotFoundError as exc:
        raise ValidationError(f"audio file not found: {path.name}") from exc
    except Exception as exc:
        raise ValidationError(
            f"the audio could not be decoded: {path.name}", details={"reason": str(exc)[:300]}
        ) from exc

    if y.size == 0:
        raise ValidationError("audio file is empty")
    if sr != SAMPLE_RATE:
        raise ValidationError(f"unexpected sample rate {sr}")
    if float(np.max(np.abs(y))) <= 1e-4:
        # Silence has no tempo, no chords and no notes; saying so is far more
        # useful than "not enough onset energy".
        raise ValidationError(
            "the audio is silent, so there is nothing to transcribe",
            details={"fileName": path.name},
        )
    return np.asarray(y, dtype=np.float32), int(sr)


def _build_transcriber(name: str, settings: Settings | None = None) -> Transcriber:
    """Pick the transcription model, refusing clearly rather than falling back.

    A silent fallback is the wrong behaviour here. If a caller asks for Transkun's
    durations and it is not installed, quietly returning basic-pitch notes would hand
    back a score whose note lengths are the artefact they asked to avoid, with nothing
    in the response saying so.

    The return type is the protocol rather than a base class because the two models share
    no ancestry: ``BasicPitchTranscriber`` runs ONNX in this process while
    ``TranskunTranscriber`` shells out to an environment that has torch. The only contract
    the pipeline needs is ``transcribe_array``.
    """
    resolved = settings or Settings.from_env()
    if name == "basic_pitch":
        return BasicPitchTranscriber()
    if name != "transkun":
        raise ValidationError(
            f"unsupported transcriber {name!r}", details={"supported": list(TRANSCRIBER_NAMES)}
        )

    from services.audio_analysis.transkun import TranskunTranscriber

    try:
        return TranskunTranscriber(resolved.transkun_python or None)
    except ValidationError as exc:
        raise ValidationError(
            "the transkun transcriber was requested but is not installed",
            details={
                **exc.details,
                "hint": "pip install transkun in a separate environment, "
                "then set PIANOFLOW_TRANSKUN_PYTHON to its python",
            },
        ) from exc


def generate_score(
    audio_path: Path,
    request: ScoreRequest,
    *,
    output_dir: Path | None = None,
    settings: Settings | None = None,
) -> ScoreResult:
    """Run the whole pipeline on one audio file."""
    import time

    started = time.perf_counter()
    y, sr = _load_audio(audio_path)

    total_seconds = len(y) / sr
    truncated = False
    if request.preview_seconds and total_seconds > request.preview_seconds:
        y = y[: int(request.preview_seconds * sr)]
        truncated = True
        total_seconds = request.preview_seconds

    try:
        grid = build_bar_grid(y, sr)
    except ValueError as exc:
        # No steady pulse: a drone, an ambient pad, or a clip with no rhythm.
        raise ValidationError(
            "no steady tempo could be found, so there is no bar to write a score against",
            details={"reason": str(exc)},
        ) from exc

    transcriber = _build_transcriber(request.transcriber or DEFAULT_TRANSCRIBER, settings)
    events = transcriber.transcribe_array(y, sr)
    if not events:
        raise ValidationError(
            "no notes were detected in this audio",
            details={
                "analysedSeconds": round(total_seconds, 3),
                "transcriber": request.transcriber or DEFAULT_TRANSCRIBER,
            },
        )

    # Replace the model's velocity guess with a measurement of the recording.
    #
    # The guess is barely usable: on 星辰大海 it correlated +0.235 with how loud the
    # notes actually were, and rendered to velocities between 31 and 103 with a
    # standard deviation of 14.7 - everything in the middle, which is what the user
    # heard as every key being the same volume. Measuring the audio gives 22.9 dB of
    # real range and doubles the spread.
    profile = measure_dynamics(y, sr, events)
    events = apply_dynamics(events, profile)

    # The span budget is passed in so chords too wide for one hand are re-voiced by
    # octave instead of being torn between the two. Without it the fitter never ran
    # and the response reported chordsFolded: 0 on a piece that needed 111 folds.
    assignment = split_hands(events, config=HandSplitConfig(span_cap=SPAN_CAP[request.difficulty]))
    buckets = hand_events_by_bar(assignment, grid)
    quality: QualityReport = assess_bars(buckets, grid)
    chords = detect_chords(y, sr)
    tonic, mode, key_confidence = estimate_key(y, sr)

    arrangement = arrange(
        buckets,
        grid,
        chords=chords,
        quality=quality,
        fidelity=request.fidelity,
        difficulty=request.difficulty,
        pattern=request.pattern or None,
        fill_chords=request.fill_chords,
        assignment=assignment,
        texture=request.texture or None,
        texture_plan=None,  # TODO: parse texture_plan from request
    )
    score = build_score(
        arrangement,
        grid,
        title=request.title,
        key_tonic=tonic,
        key_mode=mode,
    )
    musicxml = score_musicxml(score)

    musicxml_path: Path | None = None
    midi_path: Path | None = None
    piano_path: Path | None = None
    piano_info: Mapping[str, object] = {}
    if request.write_files and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(audio_path).stem
        musicxml_path = to_musicxml(score, output_dir / f"{stem}.musicxml")
        midi_path = write_midi(score, output_dir / f"{stem}.mid")
        logger.info("score files: %s, %s", musicxml_path, midi_path)
        # The piano track is optional: a missing synth must not lose the score,
        # it only means follow-along falls back to the source recording.
        piano_path, piano_info = _render_piano(
            score,
            output_dir / f"{stem}.piano.wav",
            settings or Settings.from_env(),
            source_audio=audio_path,
        )

    elapsed = time.perf_counter() - started
    result = ScoreResult(
        score=score,
        musicxml=musicxml,
        arrangement=arrangement.to_dict(),
        fidelity_report=dict(arrangement.voicing),
        dynamics={
            "source": "measured-from-audio",
            "spreadDb": round(profile.spread_db, 1),
            "modelCorrelation": round(profile.correlation, 3),
        },
        grid=grid.to_dict(),
        quality=quality.to_dict(),
        hand_split=assignment.to_dict(),
        key={
            "tonic": tonic,
            "mode": mode,
            "confidence": round(float(key_confidence), 3),
            "fifths": score.key_fifths,
        },
        chords=[c.to_dict() for c in chords],
        musicxml_path=musicxml_path,
        midi_path=midi_path,
        piano_path=piano_path,
        piano=piano_info,
        truncated=truncated,
        analysed_seconds=total_seconds,
        transcriber=request.transcriber or DEFAULT_TRANSCRIBER,
    )
    result.to_dict()["request"] = request.to_dict()
    logger.info(
        "score done in %.2fs: %d bars, %d notes, %d recomposed",
        elapsed,
        len(score.measures),
        score.note_count,
        len(arrangement.recomposed),
    )
    return result


def summarise(result: ScoreResult) -> dict[str, object]:
    """A short payload for logs and the SSE progress stream."""
    return {
        "measures": len(result.score.measures),
        "notes": result.score.note_count,
        "tiedNotes": result.score.tied_count,
        "meanBarQuality": result.quality.get("meanScore"),
        "recomposedBars": result.quality.get("recomposedBars"),
        "truncated": result.truncated,
        # Whether follow-along will hear the generated piano or the original mix.
        "pianoTrack": result.piano_path is not None,
    }


def resolve_audio_path(cache_manager: object, key: str) -> Path:
    """Look a cached audio file up, raising the API's own not-found error."""
    try:
        _entry, path = cache_manager.resolve_audio(key)  # type: ignore[attr-defined]
    except KeyError as exc:
        raise NotFoundError(f"no cached audio for key {key}") from exc
    if not path.is_file():
        raise NotFoundError(f"cached audio is missing on disk: {path.name}")
    return path


def summarise_midi(path: Path) -> dict[str, object]:
    return dict(midi_summary(path))


__all__ = [
    "DEFAULT_PATTERN",
    "DEFAULT_PREVIEW_SECONDS",
    "DEFAULT_TRANSCRIBER",
    "DIFFICULTY_HINTS",
    "MAX_PREVIEW_SECONDS",
    "MAX_UPLOAD_BYTES",
    "PATTERN_NAMES",
    "SAMPLE_RATE",
    "TRANSCRIBER_HINTS",
    "TRANSCRIBER_NAMES",
    "UPLOAD_EXTENSIONS",
    "ScoreRequest",
    "ScoreResult",
    "generate_score",
    "resolve_audio_path",
    "summarise",
    "summarise_midi",
    "validate_request",
]
