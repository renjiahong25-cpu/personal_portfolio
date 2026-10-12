"""URL routing table and SPA static hosting."""

from __future__ import annotations

import json
import logging
from mimetypes import guess_type
from pathlib import Path

import tornado.web

from container import Services, build_services
from core.config import Settings
from core.paths import FRONTEND_DIST

from .handlers.cache import CacheAudioHandler, CacheCleanHandler, CacheStatusHandler
from .handlers.health import HealthHandler
from .handlers.media import CoverHandler
from .handlers.music import (
    DownloadHandler,
    DownloadStreamHandler,
    DownloadTaskHandler,
    EventsHandler,
    QueueHandler,
    SearchHandler,
)
from .handlers.score import (
    ScoreAnalyseHandler,
    ScoreMidiHandler,
    ScoreMusicXmlHandler,
    ScoreOptionsHandler,
    ScorePianoHandler,
    ScoreUploadHandler,
)

logger = logging.getLogger(__name__)

TASK_ID_PATTERN = r"([0-9a-f]{32})"

#: Used when the MIME database has no entry. Vite emits .js/.css/.svg/.woff2,
#: and Windows' registry-driven mimetypes module is not guaranteed to know them.
_EXTRA_TYPES = {
    ".js": "text/javascript; charset=UTF-8",
    ".mjs": "text/javascript; charset=UTF-8",
    ".css": "text/css; charset=UTF-8",
    ".json": "application/json; charset=UTF-8",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=UTF-8",
}
_FALLBACK_TYPE = "application/octet-stream"


def content_type_for(path: Path) -> str:
    """MIME type for a built asset.

    This must be right or the page is blank: a browser handed a script labelled
    ``text/html`` refuses to execute it and logs a MIME mismatch, so the SPA never
    mounts. Tornado defaults to ``text/html`` when no type is set, which is what
    the static handler used to do.
    """
    suffix = path.suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    guessed, _encoding = guess_type(path.name)
    return guessed or _FALLBACK_TYPE


class SpaHandler(tornado.web.RequestHandler):
    """Serve the built Vue app, falling back to index.html for client routes.

    An unmatched ``/api/...`` path must not fall through to the SPA: returning
    index.html with HTTP 200 turns a typo'd endpoint into
    ``Unexpected token '<' in JSON`` on the client and sends debugging in the
    wrong direction.
    """

    API_PREFIX = "/api/"

    def initialize(self) -> None:
        self._root = FRONTEND_DIST

    async def get(self, path: str = "") -> None:
        if f"/{path.lstrip('/')}".startswith(self.API_PREFIX):
            self.set_status(404)
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            self.finish(
                json.dumps(
                    {
                        "error": {
                            "code": "not_found",
                            "message": f"unknown endpoint: /{path.lstrip('/')}",
                        }
                    },
                    ensure_ascii=False,
                )
            )
            return

        index = self._root / "index.html"
        if not index.is_file():
            self.set_status(503)
            self.set_header("Content-Type", "text/plain; charset=UTF-8")
            self.finish(
                "frontend is not built yet.\n"
                "Run: cd frontend && npm install && npm run build\n"
                "Or use the Vite dev server on http://127.0.0.1:5173\n"
            )
            return

        candidate = (self._root / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self._root.resolve())
        except ValueError:
            candidate = index
        if not candidate.is_file():
            candidate = index
        self.set_header("Cache-Control", "no-cache")
        self.set_header("Content-Type", content_type_for(candidate))
        self.set_header("Content-Length", str(candidate.stat().st_size))
        with candidate.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                self.write(chunk)
        await self.flush()


def make_routes() -> list[tuple[str, type[tornado.web.RequestHandler]]]:
    return [
        (r"/api/health", HealthHandler),
        (r"/api/media/cover", CoverHandler),
        (r"/api/music/search", SearchHandler),
        (r"/api/events", EventsHandler),
        (r"/api/music/download-audio", DownloadHandler),
        (r"/api/music/download-audio/queue", QueueHandler),
        (r"/api/music/download-audio/stream", DownloadStreamHandler),
        (rf"/api/music/download-audio/{TASK_ID_PATTERN}", DownloadTaskHandler),
        (r"/api/cache/status", CacheStatusHandler),
        (r"/api/cache/audio/([0-9a-f]{40})", CacheAudioHandler),
        (r"/api/cache/clean", CacheCleanHandler),
        (r"/api/score/options", ScoreOptionsHandler),
        (r"/api/score/analyse", ScoreAnalyseHandler),
        (r"/api/score/upload", ScoreUploadHandler),
        (r"/api/score/musicxml", ScoreMusicXmlHandler),
        (r"/api/score/midi", ScoreMidiHandler),
        (r"/api/score/piano", ScorePianoHandler),
        (r"/(.*)", SpaHandler),
    ]


def make_app(settings: Settings, services: Services | None = None) -> tornado.web.Application:
    resolved = services or build_services(settings)
    return tornado.web.Application(
        make_routes(),
        services=resolved,
        static_path=None,
        serve_traceback=False,
        autoreload=False,
    )


__all__ = ["SpaHandler", "content_type_for", "make_app", "make_routes"]
