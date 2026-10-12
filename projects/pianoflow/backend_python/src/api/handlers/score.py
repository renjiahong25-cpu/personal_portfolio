"""Endpoints that turn cached or uploaded audio into a playable piano score.

The analyse response carries ``score.notes``: every note with its exact start
time, duration, pitch and hand. Follow-along mode needs it, because a piano key
can only be pressed at the moment its note actually sounds, and the per-measure
note counts the score view used to have cannot say when that is.

Two entry points, one pipeline:

* ``POST /api/score/analyse`` with a cache key - the phase 1 song
* ``POST /api/score/upload``  multipart - a local file, which is normalised into
  the cache first and then scored by exactly the same code

The analysis runs in a thread. It is CPU-bound for seconds (measured 91x realtime
on the test piece, so a 4 minute song is a few seconds) and running it on the
event loop would stall every other request including the SSE progress stream.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import tempfile
from pathlib import Path
from typing import Protocol

from core.config import Settings
from core.errors import NotFoundError, ValidationError
from services.score.service import ScoreRequest, ScoreResult, generate_score, validate_request
from services.score.upload import UploadResult, store_upload

from .base import BaseHandler, api_endpoint

logger = logging.getLogger(__name__)

CACHE_KEY_PATTERN = r"([0-9a-f]{40})"
#: Refuse an upload body larger than this before reading it into memory.
UPLOAD_BODY_LIMIT = 100 * 1024 * 1024
#: Give the whole request a ceiling; the audio limit plus conversion headroom.
ANALYSIS_TIMEOUT_SECONDS = 900


def _upload_dir(settings: Settings) -> Path:
    return Path(settings.cache_dir).parent / "uploads"


def _score_dir(settings: Settings) -> Path:
    return Path(settings.cache_dir).parent / "scores"


class ScoreRunner(Protocol):
    """The analysis entry point, as the handler calls it.

    A Protocol rather than a Callable alias because ``output_dir`` is keyword
    only; the HTTP tests substitute their own double and both must match.
    """

    def __call__(
        self, audio_path: Path, request: ScoreRequest, *, output_dir: Path | None = ...
    ) -> ScoreResult: ...


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


class ScoreAnalyseHandler(BaseHandler):
    """POST /api/score/analyse {"cacheKey": ..., "fidelity": ..., ...}"""

    @api_endpoint
    async def post(self) -> None:
        payload = self.json_body()
        key = self.require_str(payload, "cacheKey")
        try:
            _entry, path = self.services.cache.resolve_audio(key)
        except KeyError as exc:
            raise NotFoundError(f"no cached audio for key {key}") from exc
        if not path.is_file():
            raise NotFoundError(f"cached audio is missing on disk: {path.name}")

        title = self._title_from_cache(key)
        request = validate_request(payload, default_title=title)
        result = await _run_analysis(path, request, self.services.settings)
        body = result.to_dict()
        body["request"] = request.to_dict()
        body["cacheKey"] = key
        self.write_json(body)

    def _title_from_cache(self, key: str) -> str:
        """Use the cached song title, so the score is named without being asked."""
        try:
            entry = self.services.cache.get_entry(key)
        except Exception:  # noqa: BLE001 - a missing title must not fail the request
            return ""
        return str(getattr(entry, "title", "") or "")


class ScoreUploadHandler(BaseHandler):
    """POST /api/score/upload  multipart/form-data

    Accepts either ``audio`` alone (store and return a key) or ``audio`` plus the
    analysis options (store and analyse in one round trip). The frontend uses the
    one-shot form so a local file behaves exactly like a downloaded song.
    """

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
            request = validate_request(options)
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
        """Read the analysis options out of the multipart form fields.

        ``transcriber`` is in this list because a field that the JSON path honours and the
        upload path silently drops is worse than one that is absent: the upload would run
        a different model than the user picked and still report success.
        """
        options: dict[str, object] = {}
        for field_name in (
            "fidelity",
            "difficulty",
            "pattern",
            "title",
            "previewSeconds",
            "transcriber",
            "texture",
            "texturePlan",
        ):
            value = self.get_body_argument(field_name, None)
            if value is not None and value.strip():
                options[field_name] = value.strip()
        return options


class ScoreMusicXmlHandler(BaseHandler):
    """GET /api/score/musicxml?cacheKey=... - the notated score, for OSMD."""

    @api_endpoint
    async def get(self) -> None:
        path = self._latest_score_path("musicxml")
        self.set_header("Content-Type", "application/vnd.recordare.musicxml+xml; charset=UTF-8")
        self.set_header("Cache-Control", "private, max-age=60")
        self.write(path.read_text(encoding="utf-8"))
        await self.flush()

    def _latest_score_path(self, suffix: str) -> Path:
        directory = _score_dir(self.services.settings)
        if not directory.is_dir():
            raise NotFoundError("no score has been generated yet")
        candidates = sorted(
            directory.glob(f"*.{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not candidates:
            raise NotFoundError("no score has been generated yet")
        return candidates[0]


class ScoreMidiHandler(BaseHandler):
    """GET /api/score/midi - the playable two-track version."""

    @api_endpoint
    async def get(self) -> None:
        directory = _score_dir(self.services.settings)
        if not directory.is_dir():
            raise NotFoundError("no score has been generated yet")
        candidates = sorted(directory.glob("*.mid"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise NotFoundError("no score has been generated yet")
        target = candidates[0]
        self.set_header("Content-Type", "audio/midi")
        self.set_header("Content-Disposition", f'attachment; filename="{target.name}"')
        with target.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                self.write(chunk)
                await self.flush()


class ScorePianoHandler(BaseHandler):
    """GET /api/score/piano - the piano track generated from the last score.

    This is the default follow-along audio. It is synthesised from ``score.notes``,
    the same list that positions the notes on the staff and lights the keyboard
    keys, so what the player hears cannot drift away from what they read.

    When no synth is installed there is nothing to serve, and the UI falls back to
    the source recording rather than showing a player with no audio behind it.
    """

    @api_endpoint
    async def get(self) -> None:
        directory = _score_dir(self.services.settings)
        if not directory.is_dir():
            raise NotFoundError("no score has been generated yet")
        candidates = sorted(
            directory.glob("*.piano.wav"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not candidates:
            raise NotFoundError(
                "no piano track is available; install fluidsynth and a SoundFont to get one"
            )
        target = candidates[0]
        # Inline, not an attachment: this is played by the audio element.
        self.set_header("Content-Type", "audio/wav")
        self.set_header("Content-Length", str(target.stat().st_size))
        self.set_header("Cache-Control", "private, max-age=60")
        self.set_header("Accept-Ranges", "bytes")
        self.write(target.read_bytes())
        await self.flush()


class ScoreOptionsHandler(BaseHandler):
    """GET /api/score/options - what the UI may offer, from one source of truth."""

    @api_endpoint
    async def get(self) -> None:
        from services.arrangement.textures import TEXTURE_LABELS
        from services.audio_analysis.transkun import TranskunTranscriber
        from services.score.service import (
            DEFAULT_TRANSCRIBER,
            DIFFICULTY_HINTS,
            FIDELITIES,
            MAX_PREVIEW_SECONDS,
            PATTERN_NAMES,
            TRANSCRIBER_HINTS,
            TRANSCRIBER_NAMES,
        )

        self.write_json(
            {
                "fidElities": list(FIDELITIES),
                "difficulties": DIFFICULTY_HINTS,
                "patterns": list(PATTERN_NAMES),
                "textures": {t.value: label for t, label in TEXTURE_LABELS.items()},
                "maxPreviewSeconds": MAX_PREVIEW_SECONDS,
                "transcribers": list(TRANSCRIBER_NAMES),
                "transcriberHints": TRANSCRIBER_HINTS,
                "defaultTranscriber": DEFAULT_TRANSCRIBER,
                # Offering a model that cannot run is worse than not offering it, so the
                # UI is told plainly whether Transkun is installed on this machine.
                "transkunAvailable": TranskunTranscriber.is_available(),
            }
        )


__all__ = [
    "ANALYSIS_TIMEOUT_SECONDS",
    "UPLOAD_BODY_LIMIT",
    "ScoreAnalyseHandler",
    "ScoreMidiHandler",
    "ScoreMusicXmlHandler",
    "ScoreOptionsHandler",
    "ScorePianoHandler",
    "ScoreUploadHandler",
]
