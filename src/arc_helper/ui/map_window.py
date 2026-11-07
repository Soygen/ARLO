"""F8 map filter window (Qt / QGraphicsView)."""

from __future__ import annotations

import logging
import math
from threading import Thread
from typing import TYPE_CHECKING

from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtGui import QImage
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPen
from PySide6.QtGui import QPixmap
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QCheckBox
from PySide6.QtWidgets import QComboBox
from PySide6.QtWidgets import QGraphicsEllipseItem
from PySide6.QtWidgets import QGraphicsPixmapItem
from PySide6.QtWidgets import QGraphicsScene
from PySide6.QtWidgets import QGraphicsView
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from arc_helper.config import DATA_DIR
from arc_helper.map_data import CATEGORIES
from arc_helper.map_data import CATEGORY_COLORS
from arc_helper.map_data import MAP_DISPLAY_NAMES
from arc_helper.map_data import MapCatalog
from arc_helper.map_data import MapId
from arc_helper.map_data import MapMarker
from arc_helper.map_data import MapMeta
from arc_helper.map_data import get_map_catalog
from arc_helper.map_data import latlng_to_norm
from arc_helper.map_data import map_display_name
from arc_helper.map_data import marker_label
from arc_helper.map_data import reference_path
from arc_helper.map_filter import MapFilterState
from arc_helper.map_filter import resolve_markers

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("arc_helper")

BG = "#0e1117"
PANEL = "#161b22"
FG = "#e6edf3"
FG_DIM = "#8b949e"
ACCENT = "#58a6ff"
PLAYER_COLOR = "#2dd4bf"
MAX_PINS = 2500
DRAW_PIN_CAP = 400
DISPLAY_MAX_EDGE = 1536
SEARCH_DEBOUNCE_MS = 120
FILTER_NOTIFY_DEBOUNCE_MS = 250
_MIN_SEARCH_LEN = 3


