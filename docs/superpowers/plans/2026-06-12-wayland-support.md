# Wayland Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ARLO work on Wayland (KDE Plasma first) by adding a portal+PipeWire screen-capture backend, an evdev hold-Right-Ctrl hotkey, and scale-corrected overlay/cursor coordinates — while keeping Windows and X11 behavior unchanged.

**Architecture:** All platform-specific screen access moves behind a `ScreenBackend` interface in a new `src/arc_helper/screen/` package (physical pixels at the boundary). The Wayland backend holds a persistent xdg-desktop-portal ScreenCast session (D-Bus via Gio) feeding a `pipewiresrc → videoconvert → appsink` GStreamer pipeline; grabs are in-memory crops of the latest frame. Hotkey handling moves behind a `HotkeyMonitor` interface with an evdev implementation for Wayland. tkinter overlays stay (running via XWayland) with physical→logical coordinate mapping and X-Shape click-through.

**Tech Stack:** Python 3.12, PyGObject (Gio/GLib/Gst/GstVideo), GStreamer + gst-plugin-pipewire, python-evdev + python-xlib (both already present via pynput), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-06-12-wayland-support-design.md`

**Deliberate deviations from the spec** (call out to the user at the end):
1. Env var is `SCREEN_BACKEND` (not `ARLO_SCREEN_BACKEND`) — the codebase's existing `.env` vars have no prefix (`DEBUG_MODE`, `TRIGGER_REGION_X`, …); consistency wins.
2. Click-through is applied not only to the calibrate tracking overlay but also to the calibrate region rectangles, the `SHOW_CAPTURE_AREA` debug overlay, and the recommendation popup — same bug class, same one-line fix. The StatusWindow is exempt (right-click-to-quit must stay clickable).
3. Fix a latent import bug found during planning: `main.py:98` imports `src.arc_helper.ocr`, which creates a duplicate module instance; it must be `arc_helper.ocr`.

## Verified environment facts (do not re-derive; these were checked live on this machine)

- Portal present: `org.freedesktop.portal.ScreenCast` with `AvailableCursorModes = 7`, `AvailableSourceTypes = 7`; backend `xdg-desktop-portal-kde 6.6.5`, `xdg-desktop-portal 1.20.4`.
- `gstreamer 1.28.3`, `gst-plugins-base`, `gst-plugin-pipewire 1.6.6`, `pipewire 1.6.6` installed; `gst-inspect-1.0 pipewiresrc` works; all needed typelibs in `/usr/lib/girepository-1.0` (Gst, GstApp, GstVideo, Gio).
- venv Python is 3.12.13 (uv-managed); system `python-gobject` is for 3.14 and **cannot** be used — PyGObject must be pip-built into the venv.
- The user is **already in the `input` group**: `evdev.list_devices()` returns 30 devices and `active_keys()` works. No usermod needed on this machine (still document it for others).
- Xlib (XWayland) display size is **2194×1234** while the physical monitor is **3840×2160** → scale factor ≈ 1.7502. `pynput` cursor queries work under this Wayland session.
- SHAPE extension is present; python-xlib's `window.shape_rectangles(operation, destination_kind, ordering, x_offset, y_offset, rectangles)` exists (verified against installed source); `shape.SO.Set = 0`, `shape.SK.Input = 2`, `X.Unsorted = 0`; an empty rectangle list = empty input region = full click-through.
- `evdev.ecodes.ecodes["KEY_RIGHTCTRL"] == 97`; `InputDevice.active_keys(verbose=False)` exists; `list_devices()` silently omits devices without R+W access (empty list ⇒ permissions).
- Gio (verified by live execution): `signal_subscribe(sender, interface_name, member, object_path, arg0, flags, callback)` — callback `(conn, sender_name, object_path, interface_name, signal_name, parameters)`; `call_sync(bus_name, object_path, interface_name, method_name, parameters, reply_type, flags, timeout_msec, cancellable)`; `call_with_unix_fd_list_sync(..., timeout_msec, fd_list, cancellable)` returns `(reply, out_fd_list)`; `out_fd_list.get(idx)` returns a **dup'd fd the caller owns** (close it in stop). Signal callbacks fire only when the global default GLib main context is iterated → run a `GLib.MainLoop` in a daemon thread.
- Portal protocol: `CreateSession` Response carries `session_handle` typed **`s` (string), not `o`** — known quirk. Request paths are `/org/freedesktop/portal/desktop/request/<SENDER>/<TOKEN>` with SENDER = unique name minus leading `:`, `.`→`_`. Response codes: 0 ok, 1 user-cancelled, 2 other. `restore_token` arrives in the **Start** response (single-use — always save the new one). `OpenPipeWireRemote` is a plain sync method (no Request/Response).
- pipewiresrc: use `fd=<fd> path=<node_id>` (deprecated-but-blessed for portals; the pipewire source explicitly keeps it for portal apps). pipewiresrc **dups the fd**, so we keep ownership. `keepalive-time=500` re-pushes the last buffer every 500 ms when the compositor is idle (KWin is damage-driven; Plasma 6.3+ sends one initial frame on connect). appsink: `emit-signals=true max-buffers=2 drop=true sync=false`; callback must return `Gst.FlowReturn.OK`; `buf.map()` returns `(ok, mapinfo)` and `mapinfo.data` is a `bytes` copy; stride from `GstVideo.buffer_get_video_meta(buf).stride[0]` when meta exists, else `width*4`.
- PyGObject pip build on Arch needs: `cairo pkgconf gcc` (NOT `glib2-devel` — Arch keeps headers/.pc files in `glib2`, already installed). pip metadata hard-requires pycairo, so cairo is unavoidable. Typelib discovery from the venv is automatic on Arch.
- Ruff: repo has 13 pre-existing violations; pre-commit runs `ruff --fix` + `ruff-format`. New code must be clean. Rules to respect in new code: EM101/EM102 (assign `msg = ...` before `raise X(msg)`), FBT (keyword-only booleans), single-line isort imports. f-string logging is fine (matches existing code).

## File structure

```
src/arc_helper/
  screen/
    __init__.py        # get_backend() singleton, reset_backend(), phys_to_tk()
    base.py            # Point, ScreenBackend protocol, choose_backend_name(), compute_scale()
    frame.py           # crop_bgrx() — pure, unit-tested
    token_store.py     # restore-token persistence — pure, unit-tested
    mss_backend.py     # extracted current Windows/X11 behavior
    portal.py          # ScreenCastSession (D-Bus handshake via Gio)
    wayland_backend.py # GStreamer pipeline + frame store + cursor mapping
    diag.py            # python -m arc_helper.screen.diag
  hotkey.py            # HotkeyMonitor protocol + Windows/X11/evdev impls
  clickthrough.py      # make_click_through(tk window)
  ocr.py               # MODIFIED: delegates grab/cursor to backend
  config.py            # MODIFIED: resolution delegates; new settings fields
  main.py              # MODIFIED: hotkey rewiring, DebugOverlay fixes
  overlay.py           # MODIFIED: phys_to_tk positioning, popup click-through
  calibrate.py         # MODIFIED: click-through, scaled geometry, backend cursor
tests/
  test_backend_selection.py
  test_scale.py
  test_frame.py
  test_token_store.py
  test_hotkey.py
