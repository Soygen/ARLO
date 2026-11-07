# Phase 0: Qt overlay spike (KDE Plasma 6.6, Wayland)

Script: `scripts/qt_overlay_spike.sh <xcb|wayland|layershell> [--window-type tool|tooltip|bypass] [--target] [--dots N] [--hz N] [--seconds N]`.
Results append to `debug/qt_spike_results.jsonl`.

Environment: KDE Plasma 6.6.6 (kwin_wayland), 2560x1440, scale 1.0, game fullscreen and focused.
PySide6 6.12 wheel for xcb and plain Wayland; nixpkgs PySide6 6.11 with LayerShellQt 6.6.6 for layer-shell.

## Results

| Backend | Over focused game | Alpha | Position | Click-through (drag game map) | Paint |
|---|---|---|---|---|---|
| A. xcb, `Tool` + `StaysOnTop` | no (KWin stacks fullscreen above it) | n/a | yes | n/a | ~4 ms / 60 |
| A. xcb, `bypass` (override-redirect) | **yes** | **yes** | **yes** | **PASS** (manual: click/drag through overlay into game) | ~4 ms / 60; ~15 ms / 300 |
| A. xcb, `tooltip` | not retested over focused game | yes | yes | not tested | ~5 ms / 60 |
| B. Wayland + LayerShellQt | **yes** (screenshot) | **yes** | **yes** | pending manual | ~4 ms / 60; ~16 ms / 300 |
| C. plain Wayland xdg toplevel | no | n/a | no protocol control | n/a | ~4 ms / 60 |

### Manual notes

- **2026-11-03:** `scripts/qt_overlay_spike.sh xcb --window-type bypass --seconds 60` — click-through **PASS** (could click through and drag in-game).

### Still to verify by hand

1. Layer-shell click-through: `scripts/qt_overlay_spike.sh layershell --seconds 60` — same drag test.
2. Load feel: add `--dots 300` on the backend you prefer; watch for input lag / stutter while playing (paint is ~15–16 ms in JSONL — fine if the game still feels snappy).
3. Focus: alt-tab into the game with the overlay up; it must stay above.

## Notes for the migration

- KDE exports a system `QT_PLUGIN_PATH` (Qt 6.11). It must be unset for the PySide6 wheel, or the Wayland shell plugins fail to load (ABI mismatch).
- The PySide6 wheel on NixOS needs extra runtime libraries in the devShell `LD_LIBRARY_PATH` (added in `flake.nix`).
- LayerShellQt has no Python bindings. The spike calls it through ctypes with mangled C++ symbols, and it must be built against the same Qt as PySide6 (nixpkgs, not PyPI).
- Layer-shell exists on KDE and wlroots compositors (Hyprland, Sway), but **not on GNOME**. The xcb override-redirect path works anywhere XWayland exists.
