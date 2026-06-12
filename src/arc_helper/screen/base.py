"""Backend-neutral types and selection logic for screen access.

This module must stay free of arc_helper.config imports so unit tests
can import it without triggering settings/logging setup.
"""

from collections.abc import Mapping
from typing import Protocol

from PIL import Image
from pydantic import BaseModel


class Point(BaseModel):
    """Screen coordinates in physical pixels."""

    x: int
    y: int


class ScreenBackend(Protocol):
    """Platform-specific screen access. All coordinates are physical pixels."""

    name: str

    def start(self) -> None:
        """Acquire resources. grab()/cursor_position()/resolution() are only valid after this."""
        ...

    def stop(self) -> None:
        """Release resources. Safe to call multiple times."""
        ...

    def grab(self, bbox: tuple[int, int, int, int]) -> Image.Image:
        """Capture (left, top, right, bottom) and return an RGB image."""
        ...

    def cursor_position(self) -> Point:
        """Current pointer position in physical pixels."""
        ...

    def resolution(self) -> tuple[int, int]:
        """(width, height) of the captured monitor in physical pixels."""
        ...

    @property
    def tk_scale(self) -> float:
        """Divide physical pixels by this to get tkinter window coordinates.

        A property (not a plain attribute like ``name``) because backends
        compute it at runtime from the compositor's scale factor.
        """
        ...


def choose_backend_name(
    platform: str, environ: Mapping[str, str], override: str = "auto"
) -> str:
    """Pick the backend: 'windows', 'x11' or 'wayland'."""
    if platform == "win32":
        return "windows"
    override = override.lower()
    if override in {"x11", "wayland"}:
        return override
    if environ.get("WAYLAND_DISPLAY") or environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    return "x11"


def compute_scale(physical_width: int, logical_width: int) -> float:
    """Scale factor between physical pixels and X11/tk logical pixels.

    Returns physical_width / logical_width, or 1.0 if logical_width <= 0.
    """
    if logical_width <= 0:
        return 1.0
    return physical_width / logical_width
