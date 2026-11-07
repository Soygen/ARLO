"""Phase 0 spike: can a Qt overlay be transparent, positioned, on top, and click-through?

Standalone — does not import arc_helper. Run via ``scripts/qt_overlay_spike.sh``.

Backends (``--backend``):
  xcb         Qt on XWayland (QT_QPA_PLATFORM=xcb)
  wayland     Qt native Wayland, plain xdg toplevel (control case)
  layershell  Qt native Wayland + LayerShellQt (overlay layer, absolute margins)

The overlay draws:
  * a faint frame around its own bounds
  * a crosshair at screen pixel (--rect x+100, y+100) labelled with its expected position
  * N anti-aliased, semi-transparent dots re-randomised at --hz
  * a HUD with frame-time stats

``--target`` also opens a normal (input-accepting) window that counts clicks,
so you can click *through* the overlay onto it without the game.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtCore import QRect
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtGui import QFont
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QWidget

COLORS = ["#4aa3ff", "#ffc400", "#ff5e9e", "#59d97a", "#ff8a3d", "#c77dff"]


# --- LayerShellQt via ctypes (no Python bindings exist) -----------------------


class _QMargins(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("top", ctypes.c_int),
        ("right", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


def apply_layer_shell(widget: QWidget, rect: QRect) -> str:
    """Configure ``widget``'s QWindow as an overlay layer surface. Call before show()."""
    import shiboken6

    lib_path = os.environ.get("LAYER_SHELL_QT_LIB")
    if not lib_path:
        return "LAYER_SHELL_QT_LIB not set"
    lib = ctypes.CDLL(lib_path)

    widget.winId()  # create the native QWindow
    qwindow = widget.windowHandle()
    ptr = shiboken6.getCppPointer(qwindow)[0]

    get = lib._ZN12LayerShellQt6Window3getEP7QWindow
    get.restype = ctypes.c_void_p
    get.argtypes = [ctypes.c_void_p]
    lsw = get(ptr)
    if not lsw:
        return "LayerShellQt::Window::get returned null"

    def call(sym: str, *args, argtypes):
        fn = getattr(lib, sym)
        fn.restype = None
        fn.argtypes = [ctypes.c_void_p, *argtypes]
        fn(lsw, *args)

    layer_overlay = 3
    anchor_top, anchor_left = 1, 4
    kb_none = 0
    call(
        "_ZN12LayerShellQt6Window8setLayerENS0_5LayerE",
        layer_overlay,
        argtypes=[ctypes.c_int],
    )
    call(
        "_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE",
        anchor_top | anchor_left,
        argtypes=[ctypes.c_int],
    )
    margins = _QMargins(rect.x(), rect.y(), 0, 0)
    call(
        "_ZN12LayerShellQt6Window10setMarginsERK8QMargins",
        ctypes.byref(margins),
        argtypes=[ctypes.c_void_p],
    )
    call(
        "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE",
        kb_none,
        argtypes=[ctypes.c_int],
    )
    call("_ZN12LayerShellQt6Window16setExclusiveZoneEi", -1, argtypes=[ctypes.c_int])
    return "ok"


# --- overlay ------------------------------------------------------------------


