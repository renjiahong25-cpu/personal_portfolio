"""Endpoints that turn cached or uploaded audio into a playable piano score.

Two product modules, one pipeline:

* **Piano transcription** - a piano recording, turned into a faithful score.
  - ``POST /api/score/transcribe``           cached song
  - ``POST /api/score/transcribe/upload``    a local file (multipart)
  - ``GET  /api/score/transcribe/options``   what the UI may offer
* **Pop to piano** - a recording, turned into a playable arrangement: the right
  hand keeps the transcribed melody, the left hand is rebuilt from patterns or
  textures.
  - ``POST /api/score/arrange``              cached song
  - ``POST /api/score/arrange/upload``       a local file (multipart)
  - ``GET  /api/score/arrange/options``      what the UI may offer

Both modules resolve the audio (a cache key, or an upload normalised into the
cache) and hand it to the same ``generate_score`` call, so a local file behaves
identically to a downloaded song. The split lives in the validation: each
module's ``validate_*_request`` refuses the other module's options, so a request
can never run a different pipeline than the one its URL promised.

The analysis runs in a thread. It is CPU-bound for seconds (measured 91x realtime
on the test piece, so a 4 minute song is a few seconds) and running it on the
event loop would stall every other request including the SSE progress stream.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import tempfile
from pathlib import Path
from typing import Protocol

from core.config import Settings
from core.errors import NotFoundError, ValidationError
from services.arrangement.arranger import ACCOMPANIMENT_NAMES
from services.arrangement.sections import (
    DEFAULT_SECTION_TEXTURES,
    INTRO_TEXTURE_NO_MOTIF,
    SECTION_LABELS,
)
from services.score.service import (
    ACCOMPANIMENT_HINTS,
    ARRANGE_FIDELITIES,
    DEFAULT_ACCOMPANIMENT,
    DEFAULT_TRANSCRIBER,
    DIFFICULTY_HINTS,
    FIDELITY_HINTS,
    MAX_PREVIEW_SECONDS,
    PATTERN_NAMES,
    TRANSCRIBE_FIDELITIES,
    TRANSCRIBER_HINTS,
    TRANSCRIBER_NAMES,
    ScoreRequest,
    ScoreResult,
    generate_score,
    get_section_analysis,
    validate_arrange_request,
    validate_transcribe_request,
)
from services.score.upload import UploadResult, store_upload

from .base import BaseHandler, api_endpoint
from .streaming import send_file_range

logger = logging.getLogger(__name__)

CACHE_KEY_PATTERN = r"([0-9a-f]{40})"
#: A cache key (sha1, from ``CacheManager.make_key``) or an upload id (sha256,
#: from ``services.score.upload.hash_file``). Hex only, so a key can be pasted
#: into a filename without escaping anything.
OUTPUT_KEY_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
#: Refuse an upload body larger than this before reading it into memory.
UPLOAD_BODY_LIMIT = 100 * 1024 * 1024
#: Give the whole request a ceiling; the audio limit plus conversion headroom.
ANALYSIS_TIMEOUT_SECONDS = 900


def _upload_dir(settings: Settings) -> Path:
    return Path(settings.cache_dir).parent / "uploads"


def _score_dir(settings: Settings) -> Path:
    return Path(settings.cache_dir).parent / "scores"


def _transcriber_options() -> dict[str, object]:
    """The transcriber block both module options endpoints share.

    Offering a model that cannot run is worse than not offering it, so the UI is
    told plainly whether Transkun is installed on this machine.
    """
    from services.audio_analysis.transkun import TranskunTranscriber

    return {
        "transcribers": list(TRANSCRIBER_NAMES),
        "transcriberHints": TRANSCRIBER_HINTS,
        "defaultTranscriber": DEFAULT_TRANSCRIBER,
        "transkunAvailable": TranskunTranscriber.is_available(),
    }


class ScoreRunner(Protocol):
    """The analysis entry point, as the handler calls it.

    A Protocol rather than a Callable alias because ``output_dir`` is keyword
    only; the HTTP tests substitute their own double and both must match.
    """

    def __call__(
        self, audio_path: Path, request: ScoreRequest, *, output_dir: Path | None = ...
    ) -> ScoreResult: ...


class RequestValidator(Protocol):
    """A module's request validator: JSON or form fields in, ScoreRequest out."""

    def __call__(
        self, payload: dict[str, object], *, default_title: str = ""
    ) -> ScoreRequest: ...


