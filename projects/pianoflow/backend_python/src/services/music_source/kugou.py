"""KuGou Music source, fronted by the local KuGouMusicApi sidecar (port 5000).

KuGou refuses search and playback without a logged-in account, so this
extractor treats the cookie in ``data/cookies/kugou.txt`` as a hard
requirement and says so plainly when it is missing, instead of letting the
platform's ``error_code 152`` surface as a mystery 502.

The sidecar self-registers its device-fingerprint cookies (``KUGOU_API_*``)
via ``Set-Cookie``; the shared httpx client's cookie jar keeps them across
requests so the device looks the same on every call.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, ClassVar

import httpx

from core.config import Settings
from core.errors import ExtractorUnavailableError, UpstreamBlockedError, UpstreamError
from services.music_source.base import Extractor, Track
from services.music_source.netease import read_cookie_file

logger = logging.getLogger(__name__)


def _missing_cookie_message(path: str) -> str:
    return (
        "酷狗需要账号 Cookie：在浏览器登录 kugou.com 后，从请求头导出 Cookie"
        f"（至少含 token、userid、dfid 三项）填入 {path}"
    )


class KugouExtractor(Extractor):
    name: ClassVar[str] = "kugou"
    display_name: ClassVar[str] = "酷狗音乐"
    supports_download: ClassVar[bool] = True

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.kugou_sidecar_url,
                timeout=httpx.Timeout(20.0),
                follow_redirects=True,
            )
        return self._client

    def canonical_url(self, source_id: str) -> str:
        # The search page by hash always resolves to the song, unlike a guessed
        # permalink pattern that could 404.
        return f"https://www.kugou.com/search/searchSong?searchWord={source_id}"

    def cookie_configured(self) -> bool:
        return bool(read_cookie_file(self._settings.kugou_cookie_file))

    def _require_cookie(self) -> dict[str, str]:
        cookie = read_cookie_file(self._settings.kugou_cookie_file)
        if not cookie:
            raise UpstreamBlockedError(
                _missing_cookie_message(self._settings.kugou_cookie_file), retry_after=0
            )
        return {"Cookie": cookie}

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.get(path, params=params, headers=self._require_cookie())
        except httpx.HTTPError as exc:
            raise ExtractorUnavailableError(
                "酷狗 sidecar 未响应，请先运行 tools/sidecars/start-sidescars.ps1",
                details={"sidecar": self._settings.kugou_sidecar_url, "error": str(exc)},
            ) from exc
        if response.status_code >= 400:
            raise UpstreamError(f"酷狗 sidecar 返回 HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamError("酷狗 sidecar 返回了非 JSON 响应") from exc
        if not isinstance(body, dict):
            raise UpstreamError("酷狗 sidecar 响应结构异常")
        return body

    async def search(self, keyword: str, limit: int) -> list[Track]:
        body = await self._get("/search", {"keywords": keyword, "type": "song", "pagesize": limit})
        if body.get("status") != 1 or body.get("error_code") != 0:
            code = body.get("error_code")
            if code == 152:
                raise UpstreamBlockedError(
                    "酷狗拒绝了本次搜索（error_code 152）：账号 Cookie 缺失或已过期，"
                    f"请重新导出到 {self._settings.kugou_cookie_file}",
                    retry_after=0,
                )
            raise UpstreamError(
                "酷狗搜索失败", details={"errorCode": code, "errorMsg": body.get("error_msg")}
            )
        data = body.get("data") or {}
        lists = data.get("lists") or (data.get("result") or {}).get("lists") or []
        tracks: list[Track] = []
        for item in lists:
            if not isinstance(item, dict):
                continue
            track_hash = str(item.get("hash") or "").strip().lower()
            title = str(item.get("song_name") or "").strip()
            if not track_hash or not title:
                continue
            tracks.append(self._map_song(track_hash, item))
        return tracks

    def _map_song(self, track_hash: str, item: dict[str, Any]) -> Track:
        singers = [s for s in item.get("singers") or [] if isinstance(s, dict)]
        artist = str(item.get("singer_name") or "").strip() or " / ".join(
            str(s.get("name", "")).strip() for s in singers if s.get("name")
        )
        duration = item.get("duration") or item.get("timelength")
        picture = singers[0].get("image") if singers else None
        return Track(
            source="kugou",
            source_id=track_hash,
            title=item.get("song_name", "").strip(),
            artist=artist or None,
            duration=float(duration) if duration else None,
            thumbnail=str(picture) if picture else None,
            webpage_url=self.canonical_url(track_hash),
            extra={"album": item.get("album_name")},
        )

    async def audio_url(self, source_id: str) -> str:
        body = await self._get(
            "/song/url", {"hash": source_id, "quality": "320", "album_id": 0}
        )
        if body.get("status") != 1 or body.get("error_code") != 0:
            raise UpstreamError(
                "酷狗取播放地址失败",
                details={"errorCode": body.get("error_code"), "errorMsg": body.get("error_msg")},
            )
        data = body.get("data") or []
        item = data[0] if isinstance(data, list) and data else {}
        url = item.get("mp3") or item.get("ogg") if isinstance(item, dict) else None
        if not url:
            raise UpstreamBlockedError(
                "该歌曲当前无可用音频：未登录、非会员或地区受限，请检查酷狗账号 Cookie",
                retry_after=0,
            )
        return str(url)

    async def lyrics(self, source_id: str) -> str | None:
        # Two upstream hops: find the lyric record by hash, then download it.
        body = await self._get("/search/lyric", {"hash": source_id, "man": "no"})
        if body.get("status") != 1:
            return None
        payload = body.get("data")
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        payload = payload or {}
        candidate = payload.get("result") or payload
        if isinstance(candidate, list):
            candidate = candidate[0] if candidate else {}
        if not isinstance(candidate, dict):
            return None
        lyric_id = str(candidate.get("id") or "")
        accesskey = str(candidate.get("accesskey") or candidate.get("accessKey") or "")
        if not (lyric_id and accesskey):
            return None
        # Word-level KRC drives the karaoke glow; plain LRC is the fallback.
        # Both downloads share id/accesskey, so fetch them concurrently.
        krc_raw, lrc_raw = await asyncio.gather(
            self._get(
                "/lyric",
                {"id": lyric_id, "accesskey": accesskey, "fmt": "krc", "decode": "true"},
            ),
            self._get(
                "/lyric",
                {"id": lyric_id, "accesskey": accesskey, "fmt": "lrc", "decode": "true"},
            ),
        )
        krc = self._decode_payload(krc_raw)
        plain = self._decode_payload(lrc_raw)
        if not krc and not plain:
            return None
        result: dict[str, Any] = {}
        if plain:
            result["lrc"] = plain
        if krc:
            result["krc"] = krc
        return result

    @staticmethod
    def _decode_payload(body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        text = body.get("decodeContent") or body.get("content")
        if not text:
            return None
        text = str(text)
        try:
            decoded = base64.b64decode(text, validate=True)
            return decoded.decode("utf-8", errors="replace").strip() or None
        except Exception:  # noqa: BLE001 - already-decoded text falls through
            return text.strip() or None

    async def health(self) -> dict[str, Any]:
        # No search probe here: without an account cookie it would always fail,
        # and the point of this endpoint is "is the sidecar running at all".
        try:
            response = await self.client.get("/", timeout=httpx.Timeout(3.0))
        except httpx.HTTPError as exc:
            return {
                "name": self.name,
                "available": False,
                "supportsDownload": self.supports_download,
                "error": f"sidecar 未响应：{exc}",
            }
        return {
            "name": self.name,
            "available": True,
            "supportsDownload": self.supports_download,
            "cookieConfigured": self.cookie_configured(),
        }


__all__ = ["KugouExtractor"]
