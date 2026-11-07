"""
ARLO - Main Application.
Coordinates OCR scanning and overlay display.
"""

import os
import queue
import signal
import sys
import threading
import time
import traceback
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from enum import auto
from pathlib import Path
from threading import ExceptHookArgs
from threading import Thread
from types import TracebackType
from typing import ClassVar

import numpy as np
from dotenv import load_dotenv
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QWidget

from arc_helper.config import APP_DIR
from arc_helper.config import Settings
from arc_helper.config import SettingsManager
from arc_helper.config import get_settings
from arc_helper.config import logger
from arc_helper.database import Database
from arc_helper.database import Item
from arc_helper.database import get_database
from arc_helper.database import names_match
from arc_helper.hotkey import HotkeyMonitor
from arc_helper.hotkey import TapHotkeyMonitor
from arc_helper.hotkey import get_hotkey_monitor
from arc_helper.hotkey import get_map_find_tap_monitor
from arc_helper.hotkey import get_map_tap_monitor
from arc_helper.map_data import get_map_catalog
from arc_helper.map_filter import MapFilterState
from arc_helper.map_filter import resolve_markers
from arc_helper.map_sync import MapSyncEngine
from arc_helper.map_sync import ViewportRegistration
from arc_helper.map_sync import capture_map_thumb
from arc_helper.map_sync import latlng_to_screen
from arc_helper.map_sync import thumbs_differ
from arc_helper.ocr import OCREngine
from arc_helper.ocr import OCREngineManager
from arc_helper.ocr import get_ocr_engine
from arc_helper.progress import ProgressProfile
from arc_helper.progress import apply_progress
from arc_helper.progress import load_progress
from arc_helper.progress_import_server import IMPORT_HOST
from arc_helper.progress_import_server import IMPORT_PORT
from arc_helper.progress_import_server import account_sync_last_synced_at
from arc_helper.progress_import_server import account_sync_mtime
from arc_helper.progress_import_server import format_progress_status
from arc_helper.progress_import_server import parse_synced_at
from arc_helper.progress_import_server import start_import_server
from arc_helper.progress_import_server import stop_import_server
from arc_helper.projects import ProjectCatalog
from arc_helper.projects import get_project_catalog
from arc_helper.quests import QuestCatalog
from arc_helper.quests import get_quest_catalog
from arc_helper.resolution_profiles import get_profile_manager
from arc_helper.screen import ScreenCaptureUnavailable
from arc_helper.traders import TraderCatalog
from arc_helper.traders import get_trader_catalog
from arc_helper.ui import InGameMapPins
from arc_helper.ui import MapWindow
from arc_helper.ui import OverlayPin
from arc_helper.ui import OverlayWindow
from arc_helper.ui import StatusWindow
from arc_helper.ui import prepare_qt_env
from arc_helper.ui.platform import apply_overlay_flags

# How long the startup / post-import progress flash stays on the status pill.
_PROGRESS_FLASH_MS = 5000
_MAP_MISS_STREAK_HINT = 8

load_dotenv(Path(__file__).with_name(".env"), override=False)


class ScannerState(Enum):
    """State machine for the scanner."""

    IDLE = auto()  # Scanning for trigger (INVENTORY)
    ACTIVE = auto()  # Inventory detected, scanning tooltip
    PAUSED = auto()  # Temporarily paused
    STOPPED = auto()  # Fully stopped