async def _run_analysis(
    path: Path, request: ScoreRequest, settings: Settings, *, runner: ScoreRunner | None = None
) -> ScoreResult:
    """Run the CPU-bound pipeline off the event loop.

    The analysis is seconds of numpy and ONNX work (measured 91x realtime on the
    test piece, so a full song is a few seconds). On the event loop that would
    stall every other request, including the SSE progress stream.

    ``runner`` exists so the HTTP contract can be tested without paying for real
    transcription; it defaults to the module-level name the tests monkeypatch.
    """
    loop = asyncio.get_running_loop()
    analyze: ScoreRunner = runner if runner is not None else generate_score
    call = functools.partial(analyze, path, request, output_dir=_score_dir(settings))
    return await asyncio.wait_for(
        loop.run_in_executor(None, call),
        timeout=ANALYSIS_TIMEOUT_SECONDS,
    )


def _resolve_cached_audio(handler: BaseHandler, key: str) -> tuple[Path, str]:
    """Resolve a cache key to a file on disk, plus the cached title as default.

    A missing title must not fail the request: the score is still valid, it is
    just named after the file rather than the song.
    """
    try:
        _entry, path = handler.services.cache.resolve_audio(key)
    except KeyError as exc:
        raise NotFoundError(f"no cached audio for key {key}") from exc
    if not path.is_file():
        raise NotFoundError(f"cached audio is missing on disk: {path.name}")
    try:
        entry = handler.services.cache.get_entry(key)
    except Exception:  # noqa: BLE001 - a missing title must not fail the request
        title = ""
    else:
        title = str(getattr(entry, "title", "") or "")
    return path, title


def _resolve_output(
    handler: BaseHandler, suffix: str, label: str, missing_message: str
) -> Path:
    """Find the generated file for ``suffix``, addressing it by cache key.

    Every score output is written as ``<cache key>.<suffix>``, because the
    pipeline names its files after the audio it analysed (``service.py``). So
    ``?cacheKey=<key>`` resolves one song's file exactly. That is the whole point:
    "whichever file is newest" can only ever serve one song, and a player with two
    tabs open on two scores - or one tab comparing a stale score against a fresh
    one - gets whichever was written last, which is not the audio the notation on
    screen belongs to.

    A caller that does not know the key keeps the old behaviour. That is not
    nostalgia: the download and the MusicXML links are opened straight from a
    browser address bar or a bookmark, with no payload to carry a key in.
    """
    key = (handler.get_query_argument("cacheKey", "") or "").strip()
    if key:
        if not OUTPUT_KEY_PATTERN.match(key):
            raise ValidationError(
                "cacheKey must be a 40- or 64-character hex cache key",
                details={"cacheKey": key},
            )
        target = _score_dir(handler.services.settings) / f"{key}.{suffix}"
        if not target.is_file():
            raise NotFoundError(f"no {label} was generated for cache key {key}")
        return target

    directory = _score_dir(handler.services.settings)
    if not directory.is_dir():
        raise NotFoundError("no score has been generated yet")
    # ``st_mtime_ns``, not ``st_mtime``: the float cannot distinguish two writes
    # within ~25 microseconds on Windows (FILETIME is past a double's mantissa),
    # so back-to-back generations would tie and "newest" would fall back to the
    # directory order, which is not the order the files were written in.
    candidates = sorted(
        directory.glob(f"*.{suffix}"), key=lambda p: p.stat().st_mtime_ns, reverse=True
    )
    if not candidates:
        raise NotFoundError(missing_message)
    return candidates[0]


