"""Loopback-only static host for the built three.js bundle and the VRM model.

QWebEngineView refuses to fetch file:// subresources from an http:// page, and Chromium
blocks WebGL on some file:// setups, so the bundle is served from a real HTTP origin. The
server binds 127.0.0.1 on an ephemeral port, serves only three allow-listed roots, and
answers POST /__c03 as the JavaScript -> Python command channel.
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

BRIDGE_PATH = "/__c03"
# Ephemeral port by default. Set COMPANION_PET_ASSETS_PORT=8799 to match the vite dev
# proxy in packages/companion-pet-web/vite.config.js when hacking on the page itself.
DEFAULT_PORT = int(os.environ.get("COMPANION_PET_ASSETS_PORT", "0") or 0)

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("model/gltf-binary", ".vrm")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/svg+xml", ".svg")


class _Handler(BaseHTTPRequestHandler):
    server_version = "CompanionAssets/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, roots: dict[str, Path], on_bridge=None, **kwargs):
        self._roots = roots
        self._on_bridge = on_bridge
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # keep the Qt console readable
        return

    def _resolve(self, path: str) -> Path | None:
        rel = unquote(urlparse(path).path).lstrip("/")
        if not rel:
            rel = "index.html"
        for prefix, root in self._roots.items():
            if prefix and not rel.startswith(prefix):
                continue
            candidate = (root / rel[len(prefix):]).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                return None
            if candidate.is_file():
                return candidate
        return None

    def do_GET(self):  # noqa: N802
        target = self._resolve(self.path)
        if target is None:
            self.send_error(404, "not found")
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != BRIDGE_PATH:
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = {"method": "__malformed__", "raw": raw[:400].decode("utf-8", "replace")}
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if self._on_bridge is not None:
            self._on_bridge(message)


class AssetsServer:
    """Threaded loopback server. `url` is the origin the WebView loads."""

    def __init__(self, dist_dir: Path | str, models: Path | str, on_bridge=None):
        self.dist = Path(dist_dir).resolve()
        self.models = Path(models).resolve()
        self._on_bridge = on_bridge
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if self._httpd is not None:
            return self.url
        if not (self.dist / "index.html").exists():
            raise FileNotFoundError(
                f"前端产物缺失：{self.dist / 'index.html'} 不存在。请先在 "
                f"packages/companion-pet-web 执行 npm.cmd run build。"
            )
        handler = partial(_Handler, roots={"": self.dist, "model/": self.models}, on_bridge=self._on_bridge)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="companion-assets", daemon=True)
        self._thread.start()
        return self.url

    @property
    def url(self) -> str:
        if self._httpd is None:
            return ""
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}/"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None