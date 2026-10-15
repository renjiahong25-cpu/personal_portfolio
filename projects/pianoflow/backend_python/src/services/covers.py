"""Bounded source-cover download, also used to persist library artwork."""

from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.errors import UpstreamError, ValidationError

ALLOWED_COVER_SUFFIXES = (".hdslb.com", ".music.126.net", ".kugou.com")
REFERERS = {
    ".hdslb.com": "https://www.bilibili.com/",
    ".music.126.net": "https://music.163.com/",
    ".kugou.com": "https://www.kugou.com/",
}
MAX_COVER_BYTES = 8 * 1024 * 1024


def validate_cover_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValidationError("cover url must use https")
    host = (parsed.hostname or "").lower()
    if not host or not host.endswith(ALLOWED_COVER_SUFFIXES) or parsed.port not in (None, 443):
        raise ValidationError("cover host is not allowed")
    if parsed.username or parsed.password:
        raise ValidationError("cover url must not contain credentials")


async def fetch_cover(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    validate_cover_url(url)
    host = (urlparse(url).hostname or "").lower()
    headers = {"Referer": next(v for k, v in REFERERS.items() if host.endswith(k))}
    # Validate every redirect too: an allowed CDN must never become an SSRF relay.
    for _ in range(4):
        validate_cover_url(url)
        async with client.stream("GET", url, headers=headers, timeout=15.0,
                                 follow_redirects=False) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                raise UpstreamError(f"封面地址返回 HTTP {response.status_code}")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_COVER_BYTES:
                    raise UpstreamError("封面图片超过大小限制")
            if not chunks:
                raise UpstreamError("封面图片为空")
            content = bytes(chunks)
            # NetEase's real CDN sometimes labels JPEGs octet-stream. Check
            # raster magic bytes so a bad header neither drops valid art nor
            # permits HTML/SVG to be served as an image under our own origin.
            content_type = image_mime(content)
            if not content_type:
                raise UpstreamError("上游返回的不是受支持的封面图片")
            return content, content_type
    raise UpstreamError("封面重定向过多")


def image_mime(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


async def persist_cover(client: httpx.AsyncClient, url: str | None, root: Path, key: str) -> str | None:
    if not url:
        return None
    content, mime = await fetch_cover(client, url)
    suffix = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}[mime]
    filename = f"{key}.cover.{suffix}"
    temporary = root / f"{filename}.part"
    temporary.write_bytes(content)
    temporary.replace(root / filename)
    return filename