class ScoreTranscribeHandler(BaseHandler):
    """POST /api/score/transcribe {"cacheKey": ..., "fidelity": "transcribed"|"as-played", ...}

    The piano module: the recording is a piano performance and the score stays
    faithful to it. Arranging fidelities and options are refused here, not
    defaulted away.
    """

    @api_endpoint
    async def post(self) -> None:
        payload = self.json_body()
        key = self.require_str(payload, "cacheKey")
        path, title = _resolve_cached_audio(self, key)
        request = validate_transcribe_request(payload, default_title=title)
        result = await _run_analysis(path, request, self.services.settings)
        body = result.to_dict()
        body["request"] = request.to_dict()
        body["cacheKey"] = key
        self.write_json(body)


class ScoreArrangeHandler(BaseHandler):
    """POST /api/score/arrange {"cacheKey": ..., "fidelity": "hybrid"|"arranged", ...}

    The pop-to-piano module: the right hand keeps the transcribed melody, the
    left hand is rebuilt from patterns or textures. Faithful fidelities are
    refused here; they belong to /api/score/transcribe.
    """

    @api_endpoint
    async def post(self) -> None:
        payload = self.json_body()
        key = self.require_str(payload, "cacheKey")
        path, title = _resolve_cached_audio(self, key)
        request = validate_arrange_request(payload, default_title=title)
        result = await _run_analysis(path, request, self.services.settings)
        body = result.to_dict()
        body["request"] = request.to_dict()
        body["cacheKey"] = key
        self.write_json(body)


class ScoreArrangeSectionsHandler(BaseHandler):
    """GET /api/score/arrange/sections?cacheKey=... - the song's detected sections.

    Runs Whisper once per song for the vocal evidence (30-60 s on CPU) and then
    returns the labelled sections with the evidence behind each label, so the UI
    can show the user *why* a section was called what before they pick textures.
    After the first run the result is served from the per-file cache.
    """

    @api_endpoint
    async def get(self) -> None:
        key = self.get_query_argument("cacheKey", None)
        if not key:
            raise ValidationError("missing required query parameter 'cacheKey'")
        path, _title = _resolve_cached_audio(self, key)
        loop = asyncio.get_running_loop()
        analysis = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(
                    get_section_analysis,
                    path,
                    settings=self.services.settings,
                ),
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
        body = analysis.to_dict()
        body["cacheKey"] = key
        self.write_json(body)


class _ScoreUploadHandler(BaseHandler):
    """Shared multipart upload: store the file in the cache, analyse on request.

    Accepts either ``audio`` alone (store and return a key) or ``audio`` plus the
    module's analysis options (store and analyse in one round trip). Subclasses
    pick the module by setting ``validator`` and ``option_fields``.

    The option list is an explicit whitelist per module: a field the JSON path
    honours and the upload path silently drops is worse than one that is absent,
    so an arranging option sent to the transcribe upload is dropped by the same
    rule as any unknown field rather than changing the pipeline that runs.
    """

    validator: object = None
    option_fields: tuple[str, ...] = ()

    @api_endpoint
    async def post(self) -> None:
        settings = self.services.settings
        upload = self.request.body
        if len(upload) > UPLOAD_BODY_LIMIT:
            raise ValidationError(
                "the upload is too large",
                details={"maxBytes": UPLOAD_BODY_LIMIT},
            )

        if not self.request.files:
            raise ValidationError("no file part found; send multipart/form-data with 'audio'")
        parts = self.request.files["audio"]
        if not parts:
            raise ValidationError("the 'audio' file part is empty")
        part = parts[0]

        suffix = Path(part.filename or "upload.bin").suffix or ".bin"
        with tempfile.TemporaryDirectory() as staging:
            staged = Path(staging) / f"upload{suffix}"
            staged.write_bytes(part["body"])

            loop = asyncio.get_running_loop()
            result: UploadResult = await loop.run_in_executor(
                None,
                lambda: store_upload(
                    staged,
                    original_name=part.filename or f"upload{suffix}",
                    upload_dir=_upload_dir(settings),
                    ffmpeg_dir=settings.ffmpeg_dir_or_none(),
                    max_seconds=settings.max_audio_seconds,
                ),
            )

        body: dict[str, object] = {"upload": result.to_dict()}
        if self._wants_analysis():
            options = self._options_from_fields()
            options.setdefault("title", result.original_name.rsplit(".", 1)[0])
            validate: RequestValidator = self.validator  # type: ignore[assignment]
            request = validate(options)
            analysis = await _run_analysis(result.path, request, settings)
            payload = analysis.to_dict()
            payload["request"] = request.to_dict()
            payload["upload"] = result.to_dict()
            self.write_json(payload)
            return
        self.write_json(body)

    def _wants_analysis(self) -> bool:
        """Analyse straight away unless the caller opted out."""
        value = self.get_body_argument("analyse", None)
        return value is None or value.strip().lower() not in ("0", "false", "no")

    def _options_from_fields(self) -> dict[str, object]:
        """Read the module's analysis options out of the multipart form fields.

        ``fillChords`` arrives as a string on this path, so it is converted to a
        real bool here: the validator refuses anything that is not one, and a
        "false" that read as truthy would flip the pipeline silently.
        """
        options: dict[str, object] = {}
        for field_name in self.option_fields:
            value = self.get_body_argument(field_name, None)
            if value is None or not value.strip():
                continue
            value = value.strip()
            if field_name == "fillChords":
                options[field_name] = value.lower() in ("1", "true", "yes")
            else:
                options[field_name] = value
        return options


