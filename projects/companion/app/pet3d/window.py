"""The frameless, topmost, no-taskbar VRM pet window (docs/03 card 1).

Product shape: there is no main window. Only the pet (plus optional bubble and tray).
`PetWindow` is a drop-in replacement for the old `desktop.Pet`, so `Companion` keeps
working unchanged: same signals, same `state` / `caption` attributes, same `show()`.

Responsibility split:
    window.py               composition, geometry, passthrough, hotkeys, tray
    renderer.py             the WebView and the JS bridge
    fallback2d.py           the 2D pet used when WebGL or the VRM fails
    window_passthrough.py   the Win32 calls; win32_styles.py holds their pure-data twins
    geometry.py             pure multi-monitor / DPI math, unit tested without a display
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QPoint,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import geometry as geo
from . import vrm_loader, window_passthrough as wp
from .config import PetConfig
from .fallback2d import Pet2DFallback
from .tray import PetTray

# How often topmost is re-asserted. docs/03 warns against over-polling and fighting drags.
TOPMOST_INTERVAL_MS = 5000
DRAG_INTERVAL_MS = 16
LOOKAT_INTERVAL_MS = 66
TICK_INTERVAL_MS = 100

HOTKEY_TOGGLE = 1
HOTKEY_QUIT = 2


def _webengine_ok() -> tuple[bool, str]:
    try:
        from .renderer import webengine_available
    except Exception as exc:  # pragma: no cover - depends on local Qt install
        return False, str(exc)
    return webengine_available()


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Turns WM_HOTKEY into Python callbacks.

    QAbstractNativeEventFilter is not a QObject, and an instance-level `Signal()`
    has no `emit()` - it crashes exactly when WM_HOTKEY arrives, i.e. on the V6
    recovery path. Plain callables are the safe bridge: Qt invokes
    nativeEventFilter on the GUI thread, so the callbacks run where widgets may
    be touched. The window owns the filter and clears it in shutdown().
    """

    def __init__(self):
        super().__init__()
        self.on_toggle = None
        self.on_quit = None

    def nativeEventFilter(self, event_type, message):
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False
        try:
            import ctypes
            from ctypes import wintypes

            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        except Exception:
            return False
        if msg.message != wp.WM_HOTKEY:
            return False
        handler = None
        if msg.wParam == HOTKEY_TOGGLE:
            handler = self.on_toggle
        elif msg.wParam == HOTKEY_QUIT:
            handler = self.on_quit
        if handler is not None:
            handler()
        return True