pyproject.toml         # MODIFIED: wayland extra, pytest, uv dependency-metadata
.env.example           # MODIFIED: SCREEN_BACKEND, HOTKEY_KEY
.gitignore             # MODIFIED: token file, diag output
flake.nix              # MODIFIED: GStreamer/GI typelib env
README.md              # MODIFIED: Wayland section
```

---

### Task 0: Foundations — dependencies and test scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/` (directory; pytest needs no `__init__.py`)

- [ ] **Step 0.1: Install system build dependencies (idempotent)**

Run:
```bash
sudo pacman -S --needed cairo pkgconf gcc
```
Expected: installs or reports "is up to date". (glib2, gstreamer, gst-plugins-base, gst-plugin-pipewire, pipewire, xdg-desktop-portal-kde are already installed — verified.)

- [ ] **Step 0.2: Edit pyproject.toml**

In `[project.optional-dependencies]`, change `dev` and add `wayland`:

```toml
[project.optional-dependencies]
dev = [
    "ruff>=0.1.0",
    "mypy>=1.0.0",
    "pyinstaller>=6.0.0",
    "pytest>=8.0",
]
wayland = [
    "pygobject>=3.50; sys_platform == 'linux'",
]
```

Append at the end of the file (top-level sections):

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]

# Static metadata so uv never builds the PyGObject sdist at lock time
# (PyGObject ships sdist-only; building requires Linux system headers).
[[tool.uv.dependency-metadata]]
name = "pygobject"
requires-dist = ["pycairo>=1.16"]
requires-python = ">=3.9"
```

- [ ] **Step 0.3: Sync and verify**

Run:
```bash
uv sync --all-extras
uv run python -c "import gi; gi.require_version('Gst', '1.0'); gi.require_version('GstVideo', '1.0'); from gi.repository import Gio, GLib, Gst, GstVideo; Gst.init(None); print('gi OK', Gst.version_string())"
uv run python -m pytest --version
```
Expected: `gi OK GStreamer 1.28.3` (or similar) and a pytest version. If the PyGObject build fails, the error will name a missing `.pc` file — install the corresponding Arch package and re-run (`girepository-2.0` → `glib2`, `cairo` → `cairo`).

- [ ] **Step 0.4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Add wayland (PyGObject) and pytest dependencies"
```

---

### Task 1: `screen/base.py` — Point, protocol, backend selection, scale math

**Files:**
- Create: `src/arc_helper/screen/__init__.py` (minimal for now)
- Create: `src/arc_helper/screen/base.py`
- Test: `tests/test_backend_selection.py`, `tests/test_scale.py`

- [ ] **Step 1.1: Write the failing tests**

`tests/test_backend_selection.py`:
```python
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
```

`tests/test_scale.py`:
```python
"""Tests for physical/logical scale computation."""

import pytest

from arc_helper.screen.base import compute_scale


def test_identity():
    assert compute_scale(3840, 3840) == 1.0


def test_fractional_175_percent():
    assert compute_scale(3840, 2194) == pytest.approx(1.7502, abs=1e-3)


def test_zero_logical_width_is_safe():
    assert compute_scale(3840, 0) == 1.0


def test_negative_logical_width_is_safe():
    assert compute_scale(3840, -5) == 1.0
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arc_helper.screen'`

- [ ] **Step 1.3: Implement**

`src/arc_helper/screen/__init__.py` (minimal; expanded in Task 4):
```python
"""Screen access backends: capture, cursor position, resolution."""
```

`src/arc_helper/screen/base.py`:
```python
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

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def grab(self, bbox: tuple[int, int, int, int]) -> Image.Image:
        """Capture (left, top, right, bottom) and return an RGB image."""
        ...

    def cursor_position(self) -> Point: ...

    def resolution(self) -> tuple[int, int]: ...

    @property
    def tk_scale(self) -> float:
        """Divide physical pixels by this to get tkinter window coordinates."""
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
    """Scale factor between physical pixels and X11/tk logical pixels."""
    if logical_width <= 0:
        return 1.0
    return physical_width / logical_width
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest tests/ -v`
Expected: all 13 tests PASS.

- [ ] **Step 1.5: Lint and commit**

```bash
uv run ruff check src/arc_helper/screen tests
git add src/arc_helper/screen tests/test_backend_selection.py tests/test_scale.py
git commit -m "Add screen backend protocol, selection logic and scale math"
```

---

### Task 2: `screen/frame.py` — BGRx frame cropping

**Files:**
- Create: `src/arc_helper/screen/frame.py`
- Test: `tests/test_frame.py`

- [ ] **Step 2.1: Write the failing tests**

`tests/test_frame.py`:
```python
"""Tests for BGRx frame cropping."""

import pytest

from arc_helper.screen.frame import crop_bgrx


def make_frame(width: int, height: int, stride: int | None = None) -> bytes:
    """Synthetic BGRx frame: pixel (x, y) has B=x%256, G=y%256, R=(x+y)%256."""
    stride = stride if stride is not None else width * 4
    rows = []
    for y in range(height):
        row = bytearray(stride)
        for x in range(width):
            row[x * 4] = x % 256
            row[x * 4 + 1] = y % 256
            row[x * 4 + 2] = (x + y) % 256
            row[x * 4 + 3] = 255
        rows.append(bytes(row))
    return b"".join(rows)


def expected_rgb(x: int, y: int) -> tuple[int, int, int]:
    return ((x + y) % 256, y % 256, x % 256)


def test_full_frame_crop():
    data = make_frame(8, 6)
    img = crop_bgrx(data, 8, 6, 32, (0, 0, 8, 6))
    assert img.size == (8, 6)
    assert img.mode == "RGB"
    assert img.getpixel((0, 0)) == expected_rgb(0, 0)
    assert img.getpixel((7, 5)) == expected_rgb(7, 5)


def test_offset_crop():
    data = make_frame(20, 10)
    img = crop_bgrx(data, 20, 10, 80, (5, 2, 15, 8))
    assert img.size == (10, 6)
    # pixel (0,0) of the crop is frame pixel (5,2)
    assert img.getpixel((0, 0)) == expected_rgb(5, 2)
    assert img.getpixel((9, 5)) == expected_rgb(14, 7)


def test_padded_stride():
    # 16 bytes of padding per row
    data = make_frame(8, 6, stride=8 * 4 + 16)
    img = crop_bgrx(data, 8, 6, 8 * 4 + 16, (1, 1, 7, 5))
    assert img.size == (6, 4)
    assert img.getpixel((0, 0)) == expected_rgb(1, 1)


def test_bbox_clamped_to_frame():
    data = make_frame(10, 10)
    img = crop_bgrx(data, 10, 10, 40, (-5, -5, 50, 50))
    assert img.size == (10, 10)


def test_bbox_fully_outside_returns_minimal_image():
    data = make_frame(10, 10)
    img = crop_bgrx(data, 10, 10, 40, (100, 100, 200, 200))
    assert img.size[0] >= 1
    assert img.size[1] >= 1


def test_short_buffer_raises():
    data = make_frame(10, 10)[:-50]
    with pytest.raises(ValueError, match="frame buffer too small"):
        crop_bgrx(data, 10, 10, 40, (0, 0, 10, 10))
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_frame.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `arc_helper.screen.frame`.

- [ ] **Step 2.3: Implement**

`src/arc_helper/screen/frame.py`:
```python
"""Cropping raw BGRx frames into PIL images."""

