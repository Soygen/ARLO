"""Screen access backends: capture, cursor position, resolution.

get_backend() picks and starts the right backend once per process:
Windows -> mss/WinAPI; Linux Wayland session -> portal+PipeWire;
otherwise X11 via mss. Override with SCREEN_BACKEND=x11|wayland in .env.
"""

import os
import sys
import threading

from .base import Point
from .base import ScreenBackend
from .base import choose_backend_name

__all__ = ["Point", "ScreenBackend", "get_backend", "phys_to_tk", "reset_backend"]

_backend: ScreenBackend | None = None  # noqa: RUF067
_lock = threading.Lock()  # noqa: RUF067


def get_backend() -> ScreenBackend:  # noqa: RUF067
    global _backend  # noqa: PLW0603 - process-wide singleton, mirrors SettingsManager
    with _lock:
        if _backend is None:
            from arc_helper.config import get_settings
            from arc_helper.config import logger

            name = choose_backend_name(
                sys.platform, os.environ, get_settings().screen_backend
            )
            if name == "wayland":
                from .wayland_backend import WaylandBackend

                backend: ScreenBackend = WaylandBackend()
            else:
                from .mss_backend import MssBackend

                backend = MssBackend(name)
            logger.info(f"Screen backend: {name}")
            backend.start()
            _backend = backend
        return _backend


def reset_backend() -> None:  # noqa: RUF067
    """Stop and forget the backend (used by tests)."""
    global _backend  # noqa: PLW0603
    with _lock:
        if _backend is not None:
            _backend.stop()
            _backend = None


def phys_to_tk(value: float) -> int:  # noqa: RUF067
    """Convert physical pixels to tkinter window coordinates."""
    return round(value / get_backend().tk_scale)
