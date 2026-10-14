"""Cache endpoints: status, audio streaming and cleanup."""

from __future__ import annotations

import logging

from core.errors import ValidationError

from .base import BaseHandler, api_endpoint
from .streaming import send_file_range

logger = logging.getLogger(__name__)

CLEAN_MODES = ("cleanup", "clear")

#: What each cached format is served as. The browser picks its decoder from this
#: and nothing else, so a format that is missing here plays as silence rather than
#: as an error - which is why lossless and streaming-container formats are listed
#: even though the download path only ever produces wav and mp3 today
#: (``services.download.audio_service.SUPPORTED_FORMATS``). They are here for the
#: formats an upload or a hand-placed file can carry, not for formats the app
#: claims to produce.
CONTENT_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "opus": "audio/ogg",
}


class CacheStatusHandler(BaseHandler):
    """GET /api/cache/status"""

    @api_endpoint
    async def get(self) -> None:
        self.write_json(self.services.cache.stats())


class CacheAudioHandler(BaseHandler):
    """GET /api/cache/audio/<key> -> play the cached audio (Range aware).

    The browser is what drives the Range protocol here: an ``<audio>`` element
    seeks by asking for a window of bytes, and a 60 MB wav cannot be answered by
    reading the file into memory, so the body is streamed in chunks.
    """

    @api_endpoint
    async def get(self, key: str) -> None:
        entry, path = self.services.cache.resolve_audio(key)
        await send_file_range(
            self,
            path,
            content_type=CONTENT_TYPES.get(entry.audio_format, "application/octet-stream"),
            cache_control="private, max-age=3600",
        )


class CacheCleanHandler(BaseHandler):
    """POST /api/cache/clean {"mode": "cleanup" | "clear"}"""

    @api_endpoint
    async def post(self) -> None:
        payload = self.json_body()
        mode = str(payload.get("mode") or "cleanup").strip().lower()
        if mode not in CLEAN_MODES:
            raise ValidationError(
                f"unsupported mode {mode!r}", details={"supported": list(CLEAN_MODES)}
            )
        cache = self.services.cache
        report = cache.clear() if mode == "clear" else cache.cleanup()
        self.write_json({"mode": mode, **report.to_dict()})


__all__ = ["CONTENT_TYPES", "CacheAudioHandler", "CacheCleanHandler", "CacheStatusHandler"]