import numpy as np
from PIL import Image


def crop_bgrx(
    data: bytes,
    width: int,
    height: int,
    stride: int,
    bbox: tuple[int, int, int, int],
) -> Image.Image:
    """Crop (left, top, right, bottom) out of a BGRx frame, returning RGB.

    Coordinates are clamped to the frame; the result is always >= 1x1.
    """
    needed = height * stride
    if len(data) < needed:
        msg = f"frame buffer too small: {len(data)} < {needed} ({width}x{height}, stride {stride})"
        raise ValueError(msg)

    left, top, right, bottom = (int(v) for v in bbox)
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))

    arr = np.frombuffer(data, dtype=np.uint8)[:needed]
    rows = arr.reshape(height, stride)
    pixels = rows[:, : width * 4].reshape(height, width, 4)
    crop = pixels[top:bottom, left:right, :3][:, :, ::-1]  # BGRx -> RGB
    return Image.fromarray(np.ascontiguousarray(crop), "RGB")
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_frame.py -v`
Expected: 6 PASS.

- [ ] **Step 2.5: Lint and commit**

```bash
uv run ruff check src/arc_helper/screen tests
git add src/arc_helper/screen/frame.py tests/test_frame.py
git commit -m "Add BGRx frame cropping for portal capture"
```

---

### Task 3: `screen/token_store.py` — restore-token persistence

**Files:**
- Create: `src/arc_helper/screen/token_store.py`
- Test: `tests/test_token_store.py`
- Modify: `.gitignore`

- [ ] **Step 3.1: Write the failing tests**

`tests/test_token_store.py`:
```python
"""Tests for ScreenCast restore-token persistence."""

from arc_helper.screen.token_store import clear_token
from arc_helper.screen.token_store import load_token
from arc_helper.screen.token_store import save_token


def test_load_missing_returns_none(tmp_path):
    assert load_token(tmp_path / "token") is None


def test_round_trip(tmp_path):
    path = tmp_path / "token"
    save_token("abc123", path)
    assert load_token(path) == "abc123"


def test_whitespace_stripped(tmp_path):
    path = tmp_path / "token"
    path.write_text("  abc123\n", encoding="utf-8")
    assert load_token(path) == "abc123"


def test_empty_file_returns_none(tmp_path):
    path = tmp_path / "token"
    path.write_text("", encoding="utf-8")
    assert load_token(path) is None


def test_clear(tmp_path):
    path = tmp_path / "token"
    save_token("abc123", path)
    clear_token(path)
    assert load_token(path) is None
    clear_token(path)  # idempotent
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_token_store.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3.3: Implement**

`src/arc_helper/screen/token_store.py`:
```python
"""Persistence for the xdg-desktop-portal ScreenCast restore token.

The token lets later runs reuse the user's one-time screen-share approval.
Tokens are single-use: every portal Start() returns a fresh one to save.
"""

from pathlib import Path


def _default_path() -> Path:
    from arc_helper.config import APP_DIR

    return APP_DIR / ".screencast_restore_token"


def load_token(path: Path | None = None) -> str | None:
    target = path if path is not None else _default_path()
    try:
        text = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_token(token: str, path: Path | None = None) -> None:
    target = path if path is not None else _default_path()
    target.write_text(token, encoding="utf-8")


def clear_token(path: Path | None = None) -> None:
    target = path if path is not None else _default_path()
    target.unlink(missing_ok=True)
```

- [ ] **Step 3.4: Run tests, add gitignore entries**

Run: `uv run pytest tests/test_token_store.py -v` → 5 PASS.

Append to `.gitignore`:
```
.screencast_restore_token
diag_frame.png
```

- [ ] **Step 3.5: Lint and commit**

```bash
uv run ruff check src/arc_helper/screen tests
git add src/arc_helper/screen/token_store.py tests/test_token_store.py .gitignore
git commit -m "Add restore-token persistence for portal sessions"
```

---

### Task 4: mss backend extraction + singleton wiring into ocr/config

**Files:**
- Create: `src/arc_helper/screen/mss_backend.py`
- Modify: `src/arc_helper/screen/__init__.py`
- Modify: `src/arc_helper/ocr.py` (lines 1–63: imports, `grab_screen`, `Point`, `get_cursor_position`)
- Modify: `src/arc_helper/config.py` (`get_screen_resolution`, new Settings fields, `save_to_env`)

- [ ] **Step 4.1: Implement `mss_backend.py`** (verbatim extraction of current behavior)

`src/arc_helper/screen/mss_backend.py`:
```python
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

    def grab(self, bbox: tuple[int, int, int, int]) -> Image.Image:
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

    def cursor_position(self) -> Point:
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

    def resolution(self) -> tuple[int, int]:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        import mss

        with mss.mss() as sct:
            monitor = sct.monitors[1]  # 0 is the combined virtual monitor
            return monitor["width"], monitor["height"]
```

- [ ] **Step 4.2: Implement the singleton in `screen/__init__.py`** (replace the stub)

```python
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

_backend: ScreenBackend | None = None
_lock = threading.Lock()


def get_backend() -> ScreenBackend:
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


def reset_backend() -> None:
    """Stop and forget the backend (used by tests)."""
    global _backend  # noqa: PLW0603
    with _lock:
        if _backend is not None:
            _backend.stop()
            _backend = None


def phys_to_tk(value: float) -> int:
    """Convert physical pixels to tkinter window coordinates."""
    return round(value / get_backend().tk_scale)
```

Note: `from .wayland_backend import WaylandBackend` will fail until Task 6 — that's fine; it's only reached when the wayland backend is selected, and Task 6 lands before any Wayland run.

- [ ] **Step 4.3: Rewire `ocr.py`**

Replace lines 1–63 of `src/arc_helper/ocr.py` (everything from the module docstring through `get_cursor_position`, inclusive) with:

```python
"""
OCR module for ARLO.
Screen capture and text extraction using Tesseract.
"""

import re
import string

import numpy as np
import pytesseract
from PIL import Image
from PIL import ImageOps
from pydantic import BaseModel

from .config import RegionMixin
from .config import get_screen_resolution
from .config import get_settings
from .config import logger
from .screen import Point
from .screen import get_backend


def grab_screen(bbox: tuple[int, int, int, int]) -> Image.Image:
    """Capture a screen region via the active screen backend."""
    return get_backend().grab(bbox)


class OCRResult(BaseModel):
    """Result of an OCR operation."""

    text: str | None
    confidence: float = 0.0
    raw_text: str = ""


def get_cursor_position() -> Point:
    """Get current cursor position on screen in physical pixels."""
    return get_backend().cursor_position()
```

(The old `Point` class definition, `import ctypes`, `import sys`, `import typing`, `import mss`, and the platform branches disappear; `Point` is now re-exported from `arc_helper.screen`. Everything from `class OCREngine:` down is untouched.)

- [ ] **Step 4.4: Rewire `config.py`**

Replace the body of `get_screen_resolution` (config.py:40-49) with:

```python
def get_screen_resolution() -> tuple[int, int]:
    """Get the primary monitor resolution in physical pixels."""
    from .screen import get_backend  # local import: screen imports config

    return get_backend().resolution()
```

