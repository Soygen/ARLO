"""Make tk overlay windows input-transparent so clicks pass through.

X11/XWayland: empty SHAPE input region. Windows: WS_EX_TRANSPARENT.
Best-effort: failure logs a warning, never crashes (the overlay still
works, it just eats clicks like before).
"""

import sys
import tkinter as tk

from arc_helper.config import logger


def make_click_through(window: tk.Misc) -> None:
    window.update_idletasks()  # realize the native window before winfo_id()
    try:
        if sys.platform == "win32":
            _windows_click_through(window)
        else:
            _x11_click_through(window)
    except Exception as e:  # noqa: BLE001 - cosmetic feature, never fatal
        logger.warning(f"Could not make overlay click-through: {e}")


def _windows_click_through(window: tk.Misc) -> None:
    # NOTE: untested on Windows in this change; verified on X11/XWayland.
    import ctypes

    gwl_exstyle = -20
    ws_ex_layered = 0x00080000
    ws_ex_transparent = 0x00000020
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    style = user32.GetWindowLongPtrW(hwnd, gwl_exstyle)
    user32.SetWindowLongPtrW(hwnd, gwl_exstyle, style | ws_ex_layered | ws_ex_transparent)


def _x11_click_through(window: tk.Misc) -> None:
    from Xlib import X
    from Xlib import display as xlib_display
    from Xlib.ext import shape

    xdisplay = xlib_display.Display()
    try:
        if not xdisplay.has_extension("SHAPE"):
            logger.warning("X server lacks SHAPE; overlay will not be click-through")
            return
        xwindow = xdisplay.create_resource_object("window", window.winfo_id())
        # Empty input region: every click lands on whatever is underneath
        xwindow.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
        xdisplay.flush()
    finally:
        xdisplay.close()
