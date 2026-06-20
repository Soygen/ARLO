"""mss-based backend: native capture on Windows, XGetImage on X11."""

import ctypes
import sys
import typing

from PIL import Image

from .base import Point


class MssBackend:
    """The pre-Wayland behavior, unchanged. name is 'windows' or 'x11'."""

    def __init__(self, name: str):
        self.name = name

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    @property
    def tk_scale(self) -> float:
        return 1.0

    def grab(self, bbox: tuple[int, int, int, int]) -> Image.Image:  # noqa: PLR6301
        import mss

        left, top, right, bottom = bbox
        with mss.mss() as sct:
            monitor = {
                "top": top,
                "left": left,
                "width": right - left,
                "height": bottom - top,
            }
            screenshot = sct.grab(monitor)
            return Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    def cursor_position(self) -> Point:  # noqa: PLR6301
        if sys.platform == "win32":
            # Ensure we're DPI aware to get physical coordinates
            ctypes.windll.user32.SetProcessDPIAware()

            class POINT(ctypes.Structure):
                _fields_: typing.ClassVar = [
                    ("x", ctypes.c_long),
                    ("y", ctypes.c_long),
                ]

            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return Point(x=pt.x, y=pt.y)
        from pynput import mouse as pynput_mouse

        pos = pynput_mouse.Controller().position
        return Point(x=int(pos[0]), y=int(pos[1]))

    def resolution(self) -> tuple[int, int]:  # noqa: PLR6301
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[1]  # 0 is the combined virtual monitor
            return monitor["width"], monitor["height"]