class PetWindow(QWidget):
    settings_requested = Signal()
    pause_requested = Signal()
    interrupt_requested = Signal()
    mute_requested = Signal()
    quit_requested = Signal()
    status_changed = Signal(str)

    def __init__(self, config: PetConfig | None = None, project_root: Path | str | None = None):
        super().__init__()
        self.config = config or PetConfig.load()
        self.root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent.parent
        self.layout_snapshot = geo.Layout()
        self.click_through = False
        self.bubble_enabled = self.config.bubble
        self.look_at_enabled = self.config.look_at_mouse
        self.mode_3d = False
        self.fps_value = 0.0
        self.renderer_kind = ""

        # Attributes the old desktop.Pet exposed and Companion still reads.
        self.state = "idle"
        self.caption = "正在启动 3D 角色…"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)

        self._drag_last: QPoint | None = None
        self.hotkey_specs: list[tuple[int, int, int]] = []

        self._set_window_attributes()
        self.setFixedSize(*self.config.logical_size)
        self._build_body()
        self._build_tray()
        self._build_hotkeys()
        self._connect_display_signals()

        self.cursor_timer = QTimer(self)
        self.cursor_timer.timeout.connect(self._on_cursor)
        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self._reassert_topmost)
        self.drag_timer = QTimer(self)
        self.drag_timer.setInterval(DRAG_INTERVAL_MS)
        self.drag_timer.timeout.connect(self._on_drag_tick)

        self.timer.start(TICK_INTERVAL_MS)
        self.topmost_timer.start(TOPMOST_INTERVAL_MS)
        self.cursor_timer.start(LOOKAT_INTERVAL_MS)

    # ------------------------------------------------------------------ construction

    def _set_window_attributes(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowTitle("星澜 · 桌面宠物")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

    def _build_body(self) -> None:
        self.pet_2d = Pet2DFallback(caption=self.caption, parent=self)
        self.pet_2d.setGeometry(self.rect())
        self.pet_2d.settings_requested.connect(self.settings_requested)
        self.pet_2d.pause_requested.connect(self.pause_requested)
        self.pet_2d.interrupt_requested.connect(self.interrupt_requested)
        self.pet_2d.mute_requested.connect(self.mute_requested)
        self.pet_2d.quit_requested.connect(self.quit_requested)

        self.view3d = None
        ok, reason = _webengine_ok()
        if not ok:
            self.set_2d_fallback(f"WebView 不可用：{reason}")
            return
        try:
            model_path = vrm_loader.resolve(self.root / "assets" / "models", self.config.model)
        except vrm_loader.VrmFormatError as exc:
            self.set_2d_fallback(str(exc))
            return
        try:
            from .renderer import Pet3DView

            self.view3d = Pet3DView(model_path, self.config, parent=self)
        except Exception as exc:  # pragma: no cover - depends on local Qt install
            self.set_2d_fallback(f"3D 视图创建失败：{exc}")
            return

        self.view3d.setGeometry(self.rect())
        self.view3d.ready.connect(self._on_3d_ready)
        self.view3d.failed.connect(self._on_3d_failed)
        self.view3d.fps.connect(self._on_fps)
        self.view3d.clicked.connect(self._on_3d_clicked)
        self.view3d.drag_begin.connect(self._on_3d_drag_begin)
        self.view3d.drag_end.connect(self._on_3d_drag_end)
        self.view3d.context_menu.connect(self._on_3d_context_menu)
        self.view3d.page_log.connect(lambda m: self.status_changed.emit("3D：" + m))
        self.set_2d_fallback("正在加载 VRM 模型…")
        self.view3d.show()
        QTimer.singleShot(0, self.view3d.start)

    def _on_fps(self, value: float) -> None:
        self.fps_value = value

    def _build_tray(self) -> None:
        self.tray = PetTray(self)
        self.tray.toggled_passthrough.connect(self.toggle_passthrough)
        self.tray.toggled_bubble.connect(self.set_bubble)
        self.tray.toggled_look_at.connect(self.set_look_at)
        self.tray.show_settings.connect(self.settings_requested)
        self.tray.reload_model.connect(self._on_reload_requested)
        self.tray.quit_requested.connect(self.quit_requested)
        self.tray.set_bubble(self.bubble_enabled)
        self.tray.set_look_at(self.look_at_enabled)
        if self.config.tray and _tray_available():
            self.tray.show()

    def _build_hotkeys(self) -> None:
        self.hotkey_filter = _HotkeyFilter()
        self.hotkey_filter.on_toggle = self.toggle_passthrough
        self.hotkey_filter.on_quit = self.quit_requested.emit
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self.hotkey_filter)
        try:
            self.hotkey_specs = [
                (HOTKEY_TOGGLE,) + wp.parse_hotkey(self.config.toggle_hotkey),
                (HOTKEY_QUIT,) + wp.parse_hotkey(self.config.quit_hotkey),
            ]
        except ValueError as exc:
            self.hotkey_specs = []
            self.status_changed.emit(f"热键配置无效：{exc}")

    def _connect_display_signals(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)
        self._bound_screens: list = []
        self._bind_screen_signals()
        QTimer.singleShot(0, self.refresh_layout)

    def _bind_screen_signals(self) -> None:
        app = QGuiApplication.instance()
        for screen in app.screens():
            for name in ("geometryChanged", "physicalDotsPerInchChanged", "logicalDotsPerInchChanged"):
                signal = getattr(screen, name, None)
                if signal is None:
                    continue
                if self._bound_screens:  # never disconnect before the first bind
                    try:
                        signal.disconnect(self.refresh_layout)
                    except (RuntimeError, TypeError):
                        pass
                signal.connect(self.refresh_layout)
        self._bound_screens = list(app.screens())

    def _on_screens_changed(self, *_):
        self._bind_screen_signals()
        self.refresh_layout(reanchor=True)

    # ------------------------------------------------------------------ geometry / DPI

    def _qt_screen_for(self, monitor: geo.Monitor):
        """Qt screen paired with a Win32 monitor, matched on device name then overlap."""
        app = QGuiApplication.instance()
        if app is None:
            return None
        screens = list(app.screens())
        for screen in screens:
            if screen.name() and screen.name() == monitor.name:
                return screen
        if not screens:
            return None
        centre = monitor.physical.center
        for screen in screens:
            geometry = screen.geometry()
            scale = float(screen.devicePixelRatio() or 1.0)
            physical = geo.Rect(
                round(geometry.left() * scale),
                round(geometry.top() * scale),
                round(geometry.right() * scale),
                round(geometry.bottom() * scale),
            )
            if physical.contains_point(*centre):
                return screen
        return screens[0] if len(screens) == 1 else None

    def refresh_layout(self, *_args, reanchor: bool = False) -> geo.Layout:
        """Re-read every monitor from Win32 and, on a change, re-seat the window.

        Values come from `EnumDisplayMonitors` in physical pixels, so the same code is
        correct on 100% / 150% / 200% screens and across monitors with different scaling.
        """
        monitors = [
            geo.Monitor(
                name=entry["name"],
                physical=geo.Rect(entry["left"], entry["top"], entry["right"], entry["bottom"]),
                scale=float(entry["scale"]) or 1.0,
            )
            for entry in wp.enum_monitors()
        ]
        if monitors:
            self.layout_snapshot = geo.Layout(tuple(monitors))

        if self.config.window_mode == "fullscreen" and self.layout_snapshot.monitors:
            virtual = self.layout_snapshot.virtual
            self.setGeometry(virtual.left, virtual.top, virtual.width, virtual.height)
            self._report_layout()
            return self.layout_snapshot

        rect = wp.window_rect(int(self.winId()))
        if rect:
            current = geo.Rect(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])
            target = current if not reanchor else geo.reanchor(
                current, self.layout_snapshot, self.config.logical_size
            )
            if target.as_tuple() != current.as_tuple():
                self._move_physical(target)
        self._report_layout()
        return self.layout_snapshot

    def _move_physical(self, target: geo.Rect) -> None:
        """Place the window using a Win32 physical rectangle."""
        monitor = self.layout_snapshot.monitor_nearest(target)
        if monitor is None:
            return
        screen = self._qt_screen_for(monitor)
        if screen is None:
            return
        local = monitor.to_logical(target)
        origin = screen.geometry().topLeft()
        self.move(origin.x() + local.left, origin.y() + local.top)

    def _current_physical(self) -> geo.Rect | None:
        rect = wp.window_rect(int(self.winId()))
        if not rect:
            return None
        return geo.Rect(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])

    def _report_layout(self) -> None:
        rect = self._current_physical()
        if rect is None:
            return
        monitor = self.layout_snapshot.monitor_nearest(rect)
        scale = f"{monitor.scale * 100:.0f}%" if monitor else "未知"
        name = monitor.name if monitor else "?"
        self.status_changed.emit(f"显示器 {name} · 缩放 {scale} · 物理位置 {rect.left},{rect.top}")

    # ------------------------------------------------------------------ lifecycle

    def show(self) -> None:  # noqa: D102 - Qt override
        super().show()
        self._initial_placement()
        QTimer.singleShot(0, self._apply_win32_styles)
        QTimer.singleShot(600, self._apply_win32_styles)

    def _initial_placement(self) -> None:
        if self.config.window_mode == "fullscreen":
            self.refresh_layout()
            return
        layout = self.refresh_layout()
        if not layout.monitors:
            return
        primary = layout.monitors[0]
        width = round(self.width() * primary.scale)
        height = round(self.height() * primary.scale)
        target = geo.Rect(
            primary.physical.right - width - round(40 * primary.scale),
            primary.physical.bottom - height - round(40 * primary.scale),
            primary.physical.right - round(40 * primary.scale),
            primary.physical.bottom - round(40 * primary.scale),
        )
        self._move_physical(primary.clamp(target))

    def _apply_win32_styles(self) -> None:
        hwnd = int(self.winId())
        wp.apply_base_style(hwnd)
        self.apply_passthrough(self.config.click_through or self.config.start_click_through)
        self.register_hotkeys_now()

    def close(self) -> None:  # noqa: D102 - Qt override
        self.shutdown()
        super().close()

    def shutdown(self) -> None:
        for timer in (self.timer, self.topmost_timer, self.cursor_timer, self.drag_timer):
            timer.stop()
        app = QCoreApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self.hotkey_filter)
        hwnd = int(self.winId())
        for hotkey_id, _mods, _vk in self.hotkey_specs:
            wp.unregister_hotkey(hwnd, hotkey_id)
        self._hotkeys_live = False
        # No SetLayeredWindowAttributes here on purpose: the window is alpha-composed
        # by Qt (UpdateLayeredWindow) and mixing the two APIs breaks repaints. The
        # click-through bit lives in the ex-style and dies with the window.
        if self.view3d is not None:
            self.view3d.shutdown()
            self.view3d = None
        self.tray.hide()
        self.pet_2d.timer.stop()

    # ------------------------------------------------------------------ passthrough
    def apply_passthrough(self, enabled: bool) -> bool:
        """Enable or disable mouse click-through on the live window.

        Shipped mechanism (decided by the 2026-10-15 live probe, full evidence in
        work/c03-REPORT.md): WS_EX_LAYERED + WS_EX_TRANSPARENT on the pet window.
        Per-pixel options were both disproven on this stack - SetLayeredWindowAttributes
        (LWA_COLORKEY) is mutually exclusive with the UpdateLayeredWindow surface Qt
        paints for WA_TranslucentBackground (every repaint then fails), and the Qt6
        WebEngine child window hit-tests every pixel regardless of alpha. So
        click-through passes input through the whole rectangle. That matches the
        product rule in docs/03 §3.3: the pet body need not be clickable in this mode,
        and recovery is the global hotkey or the tray icon - never a click on the pet.
        Interactive mode clears WS_EX_TRANSPARENT, so apply_base_style / V-style
        audits keep proving the bit is absent outside click-through.
        """
        self.click_through = bool(enabled)
        ok = wp.set_click_through_via_style(int(self.winId()), self.click_through)
        if self.view3d is not None:
            self.view3d.configure(clickThrough=self.click_through)
        self.tray.set_passthrough(self.click_through)
        self.status_changed.emit(
            (
                "已开启穿透：窗口不再接收鼠标（含角色本体）；用全局热键或托盘恢复"
                if self.click_through
                else "已恢复可交互"
            )
            + ("" if ok else "；但 Win32 未接受该调用")
        )
        return ok

    def toggle_passthrough(self) -> None:
        self.apply_passthrough(not self.click_through)

    def register_hotkeys_now(self) -> list[str]:
        """Register the global hotkeys. Needs a valid HWND, so it runs after show().

        Idempotent: show() applies Win32 styles twice (0 ms and 600 ms); registering
        twice would report a bogus "hotkey already taken" failure to the user.
        """
        if getattr(self, "_hotkeys_live", False):
            return []
        hwnd = int(self.winId())
        failures = []
        for hotkey_id, mods, vk in self.hotkey_specs:
            if not wp.register_hotkey(hwnd, hotkey_id, mods, vk):
                failures.append(
                    self.config.toggle_hotkey if hotkey_id == HOTKEY_TOGGLE else self.config.quit_hotkey
                )
        if not failures:
            self._hotkeys_live = True
            self.status_changed.emit(
                f"全局热键已注册：穿透/恢复 {self.config.toggle_hotkey} · 退出 {self.config.quit_hotkey}"
            )
        else:
            self.status_changed.emit("以下全局热键注册失败（可能被其他程序占用）：" + "、".join(failures))
        return failures

    # ------------------------------------------------------------------ 3D callbacks

    def _on_3d_ready(self, kind: str, fps: float) -> None:
        self.mode_3d = True
        self.renderer_kind = kind
        self.fps_value = fps
        self.pet_2d.hide()
        self.view3d.show()
        self.view3d.raise_()
        self.view3d.configure(
            lookAtMouse=self.look_at_enabled,
            idleAnimation=self.config.idle_animation,
            clickReaction=self.config.click_reaction,
            bubble=self.bubble_enabled,
            clickThrough=self.click_through,
        )
        self.view3d.set_state(self.state, self.caption)
        self.status_changed.emit(f"3D 角色已就绪（{kind}）")

    def _on_3d_failed(self, reason: str) -> None:
        if self.mode_3d:
            return
        self.set_2d_fallback(reason)
        self.status_changed.emit(reason)

    def set_2d_fallback(self, reason: str) -> None:
        """Land on the 2D pet with a visible explanation - never a blank window."""
        self.mode_3d = False
        if self.view3d is not None:
            self.view3d.hide()
        self.pet_2d.set_error(reason)
        self.pet_2d.caption = self.caption
        self.pet_2d.setGeometry(self.rect())
        self.pet_2d.show()
        self.pet_2d.raise_()

    def _on_reload_requested(self) -> None:
        if self.view3d is None:
            self.set_2d_fallback("WebView 或模型不可用，无法重新加载")
            return
        self.mode_3d = False
        self.set_2d_fallback("正在重新加载 3D 角色…")
        self.view3d.run_js("window.__c03 && window.__c03.reload()")
        self.view3d.show()
        self.view3d.raise_()

    def _on_3d_clicked(self, _x: float, _y: float) -> None:
        if self.view3d is not None:
            self.view3d.react_click()

    def _on_3d_drag_begin(self, _x: int, _y: int) -> None:
        self._drag_last = QCursor.pos()
        self.drag_timer.start()

    def _on_3d_drag_end(self) -> None:
        # Without this the drag timer would keep the window glued to the cursor
        # after the button is released.
        self._drag_last = None
        self.drag_timer.stop()

    def _on_3d_context_menu(self, x: int, y: int) -> None:
        menu = QMenu(self)
        menu.addAction("设置 / 聊天", self.settings_requested.emit)
        menu.addAction("暂停观察", self.pause_requested.emit)
        menu.addAction("打断回答", self.interrupt_requested.emit)
        menu.addAction("静音 / 恢复语音", self.mute_requested.emit)
        menu.addSeparator()
        menu.addAction(
            "取消穿透（恢复可交互）" if self.click_through else "开启穿透"
        ).triggered.connect(self.toggle_passthrough)
        menu.addAction("退出桌宠", self.quit_requested.emit)
        menu.exec(QPoint(x, y) if (x, y) != (0, 0) else QCursor.pos())

    # ------------------------------------------------------------------ drag & cursor

    def _on_drag_tick(self) -> None:
        if self._drag_last is None:
            self.drag_timer.stop()
            return
        now = QCursor.pos()
        delta = now - self._drag_last
        self._drag_last = now
        if delta.x() or delta.y():
            self.move(self.pos() + delta)

    def _on_cursor(self) -> None:
        if not (self.mode_3d and self.look_at_enabled and self.view3d is not None):
            return
        pos = QCursor.pos()
        monitor = self.layout_snapshot.monitor_nearest(geo.Rect(pos.x(), pos.y(), pos.x() + 1, pos.y() + 1))
        if monitor is None:
            return
        nx, ny = geo.cursor_norm((pos.x(), pos.y()), monitor)
        self.view3d.set_cursor_norm(nx, ny)

    def _reassert_topmost(self) -> None:
        if self.isVisible():
            wp.reassert_topmost(int(self.winId()))

    def _on_tick(self) -> None:
        self.pet_2d.state = self.state
        self.pet_2d.caption = self.caption
        if self.mode_3d and self.view3d is not None:
            self.view3d.set_state(self.state, self.caption if self.bubble_enabled else "")

    # ------------------------------------------------------------------ options

    def set_bubble(self, enabled: bool) -> None:
        self.bubble_enabled = bool(enabled)
        self.tray.set_bubble(self.bubble_enabled)
        if self.view3d is not None:
            self.view3d.configure(bubble=self.bubble_enabled)

    def set_look_at(self, enabled: bool) -> None:
        self.look_at_enabled = bool(enabled)
        self.tray.set_look_at(self.look_at_enabled)
        if self.view3d is not None:
            self.view3d.configure(lookAtMouse=self.look_at_enabled)

    # ------------------------------------------------------------------ Qt events

    def resizeEvent(self, event) -> None:  # noqa: D102 - Qt override
        self.pet_2d.setGeometry(self.rect())
        if self.view3d is not None:
            self.view3d.setGeometry(self.rect())
            if self.mode_3d:
                self.view3d.run_js(
                    "window.__c03 && window.__c03.resize(%d, %d, %.4f)"
                    % (self.width(), self.height(), self.devicePixelRatioF())
                )
        super().resizeEvent(event)


def _tray_available() -> bool:
    app = QApplication.instance()
    checker = getattr(app, "trayAvailable", None) if app is not None else None
    if checker is None:
        return False
    try:
        return bool(checker())
    except Exception:
        return False