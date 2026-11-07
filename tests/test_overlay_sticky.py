"""Wayland overlay: no auto-hide while sticky; no remount on same item."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QWidget

from arc_helper.database import Item
from arc_helper.ui.overlay import OverlayWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wayland_overlay(qapp):
    _ = qapp
    with (
        patch("arc_helper.ui.overlay.get_settings") as gs,
        patch("arc_helper.ui.overlay.get_dpi_scale", return_value=1.0),
        patch("arc_helper.ui.overlay.make_click_through"),
        patch("arc_helper.ui.overlay._using_wayland", return_value=True),
    ):
        settings = MagicMock()
        settings.overlay.x = 100
        settings.overlay.y = 100
        settings.overlay.display_time = 1.0
        gs.return_value = settings
        win = OverlayWindow()
        yield win
        win.hide()
        win.deleteLater()


def test_wayland_show_does_not_arm_hide_timer(wayland_overlay):
    item = Item(name="Wires", action="SELL")
    wayland_overlay.show_item("Wires", item)
    assert not wayland_overlay._hide_timer.isActive()
    assert wayland_overlay.is_visible()


def test_wayland_same_item_skips_remount(wayland_overlay):
    item = Item(name="Wires", action="SELL")
    with (
        patch.object(QWidget, "show") as qshow,
        patch.object(wayland_overlay, "raise_") as raise_,
    ):
        wayland_overlay.show_item("Wires", item)
        wayland_overlay.show_item("WIRE5", item)

    assert qshow.call_count == 1
    assert raise_.call_count == 1
    assert not wayland_overlay._hide_timer.isActive()


def test_wayland_hide_from_scanner(wayland_overlay):
    item = Item(name="Wires", action="SELL")
    wayland_overlay.show_item("Wires", item)
    wayland_overlay.hide()
    assert not wayland_overlay.is_visible()


def test_sell_price_only_when_in_raid(wayland_overlay):
    item = Item(name="Wires", action="SELL", sell_price=640, stack_size=20)
    wayland_overlay.show_item("Wires", item, in_raid=False)
    assert "Sell:" not in wayland_overlay.info_label.text()
    assert "Stack: 20" in wayland_overlay.info_label.text()

    wayland_overlay.hide()
    wayland_overlay.show_item("Metal Parts", item, in_raid=True)
    text = wayland_overlay.info_label.text()
    assert "Sell: 640" in text
    assert "Stack: 20" in text
