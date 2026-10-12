"""Media proxy endpoints.

Bilibili's cover CDN rejects requests whose ``Referer`` is not bilibili.com
(anti-hotlink). A browser loading ``https://i0.hdslb.com/...`` from our own
page therefore gets 403 and the cards render blank, so covers are fetched
server-side with a correct Referer and re-served to the browser.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from core.errors import UpstreamError, ValidationError

from .base import BaseHandler, api_endpoint

logger = logging.getLogger(__name__)

#: Only Bilibili's own image CDN may be proxied (SSRF guard).
ALLOWED_COVER_SUFFIXES = (".hdslb.com",)
REFERER = "https://www.bilibili.com/"


class CoverHandler(BaseHandler):
    """GET /api/media/cover?url=<https://i0.hdslb.com/...>"""

    @api_endpoint
    async def get(self) -> None:
        url = self.get_argument("url", "").strip()
        _validate_cover_url(url)

        try:
            response = await self.services.http.get(url, headers={"Referer": REFERER}, timeout=15.0)
        except Exception as exc:
            raise UpstreamError(f"封面获取失败: {exc}") from exc

        if response.status_code != 200 or not response.content:
            logger.warning("cover upstream returned %s for %s", response.status_code, url[:80])
            self.set_status(404)
            self.set_header("Content-Type", "text/plain; charset=UTF-8")
            self.finish("cover unavailable")
            return

        content_type = response.headers.get("content-type", "image/jpeg")
        if not content_type.startswith("image/"):
            raise UpstreamError(f"上游返回的不是图片: {content_type}")

        self.set_header("Content-Type", content_type)
        # The upstream URL already carries a content hash, so it is safe to let
        # the browser cache it hard.
        self.set_header("Cache-Control", "public, max-age=604800, immutable")
        self.write(response.content)


def _validate_cover_url(url: str) -> None:
    if not url:
        raise ValidationError("query parameter 'url' is required")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValidationError("cover url must use https")
    host = (parsed.hostname or "").lower()
    if not host or not host.endswith(ALLOWED_COVER_SUFFIXES):
        raise ValidationError(
            "cover host is not allowed", details={"allowed": list(ALLOWED_COVER_SUFFIXES)}
        )


__all__ = ["ALLOWED_COVER_SUFFIXES", "CoverHandler"]
