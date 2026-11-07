"""Edge-triggered map hotkey helpers."""

from arc_helper.hotkey import EdgeFromHeld
from arc_helper.hotkey import EvdevKeyTapMonitor
from arc_helper.hotkey import tk_keysym_to_evdev


def test_tk_keysym_to_evdev():
    assert tk_keysym_to_evdev("F8") == "KEY_F8"
    assert tk_keysym_to_evdev("key_f9") == "KEY_F9"
    assert tk_keysym_to_evdev("KEY_F8") == "KEY_F8"


def test_edge_fires_once_per_press():
    state = {"held": False}
    edge = EdgeFromHeld(lambda: state["held"])
    assert edge.consume_press() is False
    state["held"] = True
    assert edge.consume_press() is True
    assert edge.consume_press() is False  # still held
    state["held"] = False
    assert edge.consume_press() is False
    state["held"] = True
    assert edge.consume_press() is True


def test_evdev_tap_monitor_constructs():
    mon = EvdevKeyTapMonitor("KEY_F8")
    assert mon.consume_press() is False
