"""Tray icon for the frameless pet.

docs/03 card 1: there is no main window; the tray is where settings, passthrough and exit
live. It is also recovery channel #2 - if the pet is click-through and the hotkey is
somehow unavailable, the tray icon is still a normal interactive surface.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

TRAY_TOOLTIP = "星澜 · 桌面宠物"


def make_icon(size: int = 64) -> QIcon:
    """Draw the icon at runtime so no binary asset has to ship."""
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#152d46"))
    painter.setPen(QColor("#52dbdc"))
    painter.drawRoundedRect(4, 4, size - 8, size - 8, size // 4, size // 4)
    painter.setBrush(QColor("#ffe09a"))
    painter.setPen(QColor("#97ffec"))
    painter.drawEllipse(size // 2 - size // 12, size // 4, size // 6, size // 6)
    painter.drawRoundedRect(size // 3, size // 2, size // 3, size // 4, 6, 6)
    painter.end()
    return QIcon(pix)


class PetTray(QObject):
    toggled_passthrough = Signal()
    toggled_bubble = Signal(bool)
    toggled_look_at = Signal(bool)
    show_settings = Signal()
    reload_model = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(make_icon(), parent)
        self.tray.setToolTip(TRAY_TOOLTIP)

        menu = QMenu()
        self.act_passthrough = QAction("穿透 / 可交互", menu)
        self.act_passthrough.setCheckable(True)
        self.act_passthrough.triggered.connect(self.toggled_passthrough)
        menu.addAction(self.act_passthrough)

        menu.addSeparator()
        self.act_settings = QAction("设置 / 聊天", menu)
        self.act_settings.triggered.connect(self.show_settings)
        menu.addAction(self.act_settings)

        self.act_bubble = QAction("显示气泡", menu)
        self.act_bubble.setCheckable(True)
        self.act_bubble.setChecked(True)
        self.act_bubble.toggled.connect(self.toggled_bubble)
        menu.addAction(self.act_bubble)

        self.act_look_at = QAction("视线跟随鼠标", menu)
        self.act_look_at.setCheckable(True)
        self.act_look_at.setChecked(True)
        self.act_look_at.toggled.connect(self.toggled_look_at)
        menu.addAction(self.act_look_at)

        menu.addSeparator()
        self.act_reload = QAction("重新加载 3D 角色", menu)
        self.act_reload.triggered.connect(self.reload_model)
        menu.addAction(self.act_reload)

        menu.addSeparator()
        self.act_quit = QAction("退出桌宠", menu)
        self.act_quit.triggered.connect(self.quit_requested)
        menu.addAction(self.act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.menu = menu

    def _activated(self, reason) -> None:
        # Double click / trigger on the icon is the "I want the pet back" affordance.
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggled_passthrough.emit()

    def set_passthrough(self, enabled: bool) -> None:
        self.act_passthrough.blockSignals(True)
        self.act_passthrough.setChecked(enabled)
        self.act_passthrough.setText("穿透中 · 点击恢复可交互" if enabled else "穿透 / 可交互")
        self.act_passthrough.blockSignals(False)

    def set_bubble(self, enabled: bool) -> None:
        self.act_bubble.blockSignals(True)
        self.act_bubble.setChecked(enabled)
        self.act_bubble.blockSignals(False)

    def set_look_at(self, enabled: bool) -> None:
        self.act_look_at.blockSignals(True)
        self.act_look_at.setChecked(enabled)
        self.act_look_at.blockSignals(False)

    def notify(self, title: str, message: str) -> None:
        self.tray.showMessage(title, message, make_icon(), 4000)

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()