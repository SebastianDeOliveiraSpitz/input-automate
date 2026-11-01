"""Virtual output device: injects mouse/keyboard events via uinput.

Games and applications receive these as real hardware input. Headless-testable:
pass a fake ``backend`` implementing the small ``UinputBackend`` protocol.

Permissions: /dev/uinput must be writable (see install/system-setup.sh).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from evdev import ecodes

logger = logging.getLogger(__name__)

__all__ = ["VirtualDevice", "uinput_available"]

UINPUT_PATH = Path("/dev/uinput")
UINPUT_MODULE_NAME = "input-automation-virtual"

ButtonType = str  # "left" | "right" | "middle"


class UinputBackend(Protocol):
    """Surface of uinput.Device that VirtualDevice needs."""

    def emit(self, event: tuple[int, int], value: int, syn: bool = True) -> None: ...


class RecordingBackend:
    """Records every emit() instead of sending events to the kernel.

    Used for ``--dry-run`` and tests; also handy to inspect what a macro
    would send without touching real input.
    """

    def __init__(self) -> None:
        self.events: list[tuple[tuple[int, int], int, bool]] = []

    def emit(self, event: tuple[int, int], value: int, syn: bool = True) -> None:
        self.events.append((event, value, syn))

    def describe(self) -> str:
        names = cast(dict[int, dict[int, str]], ecodes.bytype)
        parts = []
        for (etype, code), value, _syn in self.events:
            code_name = names.get(etype, {}).get(code, f"code{code}")
            parts.append(f"{code_name}={value}")
        return " ".join(parts)


BackendFactory = Callable[[list[tuple[int, int]], str], UinputBackend]


def _default_event_specs() -> list[tuple[int, int]]:
    """Event set for the virtual device: standard keys + mouse axes/buttons."""
    specs: list[tuple[int, int]] = [
        (ecodes.EV_KEY, code) for code in range(1, 249)  # KEY_ESC..KEY_MICMUTE band
    ]
    specs += [
        (ecodes.EV_REL, ecodes.REL_X),
        (ecodes.EV_REL, ecodes.REL_Y),
        (ecodes.EV_REL, ecodes.REL_WHEEL),
        (ecodes.EV_REL, ecodes.REL_HWHEEL),
        (ecodes.EV_KEY, ecodes.BTN_LEFT),
        (ecodes.EV_KEY, ecodes.BTN_RIGHT),
        (ecodes.EV_KEY, ecodes.BTN_MIDDLE),
    ]
    return specs


def _real_factory(event_specs: list[tuple[int, int]], name: str) -> UinputBackend:
    import uinput as uinput_module

    return cast(UinputBackend, uinput_module.Device(event_specs, name=name))


def uinput_available() -> tuple[bool, str]:
    """Check that /dev/uinput is writable; return (ok, hint)."""
    if not UINPUT_PATH.exists():
        return False, (
            f"{UINPUT_PATH} does not exist — load the kernel module: sudo modprobe uinput"
        )
    if not os.access(UINPUT_PATH, os.W_OK):
        return False, (
            f"no write permission on {UINPUT_PATH}. Install the udev rule "
            "(install/system-setup.sh) or add your user to the 'input' group, then relogin."
        )
    return True, ""


class VirtualDevice:
    """Emit mouse/keyboard events through a virtual uinput device."""

    def __init__(
        self,
        backend: UinputBackend | None = None,
        *,
        name: str = UINPUT_MODULE_NAME,
    ) -> None:
        self._backend = backend if backend is not None else _real_factory(
            _default_event_specs(), name
        )
        self._closed = False

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> VirtualDevice:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        destroy = getattr(self._backend, "destroy", None)
        if callable(destroy):
            destroy()

    # -- keyboard -----------------------------------------------------------

    def key_press(self, code: int) -> None:
        self._check_closed()
        self._backend.emit((ecodes.EV_KEY, code), 1)

    def key_release(self, code: int) -> None:
        self._check_closed()
        self._backend.emit((ecodes.EV_KEY, code), 0)

    def key_tap(self, code: int, times: int = 1, delay_ms: int = 10) -> None:
        """Press and release ``code`` ``times`` times."""
        for i in range(times):
            self.key_press(code)
            time.sleep(delay_ms / 1000)
            self.key_release(code)
            if i < times - 1:
                time.sleep(delay_ms / 1000)

    # -- mouse --------------------------------------------------------------

    def mouse_move(self, dx: int, dy: int) -> None:
        """Move the cursor by a relative offset."""
        self._check_closed()
        if dx:
            self._backend.emit((ecodes.EV_REL, ecodes.REL_X), dx, syn=False)
        if dy:
            self._backend.emit((ecodes.EV_REL, ecodes.REL_Y), dy)

    def mouse_click(self, button: ButtonType = "left", times: int = 1) -> None:
        codes = {
            "left": ecodes.BTN_LEFT,
            "right": ecodes.BTN_RIGHT,
            "middle": ecodes.BTN_MIDDLE,
        }
        if button not in codes:
            raise ValueError(f"unknown mouse button {button!r}")
        code = codes[button]
        for _ in range(times):
            self._backend.emit((ecodes.EV_KEY, code), 1)
            time.sleep(0.01)
            self._backend.emit((ecodes.EV_KEY, code), 0)
            if times > 1:
                time.sleep(0.05)

    def scroll(self, dy: int) -> None:
        """Scroll vertically; positive dy scrolls up."""
        self._check_closed()
        self._backend.emit((ecodes.EV_REL, ecodes.REL_WHEEL), dy)

    # -- helpers ------------------------------------------------------------

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("virtual device is closed")
