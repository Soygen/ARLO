"""Calibration tool (Qt)."""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytesseract
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtGui import QImage
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QGroupBox
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QMessageBox
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QScrollArea
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QTableWidget
from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from arc_helper.config import APP_DIR
from arc_helper.config import OverlaySettings
from arc_helper.config import ScanSettings
from arc_helper.config import Settings
from arc_helper.config import SettingsManager
from arc_helper.config import TooltipCaptureSettings
from arc_helper.config import TooltipRegion
from arc_helper.config import TriggerRegion
from arc_helper.config import TriggerRegion2
from arc_helper.config import get_settings
from arc_helper.config import logger
from arc_helper.database import get_database
from arc_helper.ocr import get_cursor_position
from arc_helper.ocr import get_ocr_engine
from arc_helper.ocr import grab_screen
from arc_helper.ui import prepare_qt_env
from arc_helper.ui.platform import apply_overlay_flags

if TYPE_CHECKING:
    from PIL import Image

_PREVIEW_MAX_WIDTH = 350


class _RegionPreview(QWidget):
    def __init__(self, color: str) -> None:
        super().__init__()
        apply_overlay_flags(self, click_through=True)
        self.setStyleSheet(f"background: {color};")
        self.hide()

    def place(self, x: int, y: int, w: int, h: int) -> None:
        self.setGeometry(x, y, max(1, w), max(1, h))
        self.show()
        self.raise_()