class ScoreTranscribeUploadHandler(_ScoreUploadHandler):
    """POST /api/score/transcribe/upload - the piano module's upload.

    ``fillChords`` is whitelisted here (the piano module owns it) and absent from
    the arrange upload, so each module runs exactly the pipeline its options
    describe.
    """

    validator = staticmethod(validate_transcribe_request)
    option_fields = (
        "fidelity",
        "difficulty",
        "fillChords",
        "transcriber",
        "previewSeconds",
        "title",
    )


class ScoreArrangeUploadHandler(_ScoreUploadHandler):
    """POST /api/score/arrange/upload - the pop-to-piano module's upload."""

    validator = staticmethod(validate_arrange_request)
    option_fields = (
        "fidelity",
        "difficulty",
        "pattern",
        "transcriber",
        "previewSeconds",
        "title",
        "texture",
        "texturePlan",
        "accompaniment",
        "sectionTextures",
    )


class ScoreMusicXmlHandler(BaseHandler):
    """GET /api/score/musicxml?cacheKey=... - the notated score, for OSMD."""

    suffix = "musicxml"
    label = "score"
    missing_message = "no score has been generated yet"

    @api_endpoint
    async def get(self) -> None:
        path = _resolve_output(self, self.suffix, self.label, self.missing_message)
        self.set_header("Content-Type", "application/vnd.recordare.musicxml+xml; charset=UTF-8")
        self.set_header("Cache-Control", "private, max-age=60")
        self.write(path.read_text(encoding="utf-8"))
        await self.flush()


class ScoreMidiHandler(BaseHandler):
    """GET /api/score/midi - the playable two-track version (Range aware)."""

    suffix = "mid"
    label = "MIDI file"
    missing_message = "no score has been generated yet"

    @api_endpoint
    async def get(self) -> None:
        target = _resolve_output(self, self.suffix, self.label, self.missing_message)
        # A download, not inline playback, but an interrupted download must be
        # resumable: the same 206 handling the audio routes use, on the same file.
        await send_file_range(
            self,
            target,
            content_type="audio/midi",
            cache_control="private, max-age=60",
            content_disposition=f'attachment; filename="{target.name}"',
        )


