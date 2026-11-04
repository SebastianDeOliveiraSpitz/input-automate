"""Real-time input event capture from evdev devices.

Normalized event model (``InputEvent``) plus a thread-per-device listener.
Headless-testable via the same injectable seams as discovery (an
``open_device_fn`` that can return fake devices).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from evdev import ecodes

from input_automation.devices.discovery import (
    INPUT_DIR,
    DeviceLike,
    ErrorCallback,
    list_devices,
    open_device,
)

logger = logging.getLogger(__name__)

__all__ = ["DeviceListener", "InputEvent"]

_TYPE_NAMES = {
    ecodes.EV_SYN: "syn",
    ecodes.EV_KEY: "key",
    ecodes.EV_REL: "rel",
    ecodes.EV_ABS: "abs",
}

_KEY_ACTIONS = {0: "release", 1: "press", 2: "repeat"}
_REL_MOTION = {ecodes.REL_X, ecodes.REL_Y}
_REL_WHEEL = {
    ecodes.REL_WHEEL,
    ecodes.REL_HWHEEL,
    ecodes.REL_WHEEL_HI_RES,
    ecodes.REL_HWHEEL_HI_RES,
}


@dataclass(frozen=True)
class InputEvent:
    """One normalized input event, safe to display, queue or serialize."""

    ts: float
    path: str
    etype: str
    code: int
    code_name: str
    value: int
    action: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "path": self.path,
            "type": self.etype,
            "code": self.code,
            "code_name": self.code_name,
            "value": self.value,
            "action": self.action,
        }

    def describe(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.ts))
        stamp += f".{int(self.ts * 1000) % 1000:03d}"
        return f"{stamp}  {self.etype}:{self.code_name:<16} {self.action:<7} {self.path}"


class DeviceReader(Protocol):
    """The surface of evdev.InputDevice the listener needs."""

    name: str

    def read_loop(self) -> Iterator[object]: ...

    def close(self) -> None: ...


ReaderOpener = Callable[[Path], DeviceReader]


def _action_for(etype: int, code: int, value: int) -> str:
    if etype == ecodes.EV_KEY:
        return _KEY_ACTIONS.get(value, f"value{value}")
    if etype == ecodes.EV_REL:
        if code in _REL_MOTION:
            return "motion"
        if code in _REL_WHEEL:
            return "wheel"
        return "rel"
    if etype == ecodes.EV_ABS:
        return "change"
    return "raw"


def _normalize(item: object, path: str) -> InputEvent | None:
    if isinstance(item, tuple):
        etype, code, value = item
        ts = time.time()
    else:
        etype, code, value = item.type, item.code, item.value  # type: ignore[attr-defined]
        ts = float(item.timestamp())  # type: ignore[attr-defined]
    if etype == ecodes.EV_SYN:
        return None
    names = cast(dict[int, str], ecodes.bytype.get(etype, {}))
    return InputEvent(
        ts=ts,
        path=path,
        etype=_TYPE_NAMES.get(etype, f"type{etype}"),
        code=code,
        code_name=names.get(code, f"code{code}"),
        value=value,
        action=_action_for(etype, code, value),
    )


class DeviceListener:
    """Capture input events from one or more evdev devices.

    One thread per device pushes normalized ``InputEvent`` objects into an
    internal queue; consumers either pass a ``callback`` or iterate over the
    listener itself (until ``stop()``). With ``paths=None`` all discoverable
    devices under ``directory`` are watched.
    """

    def __init__(
        self,
        paths: list[str] | None = None,
        *,
        callback: Callable[[InputEvent], None] | None = None,
        open_device_fn: ReaderOpener | None = None,
        directory: Path = INPUT_DIR,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self._paths = list(paths) if paths is not None else None
        self._callback = callback
        self._directory = directory
        self._on_error = on_error
        if open_device_fn is not None:
            self._opener: ReaderOpener = open_device_fn
        else:
            # open_device returns DeviceLike; at runtime every real device
            # also has read_loop/close, which the listener needs.
            self._opener = cast(ReaderOpener, open_device)
        self._queue: queue.Queue[InputEvent | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._devices: list[DeviceReader] = []
        self._stopping = threading.Event()
        self._running = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._running:
            raise RuntimeError("listener already running")
        for path in self._resolve_paths():
            thread = threading.Thread(
                target=self._run_device, args=(path,), name=f"listener-{path}", daemon=True
            )
            self._threads.append(thread)
            thread.start()
        self._running = True

    def stop(self) -> None:
        self._stopping.set()
        for device in list(self._devices):
            with contextlib.suppress(OSError):
                device.close()
        # Don't join reader threads: they may be blocked in read() on an
        # evdev fd that close() doesn't always unblock from another thread.
        # They are daemon threads and exit when the process exits.
        self._threads.clear()
        self._devices.clear()
        self._running = False
        with contextlib.suppress(Exception):
            self._queue.put_nowait(None)

    def __enter__(self) -> DeviceListener:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def __iter__(self) -> Iterator[InputEvent]:
        while True:
            event = self._queue.get()
            if event is None:
                return
            yield event

    # -- internals ---------------------------------------------------------

    def _resolve_paths(self) -> list[str]:
        if self._paths is not None:
            return self._paths
        devices = list_devices(
            directory=self._directory,
            open_device_fn=cast(Callable[[Path], DeviceLike], self._opener),
            on_error=self._on_error,
        )
        return [info.path for info in devices]

    def _run_device(self, path: str) -> None:
        try:
            device = self._opener(Path(path))
        except OSError as exc:
            logger.warning("could not open %s: %s", path, exc)
            if self._on_error is not None:
                self._on_error(Path(path), exc)
            return
        self._devices.append(device)
        logger.debug("listening on %s (%s)", path, device.name)
        try:
            for item in device.read_loop():
                if self._stopping.is_set():
                    break
                event = _normalize(item, path)
                if event is None:
                    continue
                if self._callback is not None:
                    self._callback(event)
                self._queue.put(event)
        except OSError:
            if not self._stopping.is_set():
                logger.warning("device %s closed unexpectedly", path)
        finally:
            with contextlib.suppress(OSError):
                device.close()
