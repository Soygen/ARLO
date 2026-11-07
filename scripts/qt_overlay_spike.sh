#!/usr/bin/env bash
# Phase 0 Qt overlay spike launcher. Usage:
#   scripts/qt_overlay_spike.sh xcb        [extra args]
#   scripts/qt_overlay_spike.sh wayland    [extra args]
#   scripts/qt_overlay_spike.sh layershell [extra args]
# Extra args go to qt_overlay_spike.py (e.g. --target --dots 300 --seconds 30).
set -euo pipefail
cd "$(dirname "$0")/.."

backend="${1:?backend: xcb | wayland | layershell}"
shift

# KDE exports a system QT_PLUGIN_PATH (Qt 6.11); it must not leak into a
# different Qt build or the platform plugins fail to load.
unset QT_PLUGIN_PATH

case "$backend" in
  xcb|wayland)
    export QT_QPA_PLATFORM="$backend"
    exec nix develop --command bash -c \
      "unset QT_PLUGIN_PATH; uv run python scripts/qt_overlay_spike.py --backend $backend $*"
    ;;
  layershell)
    # LayerShellQt is built against nixpkgs Qt, so use nixpkgs PySide6 (same Qt)
    # rather than the PyPI wheel.
    ls_qt="$(nix build --no-link --print-out-paths nixpkgs#kdePackages.layer-shell-qt)"
    py="$(nix build --no-link --print-out-paths --impure --expr \
      '(builtins.getFlake "nixpkgs").legacyPackages.x86_64-linux.python313.withPackages (p: [ p.pyside6 ])')"
    export QT_QPA_PLATFORM=wayland
    export QT_WAYLAND_SHELL_INTEGRATION=layer-shell
    export QT_PLUGIN_PATH="$ls_qt/lib/qt-6/plugins"
    export LAYER_SHELL_QT_LIB="$ls_qt/lib/libLayerShellQtInterface.so.6"
    exec "$py/bin/python" scripts/qt_overlay_spike.py --backend layershell "$@"
    ;;
  *)
    echo "unknown backend: $backend" >&2
    exit 2
    ;;
esac
