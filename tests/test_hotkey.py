"""Tests for the evdev hotkey monitor (with fake devices)."""

import pytest

from arc_helper.hotkey import EvdevKeyMonitor


class FakeDevice:
    def __init__(self, active=(), error: Exception | None = None):
        self._active = list(active)
        self._error = error
        self.closed = False

    def active_keys(self):
        if self._error is not None:
            raise self._error
        return self._active

    def close(self):
        self.closed = True


def test_key_name_parsed():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    assert monitor.key_code == 97


def test_invalid_key_name_raises():
    with pytest.raises(ValueError, match="KEY_"):
        EvdevKeyMonitor("RIGHTCTRL")


def test_is_held_true():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = [FakeDevice(active=[97])]  # noqa: SLF001
    assert monitor.is_held() is True


def test_is_held_false():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = [FakeDevice(active=[29])]  # noqa: SLF001
    assert monitor.is_held() is False


def test_no_devices_is_false():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = []  # noqa: SLF001
    assert monitor.is_held() is False


def test_dead_device_dropped():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    dead = FakeDevice(error=OSError("unplugged"))
    alive = FakeDevice(active=[97])
    monitor._devices = [dead, alive]  # noqa: SLF001
    assert monitor.is_held() is True
    assert dead not in monitor._devices  # noqa: SLF001


def test_stop_closes_devices():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    device = FakeDevice()
    monitor._devices = [device]  # noqa: SLF001
    monitor.stop()
    assert device.closed
    assert monitor._devices == []  # noqa: SLF001


def test_empty_devices_triggers_throttled_rescan(monkeypatch):
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = []  # noqa: SLF001
    calls = []
    monkeypatch.setattr(monitor, "start", lambda: calls.append(1))
    monitor._last_scan = 0.0  # noqa: SLF001 - far in the past: rescan due
    assert monitor.is_held() is False
    assert calls == [1]


def test_rescan_is_throttled(monkeypatch):
    import time

    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = []  # noqa: SLF001
    calls = []
    monkeypatch.setattr(monitor, "start", lambda: calls.append(1))
    monitor._last_scan = time.monotonic()  # noqa: SLF001 - just scanned
    assert monitor.is_held() is False
    assert calls == []
