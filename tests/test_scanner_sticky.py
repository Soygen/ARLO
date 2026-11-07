"""Wayland-only overlay stability: keep-alive and trigger hysteresis."""

import queue
from unittest.mock import MagicMock
from unittest.mock import patch

from arc_helper.database import Item
from arc_helper.main import Scanner
from arc_helper.main import ScannerState


def _settings():
    settings = MagicMock()
    settings.overlay.display_time = 1.0
    settings.overlay.cooldown = 0.5
    settings.scan.tooltip_scan_interval = 0.3
    settings.trigger_region = MagicMock()
    settings.trigger_region2 = MagicMock()
    return settings


def _make_scanner(*, wayland: bool):
    scanner = Scanner(
        root=MagicMock(),
        overlay=MagicMock(),
        status=MagicMock(),
        db=MagicMock(),
        ui_queue=queue.Queue(),
    )
    scanner.db.lookup = MagicMock(return_value=Item(name="Wires", action="SELL"))
    scanner.db.log_missing_item = MagicMock()
    scanner.state = ScannerState.ACTIVE
    scanner._wayland_stable = wayland
    return scanner


def _drain(q):
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    return msgs


def test_wayland_same_item_keep_alive_bypasses_cooldown():
    scanner = _make_scanner(wayland=True)
    scanner._last_shown_item = "Wires"
    scanner._last_shown_time = 1_000_000.0

    with (
        patch("arc_helper.main.get_settings", return_value=_settings()),
        patch("arc_helper.main.time.time", return_value=1_000_000.1),
    ):
        scanner._handle_detected_item("WIRE5")

    assert _drain(scanner.ui_queue) == [("overlay_keep",)]


def test_x11_same_item_still_respects_cooldown():
    scanner = _make_scanner(wayland=False)
    scanner._last_shown_item = "Wires"
    scanner._last_shown_time = 1_000_000.0

    with (
        patch("arc_helper.main.get_settings", return_value=_settings()),
        patch("arc_helper.main.time.time", return_value=1_000_000.1),
    ):
        scanner._handle_detected_item("Wires")

    assert scanner.ui_queue.empty()


def test_wayland_idle_hides_overlay():
    scanner = _make_scanner(wayland=True)
    scanner._last_shown_item = "Wires"
    scanner._enter_idle("test")
    msgs = _drain(scanner.ui_queue)
    assert ("overlay_hide",) in msgs
    assert ("status", "scanning") in msgs
    assert scanner.state == ScannerState.IDLE
    assert not scanner._last_shown_item


def test_x11_idle_does_not_force_hide():
    scanner = _make_scanner(wayland=False)
    scanner._enter_idle("test")
    msgs = _drain(scanner.ui_queue)
    assert ("overlay_hide",) not in msgs


def test_wayland_trigger_needs_consecutive_misses():
    scanner = _make_scanner(wayland=True)
    ocr = MagicMock()
    ocr.check_trigger_which.return_value = None

    assert scanner._inventory_closed(ocr, _settings()) is False
    assert scanner._inventory_closed(ocr, _settings()) is True


def test_inventory_open_sets_in_raid_from_region_index():
    scanner = _make_scanner(wayland=False)
    ocr = MagicMock()
    ocr.check_trigger_which.return_value = 1

    assert scanner._inventory_closed(ocr, _settings()) is False
    assert scanner._in_raid is True

    ocr.check_trigger_which.return_value = 0
    assert scanner._inventory_closed(ocr, _settings()) is False
    assert scanner._in_raid is False


def test_wayland_empty_ocr_keeps_popup_inside_display_time():
    scanner = _make_scanner(wayland=True)
    scanner._last_shown_item = "Wires"
    scanner._last_shown_time = 1_000_000.0

    with (
        patch("arc_helper.main.get_settings", return_value=_settings()),
        patch("arc_helper.main.time.time", return_value=1_000_000.4),
    ):
        scanner._handle_empty_tooltip()

    assert _drain(scanner.ui_queue) == [("overlay_keep",)]
    assert scanner._last_shown_item == "Wires"


def test_wayland_empty_ocr_hides_after_display_time():
    scanner = _make_scanner(wayland=True)
    scanner._last_shown_item = "Wires"
    scanner._last_shown_time = 1_000_000.0

    with (
        patch("arc_helper.main.get_settings", return_value=_settings()),
        patch("arc_helper.main.time.time", return_value=1_000_001.0),
    ):
        scanner._handle_empty_tooltip()

    assert _drain(scanner.ui_queue) == [("overlay_hide",)]
    assert not scanner._last_shown_item


def test_x11_empty_ocr_does_not_hide():
    scanner = _make_scanner(wayland=False)
    scanner._last_shown_item = "Wires"
    scanner._last_shown_time = 1_000_000.0

    with patch("arc_helper.main.time.time", return_value=1_000_010.0):
        scanner._handle_empty_tooltip()

    assert scanner.ui_queue.empty()


def test_x11_trigger_closes_on_first_miss():
    scanner = _make_scanner(wayland=False)
    ocr = MagicMock()
    ocr.check_trigger_which.return_value = None

    assert scanner._inventory_closed(ocr, _settings()) is True
    assert scanner._in_raid is False