Add two fields to `class Settings` (after `database_path`):

```python
    # Screen capture backend: auto | x11 | wayland (Linux only)
    screen_backend: str = Field(
        default="auto", description="Screen backend: auto, x11 or wayland"
    )

    # Hold-to-scan hotkey, evdev key name (Wayland/Linux only)
    hotkey_key: str = Field(
        default="KEY_RIGHTCTRL", description="evdev key name for hold-to-scan"
    )
```

In `save_to_env`, append to the `lines` list (after the debug settings entries):

```python
            "",
            "# Platform settings",
            f"SCREEN_BACKEND={self.screen_backend}",
            f"HOTKEY_KEY={self.hotkey_key}",
```

- [ ] **Step 4.5: Verify nothing broke**

Run:
```bash
uv run pytest tests/ -v
uv run python -c "
import os
os.environ['SCREEN_BACKEND'] = 'x11'   # force old path; no portal yet
from arc_helper import ocr, config
print('resolution:', config.get_screen_resolution())
print('cursor:', ocr.get_cursor_position())
"
```
Expected: tests pass; the script prints the XWayland logical resolution (2194x1234 on this machine) and a cursor position — same values the old code produced. (XGetImage grabs still fail on Wayland; that's the point of the next tasks.)

- [ ] **Step 4.6: Lint and commit**

```bash
uv run ruff check src/arc_helper tests
git add -A src/arc_helper tests
git commit -m "Route screen capture, cursor and resolution through backend abstraction"
```

---

### Task 5: `screen/portal.py` — ScreenCast portal session

**Files:**
- Create: `src/arc_helper/screen/portal.py`

No unit tests (requires a live portal); verified end-to-end by Task 7's diag. All D-Bus signatures below were verified against the portal docs and live PyGObject execution — do not "fix" them from memory.

- [ ] **Step 5.1: Implement**

`src/arc_helper/screen/portal.py`:
```python
"""xdg-desktop-portal ScreenCast session (D-Bus via Gio).

Handshake: CreateSession -> SelectSources -> Start -> OpenPipeWireRemote.
Request-style methods deliver results via a Response signal on a request
object whose path is predictable from our unique bus name + handle_token.
"""

import contextlib
import os
import threading

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio  # noqa: E402
from gi.repository import GLib  # noqa: E402

from arc_helper.config import logger  # noqa: E402

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SOURCE_TYPE_MONITOR = 1
CURSOR_MODE_HIDDEN = 1  # keep the pointer out of OCR frames
PERSIST_UNTIL_REVOKED = 2

RESPONSE_TIMEOUT_S = 10.0
DIALOG_TIMEOUT_S = 120.0  # Start() may wait on the user clicking the dialog


class PortalError(RuntimeError):
    """A portal request failed or was cancelled."""


class ScreenCastSession:
    """One ScreenCast session: handshake, stream node, pipewire fd."""

    def __init__(self, restore_token: str | None = None):
        self.restore_token = restore_token
        self.new_restore_token: str | None = None
        self.node_id: int | None = None
        self.stream_props: dict = {}
        self.pipewire_fd: int = -1
        self.closed = threading.Event()  # compositor/user ended the cast

        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Signal callbacks only fire while something iterates the default
        # GLib main context; run a main loop in a daemon thread.
        self._loop = GLib.MainLoop()
        self._loop_thread: threading.Thread | None = None
        self._counter = 0
        self._session_handle: str | None = None
        self._closed_sub: int | None = None

    def open(self) -> None:
        """Run the full handshake. May block on the screen-share dialog."""
        self._loop_thread = threading.Thread(
            target=self._loop.run, name="arlo-glib-loop", daemon=True
        )
        self._loop_thread.start()

        results = self._request(
            "CreateSession",
            lambda token: GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": GLib.Variant("s", token),
                        "session_handle_token": GLib.Variant("s", "arlo"),
                    },
                ),
            ),
        )
        # Typed "s" (not "o") for backwards compatibility - portal docs quirk
        self._session_handle = results["session_handle"]
        self._subscribe_closed()

        select_options: dict[str, GLib.Variant] = {
            "types": GLib.Variant("u", SOURCE_TYPE_MONITOR),
            "multiple": GLib.Variant("b", False),  # noqa: FBT003
            "cursor_mode": GLib.Variant("u", CURSOR_MODE_HIDDEN),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        if self.restore_token:
            select_options["restore_token"] = GLib.Variant("s", self.restore_token)
        self._request(
            "SelectSources",
            lambda token: GLib.Variant(
                "(oa{sv})",
                (
                    self._session_handle,
                    {**select_options, "handle_token": GLib.Variant("s", token)},
                ),
            ),
        )

        results = self._request(
            "Start",
            lambda token: GLib.Variant(
                "(osa{sv})",
                (
                    self._session_handle,
                    "",
                    {"handle_token": GLib.Variant("s", token)},
                ),
            ),
            timeout=DIALOG_TIMEOUT_S,
        )
        streams = results.get("streams") or []
        if not streams:
            msg = "Portal returned no screen cast streams"
            raise PortalError(msg)
        self.node_id, self.stream_props = streams[0]
        self.new_restore_token = results.get("restore_token")
        logger.info(
            f"ScreenCast stream: node {self.node_id}, props {self.stream_props}"
        )

        reply, fd_list = self._bus.call_with_unix_fd_list_sync(
            PORTAL_BUS,
            PORTAL_OBJECT,
            SCREENCAST_IFACE,
            "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self._session_handle, {})),
            GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            None,
        )
        # 'h' is an index into the fd list; .get() returns a dup'd fd we own
        self.pipewire_fd = fd_list.get(reply.unpack()[0])

    def close(self) -> None:
        if self._closed_sub is not None:
            self._bus.signal_unsubscribe(self._closed_sub)
            self._closed_sub = None
        if self._session_handle is not None:
            with contextlib.suppress(GLib.Error):
                self._bus.call_sync(
                    PORTAL_BUS,
                    self._session_handle,
                    SESSION_IFACE,
                    "Close",
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            self._session_handle = None
        if self.pipewire_fd >= 0:
            os.close(self.pipewire_fd)
            self.pipewire_fd = -1
        if self._loop.is_running():
            self._loop.quit()

    # ------------------------------------------------------------------

    def _request(self, method, build_args, timeout: float = RESPONSE_TIMEOUT_S) -> dict:
        """Call a request-style portal method, wait for its Response signal."""
        self._counter += 1
        token = f"arlo{self._counter}"
        sender = self._bus.get_unique_name().removeprefix(":").replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        event = threading.Event()
        outcome: dict = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            outcome["code"] = code
            outcome["results"] = results
            event.set()

        sub = self._bus.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )
        try:
            self._bus.call_sync(
                PORTAL_BUS,
                PORTAL_OBJECT,
                SCREENCAST_IFACE,
                method,
                build_args(token),
                GLib.VariantType("(o)"),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            if not event.wait(timeout):
                msg = f"{method}: portal did not respond within {timeout:.0f}s"
                raise PortalError(msg)
        finally:
            self._bus.signal_unsubscribe(sub)

        code = outcome["code"]
        if code == 1:
            msg = f"{method}: cancelled in the screen-share dialog"
            raise PortalError(msg)
        if code != 0:
            msg = f"{method}: portal request failed (response code {code})"
            raise PortalError(msg)
        return outcome["results"]

    def _subscribe_closed(self) -> None:
        def on_closed(_conn, _sender, _path, _iface, _signal, _params):
            logger.warning("ScreenCast session closed by the compositor/user")
            self.closed.set()

        self._closed_sub = self._bus.signal_subscribe(
            PORTAL_BUS,
            SESSION_IFACE,
            "Closed",
            self._session_handle,
            None,
            Gio.DBusSignalFlags.NONE,
            on_closed,
        )
```

- [ ] **Step 5.2: Import check, lint, commit**

```bash
uv run python -c "from arc_helper.screen.portal import ScreenCastSession; print('ok')"
uv run ruff check src/arc_helper/screen
git add src/arc_helper/screen/portal.py
git commit -m "Add ScreenCast portal session handshake"
```

---

### Task 6: `screen/wayland_backend.py` — pipeline + frame store

**Files:**
- Create: `src/arc_helper/screen/wayland_backend.py`

- [ ] **Step 6.1: Implement**

`src/arc_helper/screen/wayland_backend.py`:
```python
"""Wayland screen backend: portal ScreenCast stream consumed via GStreamer.

A persistent pipewiresrc->videoconvert->appsink pipeline keeps the latest
BGRx frame in memory; grab() crops it. KWin only sends frames on screen
damage, so pipewiresrc's keepalive re-pushes the last frame periodically.
"""

import threading

from PIL import Image

from arc_helper.config import logger

from .base import Point
from .base import compute_scale
from .frame import crop_bgrx
from .token_store import load_token
from .token_store import save_token

_INSTALL_HINT = (
    "Wayland capture needs PyGObject + GStreamer.\n"
    "  Arch:        sudo pacman -S --needed cairo pkgconf gcc gstreamer gst-plugins-base gst-plugin-pipewire\n"
    "  Debian:      sudo apt install libcairo2-dev libgirepository1.0-dev pkg-config gcc gstreamer1.0-pipewire gstreamer1.0-plugins-base\n"
    "  then:        uv sync --all-extras\n"
    "Or force X11 capture with SCREEN_BACKEND=x11 in .env (requires an X11 session)."
)

try:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstVideo", "1.0")
    from gi.repository import Gst
    from gi.repository import GstVideo
except (ImportError, ValueError) as _e:
    raise ImportError(_INSTALL_HINT) from _e

# portal imports gi itself, so it must stay behind the guard above
from .portal import PortalError  # noqa: E402
from .portal import ScreenCastSession  # noqa: E402

FIRST_FRAME_TIMEOUT_S = 10.0
KEEPALIVE_MS = 500


class WaylandBackend:
    """ScreenBackend implementation for Wayland sessions."""

    name = "wayland"

    def __init__(self):
        self._session: ScreenCastSession | None = None
        self._pipeline: Gst.Element | None = None
        self._lock = threading.Lock()
        self._frame: tuple[bytes, int, int, int] | None = None  # data, w, h, stride
        self._first_frame = threading.Event()
        self._size: tuple[int, int] = (0, 0)
        self._tk_scale = 1.0
        self._restarted = False

    def start(self) -> None:
        Gst.init(None)
        self._frame = None
        self._first_frame = threading.Event()

        self._session = ScreenCastSession(restore_token=load_token())
        try:
            self._session.open()
        except PortalError as e:
            msg = (
                f"Screen sharing setup failed: {e}\n"
                "ARLO needs screen-share permission on Wayland (a one-time dialog).\n"
                "If no dialog appeared, check that xdg-desktop-portal and your "
                "desktop's portal backend are running.\n"
                "Alternatively use an X11 session with SCREEN_BACKEND=x11."
            )
            raise OSError(msg) from e
        if self._session.new_restore_token:
            save_token(self._session.new_restore_token)

        desc = (
            f"pipewiresrc fd={self._session.pipewire_fd} "
            f"path={self._session.node_id} keepalive-time={KEEPALIVE_MS} "
            "! videoconvert ! video/x-raw,format=BGRx "
            "! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
        )
        self._pipeline = Gst.parse_launch(desc)
        sink = self._pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_new_sample)
        self._pipeline.set_state(Gst.State.PLAYING)

        if not self._first_frame.wait(FIRST_FRAME_TIMEOUT_S):
            bus_error = self._collect_bus_error()
            self.stop()
            msg = (
                "No frame arrived from the compositor within "
                f"{FIRST_FRAME_TIMEOUT_S:.0f}s.{bus_error}\n"
                "If the screen-share dialog was approved, try moving the mouse "
                "(some compositors only send frames on screen changes)."
            )
            raise OSError(msg)

        with self._lock:
            assert self._frame is not None
            _, width, height, _ = self._frame
        self._size = (width, height)
        self._tk_scale = compute_scale(width, self._x11_logical_width())
        logger.info(
            f"Wayland capture: {width}x{height}, tk scale {self._tk_scale:.4g}"
        )

    def stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._session is not None:
            self._session.close()
            self._session = None

    def grab(self, bbox: tuple[int, int, int, int]) -> Image.Image:
        if self._session is not None and self._session.closed.is_set():
            self._attempt_restart()
        with self._lock:
            frame = self._frame
        if frame is None:
            msg = "No frame received from the compositor yet"
            raise OSError(msg)
        data, width, height, stride = frame
        return crop_bgrx(data, width, height, stride, bbox)

    def cursor_position(self) -> Point:
        # Wayland has no global cursor query. Xlib coordinates are valid
        # while the pointer is over an XWayland window (the Proton game is
        # one); scale them up to physical/stream pixels.
        from pynput import mouse as pynput_mouse

        pos = pynput_mouse.Controller().position
        return Point(
            x=round(pos[0] * self._tk_scale),
            y=round(pos[1] * self._tk_scale),
        )

    def resolution(self) -> tuple[int, int]:
        return self._size

    @property
    def tk_scale(self) -> float:
        return self._tk_scale

    # ------------------------------------------------------------------

    def _on_new_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        structure = sample.get_caps().get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        meta = GstVideo.buffer_get_video_meta(buf)
        stride = meta.stride[0] if meta else width * 4
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = bytes(mapinfo.data)
        finally:
            buf.unmap(mapinfo)
        if len(data) < height * stride and height > 0:
            stride = len(data) // height  # defensive: tightly packed buffer
        with self._lock:
            self._frame = (data, width, height, stride)
        self._first_frame.set()
        return Gst.FlowReturn.OK

    def _collect_bus_error(self) -> str:
        if self._pipeline is None:
            return ""
        bus = self._pipeline.get_bus()
        parts = []
        while True:
            message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR)
            if message is None:
                break
            err, debug = message.parse_error()
            parts.append(f"{err.message} ({debug})")
        return f"\nGStreamer errors: {'; '.join(parts)}" if parts else ""

    def _x11_logical_width(self) -> int:
        try:
            from Xlib import display as xlib_display

            xdisplay = xlib_display.Display()
            width = xdisplay.screen().width_in_pixels
            xdisplay.close()
        except Exception as e:  # noqa: BLE001 - no XWayland: degrade gracefully
            logger.warning(f"XWayland not reachable ({e}); assuming scale 1.0")
            return 0  # compute_scale(.., 0) -> 1.0
        return width

    def _attempt_restart(self) -> None:
        if self._restarted:
            msg = (
                "Screen sharing was stopped (portal session closed). "
                "Restart ARLO to share again."
            )
            raise OSError(msg)
        self._restarted = True
        logger.warning("Portal session closed - attempting one restart")
        self.stop()
        self.start()
```

- [ ] **Step 6.2: Import check, lint, commit**

```bash
uv run python -c "from arc_helper.screen.wayland_backend import WaylandBackend; print('ok')"
uv run ruff check src/arc_helper/screen
uv run pytest tests/ -v
git add src/arc_helper/screen/wayland_backend.py
git commit -m "Add Wayland backend: portal stream, frame store, scale mapping"
```

---

### Task 7: `screen/diag.py` + LIVE VERIFICATION CHECKPOINT

**Files:**
- Create: `src/arc_helper/screen/diag.py`

- [ ] **Step 7.1: Implement**

`src/arc_helper/screen/diag.py`:
```python
"""Live diagnostic for the screen backend.

Run:  uv run python -m arc_helper.screen.diag
Prints backend, resolution, scale and cursor samples; saves a center crop
to diag_frame.png in the app directory.
"""

import sys
import time

from arc_helper.config import APP_DIR
from arc_helper.screen import get_backend


def main() -> int:
    backend = get_backend()
    print(f"backend    : {backend.name}")
    width, height = backend.resolution()
    print(f"resolution : {width}x{height}")
    print(f"tk scale   : {backend.tk_scale:.4f}")

    for i in range(3):
        cursor = backend.cursor_position()
        print(f"cursor     : ({cursor.x}, {cursor.y})")
        if i < 2:
            time.sleep(1)

    bbox = (width // 2 - 200, height // 2 - 100, width // 2 + 200, height // 2 + 100)
    image = backend.grab(bbox)
    out_path = APP_DIR / "diag_frame.png"
    image.save(out_path)
    print(f"frame crop : {image.size[0]}x{image.size[1]} -> {out_path}")
    backend.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7.2: LIVE CHECKPOINT — first portal run**

Run: `uv run python -m arc_helper.screen.diag`

**Expected on first run:** KDE shows the screen-share dialog once → pick the monitor → output shows:
- `backend    : wayland`
- `resolution : 3840x2160` (the *physical* size — this is the fix for the 2194x1234 problem)
- `tk scale   : 1.7502` (approximately)
- three cursor samples in physical pixels (move the mouse between samples; values should change and roughly equal logical position × 1.75)
- `diag_frame.png` written; **open it and confirm it shows the actual center of the screen, sharp, correct colors** (not black, not blue-tinted — blue tint would mean BGR/RGB swapped in `crop_bgrx`).

**Expected on second run:** NO dialog (restore token used), same output. Verify by running it again.

If the dialog reappears every run: the portal denied persistence — check output of run 1 for `restore_token` issues in the log, and confirm `.screencast_restore_token` exists in the repo root.

- [ ] **Step 7.3: Lint and commit**

```bash
uv run ruff check src/arc_helper/screen
git add src/arc_helper/screen/diag.py
git commit -m "Add screen backend diagnostic command"
```

---

### Task 8: `hotkey.py` — monitors + main.py rewiring

**Files:**
- Create: `src/arc_helper/hotkey.py`
- Modify: `src/arc_helper/main.py` (remove lines 37-66 listener globals, lines 190-209 `_is_hotkey_held`, rewire call sites)
- Test: `tests/test_hotkey.py`

- [ ] **Step 8.1: Write the failing tests**

`tests/test_hotkey.py`:
```python
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
    monitor._devices = [FakeDevice(active=[97])]
    assert monitor.is_held() is True


def test_is_held_false():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = [FakeDevice(active=[29])]
    assert monitor.is_held() is False


def test_no_devices_is_false():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    monitor._devices = []
    assert monitor.is_held() is False


def test_dead_device_dropped():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    dead = FakeDevice(error=OSError("unplugged"))
    alive = FakeDevice(active=[97])
    monitor._devices = [dead, alive]
    assert monitor.is_held() is True
    assert dead not in monitor._devices


def test_stop_closes_devices():
    monitor = EvdevKeyMonitor("KEY_RIGHTCTRL")
    device = FakeDevice()
    monitor._devices = [device]
    monitor.stop()
    assert device.closed
    assert monitor._devices == []
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hotkey.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 8.3: Implement `hotkey.py`**

`src/arc_helper/hotkey.py`:
```python
"""Hold-to-scan hotkey monitors.

Windows: GetAsyncKeyState Ctrl+Shift (unchanged behavior).
X11: pynput listener Ctrl+Shift (unchanged behavior).
Wayland: evdev, hold a single key (default Right Ctrl, HOTKEY_KEY in .env).
"""

import ctypes
import os
import sys
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

    def is_held(self) -> bool:
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

    def start(self) -> None:
        import evdev

        self.stop()
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
                capabilities = device.capabilities()
                key_caps = capabilities.get(evdev.ecodes.EV_KEY, [])
                if self.key_code in key_caps:
                    self._devices.append(device)
                else:
                    device.close()
            except OSError:
                continue
        if not self._devices:
            logger.warning(
                f"Hotkey disabled: no keyboard with {self.key_name} accessible. "
                "Add your user to the 'input' group "
                "(sudo usermod -aG input $USER, then log out and back in)."
            )

    def stop(self) -> None:
        for device in self._devices:
            try:
                device.close()
            except OSError:  # noqa: PERF203 - best-effort cleanup
                pass
        self._devices = []

    def is_held(self) -> bool:
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
            self._devices.remove(device)
        return held


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
```

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hotkey.py -v`
Expected: 7 PASS.

- [ ] **Step 8.5: Rewire `main.py`**

1. Delete lines 37–66 (the `_linux_keys_pressed` global, `_linux_key_listener`, `_linux_key_listener_start`, `_linux_key_listener_stop`).
2. Delete the `_is_hotkey_held` static method (lines 190–209 in the original file).
3. Remove `import ctypes` (no longer used in main.py).
4. Add imports (with the other arc_helper imports):
   ```python
   from arc_helper.hotkey import HotkeyMonitor
   from arc_helper.hotkey import get_hotkey_monitor
   ```
5. In the `Scanner` dataclass, add a field (after `stats`):
   ```python
       hotkey: HotkeyMonitor = field(default_factory=get_hotkey_monitor)
   ```
6. In `Scanner.start`, replace
   ```python
        if sys.platform != "win32":
            _linux_key_listener_start()
   ```
   with
   ```python
        self.hotkey.start()
   ```
7. In `Scanner.stop`, replace
   ```python
        if sys.platform != "win32":
            _linux_key_listener_stop()
   ```
   with
   ```python
        self.hotkey.stop()
   ```
8. In `_scan_loop`, replace `hotkey_now = self._is_hotkey_held()` with `hotkey_now = self.hotkey.is_held()`.
9. Update the startup log line `"Hold Ctrl+Shift to force tooltip scanning (vendor screens, etc.)"` to:
   ```python
        logger.info(
            "Hold the hotkey to force tooltip scanning (Ctrl+Shift; "
            f"{get_settings().hotkey_key} on Wayland)"
        )
   ```
   (`get_settings` is already imported in main.py.)

- [ ] **Step 8.6: Verify, lint, commit**

```bash
uv run pytest tests/ -v
uv run python -c "from arc_helper.hotkey import get_hotkey_monitor; m = get_hotkey_monitor(); m.start(); print(type(m).__name__, 'held:', m.is_held()); m.stop()"
```
Expected on this machine: `EvdevKeyMonitor held: False` (or `True` while holding Right Ctrl — try both).

```bash
uv run ruff check src/arc_helper tests
git add src/arc_helper/hotkey.py src/arc_helper/main.py tests/test_hotkey.py
git commit -m "Add hotkey monitor abstraction with evdev hold-key for Wayland"
```

---

### Task 9: `clickthrough.py` — input-transparent overlays

**Files:**
- Create: `src/arc_helper/clickthrough.py`
- Modify: `src/arc_helper/calibrate.py` (`RegionSelector.show_overlay`, `TooltipCaptureConfig.start_tracking`)
- Modify: `src/arc_helper/main.py` (`DebugOverlay.__init__`)
- Modify: `src/arc_helper/overlay.py` (`OverlayWindow.__init__` — NOT StatusWindow)

- [ ] **Step 9.1: Implement `clickthrough.py`**

`src/arc_helper/clickthrough.py`:
```python
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
```

- [ ] **Step 9.2: Apply to the four overlay windows**

`calibrate.py` — add import `from arc_helper.clickthrough import make_click_through`, then:
- In `RegionSelector.show_overlay`, after `self.overlay.config(bg=self.color)` add:
  ```python
        make_click_through(self.overlay)
  ```
- In `TooltipCaptureConfig.start_tracking`, after `self.overlay.config(bg="green")` add:
  ```python
        make_click_through(self.overlay)
  ```

`main.py` — add import `from arc_helper.clickthrough import make_click_through`, and in `DebugOverlay.__init__`, after `self.window.config(bg="red")` add:
```python
        make_click_through(self.window)
```

`overlay.py` — add import `from .clickthrough import make_click_through`, and in `OverlayWindow.__init__`, after `self._setup_ui()` add:
```python
        # The popup must never swallow game clicks
        make_click_through(self.window)
```
Do NOT touch `StatusWindow` — its right-click-to-quit binding must keep receiving clicks.

- [ ] **Step 9.3: LIVE CHECKPOINT — click-through works**

Run: `uv run arc-calibrate`
- Click **Start Tracking** with the default (uncalibrated) offsets — the green box glues to the cursor. **Verify all calibration buttons still respond to clicks** (this was the phase-0 freeze bug). Click **Stop Tracking**.
- Click **Show Region** — verify clicking *through* the yellow rectangle onto whatever is below it works.

- [ ] **Step 9.4: Lint and commit**

```bash
uv run ruff check src/arc_helper
git add src/arc_helper/clickthrough.py src/arc_helper/calibrate.py src/arc_helper/main.py src/arc_helper/overlay.py
git commit -m "Make overlay windows click-through (fixes calibrate click swallowing)"
```

---

### Task 10: Coordinate mapping for tk windows

**Files:**
- Modify: `src/arc_helper/overlay.py` (`OverlayWindow.show` geometry)
- Modify: `src/arc_helper/main.py` (`DebugOverlay._update_position`)
- Modify: `src/arc_helper/calibrate.py` (`RegionSelector._update_overlay`, `TooltipCaptureConfig._update_overlay_position`, `capture_at_cursor`)

All stored settings stay physical pixels; only tk `geometry()` calls convert.

- [ ] **Step 10.1: overlay.py**

Add import: `from .screen import phys_to_tk`.
In `OverlayWindow.show`, replace
```python
        self.window.geometry(f"+{self.overlay_x}+{self.overlay_y}")
```
with
```python
        self.window.geometry(
            f"+{phys_to_tk(self.overlay_x)}+{phys_to_tk(self.overlay_y)}"
        )
```

- [ ] **Step 10.2: main.py DebugOverlay**

Replace the whole `_update_position` method with (note: also fixes the `src.arc_helper` import bug):
```python
    def _update_position(self):
        """Update overlay position to follow cursor."""
        try:
            from arc_helper.ocr import get_cursor_position
            from arc_helper.screen import phys_to_tk

            cursor = get_cursor_position()

            x = phys_to_tk(cursor.x + self.settings.tooltip_capture.offset_x)
            y = phys_to_tk(cursor.y + self.settings.tooltip_capture.offset_y)
            w = phys_to_tk(self.settings.tooltip_capture.width)
            h = phys_to_tk(self.settings.tooltip_capture.height)

            self.window.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

        # Schedule next update (every 50ms for smooth following)
        self.root.after(50, self._update_position)
```

- [ ] **Step 10.3: calibrate.py**

Add imports: `from arc_helper.ocr import get_cursor_position` and `from arc_helper.screen import phys_to_tk`.

`RegionSelector._update_overlay` — replace the geometry call with:
```python
            self.overlay.geometry(
                f"{phys_to_tk(self.width.get())}x{phys_to_tk(self.height.get())}"
                f"+{phys_to_tk(self.x.get())}+{phys_to_tk(self.y.get())}"
            )
```

`TooltipCaptureConfig._update_overlay_position` — `winfo_pointerx/y` are already tk-logical; offsets/sizes are physical and must be converted. Replace from `offset_x = self.offset_x.get()` to the end of the method with:
```python
        offset_x = self.offset_x.get()

        left = x + phys_to_tk(offset_x)
        top = y + phys_to_tk(self.offset_y.get())

        self.overlay.geometry(
            f"{phys_to_tk(self.width.get())}x{phys_to_tk(self.height.get())}"
            f"+{left}+{top}"
        )
```

`TooltipCaptureConfig.capture_at_cursor` — replace the pointer lookup (the `try: root = ... winfo_pointerx ... except` block) with the backend cursor, which is physical and consistent with `grab_screen`:
```python
        cursor = get_cursor_position()
        cursor_x, cursor_y = cursor.x, cursor.y
```
(The rest of the method already works in physical pixels; delete the now-unused `root = self.parent.winfo_toplevel()` line and keep the `return image, cursor_x, cursor_y`.)

- [ ] **Step 10.4: Verify, lint, commit**

```bash
uv run pytest tests/ -v
uv run ruff check src/arc_helper
```
Live check: `uv run arc-calibrate` → **Show Region** for Trigger Region 1 with x=404 y=163 w=279 h=80 (or any values) — the rectangle should appear at the *physically* correct screen spot (under 175% scaling it will be drawn at tk coords ÷1.75). **Test OCR** should now capture without `XGetImage` errors and the preview should show the screen content under that region.

```bash
git add src/arc_helper/overlay.py src/arc_helper/main.py src/arc_helper/calibrate.py
git commit -m "Map physical pixels to tk coordinates for overlays under Wayland scaling"
```

---

### Task 11: Config examples, README, flake.nix

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `flake.nix`

- [ ] **Step 11.1: .env.example**

Append:
```
# =============================================================================
# PLATFORM SETTINGS (Linux)
# =============================================================================
# Screen capture backend: auto | x11 | wayland
SCREEN_BACKEND=auto
# Hold-to-scan hotkey on Wayland (evdev key name)
HOTKEY_KEY=KEY_RIGHTCTRL
```

- [ ] **Step 11.2: README.md**

In **Requirements → Running from Source**, change `Windows 10/11, or Linux (X11)` to `Windows 10/11, or Linux (X11 or Wayland)`.

Replace the NixOS note line `**Note:** On Linux, ARLO uses X11 for screen capture (mss). Use an X11 session (e.g. "Plasma (X11)" at login) rather than Wayland.` with a new section (placed after the NixOS section):

```markdown
### Linux: Wayland

On a Wayland session ARLO captures the screen through the desktop portal
(xdg-desktop-portal + PipeWire) instead of X11. Requirements:

- System packages (Arch): `sudo pacman -S --needed cairo pkgconf gcc gstreamer gst-plugins-base gst-plugin-pipewire`
  (Debian/Ubuntu: `sudo apt install libcairo2-dev libgirepository1.0-dev pkg-config gcc gstreamer1.0-pipewire gstreamer1.0-plugins-base`)
- `xdg-desktop-portal` plus your desktop's backend (`xdg-desktop-portal-kde`,
  `-gnome` or `-wlr`) — preinstalled on KDE/GNOME.
