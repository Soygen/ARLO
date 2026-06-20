"""xdg-desktop-portal ScreenCast session (D-Bus via Gio).

Handshake: CreateSession -> SelectSources -> Start -> OpenPipeWireRemote.
Request-style methods deliver results via a Response signal on a request
object whose path is predictable from our unique bus name + handle_token.
"""

import itertools
import os
import threading
import time
from collections.abc import Callable

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

_instance_counter = itertools.count(1)


class PortalError(RuntimeError):
    """A portal request failed or was cancelled."""


class ScreenCastSession:
    """One ScreenCast session: handshake, stream node, pipewire fd.

    `closed` is set when the compositor or user ends the cast (e.g. KDE's
    "stop sharing"); the consumer should then close() and create a fresh
    session. It is NOT set by our own close().
    """

    def __init__(self, restore_token: str | None = None):
        self.restore_token = restore_token
        self.new_restore_token: str | None = None
        self.node_id: int | None = None
        self.stream_props: dict = {}
        self.pipewire_fd: int = -1  # dup'd from the portal; ours to close (pipewiresrc dups again internally)
        self.closed = threading.Event()  # compositor/user ended the cast

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            msg = (
                "Cannot reach the D-Bus session bus. "
                "ARLO's Wayland capture requires a desktop session."
            )
            raise PortalError(msg) from e
        # Signal callbacks only fire while something iterates the default
        # GLib main context; run a main loop in a daemon thread.
        self._loop = GLib.MainLoop()
        self._loop_thread: threading.Thread | None = None
        self._counter = 0
        self._session_handle: str | None = None
        self._closed_sub: int | None = None
        self._app_token = f"arlo{next(_instance_counter)}"
        self._abort = threading.Event()

    def open(self) -> None:
        """Run the full handshake. May block on the screen-share dialog.

        One-shot: create a new instance to reopen. On failure, call close()
        to release the partially-created session.
        """
        if self._loop_thread is not None:
            msg = "Session already opened; create a new ScreenCastSession"
            raise PortalError(msg)
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
                        "session_handle_token": GLib.Variant("s", self._app_token),
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

        try:
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
        except GLib.Error as e:
            msg = "OpenPipeWireRemote failed (is PipeWire running?)"
            raise PortalError(msg) from e
        # 'h' is an index into the fd list; .get() returns a dup'd fd we own
        self.pipewire_fd = fd_list.get(reply.unpack()[0])

    def close(self) -> None:
        self._abort.set()
        if self._closed_sub is not None:
            self._bus.signal_unsubscribe(self._closed_sub)
            self._closed_sub = None
        if self._session_handle is not None:
            try:
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
            except GLib.Error as e:
                logger.debug(f"Portal session close failed (already gone?): {e}")
            self._session_handle = None
        if self.pipewire_fd >= 0:
            os.close(self.pipewire_fd)
            self.pipewire_fd = -1
        if self._loop.is_running():
            self._loop.quit()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2.0)
            self._loop_thread = None

    # ------------------------------------------------------------------

    def _request(self, method: str, build_args: Callable[[str], GLib.Variant], timeout: float = RESPONSE_TIMEOUT_S) -> dict:
        """Call a request-style portal method, wait for its Response signal."""
        self._counter += 1
        token = f"{self._app_token}_{self._counter}"
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
            except GLib.Error as e:
                msg = (
                    f"{method}: portal call failed - is xdg-desktop-portal "
                    "running with a backend for your desktop "
                    "(xdg-desktop-portal-kde/-gnome/-wlr)?"
                )
                raise PortalError(msg) from e
            deadline = time.monotonic() + timeout
            while not event.wait(0.2):
                if self._abort.is_set():
                    msg = f"{method}: session closed during request"
                    raise PortalError(msg)
                if time.monotonic() >= deadline:
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