class _ScoreTrackHandler(BaseHandler):
    """The generated track of one kind, served inline for the audio element.

    The score pipeline renders three kinds of track - the full mix
    (``*.piano.wav``), the melody alone (``*.melody.wav``) and the
    accompaniment alone (``*.left.wav``) - and each is served by its own route.
    All three are synthesised from ``score.notes``, the same list that positions
    the notes on the staff and lights the keyboard keys, so what the player hears
    cannot drift away from what they read.

    Addressing: ``?cacheKey=<key>`` names one song's file exactly. Without it the
    route falls back to "whichever file is newest", which is what the UI used
    before a player existed and is still right for a bare browser hit - but it can
    only ever serve one song, so two tabs on two scores would fight over it. The
    cache-busting token the score payload puts on each URL changes whenever the
    content does, which is what makes the addressed URL worth having.

    The body is streamed with the same Range handling as the cache audio route:
    a rendered track is tens of megabytes, the player seeks by asking for a byte
    window, and reading the file into memory would hold the whole thing per
    client.

    When no synth is installed there is nothing to serve, and the UI falls back
    to the source recording rather than showing a player with no audio behind it.
    """

    #: The file suffix the pipeline writes for this kind of track. The render
    #: names its output ``<key>.<suffix>``, and the tracks end in ``.wav``.
    suffix = "piano.wav"
    #: The user-facing name, for the not-found message.
    label = "piano track"

    @api_endpoint
    async def get(self) -> None:
        target = _resolve_output(self, self.suffix, self.label, self._missing_message())
        # Inline, not an attachment: this is played by the audio element.
        await send_file_range(
            self, target, content_type="audio/wav", cache_control="private, max-age=60"
        )

    def _missing_message(self) -> str:
        return f"no {self.label} is available; install fluidsynth and a SoundFont to get one"


class ScorePianoHandler(_ScoreTrackHandler):
    """GET /api/score/piano - the full mix, the default follow-along audio."""

    suffix = "piano.wav"
    label = "piano track"


class ScoreMelodyHandler(_ScoreTrackHandler):
    """GET /api/score/melody - the right hand alone, for previewing the tune."""

    suffix = "melody.wav"
    label = "melody track"


class ScoreLeftHandHandler(_ScoreTrackHandler):
    """GET /api/score/left - the left hand alone, for previewing the accompaniment."""

    suffix = "left.wav"
    label = "left-hand track"


class ScoreTranscribeOptionsHandler(BaseHandler):
    """GET /api/score/transcribe/options - what the piano module may offer."""

    @api_endpoint
    async def get(self) -> None:
        self.write_json(
            {
                "fidelities": list(TRANSCRIBE_FIDELITIES),
                "fidelityHints": {
                    fidelity: FIDELITY_HINTS[fidelity] for fidelity in TRANSCRIBE_FIDELITIES
                },
                "difficulties": DIFFICULTY_HINTS,
                "maxPreviewSeconds": MAX_PREVIEW_SECONDS,
                **_transcriber_options(),
            }
        )


class ScoreArrangeOptionsHandler(BaseHandler):
    """GET /api/score/arrange/options - what the pop-to-piano module may offer."""

    @api_endpoint
    async def get(self) -> None:
        from services.arrangement.textures import TEXTURE_LABELS

        self.write_json(
            {
                "fidelities": list(ARRANGE_FIDELITIES),
                "fidelityHints": {
                    fidelity: FIDELITY_HINTS[fidelity] for fidelity in ARRANGE_FIDELITIES
                },
                "difficulties": DIFFICULTY_HINTS,
                "patterns": list(PATTERN_NAMES),
                "textures": {t.value: label for t, label in TEXTURE_LABELS.items()},
                "accompaniments": list(ACCOMPANIMENT_NAMES),
                "accompanimentHints": ACCOMPANIMENT_HINTS,
                "defaultAccompaniment": DEFAULT_ACCOMPANIMENT,
                "sectionLabels": list(SECTION_LABELS),
                # Machine-readable: the intro is the one conditional default, so
                # its no-motif value travels under its own key.
                "sectionTexturesDefaults": {
                    **dict(DEFAULT_SECTION_TEXTURES),
                    "introNoMotif": INTRO_TEXTURE_NO_MOTIF,
                },
                "maxPreviewSeconds": MAX_PREVIEW_SECONDS,
                **_transcriber_options(),
            }
        )


__all__ = [
    "ANALYSIS_TIMEOUT_SECONDS",
    "UPLOAD_BODY_LIMIT",
    "ScoreArrangeHandler",
    "ScoreArrangeOptionsHandler",
    "ScoreArrangeSectionsHandler",
    "ScoreArrangeUploadHandler",
    "ScoreLeftHandHandler",
    "ScoreMelodyHandler",
    "ScoreMidiHandler",
    "ScoreMusicXmlHandler",
    "ScorePianoHandler",
    "ScoreTranscribeHandler",
    "ScoreTranscribeOptionsHandler",
    "ScoreTranscribeUploadHandler",
]