class RegionEditor(QGroupBox):
    def __init__(
        self,
        title: str,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        color: str = "rgba(255,0,0,100)",
        x_label: str = "X",
        y_label: str = "Y",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.preview = _RegionPreview(color)
        form = QFormLayout(self)
        # Avoid clobbering QWidget.x()/y() method attributes.
        self.spin_x = QSpinBox()
        self.spin_x.setRange(-4000, 8000)
        self.spin_x.setValue(x)
        self.spin_y = QSpinBox()
        self.spin_y.setRange(-4000, 8000)
        self.spin_y.setValue(y)
        self.spin_w = QSpinBox()
        self.spin_w.setRange(1, 4000)
        self.spin_w.setValue(w)
        self.spin_h = QSpinBox()
        self.spin_h.setRange(1, 4000)
        self.spin_h.setValue(h)
        for label, box in (
            (x_label, self.spin_x),
            (y_label, self.spin_y),
            ("Width", self.spin_w),
            ("Height", self.spin_h),
        ):
            form.addRow(label, box)
            box.valueChanged.connect(self._on_change)
        btns = QHBoxLayout()
        show_btn = QPushButton("Show")
        show_btn.clicked.connect(self.show_overlay)
        hide_btn = QPushButton("Hide")
        hide_btn.clicked.connect(self.hide_overlay)
        btns.addWidget(show_btn)
        btns.addWidget(hide_btn)
        form.addRow(btns)

    def values(self) -> tuple[int, int, int, int]:
        return (
            self.spin_x.value(),
            self.spin_y.value(),
            self.spin_w.value(),
            self.spin_h.value(),
        )

    def set_values(self, x: int, y: int, w: int, h: int) -> None:
        self.spin_x.setValue(x)
        self.spin_y.setValue(y)
        self.spin_w.setValue(w)
        self.spin_h.setValue(h)

    def as_trigger(self) -> TriggerRegion:
        x, y, w, h = self.values()
        return TriggerRegion(x=x, y=y, width=w, height=h)

    def _on_change(self) -> None:
        if self.preview.isVisible():
            self.show_overlay()

    def show_overlay(self) -> None:
        x, y, w, h = self.values()
        self.preview.place(x, y, w, h)

    def hide_overlay(self) -> None:
        self.preview.hide()


class CalibrationTool(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ARLO - Calibration")
        self.resize(640, 860)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on=True)
        self.settings = get_settings()
        self.ocr = get_ocr_engine()
        self.db = get_database()
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._update_cursor)
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)

        layout.addWidget(
            QLabel(
                "Configure screen regions for ARLO.\n"
                "1. TRIGGER: where INVENTORY text appears\n"
                "2. TOOLTIP: relative to cursor\n"
                "Use Show to visualize each region."
            )
        )

        s = self.settings
        self.trigger1 = RegionEditor(
            "Trigger Region 1 (IN-MENU)",
            s.trigger_region.x,
            s.trigger_region.y,
            s.trigger_region.width,
            s.trigger_region.height,
            color="rgba(255,255,0,90)",
        )
        layout.addWidget(self.trigger1)
        t1 = QPushButton("Test OCR (trigger 1)")
        t1.clicked.connect(lambda: self._test_trigger(self.trigger1, "Trigger 1"))
        layout.addWidget(t1)

        self.trigger2 = RegionEditor(
            "Trigger Region 2 (IN-GAME)",
            s.trigger_region2.x,
            s.trigger_region2.y,
            s.trigger_region2.width,
            s.trigger_region2.height,
            color="rgba(0,120,255,90)",
        )
        layout.addWidget(self.trigger2)
        t2 = QPushButton("Test OCR (trigger 2)")
        t2.clicked.connect(lambda: self._test_trigger(self.trigger2, "Trigger 2"))
        layout.addWidget(t2)

        self.tooltip = RegionEditor(
            "Tooltip capture (offset from cursor)",
            s.tooltip_capture.offset_x,
            s.tooltip_capture.offset_y,
            s.tooltip_capture.width,
            s.tooltip_capture.height,
            color="rgba(0,200,0,90)",
            x_label="Offset X",
            y_label="Offset Y",
        )
        layout.addWidget(self.tooltip)
        tip_row = QHBoxLayout()
        track = QPushButton("Start tracking")
        track.clicked.connect(lambda: self._cursor_timer.start(50))
        stop = QPushButton("Stop tracking")
        stop.clicked.connect(self._stop_tracking)
        tip_test = QPushButton("Test OCR at cursor")
        tip_test.clicked.connect(self._test_tooltip_at_cursor)
        tip_row.addWidget(track)
        tip_row.addWidget(stop)
        tip_row.addWidget(tip_test)
        layout.addLayout(tip_row)

        self.overlay_xy = RegionEditor(
            "Overlay position (tooltip popup)",
            s.overlay.x,
            s.overlay.y,
            280,
            120,
            color="rgba(50,205,50,90)",
        )
        layout.addWidget(self.overlay_xy)

        preview_box = QGroupBox("OCR preview")
        preview_layout = QVBoxLayout(preview_box)
        self.preview_image = QLabel("Click Test OCR to preview")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        preview_layout.addWidget(self.preview_image)
        preview_layout.addWidget(self.result)
        layout.addWidget(preview_box)

        db_box = QGroupBox("Item database")
        db_layout = QHBoxLayout(db_box)
        self.db_label = QLabel(f"Items: {self.db.count()}")
        load_btn = QPushButton("Load CSV…")
        load_btn.clicked.connect(self._load_csv)
        view_btn = QPushButton("View items")
        view_btn.clicked.connect(self._view_items)
        clear_btn = QPushButton("Clear database")
        clear_btn.clicked.connect(self._clear_database)
        db_layout.addWidget(self.db_label)
        db_layout.addWidget(load_btn)
        db_layout.addWidget(view_btn)
        db_layout.addWidget(clear_btn)
        layout.addWidget(db_box)

        actions = QHBoxLayout()
        save = QPushButton("Save configuration")
        save.clicked.connect(self._save)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset_defaults)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(save)
        actions.addWidget(reset)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        layout.addStretch(1)

    def _show_preview(self, image: Image.Image) -> None:
        if image.mode != "RGB":
            image = image.convert("RGB")
        data = image.tobytes("raw", "RGB")
        qimg = QImage(
            data,
            image.width,
            image.height,
            image.width * 3,
            QImage.Format.Format_RGB888,
        ).copy()
        pix = QPixmap.fromImage(qimg)
        if pix.width() > _PREVIEW_MAX_WIDTH:
            pix = pix.scaledToWidth(
                _PREVIEW_MAX_WIDTH, Qt.TransformationMode.SmoothTransformation
            )
        self.preview_image.setPixmap(pix)

    def _test_trigger(self, editor: RegionEditor, name: str) -> None:
        region = editor.as_trigger()
        try:
            image = grab_screen(region.bbox)
            self._show_preview(image)
            found = self.ocr.check_trigger(region)
            if found:
                self.result.setText(f"{name}: ✓ INVENTORY detected!")
            else:
                self.result.setText(f"{name}: ✗ INVENTORY not found")
        except Exception as e:  # ruff: ignore[blind-except]
            logger.warning("Capture/OCR failed: %s", e)
            self.result.setText(f"{name}: ✗ Capture failed ({e})")

    def _stop_tracking(self) -> None:
        self._cursor_timer.stop()
        self.tooltip.hide_overlay()

    def _update_cursor(self) -> None:
        with suppress(Exception):
            c = get_cursor_position()
            ox, oy, w, h = self.tooltip.values()
            self.tooltip.preview.place(c.x + ox, c.y + oy, w, h)

    def _test_tooltip_at_cursor(self) -> None:
        try:
            c = get_cursor_position()
            ox, oy, w, h = self.tooltip.values()
            left, top = c.x + ox, c.y + oy
            image = grab_screen((left, top, left + w, top + h))
            self._show_preview(image)
            processed = self.ocr.preprocess_tooltip(image)
            text = str(pytesseract.image_to_string(processed, config="--psm 6") or "")
            item_name = self.ocr.parse_item_name_from_tooltip(text)
            if item_name:
                self.result.setText(f"✓ Found: '{item_name}'")
            else:
                self.result.setText("✗ No item name detected")
        except Exception as e:  # ruff: ignore[blind-except]
            self.result.setText(f"✗ OCR Error: {e}")

    def _save(self) -> None:
        x1, y1, w1, h1 = self.trigger1.values()
        x2, y2, w2, h2 = self.trigger2.values()
        ox, oy, tw, th = self.tooltip.values()
        px, py, _, _ = self.overlay_xy.values()
        settings = Settings(
            trigger_region=TriggerRegion(x=x1, y=y1, width=w1, height=h1),
            trigger_region2=TriggerRegion2(x=x2, y=y2, width=w2, height=h2),
            tooltip_region=TooltipRegion(x=0, y=0, width=100, height=100),
            tooltip_capture=TooltipCaptureSettings(
                offset_x=ox, offset_y=oy, width=tw, height=th
            ),
            overlay=OverlaySettings(
                x=px,
                y=py,
                display_time=self.settings.overlay.display_time,
                cooldown=self.settings.overlay.cooldown,
            ),
            scan=ScanSettings(
                trigger_scan_interval=self.settings.scan.trigger_scan_interval,
                tooltip_scan_interval=self.settings.scan.tooltip_scan_interval,
            ),
            map_trigger_region=self.settings.map_trigger_region,
            map_viewport=self.settings.map_viewport,
            screen_backend=self.settings.screen_backend,
            hotkey_key=self.settings.hotkey_key,
            map_hotkey=self.settings.map_hotkey,
            map_find_hotkey=self.settings.map_find_hotkey,
            debug_mode=self.settings.debug_mode,
            show_capture_area=self.settings.show_capture_area,
        )
        settings.save_to_env()
        SettingsManager.reload()
        self.settings = get_settings()
        QMessageBox.information(self, "Saved", "Configuration saved to .env file!")
        logger.info("Calibration settings saved")

    def _reset_defaults(self) -> None:
        self.trigger1.set_values(0, 0, 1, 1)
        self.trigger2.set_values(0, 0, 1, 1)
        self.tooltip.set_values(0, 0, 1, 1)

    def _refresh_db_count(self) -> None:
        self.db_label.setText(f"Items: {self.db.count()}")

    def _load_csv(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Items CSV",
            str(APP_DIR),
            "CSV files (*.csv);;All files (*)",
        )
        if not filepath:
            return
        try:
            self.db.load_csv(filepath)
            self._refresh_db_count()
            QMessageBox.information(
                self,
                "Success",
                f"Loaded items from {Path(filepath).name}\n\n"
                f"Database now contains {self.db.count()} items.",
            )
        except Exception as e:  # ruff: ignore[blind-except]
            QMessageBox.critical(self, "Error", f"Failed to load CSV:\n\n{e}")

    def _view_items(self) -> None:
        items = self.db.get_all_items()
        if not items:
            QMessageBox.information(
                self,
                "Database Empty",
                "No items in database.\n\nLoad a CSV file to add items.",
            )
            return
        win = QWidget()
        win.setWindowTitle("Item Database")
        win.resize(850, 400)
        win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on=True)
        layout = QVBoxLayout(win)
        table = QTableWidget(len(items), 6)
        table.setHorizontalHeaderLabels(
            ["Item Name", "Action", "Sell Price", "Stack", "Recycle For", "Keep For"]
        )
        for row, item in enumerate(items):
            values = (
                item.name,
                item.action,
                f"{item.sell_price:,}" if item.sell_price is not None else "",
                str(item.stack_size) if item.stack_size is not None else "",
                item.recycle_for or "",
                item.keep_for or "",
            )
            for col, val in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(val))
        table.resizeColumnsToContents()
        layout.addWidget(table)
        layout.addWidget(QLabel(f"Total items: {len(items)}"))
        # Keep a reference so GC does not close it immediately.
        self._items_window = win
        win.show()

    def _clear_database(self) -> None:
        count = self.db.count()
        if count == 0:
            QMessageBox.information(
                self, "Database Empty", "Database is already empty."
            )
            return
        confirm = QMessageBox.question(
            self,
            "Confirm Clear",
            f"Are you sure you want to delete all {count} items?\n\n"
            "This cannot be undone.",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.db.clear()
            self._refresh_db_count()
            QMessageBox.information(self, "Cleared", "Database cleared successfully.")

    def closeEvent(  # ruff: ignore[invalid-function-name]
        self, event: QCloseEvent | None
    ) -> None:
        self.trigger1.hide_overlay()
        self.trigger2.hide_overlay()
        self.tooltip.hide_overlay()
        self.overlay_xy.hide_overlay()
        self._cursor_timer.stop()
        if event is not None:
            super().closeEvent(event)


def main() -> None:
    prepare_qt_env()
    app = QApplication.instance() or QApplication(sys.argv)
    tool = CalibrationTool()
    tool.show()
    sys.exit(app.exec())