class DebugOverlay(QWidget):
    """Semi-transparent overlay showing the tooltip capture area."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        apply_overlay_flags(self, click_through=True)
        self.setWindowTitle("Capture Area")
        self.setStyleSheet("background: rgba(255,0,0,80);")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_position)
        self._timer.start(50)
        self.show()

    def _update_position(self) -> None:
        with suppress(Exception):
            from arc_helper.ocr import get_cursor_position

            cursor = get_cursor_position()
            x = cursor.x + self.settings.tooltip_capture.offset_x
            y = cursor.y + self.settings.tooltip_capture.offset_y
            w = self.settings.tooltip_capture.width
            h = self.settings.tooltip_capture.height
            self.setGeometry(int(x), int(y), int(w), int(h))

    def teardown(self) -> None:
        """Close the debug overlay (avoid QWidget.destroy override)."""
        self.close()


@dataclass
class ScannerStats:
    """Statistics for the scanner."""

    trigger_scans: int = 0
    tooltip_scans: int = 0
    items_detected: int = 0
    items_found_in_db: int = 0
    last_item: str | None = None
    last_item_time: float = 0


@dataclass
class Scanner:
    """
    Main scanner that coordinates OCR and overlay.

    Uses a two-phase approach:
    1. Low-frequency scan for "INVENTORY" text (trigger)
    2. When triggered, high-frequency scan for item names
    """

    overlay: OverlayWindow
    status: StatusWindow
    root: object | None = None
    db: Database = field(default_factory=get_database)
    state: ScannerState = ScannerState.IDLE
    stats: ScannerStats = field(default_factory=ScannerStats)
    hotkey: HotkeyMonitor = field(default_factory=get_hotkey_monitor)
    # Optional MetaForge/Embark-derived progress; filters finished keep_for uses
    progress: ProgressProfile | None = None
    quest_catalog: QuestCatalog | None = None
    project_catalog: ProjectCatalog | None = None
    trader_catalog: TraderCatalog | None = None

    # UI updates are posted here for the main (GUI) thread to apply. Qt
    # widgets are main-thread only — Application drains this queue on a QTimer.
    ui_queue: "queue.Queue[tuple]" = field(default_factory=queue.Queue)

    # Cooldown tracking
    _last_shown_item: str = ""
    _last_shown_time: float = 0
    _trigger_check_counter: int = 0

    # Wayland-only: tolerate flaky INVENTORY / tooltip OCR without remounting
    _wayland_stable: bool = False
    _trigger_miss_streak: int = 0
    _TRIGGER_MISS_LIMIT: ClassVar[int] = 2

    # Hotkey override (Ctrl+Shift to force tooltip scanning)
    _hotkey_override: bool = False

    # True when inventory was detected via trigger_region2 (in-raid)
    _in_raid: bool = False

    # Thread control
    _running: bool = False
    _scan_thread: Thread | None = None

    def start(self) -> None:
        """Start the scanner in a background thread."""
        if self._running:
            return

        self._running = True
        self.state = ScannerState.IDLE
        from arc_helper.screen import get_backend

        self._wayland_stable = get_backend().name == "wayland"
        if self._wayland_stable:
            logger.info(
                "Wayland overlay stability: hide after display time with no item, "
                "trigger miss hysteresis"
            )
        self.hotkey.start()
        self._scan_thread = Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()
        logger.info("Scanner started")

    def stop(self) -> None:
        """Stop the scanner."""
        self._running = False
        self.state = ScannerState.STOPPED
        if self._scan_thread:
            self._scan_thread.join(timeout=2.0)
        self.hotkey.stop()
        logger.info("Scanner stopped")

    def pause(self) -> None:
        """Pause scanning."""
        self.state = ScannerState.PAUSED

    def resume(self) -> None:
        """Resume scanning."""
        self.state = ScannerState.IDLE

    def _scan_loop(self) -> None:
        """Main scanning loop running in background thread."""
        settings = get_settings()
        ocr = get_ocr_engine()

        while self._running:  # ruff: ignore[too-many-nested-blocks]
            try:
                if self.state == ScannerState.PAUSED:
                    time.sleep(0.1)
                    continue

                if self.state == ScannerState.STOPPED:
                    break

                # Check hotkey override (Ctrl+Shift)
                hotkey_now = self.hotkey.is_held()

                # Phase 1: Check for trigger (INVENTORY) or hotkey
                if self.state == ScannerState.IDLE:
                    # MAP screen still shows the INVENTORY tab label in the top
                    # nav — suppress inventory scan when map chrome is visible.
                    if MapSyncEngine.detect_map_open():
                        self._update_status("map")
                        self.ui_queue.put(("map_mode", True))
                        time.sleep(settings.scan.trigger_scan_interval)
                        self.stats.trigger_scans += 1
                        continue

                    self.ui_queue.put(("map_mode", False))
                    self._update_status("scanning")

                    if hotkey_now:
                        # Hotkey pressed - enter active mode via override
                        self._hotkey_override = True
                        self._in_raid = False
                        self.state = ScannerState.ACTIVE
                        self._trigger_miss_streak = 0
                        self._update_status("hotkey")
                        logger.info("Hotkey override - activating tooltip scanner")
                    else:
                        trigger_idx = ocr.check_trigger_which(
                            [settings.trigger_region, settings.trigger_region2]
                        )
                        if trigger_idx is not None:
                            # Trigger detected! Switch to active mode
                            self._hotkey_override = False
                            self._in_raid = trigger_idx == 1  # region2 = in-raid
                            self.state = ScannerState.ACTIVE
                            self._trigger_miss_streak = 0
                            self._update_status("active")
                            context = "in-raid" if self._in_raid else "menu"
                            logger.info(
                                "INVENTORY detected (%s) - activating tooltip scanner",
                                context,
                            )
                        else:
                            # Wait before next trigger scan
                            time.sleep(settings.scan.trigger_scan_interval)

                    self.stats.trigger_scans += 1

                # Phase 2: Active mode - scan tooltip
                elif self.state == ScannerState.ACTIVE:
                    if self._hotkey_override:
                        # Hotkey mode: stay active as long as hotkey is held
                        if not hotkey_now:
                            self._hotkey_override = False
                            self._enter_idle("Hotkey released - returning to idle")
                            continue
                    else:
                        # Inventory mode: leave if MAP opens or INVENTORY closes
                        self._trigger_check_counter += 1
                        should_check_trigger = self._trigger_check_counter % 3 == 0

                        if should_check_trigger:
                            if MapSyncEngine.detect_map_open():
                                self._enter_idle(
                                    "MAP screen open - leaving inventory scan"
                                )
                                self.ui_queue.put(("map_mode", True))
                                continue
                            if self._inventory_closed(ocr, settings):
                                self._enter_idle("INVENTORY closed - returning to idle")
                                continue

                    # Scan tooltip at cursor position
                    item_name = ocr.extract_item_name_at_cursor()
                    self.stats.tooltip_scans += 1

                    if item_name:
                        self._handle_detected_item(item_name)
                    else:
                        # Wayland: one empty frame is normal. A gap as long as
                        # OVERLAY_DISPLAY_TIME means the tooltip is gone.
                        self._handle_empty_tooltip()

                    # Wait before next tooltip scan
                    time.sleep(settings.scan.tooltip_scan_interval)

            except ScreenCaptureUnavailable as e:
                logger.error(f"Screen capture lost permanently: {e}")
                self._update_status("error")
                self.state = ScannerState.STOPPED
                break  # latch: no point scanning without capture
            except Exception as e:  # ruff: ignore[blind-except]
                logger.error(f"Scanner error: {e}")
                self._update_status("error")
                time.sleep(1.0)  # Back off on error

    def _enter_idle(self, reason: str) -> None:
        """Return to idle. On Wayland, dismiss the sticky overlay explicitly."""
        self.state = ScannerState.IDLE
        self._in_raid = False
        self._trigger_miss_streak = 0
        self._last_shown_item = ""
        if self._wayland_stable:
            self.ui_queue.put(("overlay_hide",))
        self._update_status("scanning")
        logger.info(reason)

    def _inventory_closed(self, ocr: OCREngine, settings: Settings) -> bool:
        """True if the INVENTORY trigger is gone.

        On Wayland, a single failed OCR of the trigger word was bouncing
        ACTIVE↔IDLE and flickering the popup. Require consecutive misses.
        X11/Windows keep the original one-miss behavior.

        Also refreshes ``_in_raid`` from which trigger region still matches.
        """
        regions = [settings.trigger_region, settings.trigger_region2]
        trigger_idx = ocr.check_trigger_which(regions)
        if trigger_idx is not None:
            self._trigger_miss_streak = 0
            self._in_raid = trigger_idx == 1
            return False

        if not self._wayland_stable:
            self._in_raid = False
            return True

        self._trigger_miss_streak += 1
        if self._trigger_miss_streak >= self._TRIGGER_MISS_LIMIT:
            self._in_raid = False
            return True
        return False

    def _handle_empty_tooltip(self) -> None:
        """No item under the cursor.

        On Wayland a single empty read is not an item change, so the popup
        stays through brief OCR misses. Once the gap reaches
        ``OVERLAY_DISPLAY_TIME``, hide it — including after leaving the game,
        when the inventory trigger can still false-match.
        """
        if not (self._wayland_stable and self._last_shown_item):
            return
        settings = get_settings()
        if time.time() - self._last_shown_time >= settings.overlay.display_time:
            self._last_shown_item = ""
            self.ui_queue.put(("overlay_hide",))
            return
        self.ui_queue.put(("overlay_keep",))

    def _handle_detected_item(self, item_name: str) -> None:
        """Handle a detected item name."""
        settings = get_settings()
        current_time = time.time()

        # Wayland: same item (OCR-tolerant) → heartbeat only, no remount.
        # Also bypasses cooldown so a short DISPLAY_TIME cannot dismiss us.
        if (
            self._wayland_stable
            and self._last_shown_item
            and names_match(item_name, self._last_shown_item)
        ):
            self.ui_queue.put(("overlay_keep",))
            self._last_shown_time = current_time
            return

        # Check cooldown - don't spam the same item
        if (
            item_name == self._last_shown_item
            and current_time - self._last_shown_time < settings.overlay.cooldown
        ):
            return

        self.stats.items_detected += 1
        self.stats.last_item = item_name
        self.stats.last_item_time = current_time

        # Look up in database, then strip finished quest/workshop/project uses
        recommendation = self.db.lookup(item_name)
        if recommendation is not None:
            recommendation = apply_progress(
                recommendation,
                self.progress,
                self.quest_catalog,
                self.project_catalog,
                self.trader_catalog,
            )

        if recommendation:
            self.stats.items_found_in_db += 1
            logger.debug(f"Found: {item_name} → {recommendation.action}")
        else:
            logger.debug(f"Unknown item: {item_name}")
            # Log to missing items file for easier database updates
            self.db.log_missing_item(item_name)

        # Show overlay (must be done on main thread)
        self._show_overlay(item_name, recommendation, in_raid=self._in_raid)

        # Track OCR text (not DB name) so the next keep/compare matches
        self._last_shown_item = item_name
        self._last_shown_time = current_time

    def _show_overlay(
        self, item_name: str, recommendation: Item | None, *, in_raid: bool = False
    ) -> None:
        """Queue an overlay update for the main (GUI) thread to apply."""
        self.ui_queue.put(("overlay", item_name, recommendation, in_raid))

    def _update_status(self, status: str) -> None:
        """Queue a status update for the main (GUI) thread to apply."""
        self.ui_queue.put(("status", status))


class Application:
    """Main application controller."""

    def __init__(self) -> None:
        """Initialize the application."""
        # Load settings
        self.settings = get_settings()

        # Initialize database
        self.db = get_database()

        # Optional player progress. account-sync.json is the raw account-sync body.
        self.progress = load_progress()
        if self.progress is not None:
            logger.info(
                "Progress profile loaded (%s quests done, %s workshop stations)",
                len(self.progress.completed_quests),
                len(self.progress.workshop_levels),
            )
        else:
            logger.info("No account-sync.json — showing full keep recommendations")

        # Structured catalogs for remaining-need totals and buy hints
        self.quest_catalog = get_quest_catalog()
        self.project_catalog = get_project_catalog()
        self.trader_catalog = get_trader_catalog()
        if self.quest_catalog is not None:
            logger.info(
                "Quest catalog loaded (%s quests)",
                self.quest_catalog.count(),
            )
        if self.project_catalog is not None:
            logger.info(
                "Project catalog loaded (%s projects)",
                self.project_catalog.count(),
            )
        if self.trader_catalog is not None:
            logger.info("Trader catalog loaded")

        prepare_qt_env()
        existing = QApplication.instance()
        self.app = (
            existing if isinstance(existing, QApplication) else QApplication(sys.argv)
        )
        self.app.setQuitOnLastWindowClosed(False)
        self.root = None

        # Tooltip overlay + in-game map pins. F8 opens filter config
        # (evdev/GetAsyncKeyState works with the game focused on Wayland).
        self.overlay = OverlayWindow()
        self.map_catalog = get_map_catalog()
        self.map_sync = MapSyncEngine(self.map_catalog)
        self.map_filter = MapFilterState()
        self.map_pins = InGameMapPins()
        # Flags must exist before MapWindow init (reload fires on_filter_change).
        self._last_overlay_item: str | None = None
        self._map_mode_latched = False
        self._map_pins_active = False
        self._map_refresh_busy = False
        self._map_motion_busy = False
        self._map_motion_timer: QTimer | None = None
        self._map_settle_timer: QTimer | None = None
        self._map_thumb: np.ndarray | None = None
        self._map_pins_drawn_for_thumb: np.ndarray | None = None
        self._map_miss_streak = 0
        self._map_motion_streak = 0
        # Fast motion poll; ORB only after the viewport stops changing.
        self._map_motion_poll_ms = 150
        self._map_settle_ms = 400
        self.map_window = MapWindow(
            catalog=self.map_catalog,
            filter_state=self.map_filter,
            on_sync_request=self._request_map_sync,
            on_filter_change=self._on_map_filter_change,
        )
        self.map_tap: TapHotkeyMonitor = get_map_tap_monitor()
        self.map_find_tap: TapHotkeyMonitor = get_map_find_tap_monitor()
        self.status = StatusWindow(
            on_quit=self.quit,
            on_left_click=self._toggle_map_window,
        )

        # Debug overlay for visualizing capture area (separate from debug_mode)
        self.debug_overlay = None
        if self.settings.show_capture_area:
            self.debug_overlay = DebugOverlay(self.settings)

        # Create scanner. The scanner thread posts UI updates to this queue;
        # the main thread drains it on a QTimer (Qt is main-thread only).
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.scanner = Scanner(
            overlay=self.overlay,
            status=self.status,
            db=self.db,
            progress=self.progress,
            quest_catalog=self.quest_catalog,
            project_catalog=self.project_catalog,
            trader_catalog=self.trader_catalog,
            ui_queue=self.ui_queue,
        )
        self._ui_pump_timer = QTimer()
        self._ui_pump_timer.timeout.connect(self._pump_ui_queue)
        # Set from the SIGINT handler; the pump performs the actual shutdown.
        # Python only runs the handler between bytecodes, and Qt's loop is C++,
        # so raising KeyboardInterrupt inside a slot hits the crash hook.
        self._sigint_requested = False

        # MetaForge bookmarklet → localhost import bridge
        self._progress_flash_token = 0
        self._progress_flash_active = False
        start_import_server(on_import=self._on_progress_imported)

    def _on_progress_imported(self, profile: ProgressProfile) -> None:
        """Called from the import-server thread after a successful POST."""
        self.ui_queue.put(("progress_imported", profile))

    def _apply_progress_profile(self, profile: ProgressProfile) -> None:
        """Hot-reload progress used by the scanner (main thread)."""
        self.progress = profile
        self.scanner.progress = profile
        logger.info(
            "Progress reloaded from bookmarklet (%s quests, %s workshop stations)",
            len(profile.completed_quests),
            len(profile.workshop_levels),
        )
        synced = parse_synced_at(profile.metaforge_synced_at)
        self._flash_progress_status(
            format_progress_status(
                imported_mtime=time.time(),
                metaforge_synced_at=synced,
            )
        )

    def _flash_progress_status(self, message: str) -> None:
        """Show an info line on the status pill, then return to Scanning."""
        self._progress_flash_token += 1
        token = self._progress_flash_token
        self._progress_flash_active = True
        self.status.set_info(message)

        def _clear() -> None:
            if token != self._progress_flash_token:
                return
            self._progress_flash_active = False
            # Scanner status pump will re-assert ACTIVE/HOTKEY on the next tick.
            self.status.set_scanning()

        QTimer.singleShot(_PROGRESS_FLASH_MS, _clear)

    def _apply_status(self, status: str) -> None:
        """Apply a status update to the StatusWindow (main thread only)."""
        if self._progress_flash_active:
            return
        if status == "scanning":
            self.status.set_scanning()
        elif status == "active":
            self.status.set_active()
        elif status == "map":
            self.status.set_map()
        elif status == "hotkey":
            self.status.set_hotkey()
        elif status == "error":
            self.status.set_error("Error")

    def _on_map_mode(self, *, active: bool) -> None:
        """Enter/leave raid-map mode: in-game pins loop; F8 for filters."""
        if active:
            if not self._map_mode_latched:
                self._map_mode_latched = True
                self._map_pins_active = True
                self._map_thumb = None
                self._map_pins_drawn_for_thumb = None
                self._map_motion_streak = 0
                self.map_window.set_sync_status("watching")
                self._schedule_map_motion_poll(immediate=True)
            self.status.set_map()
        else:
            self._map_mode_latched = False
            self._map_pins_active = False
            self._map_miss_streak = 0
            self._map_motion_streak = 0
            self._map_thumb = None
            self._map_pins_drawn_for_thumb = None
            self._cancel_map_timers()
            self.map_pins.hide()
            self.map_window.set_sync_status("idle")

    def _cancel_map_timers(self) -> None:
        for attr in ("_map_motion_timer", "_map_settle_timer"):
            timer = getattr(self, attr)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)

    def _on_map_filter_change(self) -> None:
        """Filters changed in F8 window — redraw pins if the map is settled."""
        if not self._map_pins_active:
            return
        self._map_pins_drawn_for_thumb = None
        self._schedule_map_settle()

    def _schedule_map_motion_poll(self, *, immediate: bool = False) -> None:
        if self._map_motion_timer is not None:
            self._map_motion_timer.stop()
            self._map_motion_timer = None
        if not self._map_pins_active:
            return
        delay = 0 if immediate else self._map_motion_poll_ms
        self._map_motion_timer = QTimer()
        self._map_motion_timer.setSingleShot(True)
        self._map_motion_timer.timeout.connect(self._poll_map_motion)
        self._map_motion_timer.start(delay)

    def _poll_map_motion(self) -> None:
        """Fast viewport thumb check; hide on move, ORB only after settle."""
        self._map_motion_timer = None
        if not self._map_pins_active:
            return
        if self._map_motion_busy or self._map_refresh_busy:
            self._schedule_map_motion_poll()
            return
        self._map_motion_busy = True

        def _worker() -> None:
            try:
                thumb = capture_map_thumb()
                self.ui_queue.put(("map_thumb", thumb))
            except Exception as e:  # ruff: ignore[blind-except]
                logger.debug("Map motion poll failed: %s", e)
                self.ui_queue.put(("map_thumb", None))
            finally:
                self._map_motion_busy = False

        Thread(target=_worker, daemon=True, name="map-motion").start()

    def _on_map_thumb(self, thumb: np.ndarray | None) -> None:
        if not self._map_pins_active:
            return
        from arc_helper.map_sync import MOTION_STREAK_REQUIRED

        # First sample after map open — seed baseline, don't treat as a pan.
        if self._map_thumb is None:
            if thumb is not None:
                self._map_thumb = thumb
            self._map_motion_streak = 0
            if (
                self._map_pins_drawn_for_thumb is None
                and self._map_settle_timer is None
                and not self._map_refresh_busy
            ):
                self._schedule_map_settle()
            self._schedule_map_motion_poll()
            return

        moved = thumbs_differ(self._map_thumb, thumb)
        if thumb is not None:
            self._map_thumb = thumb

        if moved:
            self._map_motion_streak += 1
        else:
            self._map_motion_streak = 0

        if self._map_motion_streak >= MOTION_STREAK_REQUIRED:
            # Clear stale pins only after sustained pan/zoom, not hover flicker.
            self.map_pins.hide()
            self._map_pins_drawn_for_thumb = None
            self.map_window.set_sync_status("moving…")
            self._schedule_map_settle()
            self._map_motion_streak = 0
        elif (
            self._map_pins_drawn_for_thumb is None
            and self._map_settle_timer is None
            and not self._map_refresh_busy
        ):
            # Stable but nothing drawn yet (first open / failed ORB).
            self._schedule_map_settle()

        self._schedule_map_motion_poll()

    def _schedule_map_settle(self) -> None:
        """Debounce: wait until motion stops, then register + draw once."""
        if self._map_settle_timer is not None:
            self._map_settle_timer.stop()
            self._map_settle_timer = None
        if not self._map_pins_active:
            return
        self._map_settle_timer = QTimer()
        self._map_settle_timer.setSingleShot(True)
        self._map_settle_timer.timeout.connect(self._kick_map_pin_refresh)
        self._map_settle_timer.start(self._map_settle_ms)

    def _kick_map_pin_refresh(self) -> None:
        """ORB registration + pin project once the viewport has settled."""
        self._map_settle_timer = None
        if not self._map_pins_active:
            return
        if self._map_refresh_busy:
            self._schedule_map_settle()
            return
        if (
            self._map_pins_drawn_for_thumb is not None
            and self._map_thumb is not None
            and not thumbs_differ(self._map_pins_drawn_for_thumb, self._map_thumb)
        ):
            return

        self._map_refresh_busy = True
        self.map_window.set_sync_status("registering…")
        # Filter widgets must be read on the UI thread — not inside the worker.
        self.map_window.push_filter_state()
        hint = self.map_filter.map_id
        filter_snap = self.map_filter.snapshot()

        def _worker() -> None:
            try:
                reg = self.map_sync.register_once(map_id_hint=hint, detect_player=False)
                frame = self._build_pin_frame(reg, filter_snap)
                self.ui_queue.put(("map_pins_frame", frame))
            except Exception as e:  # ruff: ignore[blind-except]
                logger.warning("Map pin refresh failed: %s", e)
                self.ui_queue.put(("map_pins_frame", None))
            finally:
                self._map_refresh_busy = False

        Thread(target=_worker, daemon=True, name="map-pins").start()

    def _build_pin_frame(
        self,
        reg: ViewportRegistration | None,
        state: MapFilterState | None = None,
    ) -> dict | None:
        """Project filtered markers into overlay coords for the UI thread."""
        from arc_helper.map_filter import INGAME_PIN_CAP
        from arc_helper.map_sync import viewport_center_latlng

        if reg is None or not self.map_catalog:
            return None
        meta = self.map_catalog.get_meta(reg.map_id)
        if meta is None:
            return None

        state = self.map_filter.snapshot() if state is None else state.snapshot()
        state.map_id = reg.map_id
        # Keep shared filter map_id in sync with OCR/registration
        if reg.map_id and reg.map_id != self.map_filter.map_id:
            self.map_filter.map_id = reg.map_id

        # Warm Metaforge loose-spawn cache (network) before projecting pins.
        needle = (state.item_filter or state.search or "").strip()
        if needle:
            try:
                self.map_catalog.ensure_loose_spots(reg.map_id, needle)
            except Exception as e:  # ruff: ignore[blind-except]
                logger.debug("Loose-spot ensure failed: %s", e)

        # Prefer pins near the visible viewport — a global sample of 16 often
        # lands entirely off-screen when the raid map is zoomed in.
        origin = viewport_center_latlng(meta=meta, reg=reg)
        # Over-fetch then keep only those that project into the crop.
        candidates = resolve_markers(
            self.map_catalog,
            state,
            origin=origin,
            limit=max(INGAME_PIN_CAP * 32, 256),
            meta=meta,
        )

        left, top, _r, _b = reg.viewport_bbox
        pins: list[OverlayPin] = []
        for m in candidates:
            screen = latlng_to_screen(m.lat, m.lng, meta=meta, reg=reg)
            if screen is None:
                continue
            sx, sy = screen
            pins.append(
                OverlayPin(
                    x=sx - left,
                    y=sy - top,
                    category=m.category,
                )
            )
            if len(pins) >= INGAME_PIN_CAP:
                break

        logger.info(
            "Map pins: %s on-screen / %s candidates (origin=%s)",
            len(pins),
            len(candidates),
            "viewport" if origin else "none",
        )
        return {
            "reg": reg,
            "pins": pins,
            "marker_count": len(pins),
        }

    def _pump_ui_queue(self) -> None:
        """Drain scanner UI messages on the main thread (QTimer)."""
        if self._sigint_requested:
            self.quit()
            return

        # Global map hotkeys (work while the game has focus on Wayland/Windows)
        try:
            if self.map_tap.consume_press():
                self._toggle_map_window()
        except Exception as e:  # ruff: ignore[blind-except]
            logger.warning("Map hotkey poll failed: %s", e)
        try:
            if self.map_find_tap.consume_press():
                self._find_last_item_on_map()
        except Exception as e:  # ruff: ignore[blind-except]
            logger.warning("Map-find hotkey poll failed: %s", e)

        try:
            while True:
                msg = self.ui_queue.get_nowait()
                if msg[0] == "status":
                    self._apply_status(msg[1])
                elif msg[0] == "overlay":
                    self._last_overlay_item = msg[1]
                    self.overlay.show_item(msg[1], msg[2], in_raid=msg[3])
                elif msg[0] == "overlay_keep":
                    self.overlay.keep_alive()
                elif msg[0] == "overlay_hide":
                    self.overlay.hide()
                elif msg[0] == "progress_imported":
                    self._apply_progress_profile(msg[1])
                elif msg[0] == "map_pins_frame":
                    self._apply_map_pins_frame(msg[1])
                elif msg[0] == "map_thumb":
                    self._on_map_thumb(msg[1])
                elif msg[0] == "map_mode":
                    self._on_map_mode(active=bool(msg[1]))
        except queue.Empty:
            pass

    def _request_map_sync(self) -> None:
        """Manual Sync now from filter window — force a settled redraw."""
        self._map_pins_drawn_for_thumb = None
        self._schedule_map_settle()

    def _apply_map_pins_frame(self, frame: dict | None) -> None:
        if not self._map_pins_active:
            self.map_pins.hide()
            return
        if frame is None:
            self._map_miss_streak += 1
            self.map_pins.hide()
            # Remember this thumb so we don't re-ORB until the map moves again.
            if self._map_thumb is not None:
                self._map_pins_drawn_for_thumb = self._map_thumb.copy()
            self.map_window.set_sync_status(
                f"no match ({self._map_miss_streak})"
                if self._map_miss_streak < _MAP_MISS_STREAK_HINT
                else "no match — pan slightly"
            )
            return

        self._map_miss_streak = 0
        reg: ViewportRegistration = frame["reg"]
        pins: list[OverlayPin] = frame["pins"]
        conf = reg.confidence
        n = frame["marker_count"]
        self.map_window.set_sync_status(f"live ({conf:.0%}, {n} pins)")

        self.map_window.set_suspend_filter_notify(suspended=True)
        try:
            if reg.map_id and reg.map_id != self.map_window.current_map_id:
                self.map_window.select_map(reg.map_id)
        finally:
            self.map_window.set_suspend_filter_notify(suspended=False)

        self.map_pins.update_pins(viewport_bbox=reg.viewport_bbox, pins=pins)
        # Remember the thumb we drew for — skip re-ORB until it moves again.
        if self._map_thumb is not None:
            self._map_pins_drawn_for_thumb = self._map_thumb.copy()

    def _toggle_map_window(self, _event: object = None) -> None:
        was_visible = self.map_window.is_visible()
        self.map_window.toggle()
        if not was_visible:
            logger.info("Map filters opened (%s)", self.map_window.current_map_id)
        else:
            logger.info("Map filters hidden")

    def _find_last_item_on_map(self, _event: object = None) -> None:
        name = self._last_overlay_item or self.scanner.stats.last_item
        if not name:
            self.status.set_info("Map: no item yet — hover one first")
            QTimer.singleShot(2500, self.status.set_scanning)
            return
        self.map_window.find_item(name)
        self.status.set_info(f"Map filter: {name}")
        QTimer.singleShot(2500, self.status.set_scanning)
        if self._map_pins_active:
            self._map_pins_drawn_for_thumb = None
            self._schedule_map_settle()

    def run(self) -> None:
        """Start the application."""
        logger.info("=" * 50)
        logger.info("ARLO - Started")
        logger.info("=" * 50)
        logger.info(f"Database: {self.db.count()} items loaded")
        logger.info(f"Debug mode: {self.settings.debug_mode}")
        logger.info("=" * 50)
        logger.info("Trigger Region:")
        logger.info(
            f"  Position: ({self.settings.trigger_region.x}, {self.settings.trigger_region.y})"
        )
        logger.info(
            f"  Size: {self.settings.trigger_region.width}x{self.settings.trigger_region.height}"
        )
        logger.info("Trigger Region 2:")
        logger.info(
            f"  Position: ({self.settings.trigger_region2.x}, {self.settings.trigger_region2.y})"
        )
        logger.info(
            f"  Size: {self.settings.trigger_region2.width}x{self.settings.trigger_region2.height}"
        )
        logger.info("=" * 50)
        logger.info(
            f"Trigger scan interval: {self.settings.scan.trigger_scan_interval}s"
        )
        logger.info(
            f"Tooltip scan interval: {self.settings.scan.tooltip_scan_interval}s"
        )
        logger.info("=" * 50)
        logger.info(
            "Progress import: http://%s:%s/ (MetaForge bookmarklet)",
            IMPORT_HOST,
            IMPORT_PORT,
        )
        logger.info("Looking for INVENTORY screen...")
        logger.info(
            "Hold the hotkey to force tooltip scanning (Ctrl+Shift; "
            f"{get_settings().hotkey_key} on Wayland)"
        )
        map_hk = get_settings().map_hotkey
        find_hk = get_settings().map_find_hotkey
        logger.info(
            "Map: open raid MAP for in-game pins; %s = filters; "
            "%s = filter to last hovered item",
            map_hk,
            find_hk,
        )
        if self.map_catalog:
            logger.info(
                "Map catalog: %s markers",
                self.map_catalog.marker_count(),
            )
        else:
            logger.info(
                "Map catalog empty — run: uv run python update_db.py --maps-only"
            )
        logger.info("Press Ctrl+C in terminal to quit")
        logger.info("=" * 50)

        # Defer shutdown to the UI pump. A handler that raises KeyboardInterrupt
        # fires inside this timer slot, and PySide reports that as a crash.
        signal.signal(signal.SIGINT, lambda _signum, _frame: self._request_sigint())

        # Map filters hotkeys: global tap (evdev/GetAsyncKeyState).
        self.map_tap.start()
        self.map_find_tap.start()
        _ = (map_hk, find_hk)  # logged above; taps handled in _pump_ui_queue

        # Start scanner
        self.scanner.start()

        # Start the main-thread UI pump
        self._ui_pump_timer.start(50)

        # Brief progress-import flash on startup (file mtime + MetaForge sync).
        self._flash_progress_status(
            format_progress_status(
                imported_mtime=account_sync_mtime(),
                metaforge_synced_at=account_sync_last_synced_at(),
            )
        )

        try:
            self.app.exec()
        except KeyboardInterrupt:
            self.quit()

    def _request_sigint(self) -> None:
        """Ask the main-thread pump to shut down. Safe to call from SIGINT."""
        if self._sigint_requested:
            # Second Ctrl+C while shutdown is in progress: abort immediately.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGINT)
            return
        self._sigint_requested = True

    def quit(self) -> None:
        """Clean shutdown."""
        logger.info("Shutting down...")
        self._map_pins_active = False
        self._cancel_map_timers()
        with suppress(Exception):
            self._ui_pump_timer.stop()
        with suppress(Exception):
            self.map_pins.hide()
        stop_import_server()
        self.scanner.stop()
        with suppress(Exception):
            self.map_tap.stop()
        with suppress(Exception):
            self.map_find_tap.stop()
        from arc_helper.screen import reset_backend

        reset_backend()  # release the portal session / PipeWire stream
        self.app.quit()


def check_first_run() -> bool:
    """
    Check if this is a first run or uncalibrated state.

    Returns True if ready to run, False if calibration needed.
    """
    profile_manager = get_profile_manager()
    resolution = profile_manager.get_resolution_key()

    if profile_manager.is_uncalibrated():
        logger.info(f"Detected resolution: {resolution}")
        logger.info("Settings are uncalibrated, checking for profile...")

        if profile_manager.has_profile():
            logger.info(f"Found profile for {resolution}, applying...")
            profile_manager.apply_profile()

            # Force complete reload of everything
            SettingsManager.reload()
            OCREngineManager.reset()

            # Verify the reload worked
            new_settings = SettingsManager.get()
            logger.info(
                f"After reload - Trigger region: ({new_settings.trigger_region.x}, {new_settings.trigger_region.y})"
            )

            logger.info("Profile applied successfully!")
            return True
        supported = profile_manager.get_supported_resolutions()
        logger.warning(f"No pre-configured profile found for {resolution}.")
        logger.info(
            f"Supported resolutions: {', '.join(supported) if supported else 'None yet'}"
        )
        logger.info("Please run the Calibration tool to configure screen regions.")
        return False

    return True


def main() -> None:
    """Entry point for ARLO."""

    # Log any unhandled exception before exit
    def exception_hook(
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None or exc_value is None:
            return
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.error("=" * 50)
        logger.error("UNHANDLED EXCEPTION - APP CRASHING")
        logger.error("=" * 50)
        logger.error("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        # Also write to a crash log file
        crash_log = APP_DIR / "crash.log"
        Path(crash_log).write_text(
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            encoding="utf-8",
        )
        logger.error(f"Crash log written to: {crash_log}")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = exception_hook

    def thread_exception_hook(args: ExceptHookArgs) -> None:
        thread_name = args.thread.name if args.thread is not None else "unknown"
        logger.error("=" * 50)
        logger.error(f"THREAD EXCEPTION in {thread_name}")
        logger.error("=" * 50)
        logger.error(
            "".join(
                traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback
                )
            )
        )
        crash_log = APP_DIR / "crash.log"
        with Path(crash_log).open("a", encoding="utf-8") as f:
            f.write(f"\n\nTHREAD {thread_name}:\n")
            f.write(
                "".join(
                    traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback
                    )
                )
            )

    threading.excepthook = thread_exception_hook

    try:
        # Check first run / calibration status
        if not check_first_run():
            input("\nPress Enter to exit...")
            return

        # Auto-update item database from wiki (throttled to once per 24h)
        try:
            sys.path.insert(0, str(APP_DIR))
            from update_db import auto_update

            auto_update()
        except Exception as e:  # ruff: ignore[blind-except]
            logger.debug(f"Wiki auto-update unavailable: {e}")

        settings = get_settings()
        logger.info(
            f"Settings loaded for resolution, trigger at ({settings.trigger_region.x}, {settings.trigger_region.y})"
        )

        _ = get_ocr_engine()
        app = Application()
        app.run()

    except Exception as e:  # ruff: ignore[blind-except]
        logger.error(f"Fatal error in main: {e}")
        logger.error(traceback.format_exc())
        input("\nPress Enter to exit after error...")


if __name__ == "__main__":
    main()
