"""Score generation: one call from an audio file to a playable two-staff score.

This is the seam that ties phase 2c (transcription, grid, hand split, quality,
arrangement) to phase 2d (score model, writers). Everything expensive happens
here and the HTTP layer only translates.

The API exposes two product modules over this one pipeline:

* **piano transcription** (``/api/score/transcribe``,
  ``/api/score/transcribe/upload``) - a piano recording turned into a faithful
  score. The notes are the pianist's own: fidelity ``transcribed`` or
  ``as-played``, no accompaniment generation, no patterns or textures.
* **pop to piano** (``/api/score/arrange``, ``/api/score/arrange/upload``) - a
  recording turned into a playable arrangement: the right hand keeps the
  transcribed melody, the left hand is rebuilt from patterns or textures.
  Fidelity ``hybrid`` or ``arranged``, with a difficulty budget.

The modules are split at the validation layer, not the pipeline: each
``validate_*_request`` accepts only its own fidelities and refuses the other
module's options with a pointer to the right endpoint, so a request can never
run a different pipeline than the one its URL promised.

Each module takes a **cache key** for something already downloaded by phase 1
or a **local upload**, which is normalised into the same cache so the pipeline
never has to care where the audio came from.

A third mode, ``preview``, truncates the audio. Turning a 4 minute song into a
score takes a few seconds of CPU; that is fine for a background job but painful
in a request, so the API exposes a length cap and says plainly in the response
whether it truncated.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from core.config import Settings
from core.errors import NotFoundError, ValidationError
from services.arrangement.arranger import (
    ACCOMPANIMENT_NAMES,
    DIFFICULTIES,
    FIDELITIES,
    Fidelity,
    arrange,
)
from services.arrangement.grid import build_bar_grid
from services.arrangement.hand_split import HandSplitConfig, hand_events_by_bar, split_hands
from services.arrangement.harmony import SPAN_CAP
from services.arrangement.piano_midi import midi_summary, write_midi
from services.arrangement.piano_musicxml import score_musicxml, to_musicxml
from services.arrangement.piano_score import PianoScore, build_score
from services.arrangement.quality import QualityReport, assess_bars
from services.arrangement.textures import TexturePlan
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

#: The product's default left-hand density. The API's own default is "light" because
#: the complaint this level exists for is that the full figure buried the melody; the
#: arranger's function-level default stays "standard" (the historic behaviour) so a
#: direct caller is not silently re-voiced.
DEFAULT_ACCOMPANIMENT = "light"

#: What each density level does, in the UI's language. Served by the arrange options
#: endpoint so the labels a user reads are the server's own.
ACCOMPANIMENT_HINTS: dict[str, str] = {
    "light": "轻盈：左手只留骨架与和弦音、音量再降，旋律最突出",
    "standard": "标准：织体完整，伴奏音量适中",
    "full": "浓厚：织体完整且更响，左手存在感最强",
}

#: The fidelities each module owns. ``transcribed`` and ``as-played`` keep the
#: pianist's own notes (the piano module); ``hybrid`` and ``arranged`` rebuild
#: the left hand (the pop-to-piano module). The split is mirrored in the API:
#: see :func:`validate_transcribe_request` and :func:`validate_arrange_request`.
TRANSCRIBE_FIDELITIES: tuple[str, ...] = ("transcribed", "as-played")
ARRANGE_FIDELITIES: tuple[str, ...] = ("hybrid", "arranged")

#: What each fidelity actually does, in the UI's language. Served by the module
#: options endpoints so the labels a user reads are the server's own.
FIDELITY_HINTS: dict[str, str] = {
    "transcribed": "钢琴原谱：忠实转录，不按难度削减或移动音符",
    "as-played": "原样转录：保留每个小节的演奏，仍按难度预算削减",
    "hybrid": "混合：只重配质量不合格的小节",
    "arranged": "全部重配：左手按和弦重写，右手旋律保持转录原样",
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
    #: exclusive with texture. Stored as JSON string, but may be parsed to TexturePlan
    #: after validation.
    texture_plan: TexturePlan | str | None = None
    #: Left-hand density level (light/standard/full). The product default is "light":
    #: the full figure buried the melody. Arranging module only - the faithful
    #: transcribe module refuses it, because thinning the recording is not faithful.
    accompaniment: str = DEFAULT_ACCOMPANIMENT
    #: Per-label section textures ({"verse": "half", "chorus": "arpfull", ...}).
    #: Non-empty means the arrangement resolves the song's sections and gives each
    #: label its texture. Mutually exclusive with texture/texturePlan: a manual plan
    #: and an automatic one deciding the same bars at once is two arrangements.
    section_textures: Mapping[str, str] = field(default_factory=dict)

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
            "accompaniment": self.accompaniment,
            "sectionTextures": dict(self.section_textures),
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
    #: The melody alone (right staff) and the accompaniment alone (left staff),
    #: each rendered from the same score so the previews cannot drift from the
    #: notation. Absent with the main track when the synth is unavailable.
    melody_path: Path | None = None
    melody: Mapping[str, object] = field(default_factory=dict)
    left_path: Path | None = None
    left: Mapping[str, object] = field(default_factory=dict)
    truncated: bool = False
    analysed_seconds: float = 0.0
    #: Which model produced the notes. Reported so a score can be traced back to the
    #: transcription that made it: the two models disagree about note duration by more
    #: than a factor of three on the same audio, so this is not a detail to hide.
    transcriber: str = DEFAULT_TRANSCRIBER
    #: The detected sections, when the arrange module ran: what the left hand does
    #: in each part, and why the detector labelled it that. Empty for the
    #: transcribe module.
    sections: Sequence[Mapping[str, object]] = field(default_factory=list)
    #: The instrumental-melody lift: how many no-vocal top voices were moved to the
    #: right hand, in which bars, and for which section labels. Empty when there was
    #: nothing to lift (or the transcribe module ran).
    section_melody: Mapping[str, object] = field(default_factory=dict)

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
            "sections": list(self.sections),
            "sectionMelody": dict(self.section_melody),
            "score": self.score.to_dict(),
            "piano": self.piano,
            "melody": self.melody,
            "leftHand": self.left,
            "truncated": self.truncated,
            "analysedSeconds": round(self.analysed_seconds, 3),
            "musicxmlUrl": _versioned(
                "/api/score/musicxml", self.musicxml_path, addressed_by_key=True
            ),
            "midiUrl": _versioned("/api/score/midi", self.midi_path, addressed_by_key=True),
            # Only offered when a track was actually produced, so the UI does not
            # offer a player with nothing behind it.
            "pianoTrackUrl": _versioned(
                "/api/score/piano", self.piano_path, addressed_by_key=True
            ),
            "melodyTrackUrl": _versioned(
                "/api/score/melody", self.melody_path, addressed_by_key=True
            ),
            "leftHandTrackUrl": _versioned(
                "/api/score/left", self.left_path, addressed_by_key=True
            ),
        }


def _parse_section_textures(value: object) -> dict[str, str]:
    """Parse a ``sectionTextures`` payload (object or JSON string) to label->texture.

    Only labels and textures the detector and the texture system actually know are
    kept; an unknown entry is refused, because defaulting it away would build a plan
    the caller did not ask for. Empty input gives an empty mapping, which means
    "no section textures".
    """
    from services.arrangement.sections import SECTION_LABELS
    from services.arrangement.textures import TextureName

    if value is None:
        return {}
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid sectionTextures JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValidationError("field 'sectionTextures' must be an object of label -> texture")

    known_labels = set(SECTION_LABELS)
    out: dict[str, str] = {}
    for raw_label, raw_texture in value.items():
        label = str(raw_label).strip().lower()
        texture = str(raw_texture).strip()
        if label not in known_labels:
            raise ValidationError(
                f"unknown section label {label!r}", details={"supported": sorted(known_labels)}
            )
        if not texture:
            continue
        try:
            TextureName(texture)
        except ValueError as exc:
            raise ValidationError(
                f"unsupported texture for section {label!r}: {texture!r}",
                details={"supported": [t.value for t in TextureName]},
            ) from exc
        out[label] = texture
    return out


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

    accompaniment = str(payload.get("accompaniment") or DEFAULT_ACCOMPANIMENT).strip().lower()
    if accompaniment not in ACCOMPANIMENT_NAMES:
        raise ValidationError(
            f"unsupported accompaniment {accompaniment!r}",
            details={"supported": list(ACCOMPANIMENT_NAMES)},
        )

    section_textures = _parse_section_textures(payload.get("sectionTextures"))

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

    texture_plan_str = str(payload.get("texturePlan") or "").strip()
    if texture and texture_plan_str:
        raise ValidationError("texture and texturePlan are mutually exclusive")
    if section_textures and (texture or texture_plan_str):
        raise ValidationError(
            "sectionTextures is mutually exclusive with texture/texturePlan",
            details={"hint": "pick an automatic per-section texture or a manual one"},
        )

    texture_plan = None
    if texture_plan_str:
        import json

        from services.arrangement.textures import (
            TextureName,
            TexturePlan,
            TextureSection,
        )

        try:
            plan_data = json.loads(texture_plan_str)
            if not isinstance(plan_data, list):
                raise ValidationError("texturePlan must be a JSON array")
            sections = []
            for item in plan_data:
                if not isinstance(item, dict):
                    raise ValidationError("each texturePlan section must be an object")
                start_bar = int(item.get("startBar", 0))
                end_bar = int(item.get("endBar", 0))
                texture_name = str(item.get("texture", "")).strip()
                if not texture_name:
                    raise ValidationError("each texturePlan section must have a texture")
                try:
                    tex = TextureName(texture_name)
                except ValueError as exc:
                    raise ValidationError(
                        f"unsupported texture in texturePlan: {texture_name!r}",
                        details={"supported": [t.value for t in TextureName]},
                    ) from exc
                if start_bar < 0 or end_bar <= start_bar:
                    raise ValidationError("invalid bar range in texturePlan")
                from services.arrangement.textures import TextureSection

                sections.append(TextureSection(start_bar, end_bar, tex))
            texture_plan = TexturePlan(sections=tuple(sections))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid texturePlan JSON: {exc}") from exc

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
        accompaniment=accompaniment,
        section_textures=section_textures,
    )


def validate_transcribe_request(
    payload: dict[str, object],
    *,
    default_title: str = "",
) -> ScoreRequest:
    """The piano-transcription module: a faithful score of a piano recording.

    Fidelity is restricted to the two faithful modes; the arranging options
    (pattern, texture, texturePlan) are refused with a pointer to
    ``/api/score/arrange`` rather than defaulted away, because silently running
    a different pipeline than the URL promised is worse than a 400 that says
    where the option lives.
    """
    fidelity = str(payload.get("fidelity") or "").strip().lower()
    if fidelity in ARRANGE_FIDELITIES:
        raise ValidationError(
            f"fidelity {fidelity!r} is an arranging mode",
            details={"hint": "use POST /api/score/arrange"},
        )
    for field_name in ("pattern", "texture", "texturePlan", "accompaniment"):
        if str(payload.get(field_name) or "").strip():
            raise ValidationError(
                f"field {field_name!r} is an arranging option",
                details={"hint": "use POST /api/score/arrange"},
            )
    if _parse_section_textures(payload.get("sectionTextures")):
        raise ValidationError(
            "field 'sectionTextures' is an arranging option",
            details={"hint": "use POST /api/score/arrange"},
        )
    body = dict(payload)
    if not str(body.get("fidelity") or "").strip():
        body["fidelity"] = "transcribed"
    return validate_request(body, default_title=default_title)


def validate_arrange_request(
    payload: dict[str, object],
    *,
    default_title: str = "",
) -> ScoreRequest:
    """The pop-to-piano module: a playable arrangement of a recording.

    Fidelity is restricted to the two arranging modes. ``fillChords`` is refused
    rather than ignored: it only acts in the transcribed mode, so accepting it
    here would report success for an option that changed nothing.
    """
    fidelity = str(payload.get("fidelity") or "").strip().lower()
    if fidelity in TRANSCRIBE_FIDELITIES:
        raise ValidationError(
            f"fidelity {fidelity!r} is a faithful mode",
            details={"hint": "use POST /api/score/transcribe"},
        )
    if payload.get("fillChords") is not None:
        raise ValidationError(
            "fillChords only applies to the transcribed mode",
            details={"hint": "use POST /api/score/transcribe"},
        )
    return validate_request(payload, default_title=default_title)


def _versioned(url: str, path: Path | None, *, addressed_by_key: bool = False) -> str | None:
    """Add a cache-busting token to an output URL, or None if there is nothing.

    Every output endpoint serves "whichever file is newest", so the URL alone does
    not identify the content: generating a second score leaves the first one's URL
    valid but pointing at different audio. Two things then go wrong. The browser
    serves the cached file for up to a minute, and Vue sees an unchanged ``src`` on
    the audio element and does not reload it at all - so the notation updates while
    the previous recording keeps playing. Tying the URL to the file's own mtime
    fixes both, because the string now changes whenever the content does.

    ``addressed_by_key`` additionally carries the cache key, which turns the URL
    into an exact reference to one song's file instead of a bet on which file
    happens to be newest. The mtime token stays because it is what makes the
    browser fetch again when the *same* song is re-rendered. It is only added when
    the file is actually named after a cache key or upload id, because a key that
    the endpoint cannot resolve would turn a working player into a 404.

    A path that is missing yields ``None`` rather than the bare URL. The endpoint
    would serve some *older* file in that case, which is worse than nothing: the
    player would play a score the notation on screen is not showing.
    """
    if path is None or not path.is_file():
        return None
    versioned = f"{url}?v={path.stat().st_mtime_ns}"
    key = _cache_key_of(path)
    if addressed_by_key and key:
        versioned = f"{versioned}&cacheKey={key}"
    return versioned


#: A cache key (sha1, from ``CacheManager.make_key``) or an upload id (sha256,
#: from ``services.score.upload.hash_file``). The output files are named after the
#: audio that was analysed, so this is the only shape a stem can have.
_CACHE_KEY_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def _cache_key_of(path: Path) -> str:
    """The cache key this output was written under, or "" if it was not keyed."""
    stem = path.name.split(".", 1)[0]
    return stem if _CACHE_KEY_PATTERN.match(stem) else ""


def _render_piano(
    score: PianoScore,
    wav_path: Path,
    settings: Settings,
    source_audio: Path | None = None,
    texture_mode: bool = False,
    hands: tuple[str, ...] = ("right", "left"),
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

    ``texture_mode`` uses a drier mix (less room, less pedal) so the rhythmic
    differences between textures are audible. The default room and pedal blur them
    together, making eight textures sound identical.
    """
    if settings.fluidsynth_exe() is None or settings.soundfont() is None:
        logger.info("piano track skipped: fluidsynth or the SoundFont is not installed")
        return None, {"available": False, "reason": "the synth or SoundFont is not installed"}

    from services.score.render import render

    try:
        rendered = render(
            score,
            wav_path,
            config=settings,
            source_audio=source_audio,
            texture_mode=texture_mode,
            hands=hands,
        )
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

    # Parse texture_plan if it's a JSON string (legacy), otherwise use as-is
    texture_plan = request.texture_plan
    from services.arrangement.textures import TexturePlan

    if isinstance(texture_plan, str) and texture_plan:
        import json

        from services.arrangement.textures import TextureName, TextureSection

        try:
            plan_data = json.loads(texture_plan)
            if isinstance(plan_data, list):
                sections = []
                for item in plan_data:
                    if not isinstance(item, dict):
                        continue
                    start_bar = int(item.get("startBar", 0))
                    end_bar = int(item.get("endBar", 0))
                    texture_name = str(item.get("texture", "")).strip()
                    if texture_name:
                        try:
                            tex = TextureName(texture_name)
                        except ValueError:
                            continue
                        if start_bar < 0 or end_bar <= start_bar:
                            continue
                        from services.arrangement.textures import TextureSection

                        sections.append(TextureSection(start_bar, end_bar, tex))
                if sections:
                    default_tex = sections[-1].texture
                    texture_plan = TexturePlan(
                        sections=tuple(sections), default_texture=default_tex
                    )
            else:
                texture_plan = None
        except (json.JSONDecodeError, ValueError):
            texture_plan = None

    # The section analysis serves two masters. With ``sectionTextures`` it turns the
    # per-label choices into the same TexturePlan the manual path uses; always (for
    # an arrangement, never the faithful transcription) it lifts the instrumental
    # melody - in a section where nobody sings, the top voice of the transcription
    # is the tune, and without the lift the arranged left hand would rewrite it away.
    # The detection runs on the full audio, not the preview: the sections of a
    # 30-second clip are meaningless, and the plan has to cover the bars of whatever
    # gets arranged. The first run pays 30-60 s of Whisper; after that it is a cache
    # hit per song.
    section_payload: list[Mapping[str, object]] = []
    section_melody: Mapping[str, object] = {}
    if request.fidelity in ARRANGE_FIDELITIES:
        from services.arrangement.sections import (
            build_section_texture_plan,
            promote_section_melodies,
        )

        analysis = get_section_analysis(
            Path(audio_path),
            settings=settings,
            transcriber=request.transcriber or DEFAULT_TRANSCRIBER,
        )
        section_payload = [s.to_dict() for s in analysis.sections]
        if request.section_textures:
            resolved_plan = build_section_texture_plan(
                analysis.sections, request.section_textures
            )
            if resolved_plan is not None:
                texture_plan = resolved_plan
        buckets, section_melody = promote_section_melodies(buckets, analysis.sections)

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
        texture_plan=texture_plan if isinstance(texture_plan, TexturePlan) else None,
        accompaniment=request.accompaniment,
    )
    score = build_score(
        arrangement,
        grid,
        title=request.title,
        key_tonic=tonic,
        key_mode=mode,
    )
    musicxml = score_musicxml(score)

    # The melody-only preview: the tune without the chord-tone filling the right
    # hand carries, so 仅旋律 sounds like the song instead of like a voicing. It is
    # built from the same bars, so its times line up with the full score bar for bar
    # and the two tracks can be swapped mid-playback without the cursor jumping.
    melody_score = build_score(
        dataclasses.replace(
            arrangement,
            bars=[
                dataclasses.replace(bar, right=bar.melody, left=()) for bar in arrangement.bars
            ],
        ),
        grid,
        title=request.title,
        key_tonic=tonic,
        key_mode=mode,
    )

    musicxml_path: Path | None = None
    midi_path: Path | None = None
    piano_path: Path | None = None
    piano_info: Mapping[str, object] = {}
    melody_path: Path | None = None
    melody_info: Mapping[str, object] = {}
    left_path: Path | None = None
    left_info: Mapping[str, object] = {}
    if request.write_files and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(audio_path).stem
        musicxml_path = to_musicxml(score, output_dir / f"{stem}.musicxml")
        midi_path = write_midi(score, output_dir / f"{stem}.mid")
        logger.info("score files: %s, %s", musicxml_path, midi_path)
        # The tracks are optional: a missing synth must not lose the score, it
        # only means follow-along falls back to the source recording. Three are
        # rendered - the full mix, the melody alone and the accompaniment alone -
        # and they run in parallel, because each is its own fluidsynth process
        # and the wall clock then stays at one render rather than three.
        texture_mode = bool(request.texture or request.texture_plan)
        resolved_settings = settings or Settings.from_env()
        track_specs = (
            ("piano", f"{stem}.piano.wav", ("right", "left"), score),
            ("melody", f"{stem}.melody.wav", ("right",), melody_score),
            ("left", f"{stem}.left.wav", ("left",), score),
        )
        with ThreadPoolExecutor(max_workers=len(track_specs)) as pool:
            futures = {
                name: pool.submit(
                    _render_piano,
                    track_score,
                    output_dir / filename,
                    resolved_settings,
                    source_audio=audio_path,
                    texture_mode=texture_mode,
                    hands=hands,
                )
                for name, filename, hands, track_score in track_specs
            }
            piano_path, piano_info = futures["piano"].result()
            melody_path, melody_info = futures["melody"].result()
            left_path, left_info = futures["left"].result()

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
        melody_path=melody_path,
        melody=melody_info,
        left_path=left_path,
        left=left_info,
        truncated=truncated,
        analysed_seconds=total_seconds,
        transcriber=request.transcriber or DEFAULT_TRANSCRIBER,
        sections=section_payload,
        section_melody=section_melody,
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


