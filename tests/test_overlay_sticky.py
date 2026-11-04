"""Wayland overlay: no auto-hide while sticky; no remount on same item."""

import tkinter as tk
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from arc_helper.database import Item
from arc_helper.overlay import OverlayWindow


@pytest.fixture
def wayland_overlay():
    root = tk.Tk()
    root.withdraw()
    with (
        patch("arc_helper.overlay.get_settings") as gs,
        patch("arc_helper.overlay.get_dpi_scale", return_value=1.0),
        patch("arc_helper.overlay.make_click_through"),
        patch("arc_helper.overlay.phys_to_tk", side_effect=lambda v: v),
        patch("arc_helper.overlay._using_wayland", return_value=True),
    ):
        settings = MagicMock()
        settings.overlay.x = 100
        settings.overlay.y = 100
        settings.overlay.display_time = 1.0
        gs.return_value = settings
        win = OverlayWindow(root)
        yield win
        win.window.destroy()
        root.destroy()


def test_wayland_show_does_not_arm_hide_timer(wayland_overlay):
    item = Item(name="Wires", action="SELL")
    wayland_overlay.show("Wires", item)
    assert wayland_overlay._hide_after_id is None  # noqa: SLF001
    assert wayland_overlay.is_visible()


def test_wayland_same_item_skips_remount(wayland_overlay):
    item = Item(name="Wires", action="SELL")
    with (
        patch.object(wayland_overlay.window, "deiconify") as deiconify,
        patch.object(wayland_overlay.window, "lift") as lift,
    ):
        wayland_overlay.show("Wires", item)
        wayland_overlay.show("WIRE5", item)

    assert deiconify.call_count == 1
    assert lift.call_count == 1
    assert wayland_overlay._hide_after_id is None  # noqa: SLF001


def test_wayland_hide_from_scanner(wayland_overlay):
    item = Item(name="Wires", action="SELL")
    wayland_overlay.show("Wires", item)
    wayland_overlay.hide()
    assert not wayland_overlay.is_visible()