class Overlay(QWidget):
    def __init__(self, args: argparse.Namespace, rect: QRect):
        super().__init__()
        self.args = args
        self.rect_req = rect
        self.dots: list[tuple[float, float, QColor, float]] = []
        self.frame_ms: list[float] = []
        self.tick_gaps_ms: list[float] = []
        self._last_tick = time.perf_counter()
        self.ticks = 0
        self.layer_status = ""

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if args.window_type == "tooltip":
            flags |= Qt.WindowType.ToolTip
        elif args.window_type == "bypass":
            # X11 override-redirect: unmanaged by KWin, like Tk overrideredirect.
            flags |= Qt.WindowType.Tool | Qt.WindowType.X11BypassWindowManagerHint
        else:
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(rect)

        if args.backend == "layershell":
            self.layer_status = apply_layer_shell(self, rect)

        self._randomise()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(max(1, int(1000 / args.hz)))

    def _randomise(self) -> None:
        w, h = self.rect_req.width(), self.rect_req.height()
        self.dots = [
            (
                random.uniform(20, w - 20),
                random.uniform(20, h - 20),
                QColor(random.choice(COLORS)),
                random.uniform(4, 9),
            )
            for _ in range(self.args.dots)
        ]

    def _tick(self) -> None:
        now = time.perf_counter()
        self.tick_gaps_ms.append((now - self._last_tick) * 1000)
        self._last_tick = now
        self.ticks += 1
        self._randomise()
        self.update()

    def paintEvent(self, _event) -> None:
        t0 = time.perf_counter()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Faint frame: shows the window bounds without blocking the view.
        p.setPen(QPen(QColor(255, 255, 255, 70), 2))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))

        # Crosshair at local (100, 100) == expected screen (x+100, y+100).
        cx, cy = 100, 100
        p.setPen(QPen(QColor(255, 0, 255, 255), 2))
        p.drawLine(cx - 30, cy, cx + 30, cy)
        p.drawLine(cx, cy - 30, cx, cy + 30)
        p.setFont(QFont("Sans", 10))
        exp = (self.rect_req.x() + cx, self.rect_req.y() + cy)
        p.drawText(cx + 8, cy - 8, f"expected screen {exp}")

        for x, y, color, r in self.dots:
            c = QColor(color)
            c.setAlpha(210)
            p.setBrush(c)
            p.setPen(QPen(QColor(10, 10, 10, 200), 1.5))
            p.drawEllipse(QPoint(int(x), int(y)), r, r)

        # Semi-transparent HUD panel (tests per-pixel alpha).
        hud = QRect(self.width() - 430, 10, 420, 110)
        p.setBrush(QColor(0, 0, 0, 120))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(hud, 8, 8)
        p.setPen(QColor("white"))
        avg = statistics.mean(self.frame_ms[-200:]) if self.frame_ms else 0.0
        mx = max(self.frame_ms[-200:]) if self.frame_ms else 0.0
        gp = self.mapToGlobal(QPoint(0, 0))
        lines = [
            f"backend={self.args.backend} platform={QApplication.platformName()}",
            f"dots={self.args.dots} hz={self.args.hz} ticks={self.ticks}",
            f"paint avg={avg:.2f}ms max={mx:.2f}ms",
            f"Qt says origin=({gp.x()},{gp.y()}) req=({self.rect_req.x()},{self.rect_req.y()})",
            f"layershell: {self.layer_status or 'n/a'}",
        ]
        for i, line in enumerate(lines):
            p.drawText(hud.x() + 10, hud.y() + 20 + i * 18, line)
        p.end()
        self.frame_ms.append((time.perf_counter() - t0) * 1000)


class ClickTarget(QLabel):
    """Normal window under the overlay: clicks that pass through land here."""

    def __init__(self, rect: QRect):
        super().__init__()
        self.clicks = 0
        self.setWindowTitle("ARLO spike click target")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:#203040;color:white;font-size:22px;")
        self.setGeometry(rect)
        self._render()

    def _render(self) -> None:
        self.setText(
            f"Click target: {self.clicks} clicks\n"
            "Click on the dots/crosshair area.\n"
            "If this count goes up, click-through works."
        )

    def mousePressEvent(self, _event) -> None:
        self.clicks += 1
        self._render()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--backend", choices=["xcb", "wayland", "layershell"], required=True
    )
    ap.add_argument("--rect", default="400,200,1200,800", help="x,y,w,h physical px")
    ap.add_argument("--dots", type=int, default=60)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument(
        "--window-type", choices=["tool", "tooltip", "bypass"], default="tool"
    )
    ap.add_argument(
        "--target", action="store_true", help="open a click-counting window"
    )
    ap.add_argument("--out", default="debug/qt_spike_results.jsonl")
    args = ap.parse_args()

    x, y, w, h = (int(v) for v in args.rect.split(","))
    rect = QRect(x, y, w, h)

    app = QApplication(sys.argv)
    target = None
    if args.target:
        target = ClickTarget(QRect(x + 50, y + 50, w - 100, h - 100))
        target.show()

    ov = Overlay(args, rect)
    ov.show()

    def finish() -> None:
        fm = ov.frame_ms[5:] or [0.0]
        gaps = ov.tick_gaps_ms[5:] or [0.0]
        gp = ov.mapToGlobal(QPoint(0, 0))
        result = {
            "backend": args.backend,
            "platform": QApplication.platformName(),
            "window_type": args.window_type,
            "dots": args.dots,
            "hz": args.hz,
            "seconds": args.seconds,
            "ticks": ov.ticks,
            "paint_ms_avg": round(statistics.mean(fm), 3),
            "paint_ms_p95": round(sorted(fm)[int(len(fm) * 0.95) - 1], 3),
            "paint_ms_max": round(max(fm), 3),
            "tick_gap_ms_p95": round(sorted(gaps)[int(len(gaps) * 0.95) - 1], 3),
            "tick_gap_ms_max": round(max(gaps), 3),
            "qt_origin": [gp.x(), gp.y()],
            "requested_origin": [x, y],
            "layershell": ov.layer_status or None,
            "target_clicks": target.clicks if target else None,
            "dpr": QApplication.primaryScreen().devicePixelRatio(),
        }
        print(json.dumps(result))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(json.dumps(result) + "\n")
        app.quit()

    QTimer.singleShot(int(args.seconds * 1000), finish)
    app.exec()


if __name__ == "__main__":
    main()
