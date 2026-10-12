"""Liveness endpoint: GET /api/health"""

from __future__ import annotations

import asyncio
import shutil

from core.paths import FRONTEND_DIST

from .base import BaseHandler, api_endpoint


class HealthHandler(BaseHandler):
    @api_endpoint
    async def get(self) -> None:
        settings = self.services.settings
        ffmpeg_dir = settings.ffmpeg_dir_or_none()
        extractors = await asyncio.gather(
            *(extractor.health() for extractor in self.services.extractors.all()),
            return_exceptions=True,
        )
        sources = []
        for extractor, outcome in zip(self.services.extractors.all(), extractors, strict=True):
            if isinstance(outcome, BaseException):
                sources.append(
                    {
                        "name": extractor.name,
                        "available": False,
                        "error": str(outcome),
                    }
                )
            else:
                sources.append(outcome)

        self.write_json(
            {
                "status": "ok",
                "version": "0.9.0-phase1",
                "sources": sources,
                "ffmpeg": {
                    "configured": bool(settings.ffmpeg_location),
                    "resolved": ffmpeg_dir,
                    "onPath": shutil.which("ffmpeg"),
                },
                "frontendBuilt": (FRONTEND_DIST / "index.html").is_file(),
                "externalAiEnabled": settings.enable_external_ai,
            }
        )


__all__ = ["HealthHandler"]
