"""In-game map pin overlay: one translucent click-through surface (Qt/xcb)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint
from PySide6.QtCore import QRect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPaintEvent
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QWidget

from arc_helper.map_data import CATEGORY_COLORS
from arc_helper.ui.platform import apply_overlay_flags
from arc_helper.ui.platform import make_click_through

PIN_R = 6


@dataclass
class OverlayPin:
    """Viewport-local physical pixels (relative to viewport origin)."""

    x: float
    y: float
    category: str
    label: str = ""


class InGameMapPins(QWidget):
    """Single-window pin overlay over the raid MAP viewport."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pins: list[tuple[float, float, str]] = []
        self._last_key: tuple | None = None
        self._mapped = False
        apply_overlay_flags(self, click_through=True)
        self.setWindowTitle("ARLO Map Pins")
        self.hide()

    @property
    def ingame_enabled(self) -> bool:
        return True

    def hide(self) -> None:
        self._pins = []
        self._last_key = None
        self._mapped = False
        QWidget.hide(self)

    def update_pins(
        self,
        *,
        viewport_bbox: tuple[int, int, int, int],
        pins: list[OverlayPin],
    ) -> None:
        if not pins:
            self.hide()
            return

        left, top, _right, _bottom = viewport_bbox
        screen_pts = [(left + p.x, top + p.y, p.category) for p in pins]
        key = tuple((round(x), round(y), c) for x, y, c in screen_pts)
        if self._mapped and key == self._last_key:
            return
        self._last_key = key
        self._pins = screen_pts

        pad = 16
        xs = [s[0] for s in screen_pts]
        ys = [s[1] for s in screen_pts]
        min_x = int(min(xs) - pad)
        min_y = int(min(ys) - pad)
        max_x = int(max(xs) + pad)
        max_y = int(max(ys) + pad)
        self.setGeometry(
            QRect(min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))
        )
        # Store positions relative to this widget for paintEvent.
        self._local = [(x - min_x, y - min_y, c) for x, y, c in screen_pts]
        if not self._mapped:
            QWidget.show(self)
            self.raise_()
            make_click_through(self)
            self._mapped = True
        self.repaint()

    def paintEvent(  # ruff: ignore[invalid-function-name]
        self, _event: QPaintEvent | None
    ) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for x, y, cat in getattr(self, "_local", []):
            color = QColor(CATEGORY_COLORS.get(cat, "#ffffff"))
            color.setAlpha(230)
            p.setBrush(color)
            p.setPen(QPen(QColor(17, 17, 17, 220), 1.5))
            p.drawEllipse(QPoint(int(x), int(y)), PIN_R, PIN_R)
        p.end()
