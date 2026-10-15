"""QWebEngineView container that renders the VRM pet.

Responsibilities:
  * host the built three.js bundle served by `assets_server`
  * receive JavaScript commands over POST /__c03 and turn them into Qt signals
  * send state back with QWebEnginePage.runJavaScript
  * report readiness / failure so the caller can fall back to the 2D pet

Nothing here decides window flags, passthrough or tray behaviour - those live in
`window.py` / `window_passthrough.py` so they stay testable.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import assets_server, vrm_loader

# How long we wait for the page to say it rendered something before giving up on 3D.
READY_TIMEOUT_MS = 25000
TELEMETRY_MS = 1000


def webengine_available() -> tuple[bool, str]:
    """True only when the classes the renderer actually needs can be imported.

    Qt moved QWebEnginePage from QtWebEngineWidgets to QtWebEngineCore in Qt 6.9+;
    checking just the module import once hid a hard failure until the window opened.
    """
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        try:
            from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: F401  (Qt >= 6.9)
        except ImportError:
            from PySide6.QtWebEngineWidgets import QWebEnginePage  # noqa: F401  (Qt < 6.9)
    except Exception as exc:  # pragma: no cover - depends on the local install
        return False, f"PySide6 QtWebEngine 不可用：{exc}"
    return True, ""


def _page_class():
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage

        return QWebEnginePage
    except ImportError:  # Qt < 6.9
        from PySide6.QtWebEngineWidgets import QWebEnginePage

        return QWebEnginePage


class PetBridge(QObject):
    """Everything the page can ask the Python side to do."""

    ready = Signal(str, float)  # renderer kind, fps
    failed = Signal(str)
    fps = Signal(float)
    clicked = Signal(float, float)  # normalised canvas coords
    drag_begin = Signal(int, int)
    drag_end = Signal()
    context_menu = Signal(int, int)
    log = Signal(str)

    def __init__(self):
        super().__init__()
        self.telemetry_enabled = True

    # -- called from the asset server thread; emitting a Qt signal is thread-safe ----

    def on_message(self, message: dict) -> None:
        # A sendBeacon can still be in flight after shutdown deleted the C++ side.
        try:
            self._dispatch(message)
        except RuntimeError:
            pass  # "Signal source has been deleted" during teardown

    def _dispatch(self, message: dict) -> None:
        method = str(message.get("method", ""))
        args = message.get("args") or {}
        if method == "ready":
            self.ready.emit(str(args.get("renderer", "")), float(args.get("fps", 0.0)))
        elif method == "failed":
            self.failed.emit(str(args.get("message", "未知错误"))[:300])
        elif method == "fps":
            self.fps.emit(float(args.get("value", 0.0)))
        elif method == "clicked":
            self.clicked.emit(float(args.get("x", 0.0)), float(args.get("y", 0.0)))
        elif method == "drag_begin":
            self.drag_begin.emit(int(args.get("x", 0)), int(args.get("y", 0)))
        elif method == "drag_end":
            self.drag_end.emit()
        elif method == "context_menu":
            self.context_menu.emit(int(args.get("x", 0)), int(args.get("y", 0)))
        elif method == "log":
            self.log.emit(str(args.get("message", ""))[:300])
        elif method == "__malformed__":
            self.log.emit(f"收到无法解析的桥接消息：{args.get('raw', '')[:120]}")


class Pet3DView(QWidget):
    """Widget that owns the WebView and the loopback asset server."""

    ready = Signal(str, float)
    failed = Signal(str)
    fps = Signal(float)
    clicked = Signal(float, float)
    drag_begin = Signal(int, int)
    drag_end = Signal()
    context_menu = Signal(int, int)
    page_log = Signal(str)

    def __init__(self, model_path: Path, config, parent=None):
        super().__init__(parent)
        from PySide6.QtWebEngineWidgets import QWebEngineView

        QWebEnginePage = _page_class()

        self.config = config
        self.model_path = Path(model_path)
        self.model_name = self.model_path.name
        self.bridge = PetBridge()
        self.bridge.ready.connect(self.mark_ready)
        self.bridge.failed.connect(self.failed)
        self.bridge.fps.connect(self.fps)
        self.bridge.clicked.connect(self.clicked)
        self.bridge.drag_begin.connect(self.drag_begin)
        self.bridge.drag_end.connect(self.drag_end)
        self.bridge.context_menu.connect(self.context_menu)
        self.bridge.log.connect(self.page_log)

        dist = Path(__file__).resolve().parent.parent.parent / "packages" / "companion-pet-web" / "dist"
        self.server = assets_server.AssetsServer(dist, self.model_path.parent, self.bridge.on_message)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = QWebEngineView(self)
        page = QWebEnginePage(self.view)
        # QtWebEngine paints a white page background unless told otherwise; on a
        # WS_EX_LAYERED + LWA_COLORKEY pet that white rectangle would both kill V2
        # transparency and swallow clicks. Fully transparent keeps "nothing drawn"
        # pixels at the pure-black colour key.
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.setPage(page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.view)

        self.ready_timer = QTimer(self)
        self.ready_timer.setSingleShot(True)
        self.ready_timer.timeout.connect(self._on_ready_timeout)
        self.fps_timer = QTimer(self)
        self.fps_timer.timeout.connect(self._poll_fps)
        self._ready = False

    def start(self) -> None:
        try:
            origin = self.server.start()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        # The page resolves the model through `?model=`, so a configured non-default
        # model actually reaches the renderer instead of silently loading the default.
        from urllib.parse import quote

        url = QUrl(f"{origin}?model={quote('model/' + self.model_name)}")
        self.view.load(url)
        self.ready_timer.start(READY_TIMEOUT_MS)
        self.fps_timer.start(TELEMETRY_MS)

    def _on_ready_timeout(self) -> None:
        if not self._ready:
            self.failed.emit(
                f"3D 页面在 {READY_TIMEOUT_MS // 1000} 秒内没有完成渲染（构建产物、网络或 WebGL 可能有问题）"
            )

    def _poll_fps(self) -> None:
        self.run_js("window.__c03 && window.__c03.reportFps && window.__c03.reportFps()")

    def mark_ready(self, kind: str, fps: float) -> None:
        self._ready = True
        self.ready_timer.stop()
        self.ready.emit(kind, fps)

    def run_js(self, script: str) -> None:
        try:
            self.view.page().runJavaScript(script)
        except Exception:  # page already destroyed during shutdown
            pass

    def configure(self, **kwargs) -> None:
        """Push view options into the page. Unknown keys are ignored by the page."""
        self.run_js(f"window.__c03 && window.__c03.configure({json.dumps(kwargs, ensure_ascii=False)})")

    def set_state(self, state: str, caption: str) -> None:
        self.run_js(
            "window.__c03 && window.__c03.setState(%s, %s)"
            % (json.dumps(state), json.dumps(caption[:100]))
        )

    def set_cursor_norm(self, x: float, y: float) -> None:
        self.run_js(f"window.__c03 && window.__c03.setCursor({x:.4f}, {y:.4f})")

    def react_click(self) -> None:
        self.run_js("window.__c03 && window.__c03.react()")

    def shutdown(self) -> None:
        self.ready_timer.stop()
        self.fps_timer.stop()
        try:
            self.view.setPage(None)
        except Exception:
            pass
        self.view.deleteLater()
        self.server.stop()