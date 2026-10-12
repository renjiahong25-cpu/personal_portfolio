"""Bilibili source.

Search goes through :class:`~services.music_source.bilibili_client.BilibiliClient`
(one request, real anti-412 buvid handling). Audio extraction is still yt-dlp's
job because it already implements the signed ``/x/player/wbi/playurl`` endpoint;
we hand it the same cookie jar so downloads are not risk-controlled either.

YouTube is deliberately unsupported: it is unreachable from this machine.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.config import Settings
from core.errors import AppError
from services.music_source.base import Extractor, Track
from services.music_source.bilibili_client import BilibiliClient

logger = logging.getLogger(__name__)


class BilibiliExtractor(Extractor):
    name: ClassVar[str] = "bilibili"
    display_name: ClassVar[str] = "哔哩哔哩"
    supports_download: ClassVar[bool] = True

    WEB_ORIGIN: ClassVar[str] = "https://www.bilibili.com"

    def __init__(self, settings: Settings, client: BilibiliClient | None = None) -> None:
        self._settings = settings
        self._client = client or BilibiliClient(settings)

    @property
    def client(self) -> BilibiliClient:
        return self._client

    def canonical_url(self, source_id: str) -> str:
        return f"{self.WEB_ORIGIN}/video/{source_id}"

    def cookie_file(self) -> str | None:
        """Cookie jar shared with yt-dlp for the download leg."""
        if self._settings.ytdlp_cookie_file:
            return self._settings.ytdlp_cookie_file
        return self._client.cookie_file()

    async def search(self, keyword: str, limit: int) -> list[Track]:
        return await self._client.search(keyword, limit)

    async def health(self) -> dict[str, Any]:
        try:
            tracks = await self.search("星辰大海", 1)
        except AppError as exc:
            return {
                "name": self.name,
                "available": False,
                "supportsDownload": self.supports_download,
                "error": exc.message,
            }
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {
                "name": self.name,
                "available": False,
                "supportsDownload": self.supports_download,
                "error": str(exc),
            }
        return {
            "name": self.name,
            "available": bool(tracks),
            "supportsDownload": self.supports_download,
            "cookieConfigured": bool(self.cookie_file()),
        }


__all__ = ["BilibiliExtractor"]
