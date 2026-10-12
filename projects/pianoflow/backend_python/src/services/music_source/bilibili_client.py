"""Native Bilibili search client.

Why this exists instead of just calling yt-dlp's ``bilisearch``:

* yt-dlp's search resolves **every** entry individually (~1s each) and, in flat
  mode, returns bare ids with no title/cover at all.
* ``/x/web-interface/search/type`` returns exactly the fields a preview card
  needs (title, cover, duration, author, play count) in **one** request.
* Risk control: Bilibili answers requests without a believable device id with
  ``HTTP 412`` / ``code -412``. The community-standard mitigation — and what
  this module implements — is to fetch real ``buvid3``/``buvid4`` from
  ``/x/frontend/finger/spi``, send desktop headers, keep the cookie jar across
  requests, and space calls out.

Downloading is still delegated to yt-dlp (it already handles the signed
``/x/player/wbi/playurl`` endpoint); we hand it the same cookie jar.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from core.config import Settings
from core.errors import UpstreamBlockedError, UpstreamError
from core.throttle import MinIntervalThrottle
from services.media_headers import DESKTOP_HEADERS
from services.music_source.base import Track

logger = logging.getLogger(__name__)

BASE_HEADERS = DESKTOP_HEADERS

SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"

#: Bilibili risk-control codes that mean "slow down / you look like a bot".
BLOCKED_CODES = frozenset({-412, -799, -352})
COOKIE_DOMAIN = ".bilibili.com"
COOKIE_NAMES = ("buvid3", "buvid4")
_ONE_YEAR_SECONDS = 365 * 24 * 3600

_TAG_RE = re.compile(r"<[^>]+>")
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")


def parse_duration(value: Any) -> float | None:
    """Parse Bilibili's ``M:SS`` / ``H:MM:SS`` duration strings."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    total = int(minutes) * 60 + int(seconds)
    if hours:
        total += int(hours) * 3600
    return float(total) if total > 0 else None


def clean_title(raw: Any) -> str:
    """Strip the ``<em class="keyword">`` highlights and unescape entities."""
    if not isinstance(raw, str):
        return ""
    return html.unescape(_TAG_RE.sub("", raw)).strip()


