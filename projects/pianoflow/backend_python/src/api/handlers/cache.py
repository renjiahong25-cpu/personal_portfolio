"""Cache endpoints: status, audio streaming and cleanup."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.errors import ValidationError

from .base import BaseHandler, api_endpoint

logger = logging.getLogger(__name__)

CLEAN_MODES = ("cleanup", "clear")

CONTENT_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4"}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 64 * 1024


class CacheStatusHandler(BaseHandler):
    """GET /api/cache/status"""

    @api_endpoint
    async def get(self) -> None:
        self.write_json(self.services.cache.stats())


class CacheAudioHandler(BaseHandler):
    """GET /api/cache/audio/<key> -> play the cached audio (Range aware)."""

    @api_endpoint
    async def get(self, key: str) -> None:
        entry, path = self.services.cache.resolve_audio(key)
        size = path.stat().st_size
        start, end, status = self._parse_range(self.request.headers.get("Range"), size)

        if status == 416:
            self.set_status(416)
            self.set_header("Content-Range", f"bytes */{size}")
            self.finish()
            return

        self.set_status(status)
        self.set_header(
            "Content-Type", CONTENT_TYPES.get(entry.audio_format, "application/octet-stream")
        )
        self.set_header("Accept-Ranges", "bytes")
        self.set_header("Content-Length", str(end - start + 1))
        self.set_header("Cache-Control", "private, max-age=3600")
        if status == 206:
            self.set_header("Content-Range", f"bytes {start}-{end}/{size}")

        await self._send_file(path, start, end - start + 1)

    @staticmethod
    def _parse_range(header: str | None, size: int) -> tuple[int, int, int]:
        if not header:
            return 0, size - 1, 200
        match = _RANGE_RE.match(header.strip())
        if not match:
            # An unparsable Range must be ignored, per RFC 9110.
            return 0, size - 1, 200
        first, last = match.group(1), match.group(2)
        try:
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                if not last:
                    return 0, size - 1, 200
                start = max(0, size - int(last))
                end = size - 1
        except ValueError as exc:
            raise ValidationError(f"malformed Range header: {header}") from exc
        end = min(end, size - 1)
        if start > end or start >= size:
            return 0, 0, 416
        return start, end, 206

    async def _send_file(self, path: Path, start: int, length: int) -> None:
        remaining = length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.write(chunk)
                remaining -= len(chunk)
                await self.flush()


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


__all__ = ["CacheAudioHandler", "CacheCleanHandler", "CacheStatusHandler"]
