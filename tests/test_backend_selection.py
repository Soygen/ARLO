"""Tests for screen backend selection logic."""

from arc_helper.screen.base import choose_backend_name


def test_windows_always_wins():
    env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"}
    assert choose_backend_name("win32", env, "auto") == "windows"


def test_windows_ignores_override():
    assert choose_backend_name("win32", {}, "wayland") == "windows"


def test_linux_wayland_display_var():
    assert choose_backend_name("linux", {"WAYLAND_DISPLAY": "wayland-0"}, "auto") == "wayland"


def test_linux_xdg_session_type():
    assert choose_backend_name("linux", {"XDG_SESSION_TYPE": "wayland"}, "auto") == "wayland"


def test_linux_defaults_to_x11():
    assert choose_backend_name("linux", {}, "auto") == "x11"


def test_override_x11_beats_wayland_session():
    env = {"WAYLAND_DISPLAY": "wayland-0"}
    assert choose_backend_name("linux", env, "x11") == "x11"


def test_override_wayland_on_x11_session():
    assert choose_backend_name("linux", {}, "wayland") == "wayland"


def test_invalid_override_falls_back_to_auto():
    assert choose_backend_name("linux", {}, "bogus") == "x11"


def test_override_is_case_insensitive():
    assert choose_backend_name("linux", {"WAYLAND_DISPLAY": "wayland-0"}, "X11") == "x11"
