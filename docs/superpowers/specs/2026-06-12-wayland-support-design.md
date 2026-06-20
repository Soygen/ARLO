# Wayland Support — Design

**Date:** 2026-06-12
**Status:** Approved by user (sections reviewed interactively)
**Scope:** Portable, upstream-worthy Wayland support for ARLO's Linux path, replacing the X11-only mss capture stack. Windows behavior is unchanged.

## Problem

ARLO's Linux support depends on X11 in four places, all of which fail or degrade on a Wayland session (verified on KDE Plasma, 4K @ 175% fractional scaling):

1. **Screen capture** — `grab_screen()` (`src/arc_helper/ocr.py`) uses `mss`, which calls Xlib `XGetImage`. On Plasma Wayland this raises `ScreenShotError: XGetImage() failed` for every capture, even with XWayland present.
2. **Cursor position** — `get_cursor_position()` uses pynput/Xlib. Wayland has no global pointer query; Xlib only reports valid coordinates while the pointer is over an XWayland window.
3. **Global hotkey** — the hold-Ctrl+Shift listener (pynput, `main.py`) cannot see keystrokes delivered to Wayland-focused windows.
4. **Resolution / scaling** — `get_screen_resolution()` via mss reports the XWayland logical size (e.g. 2194×1234 = 3840 ÷ 1.75), not physical pixels, so resolution profiles fail to match.

Additionally, phase-0 testing found a calibration-tool bug independent of Wayland: the "Start Tracking" preview overlay sits directly under the pointer with default 0-offset settings and swallows all mouse clicks.

## Decisions (made with user)

- **Portability:** freedesktop-portal + PipeWire mechanisms only; no compositor-specific tools. X11/mss path is retained as a selectable backend. Intended to be upstreamable to Soygen/ARLO.
- **Capture stack:** PyGObject + GStreamer (`pipewiresrc → videoconvert → appsink`) consuming an xdg-desktop-portal ScreenCast stream. (Chosen over a pure-Python jeepney + gst-launch subprocess approach, and over raw libpipewire-via-ctypes.)
- **Cursor:** keep pynput/Xlib, corrected by an automatic scale factor (stream width ÷ X display width). Valid while the pointer is over XWayland windows — which includes the Proton game. Behind an interface so a libpipewire cursor-metadata provider can replace it later.
- **Hotkey:** hold a single key, **Right Ctrl** by default, read via evdev. Configurable through `HOTKEY_KEY` (any evdev key name, e.g. `KEY_RIGHTCTRL`). User chose Right Ctrl over Right Meta to avoid KDE's Meta-opens-launcher behavior. Requires the user in the `input` group (documented). Windows and X11 sessions keep the existing hold-Ctrl+Shift behavior.
- **Overlay:** tkinter stays, running via XWayland, with physical→logical coordinate mapping applied when positioning windows. A Wayland-native layer-shell overlay is explicitly out of scope (future work).
- **Calibrate fix in scope:** make the tracking preview overlay input-transparent so it cannot swallow clicks.

## Architecture

### New package: `src/arc_helper/screen/`

All platform-specific screen access moves behind one interface. **Everything at this boundary is physical pixels** — existing settings, region configs, and resolution profiles keep their meaning unchanged.

- **`screen/base.py`** — `ScreenBackend` protocol:
  - `start() -> None` / `stop() -> None`
  - `grab(bbox: tuple[int, int, int, int]) -> PIL.Image` (left, top, right, bottom in physical px)
  - `cursor_position() -> Point` (physical px)
  - `resolution() -> tuple[int, int]` (physical px, primary/selected monitor)
- **`screen/mss_backend.py`** — today's behavior extracted verbatim: mss grabs; cursor via WinAPI on Windows, pynput/Xlib on X11. Zero behavior change for Windows and X11 sessions.
- **`screen/wayland_backend.py`** — owns:
  - the portal D-Bus session (via Gio): CreateSession → SelectSources → Start → OpenPipeWireRemote;
  - a persistent GStreamer pipeline `pipewiresrc fd=<fd> path=<node> ! videoconvert ! video/x-raw,format=BGRx ! appsink drop=true max-buffers=1`;
  - the latest frame (bytes + stride + size) under a lock, updated by the appsink callback;
  - a `ScaleMapper` computing `scale = stream_width / x_display_width` (handles 175% fractional scaling automatically);
  - the restore token, persisted to a gitignored state file in `APP_DIR`.
  - `grab()` = numpy crop of the latest frame. `cursor_position()` = pynput logical coords × scale.
  - Portal options: source type MONITOR, `cursor_mode = hidden` (pointer never burned into OCR frames), `persist_mode = 2` + restore token (share dialog appears once ever).
- **`screen/__init__.py`** — `get_backend()` singleton + selection logic:
  - `sys.platform == "win32"` → mss backend
  - Linux with `WAYLAND_DISPLAY` set → wayland backend
  - otherwise → mss/X11 backend
  - Override: `ARLO_SCREEN_BACKEND = auto | x11 | wayland` in `.env`.
