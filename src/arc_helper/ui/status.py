"""Status pill (Qt)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtGui import QFont
from PySide6.QtGui import QFontMetrics
from PySide6.QtGui import QMouseEvent
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPaintEvent
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QWidget

from arc_helper.config import get_dpi_scale
from arc_helper.overlay_format import BG_DARK
from arc_helper.ui.platform import apply_overlay_flags

if TYPE_CHECKING:
    from collections.abc import Callable


class StatusWindow(QWidget):
    """Small always-on-top status indicator."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_quit: Callable[[], None] | None = None,
        on_left_click: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_quit = on_quit
        self.on_left_click = on_left_click
        self.dpi_scale = get_dpi_scale()
        self._text = "\u25cf Scanning..."
        self._color = QColor("#888888")
        self._bg = QColor(BG_DARK)

        # Status pill accepts clicks (left = F8, right = quit).
        apply_overlay_flags(self, click_through=False)
        self.setWindowTitle("ARLO Status")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=False)
        # Partial stylesheet updates do not clear an override-redirect
        # XWayland window when it shrinks. Paint every pixel ourselves.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, on=True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, on=True)
        self.setFont(QFont("Segoe UI", self._scale(10)))
        self.move(10, 10)
        self._apply_size()
        self.show()

    def _scale(self, value: int) -> int:
        return max(1, int(value / max(self.dpi_scale, 1.0)))

    def _apply_size(self) -> None:
        metrics = QFontMetrics(self.font())
        pad_x = self._scale(10)
        pad_y = self._scale(5)
        # +2 so the last glyph's right bearing is not clipped.
        width = metrics.horizontalAdvance(self._text) + pad_x * 2 + 2
        height = metrics.height() + pad_y * 2
        self.setFixedSize(max(1, width), max(1, height))

    def paintEvent(self, _event: QPaintEvent) -> None:  # ruff: ignore[invalid-function-name]
        painter = QPainter(self)
        # Source replaces the backing store. SourceOver would blend the new
        # background over leftover MAP ink on XWayland.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), self._bg)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setFont(self.font())
        painter.setPen(self._color)
        pad_x = self._scale(10)
        pad_y = self._scale(5)
        painter.drawText(
            self.rect().adjusted(pad_x, pad_y, -pad_x, -pad_y),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )
        painter.end()

    def resizeEvent(self, event: QResizeEvent) -> None:  # ruff: ignore[invalid-function-name]
        super().resizeEvent(event)
        # Shrink does not expose the surviving area of an override-redirect
        # window, so ask the platform for a full update of the new size.
        # Nothing is on screen until show(), and the native handle is null then.
        if self.isVisible():
            self.windowHandle().requestUpdate()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # ruff: ignore[invalid-function-name]
        # Defer quit/toggle so cleanup is not nested inside the mouse handler
        # (portal join / hotkey stop can block; re-entrancy was crashing).
        if event.button() == Qt.MouseButton.RightButton and self.on_quit:
            QTimer.singleShot(0, self.on_quit)
        elif event.button() == Qt.MouseButton.LeftButton and self.on_left_click:
            QTimer.singleShot(0, self.on_left_click)
        else:
            super().mousePressEvent(event)

    def _set_status(self, text: str, *, color: str) -> None:
        self._text = text
        self._color = QColor(color)
        self._apply_size()
        self.repaint(self.rect())

    def set_scanning(self) -> None:
        self._set_status("\u25cf Scanning...", color="#888888")

    def set_active(self) -> None:
        self._set_status("\u25cf INVENTORY", color="#32CD32")

    def set_map(self) -> None:
        self._set_status("\u25cf MAP (F8=filters)", color="#2DD4BF")

    def set_hotkey(self) -> None:
        self._set_status("\u25cf HOTKEY SCAN", color="#FFD700")

    def set_error(self, message: str) -> None:
        self._set_status(f"\u25cf {message}", color="#FF4444")

    def set_info(self, message: str, *, fg: str = "#5AD4A0") -> None:
        self._set_status(f"\u25cf {message}", color=fg)

    def hide(self) -> None:
        QWidget.hide(self)

    def show(self) -> None:
        QWidget.show(self)
        self.raise_()
