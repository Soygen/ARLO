"""Wayland screen backend: portal ScreenCast stream consumed via GStreamer.

A persistent pipewiresrc->videoconvert->appsink pipeline keeps the latest
BGRx frame in memory; grab() crops it. KWin only sends frames on screen
damage, so pipewiresrc's keepalive re-pushes the last frame periodically.
"""

import threading

from PIL import Image

from arc_helper.config import logger

from .base import Point
from .base import ScreenCaptureUnavailable
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
    from gi.repository import GLib
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
        self._terminal_error: str | None = None

    def start(self) -> None:
        Gst.init(None)
        self._frame = None
        self._first_frame = threading.Event()

        try:
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
                try:
                    save_token(self._session.new_restore_token)
                except OSError as e:
                    logger.warning(f"Could not save screen-share restore token: {e}")

            desc = (
                f"pipewiresrc fd={self._session.pipewire_fd} "
                f"path={self._session.node_id} keepalive-time={KEEPALIVE_MS} "
                "! videoconvert ! video/x-raw,format=BGRx "
                "! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
            )
            try:
                self._pipeline = Gst.parse_launch(desc)
            except GLib.Error as e:
                msg = (
                    f"GStreamer pipeline creation failed: {e.message}\n"
                    "Is gst-plugin-pipewire installed?\n" + _INSTALL_HINT
                )
                raise OSError(msg) from e
            sink = self._pipeline.get_by_name("sink")
            sink.connect("new-sample", self._on_new_sample)
            if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                msg = f"GStreamer pipeline refused to start.{self._collect_bus_error()}"
                raise OSError(msg)  # noqa: TRY301

            if not self._first_frame.wait(FIRST_FRAME_TIMEOUT_S):
                bus_error = self._collect_bus_error()
                msg = (
                    "No frame arrived from the compositor within "
                    f"{FIRST_FRAME_TIMEOUT_S:.0f}s.{bus_error}\n"
                    "If the screen-share dialog was approved, try moving the mouse "
                    "(some compositors only send frames on screen changes)."
                )
                raise OSError(msg)  # noqa: TRY301
        except Exception:
            self.stop()  # release partial session/pipeline; safe and idempotent
            raise

        with self._lock:
            frame = self._frame
        if frame is None:
            msg = "First frame vanished unexpectedly"
            raise OSError(msg)
        _, width, height, _ = frame
        # Single-monitor assumption: XWayland reports the total logical
        # screen width, the stream is one monitor. Fine for the primary-
        # monitor use case; multi-monitor scaling is a known limitation.
        # Plain attribute stores (atomic in CPython); readers may see one stale frame's worth during restart - harmless.
        self._size = (width, height)
        self._tk_scale = compute_scale(width, self._x11_logical_width())
        logger.info(
            f"Wayland capture: {width}x{height}, tk scale {self._tk_scale:.4g}"
        )

    def stop(self) -> None:
        # Order matters: NULL the pipeline first (joins streaming threads),
        # then close the session (which closes our pipewire fd).
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._session is not None:
            self._session.close()
            self._session = None

    def grab(self, bbox: tuple[int, int, int, int]) -> Image.Image:
        if self._terminal_error is not None:
            raise ScreenCaptureUnavailable(self._terminal_error)
        session = self._session  # local ref: stop() may null it concurrently
        if session is not None and session.closed.is_set():
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

    def _x11_logical_width(self) -> int:  # noqa: PLR6301
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
            # Deliberately leave the dead session in place as a tombstone;
            # clearing it would let grab() serve the frozen last frame.
            self._terminal_error = (
                "Screen sharing was stopped (portal session closed). "
                "Restart ARLO to share again."
            )
            raise ScreenCaptureUnavailable(self._terminal_error)
        self._restarted = True
        logger.warning("Portal session closed - attempting one restart")
        self.stop()
        try:
            self.start()
        except Exception as e:
            self._terminal_error = (
                f"Screen sharing could not be restored: {e}\n"
                "Restart ARLO to share again."
            )
            raise ScreenCaptureUnavailable(self._terminal_error) from e
