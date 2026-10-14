"""Cover proxy: anti-hotlink Referer plus a strict SSRF allow-list."""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from pathlib import Path

import httpx
from tornado.testing import AsyncHTTPTestCase

from api.routes import make_app
from container import build_services
from core.config import Settings

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class CoverProxyTestCase(AsyncHTTPTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pianoflow-cover-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.settings = dataclasses.replace(
            Settings.from_env(),
            cache_dir=self._tmp / "cache",
            log_dir=self._tmp / "logs",
        )
        self.services = build_services(self.settings)
        self.seen: list[httpx.Request] = []
        super().setUp()

    def get_app(self):
        return make_app(self.settings, self.services)

    def install_upstream(
        self,
        *,
        status: int = 200,
        content: bytes = PNG,
        content_type: str = "image/png",
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            return httpx.Response(
                status,
                content=content,
                headers={"content-type": content_type},
            )

        await_close = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addCleanup(lambda: None)
        self.services.http = await_close

    # --------------------------------------------------------------- tests
    def test_missing_url_is_rejected(self):
        response = self.fetch("/api/media/cover", raise_error=False)
        self.assertEqual(response.code, 400)

    def test_non_https_is_rejected(self):
        response = self.fetch("/api/media/cover?url=http://i0.hdslb.com/a.jpg", raise_error=False)
        self.assertEqual(response.code, 400)

    def test_foreign_host_is_rejected(self):
        for url in (
            "https://evil.example.com/a.jpg",
            "https://hdslb.com.evil.com/a.jpg",
            "https://localhost/a.jpg",
        ):
            response = self.fetch(f"/api/media/cover?url={url}", raise_error=False)
            self.assertEqual(response.code, 400, url)

    def test_proxy_forwards_bilibili_referer(self):
        self.install_upstream()
        response = self.fetch("/api/media/cover?url=https://i0.hdslb.com/bfs/archive/cover.jpg")

        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, PNG)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0].headers["referer"], "https://www.bilibili.com/")

    def test_proxy_forwards_netease_referer(self):
        self.install_upstream()
        response = self.fetch(
            "/api/media/cover?url=https://p3.music.126.net/abc/cover.jpg"
        )

        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, PNG)
        self.assertEqual(self.seen[0].headers["referer"], "https://music.163.com/")

    def test_proxy_forwards_kugou_referer(self):
        self.install_upstream()
        response = self.fetch(
            "/api/media/cover?url=https://m1.kugou.com/upload/cover.jpg"
        )

        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, PNG)
        self.assertEqual(self.seen[0].headers["referer"], "https://www.kugou.com/")

    def test_suffix_spoofing_is_rejected(self):
        for url in (
            "https://music.126.net.evil.com/a.jpg",
            "https://kugou.com.evil.com/a.jpg",
        ):
            response = self.fetch(f"/api/media/cover?url={url}", raise_error=False)
            self.assertEqual(response.code, 400, url)

    def test_cover_is_cacheable(self):
        self.install_upstream()
        response = self.fetch("/api/media/cover?url=https://i0.hdslb.com/bfs/archive/cover.jpg")
        self.assertIn("immutable", response.headers["Cache-Control"])

    def test_upstream_404_becomes_404(self):
        self.install_upstream(status=404, content=b"", content_type="text/plain")
        response = self.fetch(
            "/api/media/cover?url=https://i0.hdslb.com/bfs/archive/gone.jpg",
            raise_error=False,
        )
        self.assertEqual(response.code, 404)

    def test_non_image_payload_is_rejected(self):
        self.install_upstream(content_type="text/html", content=b"<html>403</html>")
        response = self.fetch(
            "/api/media/cover?url=https://i0.hdslb.com/bfs/archive/x.jpg",
            raise_error=False,
        )
        self.assertEqual(response.code, 502)

    def test_queue_endpoint_shape(self):
        response = self.fetch("/api/music/download-audio/queue")
        import json

        body = json.loads(response.body)
        self.assertEqual(response.code, 200)
        self.assertEqual(set(body) >= {"active", "queued", "queue", "runningUids"}, True)
