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
    _ensure_gst,
    build_pipeline,
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
    bus: Any, context: Any, request_path: str, results: dict[str, Any]
) -> None:
    """Iterate ``context`` until the portal Response signal fires.

    The D-Bus connection lives on a thread-default context owned by the
    worker thread; pumping it here is what delivers the signal (a blocking
    Event.wait would deadlock it).
    """
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
        deadline = time.monotonic() + PORTAL_TIMEOUT_MS / 1000
        while not done.is_set() and time.monotonic() < deadline:
            context.iteration(may_block=True)
        if not done.is_set():
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

    The whole flow runs on a worker thread with its own thread-default
    GLib context — a GUI event loop (Qt's GLib integration) owns the
    default main context, so portal responses would never be delivered
    there. See mutter_screencast_session for the full rationale.
    """
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    result: dict[str, Any] = {}

    def _run() -> None:
        context = GLib.MainContext.new()
        context.push_thread_default()
        try:
            bus = _session_bus()
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
            _portal_wait_response(bus, context, request_path, results)
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
            _portal_wait_response(bus, context, request_path, results)

            request_path = _portal_call(
                bus,
                "Start",
                GLib.Variant(
                    "(osa{sv})",
                    (session_path, "", {"handle_token": GLib.Variant("s", token)}),
                ),
                "(o)",
            )
            _portal_wait_response(bus, context, request_path, results)
            streams = results["response"][1]["streams"]
            if not streams:
                raise CaptureError("portal returned no streams")
            result["node"] = int(streams[0][0])
            _keep_alive(bus, context)  # never finalize the connection
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller thread
            result["error"] = exc
        finally:
            context.pop_thread_default()

    _run_isolated(_run, result)
    return int(result["node"])


def _run_isolated(_run: Callable[[], None], result: dict[str, Any]) -> None:
    """Run a D-Bus flow on a worker thread; re-raise its error here.

    ``_run`` must run the whole flow on its own thread-default GLib
    context. Raises on timeout or when the flow stored an ``error``.
    """
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(PORTAL_TIMEOUT_MS / 1000 + 1)
    if "error" in result:
        raise result["error"]
    if "node" not in result:
        raise CaptureError("screencast session did not deliver a node id in time")


_KEEPALIVE: tuple[Any, ...] | None = None


def _keep_alive(*objects: Any) -> None:
    """Keep D-Bus/GLib objects referenced for the process lifetime.

    Mutter watches our D-Bus client: once PyGObject finalizes the
    connection object the client looks gone and Mutter closes the session
    (the stream node then vanishes from the PipeWire registry and
    pipewiresrc fails with "target not found").
    """
    global _KEEPALIVE
    _KEEPALIVE = objects


def mutter_screencast_session() -> int:
    """Create a Mutter ScreenCast stream; return the pipewire node id.

    No dialog, but only works for callers inside the active GNOME session.

    The whole flow runs on a worker thread with its own thread-default
    GLib context. A GUI event loop (Qt with GLib integration enabled owns
    the *default* main context while running) blocks any background
    GLib.MainLoop on the default context from ever acquiring it — D-Bus
    signals are then never delivered and the node id times out. A
    thread-default context is owned exclusively by this worker thread, so
    pumping it with ``context.iteration()`` always delivers the signal,
    GUI or no GUI.
    """
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    result: dict[str, Any] = {}

    def _run() -> None:
        context = GLib.MainContext.new()
        context.push_thread_default()
        try:
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
                    raise CaptureError(
                        f"Mutter ScreenCast {method} failed: {exc}"
                    ) from exc

            # NOTE: this flow is deliberately written exactly like the
            # proven-working manual sequence — no-arg calls carry no body
            # (args=None), the signal is subscribed BEFORE Stream.Start, and
            # the node id arrives via the PipeWireStreamAdded signal (Mutter
            # ScreenCast API v4). Any reordering here broke the stream node's
            # registry export on this stack ("target not found" in
            # pipewiresrc).
            session_path = _call(
                MUTTER_IFACE_ROOT, MUTTER_PATH, "CreateSession",
                GLib.Variant("(a{sv})", ({},)), "(o)",
            ).unpack()[0]
            # Order matters: enable the session FIRST. Streams recorded
            # afterwards are started explicitly via Stream.Start. Streams
            # recorded before Session.Start are auto-started without exposing
            # the node ("Stream already started").
            _call(f"{MUTTER_IFACE_ROOT}.Session", session_path, "Start")
            # connector "" = let Mutter pick the primary monitor
            stream_path = _call(
                f"{MUTTER_IFACE_ROOT}.Session", session_path, "RecordMonitor",
                GLib.Variant("(sa{sv})", ("", {})), "(o)",
            ).unpack()[0]

            done = threading.Event()
            node: dict[str, int] = {}

            def _on_pipewire_stream_added(
                _conn: Any, _sender: Any, _path: Any, _iface: Any,
                _signal: Any, params: Any,
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
            try:
                _call(f"{MUTTER_IFACE_ROOT}.Stream", stream_path, "Start")
                deadline = time.monotonic() + PORTAL_TIMEOUT_MS / 1000
                while not done.is_set() and time.monotonic() < deadline:
                    context.iteration(may_block=True)
            finally:
                bus.signal_unsubscribe(sub)
            if not done.is_set():
                raise CaptureError(
                    "Mutter did not deliver the pipewire node id in time"
                )
            result["node"] = node["id"]
            _keep_alive(bus, context)  # never finalize the connection
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller thread
            result["error"] = exc
        finally:
            context.pop_thread_default()

    _run_isolated(_run, result)
    return int(result["node"])


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
        # GStreamer init order relative to the D-Bus flow is NOT the cause of
        # "target not found"; keep the real invariant: the D-Bus connection
        # object must stay referenced after mutter_screencast_session returns
        # (PyGObject finalizes it otherwise, Mutter kills the session, and the
        # pipewire node vanishes from the registry). See _keep_loop_alive.
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