class BilibiliClient:
    """Async search client with a persistent buvid cookie jar."""

    source = "bilibili"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._cookie_file = Path(settings.bilibili_cookie_file)
        self._lock = asyncio.Lock()
        self._throttle = MinIntervalThrottle(settings.search_min_interval_seconds)

    # ------------------------------------------------------------- lifecycle
    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.ytdlp_socket_timeout_seconds),
                headers=BASE_HEADERS,
                follow_redirects=True,
                transport=self._transport,
            )
            self._apply_cookie_file()
            if not self._has_buvid():
                await self.refresh_buvid()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def cookie_file(self) -> str | None:
        """Path to the Netscape cookie file, if one exists on disk."""
        return str(self._cookie_file) if self._cookie_file.is_file() else None

    # --------------------------------------------------------------- cookies
    def _has_buvid(self) -> bool:
        if self._client is None:
            return False
        return all(self._client.cookies.get(name) for name in COOKIE_NAMES[:1])

    def _apply_cookie_file(self) -> None:
        if self._client is None or not self._cookie_file.is_file():
            return
        for name, value in self._read_cookie_file().items():
            self._client.cookies.set(name, value, domain=COOKIE_DOMAIN, path="/")

    def _read_cookie_file(self) -> dict[str, str]:
        found: dict[str, str] = {}
        try:
            for line in self._cookie_file.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7 and parts[-2] in COOKIE_NAMES:
                    found[parts[-2]] = parts[-1]
        except OSError as exc:
            logger.warning("cannot read cookie file %s: %s", self._cookie_file, exc)
        return found

    def _write_cookie_file(self, cookies: dict[str, str]) -> None:
        if not cookies:
            return
        try:
            self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# Netscape HTTP Cookie File", "# Generated by PianoFlow"]
            for name, value in cookies.items():
                # domain, include subdomains, path, secure, expires, name, value
                lines.append(
                    f"{COOKIE_DOMAIN}\tTRUE\t/\tFALSE\t{_ONE_YEAR_SECONDS}\t{name}\t{value}"
                )
            self._cookie_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot persist cookie file %s: %s", self._cookie_file, exc)

    async def refresh_buvid(self) -> dict[str, str]:
        """Fetch a fresh device id pair; this is the anti-412 prerequisite."""
        async with self._lock:
            client = await self._ensure_client()
            try:
                response = await client.get(SPI_URL)
                response.raise_for_status()
                data = response.json().get("data") or {}
            except (httpx.HTTPError, ValueError) as exc:
                raise UpstreamError(f"无法获取 B 站设备标识 (spi): {exc}") from exc

            cookies = {
                "buvid3": str(data.get("b_3") or ""),
                "buvid4": str(data.get("b_4") or ""),
            }
            cookies = {k: v for k, v in cookies.items() if v}
            if not cookies:
                raise UpstreamError("B 站 spi 未返回 buvid，无法建立会话")

            for name, value in cookies.items():
                client.cookies.set(name, value, domain=COOKIE_DOMAIN, path="/")
            self._write_cookie_file(cookies)
            logger.info("bilibili buvid refreshed: %s", sorted(cookies))
            return cookies

    # ---------------------------------------------------------------- search
    async def search(self, keyword: str, limit: int) -> list[Track]:
        client = await self._ensure_client()
        await self._throttle.wait()

        params: dict[str, str | int] = {
            "search_type": "video",
            "keyword": keyword,
            "page": 1,
            "order": "",
            "duration": 0,
            "tids": 0,
        }
        try:
            response = await client.get(SEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"B 站搜索请求失败: {exc}") from exc

        self._raise_for_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(f"B 站返回了非 JSON 内容: {exc}") from exc

        code = payload.get("code")
        if code != 0:
            if code in BLOCKED_CODES:
                raise UpstreamBlockedError(
                    "平台风控拦截了本次搜索（B 站风控码 "
                    f"{code}）。请等待 1-2 分钟后再试，避免连续搜索。",
                    retry_after=90.0,
                )
            raise UpstreamError(
                f"B 站搜索失败 (code={code}): {payload.get('message') or '未知错误'}"
            )

        results = (payload.get("data") or {}).get("result") or []
        tracks: list[Track] = []
        for item in results:
            track = self._to_track(item)
            if track is not None:
                tracks.append(track)
            if len(tracks) >= limit:
                break
        return tracks

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code != 412 and response.is_success:
            return
        if response.status_code == 412:
            raise UpstreamBlockedError(
                "平台风控拦截了本次搜索（HTTP 412）。请等待 1-2 分钟后再试，"
                "或配置登录 cookie 降低被拦概率。",
                retry_after=90.0,
            )
        raise UpstreamError(f"B 站返回 HTTP {response.status_code}")

    @staticmethod
    def _to_track(item: dict[str, Any]) -> Track | None:
        if not isinstance(item, dict):
            return None
        source_id = str(item.get("bvid") or "").strip()
        title = clean_title(item.get("title"))
        if not source_id or not title:
            return None
        author = item.get("author")
        return Track(
            source="bilibili",
            source_id=source_id,
            title=title,
            artist=str(author) if author else None,
            duration=parse_duration(item.get("duration")),
            thumbnail=_normalise_pic(item.get("pic")),
            webpage_url=str(item.get("arcurl") or f"https://www.bilibili.com/video/{source_id}"),
            extra={
                "play": item.get("play"),
                "danmaku": item.get("video_review"),
                "pubdate": item.get("pubdate"),
            },
        )


def _normalise_pic(pic: Any) -> str | None:
    """Bilibili returns covers as ``//i0.hdslb.com/...`` or plain ``http://``.

    Both forms need fixing up: the browser refuses protocol-relative URLs over
    https (mixed content) and plain http would be blocked as mixed content too.
    """
    if not isinstance(pic, str) or not pic.strip():
        return None
    value = pic.strip()
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("http://"):
        return f"https://{value[len('http://') :]}"
    if value.startswith("https://"):
        return value
    return None


__all__ = ["BLOCKED_CODES", "BilibiliClient", "clean_title", "parse_duration"]