- **`screen/diag.py`** — `python -m arc_helper.screen.diag` prints chosen backend, negotiated resolution, scale factor, live cursor position, and writes one cropped test frame to a PNG. Used for live verification and upstream bug reports.

### New module: `src/arc_helper/hotkey.py`

`HotkeyMonitor` protocol: `start()`, `stop()`, `is_held() -> bool`. Implementations:

- **Windows** — `GetAsyncKeyState` Ctrl+Shift (moved from `main.py`, unchanged).
- **X11** — pynput listener Ctrl+Shift (moved from `main.py`, unchanged).
- **Wayland** — evdev: enumerate keyboard-capable devices, track key state of `HOTKEY_KEY` (default `KEY_RIGHTCTRL`). No new dependency — pynput already requires evdev on Linux. If `/dev/input` is unreadable, log one clear warning and run with the hotkey disabled (the INVENTORY-trigger flow is unaffected).

### Caller changes (thin)

- `ocr.py`: `grab_screen()` and `get_cursor_position()` delegate to `get_backend()`.
- `config.py`: `get_screen_resolution()` delegates to the backend.
- `main.py`: hotkey block (`_is_hotkey_held`, `_linux_key_listener_*`) replaced by `HotkeyMonitor`.
- `overlay.py`: window placement divides physical-pixel positions by the backend's scale factor (1.0 everywhere except Wayland-with-scaling) before passing to tkinter.
- `calibrate.py`: uses the backend; tracking preview overlay made input-transparent — X11 Shape extension input region via python-xlib (already a pynput dependency; works under XWayland), `WS_EX_TRANSPARENT` via ctypes on Windows.

## Runtime behavior

- **Startup (Wayland):** portal handshake; first run ever shows the desktop's screen-share dialog once (user picks the monitor), token saved; later runs are silent. Negotiated stream size becomes `resolution()`, so the 3840×2160 profile auto-loads.
- **Steady state:** frames are damage-driven — the compositor pushes only on screen change. A static screen is not an error; the last frame remains valid indefinitely. Scanner cadence (500ms trigger / 300ms tooltip) is unchanged; grabs become in-memory crops, cheaper than per-call XGetImage.
- **Shutdown:** pipeline → NULL, portal session Close.

## Error handling

| Failure | Behavior |
|---|---|
| Portal missing / user denies dialog | Fatal at startup with actionable message (install `xdg-desktop-portal` + backend, or set `ARLO_SCREEN_BACKEND=x11`, or use an X11 session). No silent fallback. |
| Restore token rejected | Re-show the dialog once, save the new token. |
| Stream dies mid-session | One automatic session-restart attempt; on failure, red error state on StatusWindow and scanner pauses. |
| `grab()` before any frame arrived / stream dead | Raises; scanner's existing error backoff handles it. |
| `/dev/input` unreadable (hotkey) | One warning, hotkey disabled, app continues. |
| Cursor over native Wayland window | Coordinates go stale (last known position). Harmless in-game (game is XWayland). Documented limitation. |

## Configuration

`.env` additions (with `.env.example` entries):

- `ARLO_SCREEN_BACKEND` — `auto` (default) | `x11` | `wayland`
- `HOTKEY_KEY` — evdev key name, default `KEY_RIGHTCTRL` (Linux/Wayland only; Windows keeps Ctrl+Shift)

## Dependencies & packaging

- `pyproject.toml`: PyGObject under a new optional-dependencies group `wayland` (`uv sync --all-extras` picks it up, matching the README's existing install instructions; Windows installs are unaffected because the extra is only needed at runtime by the wayland backend, which import-guards it).
- System packages (README): `gstreamer`, `gst-plugin-pipewire` (Arch names; apt equivalents listed), `xdg-desktop-portal` + desktop backend, Tesseract as before. One-time `sudo usermod -aG input $USER` for the hotkey.
- `flake.nix`: add gobject-introspection, gstreamer + base plugins, pipewire/gst-plugin-pipewire, cairo/pkg-config for the PyGObject build.
- Windows build (`build.py`, PyInstaller specs) untouched.

## Testing

- **Unit (pytest, no display required):** backend selection logic (platform/env matrix), `ScaleMapper` math, bbox cropping against synthetic frames, restore-token persistence round-trip, evdev key-name parsing/validation, hotkey state machine with injected events.
- **Live verification (user's machine):** `screen/diag` output — correct backend, 3840×2160 resolution, scale ≈ 1.75, cursor position sane, test PNG visually correct; then `arc-calibrate` Test OCR, then full `arc-helper` run against the game.

## Out of scope (future work)

- Wayland-native layer-shell overlay (needed only if the game stops being an XWayland client, or for GNOME stacking).
- libpipewire cursor-metadata provider (drop-in replacement behind `ScreenBackend.cursor_position()`).
- GlobalShortcuts-portal hotkey variant.
