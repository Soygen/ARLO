"""xdg-desktop-portal ScreenCast session (D-Bus via Gio).

Handshake: CreateSession -> SelectSources -> Start -> OpenPipeWireRemote.
Request-style methods deliver results via a Response signal on a request
object whose path is predictable from our unique bus name + handle_token.

The GLib main loop runs on a **private** MainContext with a **dedicated**
D-Bus connection (not ``Gio.bus_get``'s shared singleton). Qt owns the
default main context; sharing either the context or the bus connection
caused QTimer affinity errors, quit crashes, or CreateSession timeouts
(Response signals never reaching our loop).
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio  # pyright: ignore[reportAttributeAccessIssue]  # GI stubs incomplete  # ruff: ignore[module-import-not-at-top-of-file]
from gi.repository import GLib  # pyright: ignore[reportAttributeAccessIssue]  # GI stubs incomplete  # ruff: ignore[module-import-not-at-top-of-file]

from arc_helper.config import logger  # ruff: ignore[module-import-not-at-top-of-file]

if TYPE_CHECKING:
    from collections.abc import Callable

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

    def __init__(self, restore_token: str | None = None) -> None:
        self.restore_token = restore_token
        self.new_restore_token: str | None = None
        self.node_id: int | None = None
        self.stream_props: dict = {}
        self.pipewire_fd: int = -1  # dup'd from the portal; ours to close
        self.closed = threading.Event()  # compositor/user ended the cast

        # Private context so we never iterate GLib's default main context
        # (Qt owns that on Linux).
        self._context = GLib.MainContext()
        self._loop = GLib.MainLoop.new(self._context, False)  # ruff: ignore[boolean-positional-value-in-call]
        self._bus: Gio.DBusConnection | None = None
        self._bus_ready = threading.Event()
        self._bus_error: BaseException | None = None
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
            target=self._run_loop, name="arlo-glib-loop", daemon=True
        )
        self._loop_thread.start()
        if not self._bus_ready.wait(timeout=5.0):
            self.close()
            msg = "Portal GLib loop failed to start"
            raise PortalError(msg)
        if self._bus_error is not None:
            err = self._bus_error
            self.close()
            msg = (
                "Cannot reach the D-Bus session bus. "
                "ARLO's Wayland capture requires a desktop session."
            )
            raise PortalError(msg) from err
        assert self._bus is not None

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
            "multiple": GLib.Variant("b", False),  # ruff: ignore[boolean-positional-value-in-call]
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

    def _run_loop(self) -> None:
        """Iterate the private context; open a dedicated bus on this thread."""
        self._context.push_thread_default()
        try:
            try:
                # Dedicated connection so Response/Closed signals dispatch on
                # *this* context. Gio.bus_get_sync() returns a process-wide
                # singleton tied to whichever context first claimed it (often
                # the default/Qt one) — signals would never reach this loop.
                address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
                self._bus = Gio.DBusConnection.new_for_address_sync(
                    address,
                    (
                        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
                    ),
                    None,
                    None,
                )
            except BaseException as e:  # ruff: ignore[blind-except] - propagate to open()
                self._bus_error = e
                self._bus_ready.set()
                return
            self._bus_ready.set()
            self._loop.run()
        finally:
            self._context.pop_thread_default()

    def _call_on_loop(
        self, func: Callable[[], object], *, timeout: float = 5.0
    ) -> object:
        """Run ``func`` on the private-context thread and wait for its return.

        Required for ``signal_subscribe``: GIO attaches the callback to the
        thread-default context of the *subscribing* thread. Scheduling the
        subscribe via an idle on our private context ties signals to the loop
        that actually iterates it.
        """
        done = threading.Event()
        box: dict = {}

        def idle() -> bool:
            try:
                box["value"] = func()
            except BaseException as e:  # ruff: ignore[blind-except]
                box["error"] = e
            done.set()
            return GLib.SOURCE_REMOVE

        source = GLib.Idle()
        source.set_callback(idle)
        source.attach(self._context)
        if not done.wait(timeout):
            msg = "Portal GLib loop did not run scheduled work"
            raise PortalError(msg)
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def close(self) -> None:
        self._abort.set()
        # Drop subscription id without round-tripping the loop — close_sync
        # tears the connection down, and _call_on_loop can hang quit.
        self._closed_sub = None
        if self._session_handle is not None and self._bus is not None:
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
            with suppress(OSError):
                os.close(self.pipewire_fd)
            self.pipewire_fd = -1

        loop_thread = self._loop_thread
        # g_main_loop_quit is thread-safe; wakes the private-context loop.
        if self._loop.is_running():
            self._loop.quit()
        if loop_thread is not None and loop_thread is not threading.current_thread():
            loop_thread.join(timeout=2.0)
        self._loop_thread = None
        if self._bus is not None:
            try:
                self._bus.close_sync(None)
            except GLib.Error as e:
                logger.debug(f"Portal bus close failed: {e}")
            self._bus = None

    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        build_args: Callable[[str], GLib.Variant],
        timeout: float = RESPONSE_TIMEOUT_S,
    ) -> dict:
        """Call a request-style portal method, wait for its Response signal."""
        assert self._bus is not None
        self._counter += 1
        token = f"{self._app_token}_{self._counter}"
        sender = self._bus.get_unique_name().removeprefix(":").replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        event = threading.Event()
        outcome: dict = {}

        def on_response(
            _conn: object,
            _sender: object,
            _path: object,
            _iface: object,
            _signal: object,
            params: object,
        ) -> None:
            unpack = getattr(params, "unpack", None)
            if unpack is None:
                return
            code, results = unpack()
            outcome["code"] = code
            outcome["results"] = results
            event.set()

        bus = self._bus

        def subscribe() -> int:
            return bus.signal_subscribe(
                PORTAL_BUS,
                REQUEST_IFACE,
                "Response",
                request_path,
                None,
                Gio.DBusSignalFlags.NONE,
                on_response,
            )

        sub = self._call_on_loop(subscribe)
        assert isinstance(sub, int)
        try:
            try:
                bus.call_sync(
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
            with suppress(Exception):
                self._call_on_loop(lambda: bus.signal_unsubscribe(sub))

        code = outcome["code"]
        if code == 1:
            msg = f"{method}: cancelled in the screen-share dialog"
            raise PortalError(msg)
        if code != 0:
            msg = f"{method}: portal request failed (response code {code})"
            raise PortalError(msg)
        return outcome["results"]

    def _subscribe_closed(self) -> None:
        assert self._bus is not None
        bus = self._bus

        def on_closed(
            _conn: object,
            _sender: object,
            _path: object,
            _iface: object,
            _signal: object,
            _params: object,
        ) -> None:
            logger.warning("ScreenCast session closed by the compositor/user")
            self.closed.set()

        def subscribe() -> int:
            return bus.signal_subscribe(
                PORTAL_BUS,
                SESSION_IFACE,
                "Closed",
                self._session_handle,
                None,
                Gio.DBusSignalFlags.NONE,
                on_closed,
            )

        sub = self._call_on_loop(subscribe)
        assert isinstance(sub, int)
        self._closed_sub = sub