def _sections_cache_dir(settings: Settings) -> Path:
    return Path(settings.cache_dir).parent / "sections"


def get_section_analysis(
    audio_path: Path,
    *,
    settings: Settings | None = None,
    transcriber: str = DEFAULT_TRANSCRIBER,
) -> "SectionAnalysis":
    """The song's sections, cached per audio file.

    This is what the ``/api/score/arrange/sections`` endpoint and the
    ``sectionTextures`` arrange option both call, so the detection runs once and
    the second caller pays only for the cache read. The transcription here is a
    few seconds; the Whisper vocal evidence is the slow part (30-60 s on CPU) and
    is what the cache is really protecting.

    Returns a :class:`services.arrangement.sections.SectionAnalysis`. Typed as a
    string in the signature to keep the heavy import out of the module top.
    """
    from services.arrangement.sections import (
        SectionAnalysis,
        audio_identity,
        detect_sections,
        load_cached_analysis,
        run_vocal_evidence,
        store_analysis,
    )

    resolved = settings or Settings.from_env()
    cache_dir = _sections_cache_dir(resolved)
    identity = audio_identity(Path(audio_path))

    y, sr = _load_audio(Path(audio_path))
    try:
        grid = build_bar_grid(y, sr)
    except ValueError as exc:
        raise ValidationError(
            "no steady tempo could be found, so the song cannot be segmented",
            details={"reason": str(exc)},
        ) from exc

    cached = load_cached_analysis(cache_dir, identity, grid.bar_count())
    if cached is not None:
        logger.info("section cache hit for %s", identity)
        return cached

    events = _build_transcriber(transcriber, resolved).transcribe_array(y, sr)
    if not events:
        raise ValidationError(
            "no notes were detected in this audio, so there are no sections",
            details={"transcriber": transcriber},
        )

    assignment = split_hands(events, config=HandSplitConfig(span_cap=SPAN_CAP[3]))
    buckets = hand_events_by_bar(assignment, grid)
    top_voice_by_bar = [
        [n.midi for n in buckets.get(bar, ([], []))[0]] for bar in range(grid.bar_count())
    ]
    vocal = run_vocal_evidence(Path(audio_path), grid)
    sections = detect_sections(y, sr, grid, detect_chords(y, sr), top_voice_by_bar, vocal)

    analysis = SectionAnalysis(
        sections=tuple(sections),
        vocal=vocal,
        duration_seconds=len(y) / sr,
        transcriber=transcriber,
    )
    store_analysis(cache_dir, identity, analysis, grid.bar_count())
    logger.info(
        "sections for %s: %s",
        identity,
        " / ".join(f"{s.label}[{s.start_bar}-{s.end_bar})" for s in sections),
    )
    return analysis


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
    "ACCOMPANIMENT_HINTS",
    "ARRANGE_FIDELITIES",
    "DEFAULT_ACCOMPANIMENT",
    "DEFAULT_PATTERN",
    "DEFAULT_PREVIEW_SECONDS",
    "DEFAULT_TRANSCRIBER",
    "DIFFICULTY_HINTS",
    "FIDELITY_HINTS",
    "MAX_PREVIEW_SECONDS",
    "MAX_UPLOAD_BYTES",
    "PATTERN_NAMES",
    "SAMPLE_RATE",
    "TRANSCRIBE_FIDELITIES",
    "TRANSCRIBER_HINTS",
    "TRANSCRIBER_NAMES",
    "UPLOAD_EXTENSIONS",
    "ScoreRequest",
    "ScoreResult",
    "generate_score",
    "get_section_analysis",
    "resolve_audio_path",
    "summarise",
    "summarise_midi",
    "validate_arrange_request",
    "validate_request",
    "validate_transcribe_request",
]
