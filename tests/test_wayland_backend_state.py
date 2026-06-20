"""State-machine tests for WaylandBackend (no portal/pipeline needed)."""

import pytest

from arc_helper.screen.base import ScreenCaptureUnavailable
from arc_helper.screen.wayland_backend import WaylandBackend


def test_grab_before_start_raises_no_frame():
    backend = WaylandBackend()
    with pytest.raises(OSError, match="No frame received"):
        backend.grab((0, 0, 10, 10))


def test_terminal_error_short_circuits_grab():
    backend = WaylandBackend()
    backend._terminal_error = "gone"  # noqa: SLF001
    with pytest.raises(ScreenCaptureUnavailable, match="gone"):
        backend.grab((0, 0, 10, 10))


def test_second_restart_attempt_is_terminal():
    backend = WaylandBackend()
    backend._restarted = True  # noqa: SLF001
    with pytest.raises(ScreenCaptureUnavailable, match="Restart ARLO"):
        backend._attempt_restart()  # noqa: SLF001
    assert backend._terminal_error is not None  # noqa: SLF001


def test_stop_before_start_is_safe():
    backend = WaylandBackend()
    backend.stop()
    backend.stop()
