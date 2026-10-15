"""2D fallback pet.

This is the pre-C03 pet: the same QPainter character that shipped in app/desktop.py, kept
because docs/03 card 1 forbids deleting the 2D path (V12 requires it as the landing spot
when WebGL or the VRM file fails). On top of the original drawing it can show a visible
error banner, so a failure is never a silently blank window.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

ERROR_BANNER_HEIGHT = 74


class Pet2DFallback(QWidget):
    """Drop-in replacement for the old `desktop.Pet` widget."""

    settings_requested = Signal()
    pause_requested = Signal()
    interrupt_requested = Signal()
    mute_requested = Signal()
    quit_requested = Signal()

    def __init__(self, caption="双击打开设置 · 右键控制", parent=None):
        super().__init__(parent)
        # Deliberately NOT calling setWindowFlags(): this widget is hosted as a child of
        # the pet window, and a window flag would promote it to a second top-level window.
        # The pet window itself already carries Frameless + StayOnTop + Tool.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.phase = 0
        self.state = "idle"
        self.caption = caption
        self.error = ""
        self.drag = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(70)

    def set_error(self, message: str) -> None:
        self.error = (message or "")[:160]
        self.update()

    def animate(self) -> None:
        self.phase += 0.17
        self.update()

    # -- geometry: the character sits below the (optional) error banner --------------
    def _body_rect(self) -> QRect:
        banner = ERROR_BANNER_HEIGHT if self.error else 0
        return QRect(0, banner, self.width(), max(60, self.height() - banner))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        if self.error:
            p.setBrush(QColor(90, 22, 22, 235))
            p.drawRoundedRect(8, 6, self.width() - 16, ERROR_BANNER_HEIGHT - 10, 10, 10)
            p.setPen(QColor("#ffd8d8"))
            p.setFont(QFont("Microsoft YaHei", 9))
            p.drawText(
                QRect(16, 8, self.width() - 32, ERROR_BANNER_HEIGHT - 16),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap),
                "3D 不可用，已回退 2D：" + self.error,
            )
            p.setPen(Qt.PenStyle.NoPen)

        body = self._body_rect()
        top = body.top()
        p.setBrush(QColor(13, 24, 42, 225))
        p.drawRoundedRect(10, top + 10, body.width() - 20, 90, 18, 18)
        p.setPen(QColor("#d8edf6"))
        p.setFont(QFont("Microsoft YaHei", 10))
        p.drawText(
            QRect(22, top + 18, body.width() - 44, 75),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            self.caption[:100],
        )

        y = top + 145 + int(5 * math.sin(self.phase))
        p.setPen(QPen(QColor("#52dbdc"), 3))
        p.setBrush(QColor("#152d46"))
        p.drawRoundedRect(60, y, 160, 96, 30, 30)
        p.setBrush(QColor("#284d69"))
        p.drawRoundedRect(84, y + 95, 112, 42, 18, 18)
        p.drawLine(140, y, 140, y - 22)
        p.setBrush(QColor("#ffe09a"))
        p.drawEllipse(QPoint(140, y - 26), 7, 7)

        p.setPen(QPen(QColor("#97ffec"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        blink = math.sin(self.phase * 0.21) > 0.985
        if self.state == "thinking":
            p.drawEllipse(QPoint(108, y + 40), 9, 9)
            p.drawEllipse(QPoint(174, y + 40), 9, 9)
            p.setPen(QColor("#ffe09a"))
            p.drawText(235, y + 5, "···")
        elif blink:
            p.drawLine(99, y + 40, 117, y + 40)
            p.drawLine(165, y + 40, 183, y + 40)
        else:
            p.drawLine(108, y + 33, 108, y + 47)
            p.drawLine(174, y + 33, 174, y + 47)

        p.setBrush(QColor("#97ffec"))
        p.setPen(Qt.PenStyle.NoPen)
        if self.state == "speaking":
            p.drawEllipse(QRect(128, y + 62, 26, 5 + int(13 * abs(math.sin(self.phase * 2)))))
        else:
            p.drawRoundedRect(126, y + 66, 29, 5, 2, 2)

        p.setPen(QPen(QColor("#52dbdc"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(83, y + 110, 57, y + 118)
        p.drawLine(196, y + 109, 226, y + int(110 + 12 * math.sin(self.phase)))
        p.setPen(QColor("#9ab5c7"))
        p.setFont(QFont("Microsoft YaHei", 9))
        p.drawText(
            QRect(20, body.bottom() - 40, body.width() - 40, 24),
            int(Qt.AlignmentFlag.AlignCenter),
            "星澜  ·  2D 降级模式",
        )

    # -- input -----------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            self.move(event.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, event):
        self.drag = None

    def mouseDoubleClickEvent(self, event):
        self.settings_requested.emit()

    def contextMenuEvent(self, event):
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        for label, signal in [
            ("设置 / 聊天", self.settings_requested),
            ("暂停观察", self.pause_requested),
            ("打断回答", self.interrupt_requested),
            ("静音 / 恢复语音", self.mute_requested),
        ]:
            menu.addAction(label, signal.emit)
        menu.addSeparator()
        menu.addAction("退出桌宠", self.quit_requested.emit)
        menu.exec(event.globalPos())