- Python deps: `uv sync --all-extras` (builds PyGObject).
- Hold-to-scan hotkey: reading the keyboard needs your user in the `input`
  group: `sudo usermod -aG input $USER` (log out and back in). Without it the
  hotkey is disabled but everything else works. The key is `HOTKEY_KEY` in
  `.env` (default `KEY_RIGHTCTRL` — hold Right Ctrl).

On first launch your desktop shows a screen-share dialog once; pick your
monitor. The approval is remembered (`.screencast_restore_token`).

Notes and limitations:
- The game must run under XWayland (Proton's default) for cursor-relative
  tooltip detection; cursor position over native Wayland windows is stale.
- Force a backend with `SCREEN_BACKEND=x11|wayland` in `.env`.
- Diagnostic: `uv run python -m arc_helper.screen.diag` prints the backend,
  resolution, scale and cursor, and saves a test capture to `diag_frame.png`.
```

- [ ] **Step 11.3: flake.nix**

Replace the file content with:

```nix
{
  description = "ARLO - Arc Raiders Loot Overlay (dev environment for NixOS)";

  inputs.nixpkgs.url = "nixpkgs";

  outputs = { self, nixpkgs }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      # Kernel headers and compiler for building the evdev Python package (pynput dependency)
      # X11 libs so mss (X11 backend) finds libX11/libXfixes/libXrandr at runtime
      # glib/cairo so uv can build PyGObject; GStreamer + pipewire for Wayland capture
      buildInputs = with pkgs; [
        uv
        tesseract
        linuxHeaders
        gcc
        pkg-config
        libx11
        libxfixes
        libxrandr
        glib
        libffi
        cairo
        gobject-introspection
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
        pipewire
      ];
      libPath = pkgs.lib.makeLibraryPath (with pkgs; [
        libx11
        libxfixes
        libxrandr
        glib
        cairo
      ]);
      giTypelibPath = pkgs.lib.makeSearchPath "lib/girepository-1.0" (with pkgs; [
        glib.out
        gobject-introspection
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
      ]);
      gstPluginPath = pkgs.lib.makeSearchPath "lib/gstreamer-1.0" (with pkgs; [
        gst_all_1.gstreamer
        gst_all_1.gst-plugins-base
        pipewire
      ]);
    in
    {
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = buildInputs;
        C_INCLUDE_PATH = "${pkgs.linuxHeaders}/include";
        CPATH = "${pkgs.linuxHeaders}/include";
        LD_LIBRARY_PATH = libPath;
        GI_TYPELIB_PATH = giTypelibPath;
        GST_PLUGIN_SYSTEM_PATH_1_0 = gstPluginPath;
      };
    };
}
```

- [ ] **Step 11.4: Commit**

```bash
git add .env.example README.md flake.nix
git commit -m "Document Wayland setup; add Wayland deps to nix dev shell"
```

---

### Task 12: Final verification

- [ ] **Step 12.1: Full automated suite**

```bash
uv run pytest tests/ -v          # all tests pass
uv run ruff check src/arc_helper tests   # no NEW violations (13 pre-existing repo-wide is baseline)
uv run python -c "import arc_helper.main, arc_helper.calibrate, arc_helper.overlay, arc_helper.ocr, arc_helper.config, arc_helper.hotkey, arc_helper.clickthrough; print('imports ok')"
```

- [ ] **Step 12.2: Diag re-check**

`uv run python -m arc_helper.screen.diag` — no dialog (token reused), 3840x2160, scale ~1.7502, sane cursor, correct PNG.

- [ ] **Step 12.3: LIVE CHECKPOINT — calibration end-to-end**

`uv run arc-calibrate`:
1. Test OCR on a trigger region → captures without errors, preview shows screen content.
2. Start Tracking → green box follows cursor at the right *physical* spot; buttons stay clickable; Stop Tracking works.
3. Test OCR at Cursor → captures around the cursor correctly.
4. Save Configuration → `.env` written including `SCREEN_BACKEND` and `HOTKEY_KEY` lines.

- [ ] **Step 12.4: LIVE CHECKPOINT — the real thing**

With Arc Raiders running (borderless windowed, under Proton):
1. `uv run arc-helper` → log shows `Screen backend: wayland`, resolution 3840x2160, the 4K profile loads.
2. Open the in-game inventory → status dot turns to `INVENTORY`.
3. Hover an item → recommendation popup appears at the configured position, doesn't intercept clicks.
4. Close inventory, hold Right Ctrl over a vendor screen → `HOTKEY SCAN` status, tooltip scanning works, releases cleanly.
5. Right-click the status dot → clean shutdown, no tracebacks in the log.

- [ ] **Step 12.5: X11 regression statement**

We cannot test an X11 session on this machine without logging out. State in the summary: the mss path was extracted verbatim (Task 4) and selection defaults to it when `WAYLAND_DISPLAY` is absent; ask the user to (optionally) verify an X11 session later. Windows path: unchanged code, `choose_backend_name("win32", ...)` short-circuits — covered by unit tests.

- [ ] **Step 12.6: Final commit (if anything was touched during verification)**

```bash
git status   # should be clean apart from .env/.screencast_restore_token/diag_frame.png (all gitignored or local)
```

---

## Post-implementation notes for the summary to the user

- First run shows the KDE screen-share dialog once; the token file `.screencast_restore_token` keeps later runs silent. Deleting it (or KDE revoking permission in System Settings → Applications → Screen Sharing) brings the dialog back.
- Hotkey is hold-**Right Ctrl** (`HOTKEY_KEY` in `.env`); user is already in the `input` group, no setup needed.
- Known limitations: cursor position is stale over native-Wayland windows (fine in-game under Proton); the calibrate sliders' max ranges (X≤3000) predate 4K and are a pre-existing limitation, untouched.
- Upstreaming: Tasks 1–11 are self-contained and Windows-neutral; offer to open a PR against Soygen/ARLO after the user has played a session with it.
