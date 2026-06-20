"""Hold-to-scan hotkey monitors.

Windows: GetAsyncKeyState Ctrl+Shift (unchanged behavior).
X11: pynput listener Ctrl+Shift (unchanged behavior).
Wayland: evdev, hold a single key (default Right Ctrl, HOTKEY_KEY in .env).
"""

import ctypes
import os
import sys
import time
from contextlib import suppress
from typing import Protocol

from arc_helper.config import get_settings
from arc_helper.config import logger
from arc_helper.screen.base import choose_backend_name


class HotkeyMonitor(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def is_held(self) -> bool: ...


class WindowsCtrlShiftMonitor:
    """Ctrl+Shift held, via GetAsyncKeyState."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_held(self) -> bool:  # noqa: PLR6301
        try:
            user32 = ctypes.windll.user32
            # VK_CONTROL = 0x11, VK_SHIFT = 0x10; high bit = currently down
            ctrl = user32.GetAsyncKeyState(0x11) & 0x8000
            shift = user32.GetAsyncKeyState(0x10) & 0x8000
            return bool(ctrl and shift)
        except (AttributeError, OSError):
            return False


class PynputCtrlShiftMonitor:
    """Ctrl+Shift held, via a pynput listener (X11)."""

    def __init__(self):
        self._pressed: set = set()
        self._listener = None

    def start(self) -> None:
        if self._listener is not None:
            return
        from pynput import keyboard

        def on_press(key):
            self._pressed.add(key)

        def on_release(key):
            self._pressed.discard(key)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._pressed.clear()

    def is_held(self) -> bool:
        from pynput.keyboard import Key

        ctrl_keys = {Key.ctrl, Key.ctrl_l, Key.ctrl_r}
        shift_keys = {Key.shift, Key.shift_l, Key.shift_r}
        has_ctrl = any(k in self._pressed for k in ctrl_keys)
        has_shift = any(k in self._pressed for k in shift_keys)
        return has_ctrl and has_shift


class EvdevKeyMonitor:
    """A single key held, read from /dev/input (works on Wayland)."""

    RESCAN_INTERVAL_S = 5.0

    def __init__(self, key_name: str):
        import evdev

        code = evdev.ecodes.ecodes.get(key_name)
        if code is None:
            msg = (
                f"Unknown HOTKEY_KEY {key_name!r}. Use an evdev key name like "
                "KEY_RIGHTCTRL, KEY_RIGHTALT or KEY_F24."
            )
            raise ValueError(msg)
        self.key_name = key_name
        self.key_code = code
        self._devices: list = []
        self._last_scan = 0.0

    def start(self) -> None:
        import evdev

        self._last_scan = time.monotonic()
        self.stop()
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            try:
                key_caps = device.capabilities().get(evdev.ecodes.EV_KEY, [])
            except OSError:
                device.close()
                continue
            if self.key_code in key_caps:
                self._devices.append(device)
            else:
                device.close()
        if not self._devices:
            logger.warning(
                f"Hotkey disabled: no keyboard with {self.key_name} accessible. "
                "Add your user to the 'input' group "
                "(sudo usermod -aG input $USER, then log out and back in)."
            )

    def stop(self) -> None:
        for device in self._devices:
            with suppress(OSError):
                device.close()
        self._devices = []

    def is_held(self) -> bool:
        if not self._devices:
            self._maybe_rescan()
        dead = []
        held = False
        for device in self._devices:
            try:
                if self.key_code in device.active_keys():
                    held = True
                    break
            except OSError:
                dead.append(device)
        for device in dead:
            logger.warning(f"Hotkey device disappeared: {device}")
            self._devices.remove(device)
        return held

    def _maybe_rescan(self) -> None:
        """Recover from keyboard disconnect/reconnect (new event node)."""
        if time.monotonic() - self._last_scan < self.RESCAN_INTERVAL_S:
            return
        logger.info("Rescanning input devices for the hotkey")
        self.start()


def get_hotkey_monitor() -> HotkeyMonitor:
    """Pick the hotkey monitor matching the platform/session."""
    if sys.platform == "win32":
        return WindowsCtrlShiftMonitor()
    backend = choose_backend_name(
        sys.platform, os.environ, get_settings().screen_backend
    )
    if backend == "wayland":
        return EvdevKeyMonitor(get_settings().hotkey_key)
    return PynputCtrlShiftMonitor()