def _pil_to_qpixmap(im: Image.Image) -> QPixmap:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    qimg = QImage(
        data,
        im.width,
        im.height,
        im.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    return QPixmap.fromImage(qimg.copy())


class _MapView(QGraphicsView):
    def __init__(self, window: MapWindow) -> None:
        super().__init__()
        self._win = window
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#010409"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def wheelEvent(self, event: QWheelEvent) -> None:  # ruff: ignore[invalid-function-name]
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class MapWindow(QWidget):
    """Filter/config UI for in-game map pins (F8)."""

    # Emitted from the loose-spot worker; Qt queues it onto the GUI thread.
    loose_spots_ready = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        catalog: MapCatalog | None = None,
        filter_state: MapFilterState | None = None,
        on_sync_request: Callable[[], None] | None = None,
        on_filter_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog or get_map_catalog()
        self.filter_state = filter_state or MapFilterState()
        self.on_sync_request = on_sync_request
        self.on_filter_change = on_filter_change

        self.setWindowTitle("ARLO Map filters")
        self.resize(960, 720)
        self.setStyleSheet(f"background: {BG}; color: {FG};")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on=True)

        self.current_map_id = self.filter_state.map_id
        self._meta: MapMeta | None = None
        self._markers: list[MapMarker] = []
        self._base_image: Image.Image | None = None
        self._base_image_mtime: float | None = None
        self._player: tuple[float, float, float | None] | None = None
        self._item_filter: str | None = self.filter_state.item_filter
        self._suspend_filter_notify = False
        self._visible = False
        self._loose_warm_inflight = False

        self._scene = QGraphicsScene(self)
        self._basemap_item: QGraphicsPixmapItem | None = None
        self._pin_items: list[QGraphicsEllipseItem] = []
        self._player_item: QGraphicsEllipseItem | None = None

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh_markers)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._emit_filter_change)
        self.loose_spots_ready.connect(self._apply_loose_spot_refresh)

        self._build_ui()
        self._load_initial_map()
        self.hide()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QWidget()
        top.setStyleSheet(f"background: {PANEL};")
        top_l = QHBoxLayout(top)
        top_l.addWidget(QLabel("Map"))
        self._map_menu = QComboBox()
        maps: list[tuple[str, str]] = []
        if self.catalog:
            maps = [
                (m.map_id, m.display_name or map_display_name(m.map_id))
                for m in self.catalog.list_maps()
            ]
        if not maps:
            maps = [(str(mid), name) for mid, name in MAP_DISPLAY_NAMES.items()]
        for mid, name in maps:
            self._map_menu.addItem(name, mid)
        idx = self._map_menu.findData(self.filter_state.map_id)
        if idx >= 0:
            self._map_menu.setCurrentIndex(idx)
        self._map_menu.currentIndexChanged.connect(lambda _i: self.reload_map())
        top_l.addWidget(self._map_menu)

        top_l.addWidget(QLabel("Search"))
        self._search = QLineEdit(self.filter_state.search)
        self._search.setPlaceholderText("item or marker…")
        self._search.returnPressed.connect(self.refresh_markers)
        self._search.textChanged.connect(self._on_search_key)
        top_l.addWidget(self._search, stretch=1)

        find_btn = QPushButton("Find")
        find_btn.clicked.connect(self.refresh_markers)
        top_l.addWidget(find_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_search)
        top_l.addWidget(clear_btn)

        self._sync_status = QLabel("Sync: none")
        self._sync_status.setStyleSheet(f"color: {FG_DIM};")
        top_l.addWidget(self._sync_status)
        sync_btn = QPushButton("Sync now")
        sync_btn.setStyleSheet(f"color: {ACCENT};")
        sync_btn.clicked.connect(self._request_sync)
        top_l.addWidget(sync_btn)
        root.addWidget(top)

        hint = QLabel(
            "Pins draw on the in-game MAP. This window sets search/filters (F8)."
        )
        hint.setStyleSheet(f"background: {PANEL}; color: {FG_DIM}; padding: 4px 8px;")
        root.addWidget(hint)

        filt = QWidget()
        filt.setStyleSheet(f"background: {PANEL};")
        filt_l = QHBoxLayout(filt)
        self._category_boxes: dict[str, QCheckBox] = {}
        for cat in CATEGORIES:
            color = CATEGORY_COLORS.get(cat, FG)
            cb = QCheckBox(cat.title())
            cb.setChecked(cat in self.filter_state.categories)
            cb.setStyleSheet(f"color: {color};")
            cb.stateChanged.connect(lambda _s: self.refresh_markers())
            self._category_boxes[cat] = cb
            filt_l.addWidget(cb)
        self._locked_only = QCheckBox("Locked door")
        self._locked_only.setChecked(self.filter_state.locked_only)
        self._locked_only.setStyleSheet(f"color: {FG_DIM};")
        self._locked_only.stateChanged.connect(lambda _s: self.refresh_markers())
        filt_l.addWidget(self._locked_only)
        filt_l.addStretch(1)
        root.addWidget(filt)

        body = QHBoxLayout()
        self._view = _MapView(self)
        self._view.setScene(self._scene)
        body.addWidget(self._view, stretch=1)

        side = QWidget()
        side.setFixedWidth(220)
        side.setStyleSheet(f"background: {PANEL};")
        side_l = QVBoxLayout(side)
        side_l.addWidget(QLabel("Results"))
        self._results = QListWidget()
        self._results.currentRowChanged.connect(self._on_result_select)
        side_l.addWidget(self._results, stretch=1)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"color: {FG_DIM};")
        side_l.addWidget(self._count_label)
        body.addWidget(side)
        root.addLayout(body, stretch=1)

    def _load_initial_map(self) -> None:
        if self.catalog:
            metas = self.catalog.list_maps()
            if metas:
                ids = {m.map_id for m in metas}
                pick = self.filter_state.map_id
                if pick not in ids:
                    pick = (
                        MapId.BLUE_GATE if MapId.BLUE_GATE in ids else metas[0].map_id
                    )
                idx = self._map_menu.findData(pick)
                if idx >= 0:
                    self._map_menu.setCurrentIndex(idx)
                self.current_map_id = pick
                self.filter_state.map_id = pick
        self.reload_map()

    def show(self) -> None:
        QWidget.show(self)
        self.raise_()
        self.activateWindow()
        self._visible = True
        # Pick up restitched basemaps without requiring an app restart.
        if self._basemap_file_changed():
            self.reload_map()
        else:
            QTimer.singleShot(50, self._fit_view)
            self._redraw()

    def hide(self) -> None:
        QWidget.hide(self)
        self._visible = False

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def is_visible(self) -> bool:
        return self._visible

    def push_filter_state(self) -> None:
        self.filter_state.map_id = (self._map_menu.currentData() or "").strip()
        self.filter_state.search = self._search.text().strip()
        self.filter_state.item_filter = self._item_filter
        self.filter_state.categories = {
            c for c, box in self._category_boxes.items() if box.isChecked()
        }
        self.filter_state.locked_only = self._locked_only.isChecked()
        self.current_map_id = self.filter_state.map_id

    def _basemap_file_changed(self) -> bool:
        map_id = (self._map_menu.currentData() or "").strip()
        path = reference_path(map_id)
        if not path.is_file() and self._meta and self._meta.reference_rel:
            alt = DATA_DIR / self._meta.reference_rel
            if alt.is_file():
                path = alt
        if not path.is_file():
            return self._base_image is not None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        return self._base_image_mtime != mtime

    def reload_map(self) -> None:
        map_id = (self._map_menu.currentData() or "").strip()
        self.current_map_id = map_id
        self.filter_state.map_id = map_id
        self._meta = self.catalog.get_meta(map_id) if self.catalog else None
        self._base_image = None
        self._base_image_mtime = None
        path = reference_path(map_id)
        if not path.is_file() and self._meta and self._meta.reference_rel:
            alt = DATA_DIR / self._meta.reference_rel
            if alt.is_file():
                path = alt
        if path.is_file():
            try:
                raw = Image.open(path).convert("RGBA")
                self._base_image = self._for_display(raw)
                self._base_image_mtime = path.stat().st_mtime
            except OSError as e:
                logger.warning("Could not load map image %s: %s", path, e)
        if self._base_image is None and self._meta is not None:
            w = max(int(self._meta.ref_width or 0), 1024)
            h = max(int(self._meta.ref_height or 0), 1024)
            self._base_image = self._for_display(
                Image.new("RGBA", (w, h), (14, 17, 23, 255))
            )
        self.refresh_markers()
        self._fit_view()

    @staticmethod
    def _for_display(im: Image.Image) -> Image.Image:
        w, h = im.size
        edge = max(w, h)
        if edge <= DISPLAY_MAX_EDGE:
            return im
        scale = DISPLAY_MAX_EDGE / edge
        return im.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.BILINEAR,
        )

    def _fit_view(self) -> None:
        if self._basemap_item is not None:
            self._view.fitInView(self._basemap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def refresh_markers(self) -> None:
        self.push_filter_state()
        if not self.catalog or not self.filter_state.map_id:
            self._markers = []
            self._fill_results()
            self._redraw()
            self._schedule_filter_notify()
            return
        origin = None
        if self._player:
            origin = (self._player[0], self._player[1])
        self._markers = resolve_markers(
            self.catalog,
            self.filter_state,
            origin=origin,
            limit=MAX_PINS,
            meta=self._meta,
        )
        self._fill_results()
        self._redraw()
        self._schedule_filter_notify()
        needle = (
            self.filter_state.item_filter or self.filter_state.search or ""
        ).strip()
        if needle and not self._loose_warm_inflight:
            map_id = self.filter_state.map_id
            self._loose_warm_inflight = True
            Thread(
                target=self._warm_loose_spots,
                args=(map_id, needle),
                daemon=True,
                name="loose-spots",
            ).start()

    def _warm_loose_spots(self, map_id: str, item_name: str) -> None:
        try:
            if not self.catalog:
                return
            changed = self.catalog.ensure_loose_spots(map_id, item_name)
            if changed:
                self.loose_spots_ready.emit()
        except Exception as e:  # ruff: ignore[blind-except]
            logger.debug("Loose-spot warm failed: %s", e)
        finally:
            self._loose_warm_inflight = False

    def _apply_loose_spot_refresh(self) -> None:
        self._loose_warm_inflight = True
        try:
            self.refresh_markers()
        finally:
            self._loose_warm_inflight = False

    def _schedule_filter_notify(self) -> None:
        if self._suspend_filter_notify or not self.on_filter_change:
            return
        self._filter_timer.start(FILTER_NOTIFY_DEBOUNCE_MS)

    def _emit_filter_change(self) -> None:
        if self._suspend_filter_notify or not self.on_filter_change:
            return
        self.on_filter_change()

    def _fill_results(self) -> None:
        self._results.clear()
        show_loot = bool(
            (self.filter_state.search or "").strip()
            or (self.filter_state.item_filter or "").strip()
        )
        for m in self._markers[:200]:
            label = marker_label(m, with_loot=show_loot and bool(m.loot_areas))
            dist = f"  ({m.distance:.0f})" if m.distance is not None else ""
            self._results.addItem(f"{label}{dist}")
        total = len(self._markers)
        extra = " (sampled)" if total >= MAX_PINS else ""
        if total > DRAW_PIN_CAP:
            extra = (extra + f" · drawing {DRAW_PIN_CAP}").strip()
        self._count_label.setText(f"{total} pins{extra}")

    def _on_result_select(self, row: int) -> None:
        if row < 0 or row >= len(self._markers):
            return
        m = self._markers[row]
        self.center_on_latlng(m.lat, m.lng)

    def select_map(self, map_id: str) -> None:
        mid = (map_id or "").strip()
        if not mid or mid == self.current_map_id:
            return
        idx = self._map_menu.findData(mid)
        if idx >= 0:
            self._map_menu.setCurrentIndex(idx)
        else:
            self.reload_map()

    def find_item(self, item_name: str, *, map_id: str | None = None) -> None:
        if map_id:
            self.select_map(map_id)
        self._item_filter = item_name.strip()
        self._search.setText(item_name.strip())
        self.refresh_markers()

    def set_player_position(
        self,
        map_id: str,
        lat: float,
        lng: float,
        *,
        facing: float | None = None,
        status: str = "live",
    ) -> None:
        if map_id and map_id != (self._map_menu.currentData() or ""):
            self.select_map(map_id)
        self._player = (lat, lng, facing)
        self.set_sync_status(status)
        self.refresh_markers()
        self.center_on_latlng(lat, lng)

    def set_sync_status(self, status: str) -> None:
        self._sync_status.setText(f"Sync: {status}")

    def set_suspend_filter_notify(self, *, suspended: bool) -> None:
        self._suspend_filter_notify = suspended

    def clear_player_position(self, *, status: str = "none") -> None:
        self._player = None
        self.set_sync_status(status)
        self.refresh_markers()

    def center_on_latlng(self, lat: float, lng: float) -> None:
        if not self._meta or not self._base_image:
            return
        nx, ny = latlng_to_norm(lat, lng, self._meta)
        iw, ih = self._base_image.size
        self._view.centerOn(QPointF(nx * iw, ny * ih))

    def _clear_search(self) -> None:
        self._search.clear()
        self._item_filter = None
        self.refresh_markers()

    def _on_search_key(self, text: str) -> None:
        if len(text.strip()) < _MIN_SEARCH_LEN:
            return
        self._item_filter = None
        self._search_timer.start(SEARCH_DEBOUNCE_MS)

    def _request_sync(self) -> None:
        self.set_sync_status("capturing…")
        if self.on_sync_request:
            self.on_sync_request()

    def _draw_pins(self, iw: int, ih: int) -> None:
        """Add marker ellipses to the scene up to DRAW_PIN_CAP."""
        r = 4.0
        drawn = 0
        for m in self._markers:
            if not self._meta or drawn >= DRAW_PIN_CAP:
                break
            nx, ny = latlng_to_norm(m.lat, m.lng, self._meta)
            # True CRS only — never clamp. Markers past the painted reference
            # (content-cropped worldExtent) are omitted, not dragged to the edge.
            if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
                continue
            x, y = nx * iw, ny * ih
            color = QColor(CATEGORY_COLORS.get(m.category, "#ffffff"))
            ell = self._scene.addEllipse(
                x - r, y - r, r * 2, r * 2, QPen(Qt.PenStyle.NoPen), color
            )
            self._pin_items.append(ell)
            drawn += 1

    def _draw_player(self, iw: int, ih: int) -> None:
        """Draw the player chevron if position is known and on-basemap."""
        if not self._player or not self._meta:
            return
        nx, ny = latlng_to_norm(self._player[0], self._player[1], self._meta)
        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            return
        x, y = nx * iw, ny * ih
        pr = 8.0
        self._player_item = self._scene.addEllipse(
            x - pr,
            y - pr,
            pr * 2,
            pr * 2,
            QPen(QColor("white"), 2),
            QColor(PLAYER_COLOR),
        )
        facing = self._player[2]
        if facing is not None:
            tip = 14.0
            self._scene.addLine(
                x,
                y,
                x + tip * math.cos(facing),
                y - tip * math.sin(facing),
                QPen(QColor(PLAYER_COLOR), 2),
            )

    def _redraw(self) -> None:
        for item in self._pin_items:
            self._scene.removeItem(item)
        self._pin_items.clear()
        if self._player_item is not None:
            self._scene.removeItem(self._player_item)
            self._player_item = None

        if self._base_image is None:
            self._scene.clear()
            self._basemap_item = None
            self._scene.addText(
                "No basemap image cached.\nRun: uv run python update_db.py --maps-only"
            )
            return

        pix = _pil_to_qpixmap(self._base_image)
        if self._basemap_item is None:
            self._scene.clear()
            self._basemap_item = self._scene.addPixmap(pix)
        else:
            self._basemap_item.setPixmap(pix)
        # Always lock the scene to the basemap — off-image ellipses must not
        # expand the view into the black void around the PNG.
        self._scene.setSceneRect(self._basemap_item.boundingRect())

        iw, ih = self._base_image.size
        self._draw_pins(iw, ih)
        self._draw_player(iw, ih)
