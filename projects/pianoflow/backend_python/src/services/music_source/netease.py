"""NetEase Cloud Music source, fronted by the local api-enhanced sidecar.

The sidecar (``tools/sidecars/netease``, port 4000) owns the platform's
request signing; this extractor only translates between the sidecar's
pass-through JSON and the shared :class:`Track` contract, plus the direct
audio-URL route the download leg needs.

An account cookie (``data/cookies/netease.txt``) is optional: free tracks
resolve a 320 k mp3 without login. A VIP-only track comes back with
``url: null`` and is surfaced as a clear "cookie needed" error rather than a
silent empty download.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any, ClassVar

import httpx

from core.config import Settings
from core.errors import ExtractorUnavailableError, UpstreamBlockedError, UpstreamError
from services.music_source.base import Extractor, Track

logger = logging.getLogger(__name__)

WEB_ORIGIN = "https://music.163.com"
#: The search endpoint omits the album cover; one batched song/detail call
#: over every search result recovers it cheaply.
_DETAIL_CAP = 50


def read_cookie_file(path: str) -> str:
    """The first non-comment line of a cookie file, or '' when absent/empty."""
    file = Path(path)
    if not file.is_file():
        return ""
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


class NeteaseExtractor(Extractor):
    name: ClassVar[str] = "netease"
    display_name: ClassVar[str] = "网易云音乐"
    supports_download: ClassVar[bool] = True

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.netease_sidecar_url,
                timeout=httpx.Timeout(20.0),
                follow_redirects=True,
            )
        return self._client

    def canonical_url(self, source_id: str) -> str:
        return f"{WEB_ORIGIN}/#/song?id={source_id}"

    def cookie_configured(self) -> bool:
        return bool(read_cookie_file(self._settings.netease_cookie_file))

    def _headers(self) -> dict[str, str]:
        cookie = read_cookie_file(self._settings.netease_cookie_file)
        return {"Cookie": cookie} if cookie else {}

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.get(path, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ExtractorUnavailableError(
                "网易云 sidecar 未响应，请先运行 tools/sidecars/start-sidescars.ps1",
                details={"sidecar": self._settings.netease_sidecar_url, "error": str(exc)},
            ) from exc
        if response.status_code >= 400:
            raise UpstreamError(f"网易云 sidecar 返回 HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError("网易云 sidecar 返回了非 JSON 响应") from exc
        if not isinstance(body, dict):
            raise UpstreamError("网易云 sidecar 响应结构异常")
        return body

    async def search(self, keyword: str, limit: int) -> list[Track]:
        body = await self._get("/search", {"keywords": keyword, "type": 1, "limit": limit, "offset": 0})
        if body.get("code") != 200:
            raise UpstreamError("网易云搜索失败", details={"code": body.get("code")})
        songs = (body.get("result") or {}).get("songs") or []
        tracks = [
            self._map_song(song)
            for song in songs
            if isinstance(song, dict) and song.get("id") and str(song.get("name", "")).strip()
        ]
        await self._enrich_with_details(tracks)
        return tracks

    @staticmethod
    def _map_song(song: dict[str, Any]) -> Track:
        artists = " / ".join(
            str(a.get("name", "")).strip() for a in song.get("artists") or [] if a.get("name")
        )
        duration_ms = song.get("duration")
        return Track(
            source="netease",
            source_id=str(song["id"]),
            title=str(song.get("name") or "").strip(),
            artist=artists or None,
            duration=duration_ms / 1000.0 if duration_ms else None,
            thumbnail=None,
            webpage_url=f"{WEB_ORIGIN}/#/song?id={song['id']}",
            extra={"fee": song.get("fee")},
        )

    async def _enrich_with_details(self, tracks: list[Track]) -> None:
        """Fill in the album cover; a failing detail call must not kill search."""
        if not tracks:
            return
        ids = ",".join(t.source_id for t in tracks[:_DETAIL_CAP])
        try:
            body = await self._get("/song/detail", {"ids": ids})
        except (ExtractorUnavailableError, UpstreamError):
            return
        detail_by_id = {
            str(song.get("id")): song for song in body.get("songs") or [] if isinstance(song, dict)
        }
        enriched = []
        for track in tracks:
            song = detail_by_id.get(track.source_id)
            picture = (song.get("al") or {}).get("picUrl") if song else None
            if picture:
                track = dataclasses.replace(track, thumbnail=str(picture))
            enriched.append(track)
        tracks[:] = enriched

    async def audio_url(self, source_id: str) -> str:
        body = await self._get("/song/url", {"id": source_id, "br": 999000})
        data = body.get("data") or []
        item = data[0] if isinstance(data, list) and data else {}
        url = item.get("url") if isinstance(item, dict) else None
        if not url:
            fee = item.get("fee") if isinstance(item, dict) else None
            if fee in (1, 4):
                raise UpstreamBlockedError(
                    "该歌曲为 VIP/付费曲目：把浏览器登录网易云后导出的 Cookie 填入 "
                    f"{self._settings.netease_cookie_file} 再试",
                    retry_after=0,
                    details={"fee": fee},
                )
            raise UpstreamBlockedError(
                "该歌曲当前无可用音频（可能地区受限或已下架）",
                retry_after=0,
            )
        return str(url)

    async def lyrics(self, source_id: str) -> dict[str, Any] | None:
        body = await self._get("/lyric", {"id": source_id})
        lrc = body.get("lrc")
        text = lrc.get("lyric") if isinstance(lrc, dict) else lrc
        plain = str(text).strip() if text is not None else ""
        if not plain:
            return None
        result: dict[str, Any] = {"lrc": plain}
        # NetEase karaoke JSON (per-word timing). Anonymous sessions get an
        # empty payload; only echo it when it actually carries words.
        klyric = body.get("klyric")
        if isinstance(klyric, dict):
            klyric_text = str(klyric.get("lyric") or "").strip()
            if klyric_text:
                result["klyric"] = klyric_text
        elif klyric:
            result["klyric"] = str(klyric)
        return result

    async def health(self) -> dict[str, Any]:
        try:
            await self._get("/search", {"keywords": "test", "type": 1, "limit": 1})
        except ExtractorUnavailableError as exc:
            return {
                "name": self.name,
                "available": False,
                "supportsDownload": self.supports_download,
                "error": exc.message,
            }
        except UpstreamError as exc:
            return {
                "name": self.name,
                "available": False,
                "supportsDownload": self.supports_download,
                "error": exc.message,
            }
        return {
            "name": self.name,
            "available": True,
            "supportsDownload": self.supports_download,
            "cookieConfigured": self.cookie_configured(),
        }


__all__ = ["NeteaseExtractor", "read_cookie_file"]
