"""Calibration tool entry point (Qt)."""

from __future__ import annotations

import ctypes
import sys
from contextlib import suppress

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        with suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def main() -> None:
    from arc_helper.ui.calibrate import main as qt_main

    qt_main()


if __name__ == "__main__":
    main()
