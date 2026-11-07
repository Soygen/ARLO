"""Item recommendation tooltip overlay (Qt)."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from arc_helper.config import get_dpi_scale
from arc_helper.config import get_settings
from arc_helper.database import Item
from arc_helper.database import names_match
from arc_helper.overlay_format import ACTION_COLORS
from arc_helper.overlay_format import BG_PANEL
from arc_helper.overlay_format import BORDER_COLOR
from arc_helper.overlay_format import TEXT_DIM
from arc_helper.overlay_format import TEXT_INFO
from arc_helper.overlay_format import format_detail_parts
from arc_helper.ui.platform import apply_overlay_flags
from arc_helper.ui.platform import make_click_through

_UI_FONT = "Segoe UI"


def _using_wayland() -> bool:
    try:
        from arc_helper.screen import get_backend

        return get_backend().name == "wayland"
    except Exception:  # ruff: ignore[blind-except]
        return False


class OverlayWindow(QWidget):
    """Click-through tooltip showing item action / keep / recycle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = get_settings()
        self.dpi_scale = get_dpi_scale()
        self._wayland_stable: bool | None = None
        self.overlay_x = self.settings.overlay.x
        self.overlay_y = self.settings.overlay.y
        self._current_item: str | None = None
        self._mapped = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        apply_overlay_flags(self, click_through=True)
        self.setWindowTitle("ARLO")
        self._setup_ui()
        self.hide()

    def _scale(self, value: int) -> int:
        return max(1, int(value / max(self.dpi_scale, 1.0)))

    def _is_wayland(self) -> bool:
        if self._wayland_stable is None:
            self._wayland_stable = _using_wayland()
        return self._wayland_stable

    def _setup_ui(self) -> None:
        outer = QFrame(self)
        outer.setObjectName("outer")
        outer.setStyleSheet(
            f"QFrame#outer {{ background: {BORDER_COLOR}; border: none; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(outer)

        pad = self._scale(2)
        inner_layout = QVBoxLayout(outer)
        inner_layout.setContentsMargins(pad, pad, pad, pad)

        panel = QFrame()
        panel.setObjectName("panel")
        panel.setStyleSheet(
            f"QFrame#panel {{ background: {BG_PANEL}; }}"
            f"QLabel {{ background: {BG_PANEL}; color: white; }}"
        )
        inner_layout.addWidget(panel)
        self._panel_layout = QVBoxLayout(panel)
        self._panel_layout.setContentsMargins(
            self._scale(18), self._scale(12), self._scale(18), self._scale(12)
        )
        self._panel_layout.setSpacing(self._scale(4))

        self.accent_bar = QFrame()
        self.accent_bar.setFixedHeight(self._scale(4))
        self.accent_bar.setStyleSheet("background: #32CD32;")
        self._panel_layout.addWidget(self.accent_bar)

        self.name_label = QLabel("Item Name")
        self.name_label.setFont(QFont(_UI_FONT, self._scale(18), QFont.Weight.Bold))
        self._panel_layout.addWidget(self.name_label)

        self.action_label = QLabel("ACTION")
        self.action_label.setFont(QFont(_UI_FONT, self._scale(26), QFont.Weight.Bold))
        self.action_label.setStyleSheet("color: #FFD700;")
        self._panel_layout.addWidget(self.action_label)

        self.info_label = QLabel("")
        self.info_label.setFont(QFont(_UI_FONT, self._scale(13)))
        self.info_label.setStyleSheet(f"color: {TEXT_INFO};")
        self._panel_layout.addWidget(self.info_label)

        self.separator = QFrame()
        self.separator.setFixedHeight(1)
        self.separator.setStyleSheet(f"background: {BORDER_COLOR};")
        self.separator.hide()
        self._panel_layout.addWidget(self.separator)

        self.keep_label = QLabel("")
        self.keep_label.setFont(QFont(_UI_FONT, self._scale(12)))
        self.keep_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.keep_label.setWordWrap(True)
        self.keep_label.hide()
        self._panel_layout.addWidget(self.keep_label)

        self.detail_separator = QFrame()
        self.detail_separator.setFixedHeight(1)
        self.detail_separator.setStyleSheet(f"background: {BORDER_COLOR};")
        self.detail_separator.hide()
        self._panel_layout.addWidget(self.detail_separator)

        self.recycle_label = QLabel("")
        self.recycle_label.setFont(QFont(_UI_FONT, self._scale(12)))
        self.recycle_label.setStyleSheet(f"color: {TEXT_DIM};")
        self.recycle_label.setWordWrap(True)
        self.recycle_label.hide()
        self._panel_layout.addWidget(self.recycle_label)

        self.notes_label = self.keep_label
        self.adjustSize()

    def _bump_hide_timer(self) -> None:
        self._hide_timer.stop()
        self._hide_timer.start(int(self.settings.overlay.display_time * 1000))

    def _cancel_hide_timer(self) -> None:
        self._hide_timer.stop()

    def keep_alive(self) -> None:
        if not self._mapped:
            return
        if self._is_wayland():
            self._cancel_hide_timer()
            return
        self._bump_hide_timer()

    @staticmethod
    def _action_color(action_str: str) -> str:
        action_upper = action_str.upper()
        for keyword, c in ACTION_COLORS.items():
            if keyword in action_upper:
                return c
        return "#FFFFFF"

    @staticmethod
    def _build_info_parts(rec: Item, *, in_raid: bool) -> list[str]:
        parts: list[str] = []
        if in_raid and rec.sell_price is not None:
            parts.append(f"Sell: {rec.sell_price:,}\u20a1")
        if rec.stack_size is not None:
            parts.append(f"Stack: {rec.stack_size}")
        need_total = (rec.quest_needed or 0) + (rec.project_needed or 0)
        if need_total > 0:
            parts.append(f"Need: {need_total}")
        if rec.buy_from:
            parts.append(f"Buy: {rec.buy_from}")
        return parts

    def _apply_recommendation(self, rec: Item, *, in_raid: bool) -> None:
        color = self._action_color(rec.action)
        self.action_label.setText(f"\u2192 {rec.action}")
        self.action_label.setStyleSheet(f"color: {color};")
        self.accent_bar.setStyleSheet(f"background: {color};")
        self.info_label.setText(
            "    \u00b7    ".join(self._build_info_parts(rec, in_raid=in_raid))
        )
        keep_text, recycle_text = format_detail_parts(
            recycle_for=rec.recycle_for, keep_for=rec.keep_for
        )
        self._set_detail_section(keep_text, recycle_text)

    def _apply_unknown(self) -> None:
        unknown_color = ACTION_COLORS["UNKNOWN"]
        self.action_label.setText("\u2192 UNKNOWN")
        self.action_label.setStyleSheet(f"color: {unknown_color};")
        self.accent_bar.setStyleSheet(f"background: {unknown_color};")
        self.info_label.setText("")
        self._set_detail_section("Item not in database", "")

    def show_item(
        self, item_name: str, recommendation: Item | None, *, in_raid: bool = False
    ) -> None:
        """Show item info. Sell price is only shown when ``in_raid`` is True."""
        wayland = self._is_wayland()
        same = self._current_item == item_name
        if wayland and self._current_item and self._mapped:
            same = names_match(item_name, self._current_item)
        if same and self.is_visible():
            if wayland:
                self._cancel_hide_timer()
            else:
                self._bump_hide_timer()
            return

        self._current_item = item_name
        self._cancel_hide_timer()
        self.name_label.setText(item_name)

        if recommendation:
            self._apply_recommendation(recommendation, in_raid=in_raid)
        else:
            self._apply_unknown()

        self.move(int(self.overlay_x), int(self.overlay_y))
        self.adjustSize()
        if not (wayland and self._mapped):
            QWidget.show(self)
            self.raise_()
            self._mapped = True
            make_click_through(self)

        if wayland:
            self._cancel_hide_timer()
        else:
            self._bump_hide_timer()

    def _set_detail_section(self, keep_text: str, recycle_text: str) -> None:
        self.separator.hide()
        self.keep_label.hide()
        self.detail_separator.hide()
        self.recycle_label.hide()
        if not keep_text and not recycle_text:
            return
        self.separator.show()
        if keep_text:
            self.keep_label.setText(keep_text)
            self.keep_label.show()
        if keep_text and recycle_text:
            self.detail_separator.show()
        if recycle_text:
            self.recycle_label.setText(recycle_text)
            self.recycle_label.show()

    def hide(self) -> None:
        self._cancel_hide_timer()
        self._current_item = None
        QWidget.hide(self)
        self._mapped = False

    def set_position(self, x: int, y: int) -> None:
        self.overlay_x = x
        self.overlay_y = y

    def is_visible(self) -> bool:
        if self._is_wayland():
            return self._mapped
        return QWidget.isVisible(self)
