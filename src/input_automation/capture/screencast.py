"""Screen capture backends that talk to the Wayland compositor.

Two session factories produce a PipeWire node id of the monitor:

- ``mutter_screencast_session``: GNOME's org.gnome.Mutter.ScreenCast — no
  permission dialog, works unattended (same-session callers only). The
  pragmatic default for the daemon on GNOME.
- ``portal_screencast_session``: org.freedesktop.portal.ScreenCast — shows the
  GNOME share dialog (first grant needs one interactive accept; supports
  ``restore_token`` later). The user-facing/GUI path.

:class:`ScreencastBackend` glues a session factory to a GStreamer pipeline
(``pipewiresrc``) and yields BGR frames.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from input_automation.capture.base import CaptureError, Frame
from input_automation.capture.gstsource import (
    GStreamerFrameSource,
    build_pipeline,
    _ensure_gst,
)

__all__ = [
    "CaptureError",
    "ScreencastBackend",
    "mutter_screencast_session",
    "portal_screencast_session",
]


SessionFactory = Callable[[], int]
PipelineFactory = Callable[[int], GStreamerFrameSource]

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.ScreenCast"
MUTTER_BUS = "org.gnome.Mutter.ScreenCast"
MUTTER_PATH = "/org/gnome/Mutter/ScreenCast"
MUTTER_IFACE_ROOT = "org.gnome.Mutter.ScreenCast"
PORTAL_TIMEOUT_MS = 30_000


def _session_bus() -> Any:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception as exc:  # noqa: BLE001 - any D-Bus failure is a CaptureError
        raise CaptureError(
            f"no session D-Bus available ({exc}); a running GNOME session is required"
        ) from exc


def _portal_wait_response(
    bus: Any, loop: Any, request_path: str, results: dict[str, Any]
) -> None:
    """Block (in the caller thread) until the portal Response signal fires."""
    import threading

    done = threading.Event()

    def _on_response(
        _conn: Any, _sender: Any, _path: Any, _iface: Any, _signal: Any, params: Any
    ) -> None:
        results["response"] = params.unpack()
        done.set()

    sub = bus.signal_subscribe(
        PORTAL_BUS,
        "org.freedesktop.portal.Request",
        "Response",
        request_path,
        None,
        0,
        _on_response,
    )
    try:
        if not done.wait(timeout=PORTAL_TIMEOUT_MS / 1000):
            raise CaptureError("portal request timed out")
    finally:
        bus.signal_unsubscribe(sub)
    code, response = results["response"]
    if code != 0:
        raise CaptureError(f"portal request denied (code {code})")


def _portal_call(bus: Any, method: str, args: Any, out_type: str) -> str:
    reply = bus.call_sync(
        PORTAL_BUS,
        PORTAL_PATH,
        PORTAL_IFACE,
        method,
        args,
        _gvariant_type(out_type),
        0,
        PORTAL_TIMEOUT_MS,
        None,
    )
    return str(reply.unpack()[0])


def _gvariant_type(type_string: str) -> Any:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    return GLib.VariantType(type_string)


def portal_screencast_session() -> int:
    """Run the xdg-desktop-portal ScreenCast flow; return the node id.

    Shows the GNOME share dialog on the first grant.
    """
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    bus = _session_bus()
    loop = GLib.MainLoop()
    import threading

    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    try:
        token = "inputautomation"
        request_path = _portal_call(
            bus,
            "CreateSession",
            GLib.Variant(
                "(a{sv})",
                (
                    {
                        "session_handle_token": GLib.Variant("s", token),
                        "handle_token": GLib.Variant("s", token),
                    },
                ),
            ),
            "(o)",
        )
        results: dict[str, Any] = {}
        _portal_wait_response(bus, loop, request_path, results)
        session_path = results["response"][1]["session_handle"]

        request_path = _portal_call(
            bus,
            "SelectSources",
            GLib.Variant(
                "(oa{sv})",
                (
                    session_path,
                    {
                        "types": GLib.Variant("u", 1),  # MONITOR
                        "multiple": GLib.Variant("b", False),
                        "handle_token": GLib.Variant("s", token),
                    },
                ),
            ),
            "(o)",
        )
        _portal_wait_response(bus, loop, request_path, results)

        request_path = _portal_call(
            bus,
            "Start",
            GLib.Variant(
                "(osa{sv})",
                (session_path, "", {"handle_token": GLib.Variant("s", token)}),
            ),
            "(o)",
        )
        _portal_wait_response(bus, loop, request_path, results)
        streams = results["response"][1]["streams"]
        if not streams:
            raise CaptureError("portal returned no streams")
        return int(streams[0][0])
    finally:
        loop.quit()


def mutter_screencast_session() -> int:
    """Create a Mutter ScreenCast stream; return the pipewire node id.

    No dialog, but only works for callers inside the active GNOME session.
    """
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    bus = _session_bus()

    def _call(
        iface: str,
        path: str,
        method: str,
        args: Any = None,
        out_type: str | None = None,
    ) -> Any:
        try:
            return bus.call_sync(
                MUTTER_BUS,
                path,
                iface,
                method,
                args,
                _gvariant_type(out_type) if out_type is not None else None,
                0,
                PORTAL_TIMEOUT_MS,
                None,
            )
        except Exception as exc:  # noqa: BLE001
            raise CaptureError(f"Mutter ScreenCast {method} failed: {exc}") from exc

    # NOTE: this flow is deliberately written exactly like the proven-working
    # manual sequence — no-arg calls carry no body (args=None), the signal is
    # subscribed BEFORE the main loop thread starts, and the node id arrives
    # via the PipeWireStreamAdded signal (Mutter ScreenCast API v4). Any
    # reordering here broke the stream node's registry export on this stack
    # ("target not found" in pipewiresrc).
    session_path = _call(
        MUTTER_IFACE_ROOT, MUTTER_PATH, "CreateSession",
        GLib.Variant("(a{sv})", ({},)), "(o)",
    ).unpack()[0]
    # Order matters: enable the session FIRST. Streams recorded afterwards are
    # started explicitly via Stream.Start. Streams recorded before Session.Start
    # are auto-started without exposing the node ("Stream already started").
    _call(f"{MUTTER_IFACE_ROOT}.Session", session_path, "Start")
    # connector "" = let Mutter pick the primary monitor
    stream_path = _call(
        f"{MUTTER_IFACE_ROOT}.Session", session_path, "RecordMonitor",
        GLib.Variant("(sa{sv})", ("", {})), "(o)",
    ).unpack()[0]

    done = threading.Event()
    node: dict[str, int] = {}

    def _on_pipewire_stream_added(
        _conn: Any, _sender: Any, _path: Any, _iface: Any, _signal: Any, params: Any
    ) -> None:
        node["id"] = int(params.unpack()[0])
        done.set()

    sub = bus.signal_subscribe(
        MUTTER_BUS,
        f"{MUTTER_IFACE_ROOT}.Stream",
        "PipeWireStreamAdded",
        stream_path,
        None,
        0,
        _on_pipewire_stream_added,
    )
    loop = GLib.MainLoop()
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    try:
        _call(f"{MUTTER_IFACE_ROOT}.Stream", stream_path, "Start")
        if not done.wait(timeout=PORTAL_TIMEOUT_MS / 1000):
            raise CaptureError("Mutter did not deliver the pipewire node id in time")
    finally:
        bus.signal_unsubscribe(sub)
        # NOTE: do NOT quit the main loop here. Mutter watches our D-Bus
        # client; once no GLib main context services the connection, the
        # client looks gone and Mutter closes the session (~2ms later the
        # stream node disappears from the PipeWire registry → pipewiresrc
        # fails with "target not found"). The loop runs for the process
        # lifetime as a daemon thread; clean shutdown happens on exit.
        # The bus connection object MUST be kept referenced too: 'bus' is
        # local, and once this function returns PyGObject finalizes the
        # connection even with the loop running — same visible failure.
        _keep_loop_alive(loop, thread, bus)
    return node["id"]


_MAINLOOP: "tuple[Any, Any, Any] | None" = None


def _keep_loop_alive(loop: Any, thread: Any, bus: Any) -> None:
    """Keep one GLib main loop + D-Bus connection alive for the session."""
    global _MAINLOOP
    _MAINLOOP = (loop, thread, bus)


def _default_pipeline_factory(node_id: int) -> GStreamerFrameSource:
    description = (
        f"pipewiresrc path={node_id} ! videoconvert ! "
        "video/x-raw,format=BGR ! "
        "appsink name=sink max-buffers=4 drop=true"
    )
    return GStreamerFrameSource(build_pipeline(description))


class ScreencastBackend:
    """Continuous monitor capture via a compositor screencast session.

    ``backend="mutter"`` (unattended, no dialog) or ``backend="portal"``
    (user-facing dialog). Frames arrive as BGR numpy arrays.
    """

    def __init__(
        self,
        *,
        backend: str = "mutter",
        session_factory: SessionFactory | None = None,
        pipeline_factory: PipelineFactory | None = None,
    ) -> None:
        factories = {"mutter": mutter_screencast_session, "portal": portal_screencast_session}
        if backend not in factories:
            raise ValueError(f"unknown screencast backend {backend!r}")
        self._session_factory = session_factory or factories[backend]
        self._pipeline_factory = pipeline_factory or _default_pipeline_factory
        self._source: GStreamerFrameSource | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            raise RuntimeError("capture backend already running")
        # libpipewire must be initialized BEFORE the compositor session flow:
        # when GStreamer initializes only afterwards (inside the pipeline
        # build), the mutter stream node never becomes visible to pipewiresrc
        # and every connect fails with "target not found". Empirically
        # established on this stack; keep this ordering.
        _ensure_gst()
        node_id = self._session_factory()
        self._source = self._pipeline_factory(node_id)
        self._source.start()
        self._running = True

    def stop(self) -> None:
        self._running = False
        if self._source is not None:
            self._source.stop()
            self._source = None

    def frames(self) -> Iterator[Frame]:
        if self._source is None or not self._running:
            raise RuntimeError("capture backend not started")
        while self._running:
            yield self._source.read_frame()

    def grab(self, timeout: float = 10.0) -> Frame:
        """One frame; starts and stops the capture around it."""
        self.start()
        try:
            return self._source.read_frame(timeout=timeout)  # type: ignore[union-attr]
        finally:
            self.stop()

    def __enter__(self) -> ScreencastBackend:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()
