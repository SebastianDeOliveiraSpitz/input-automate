"""Enumerate input devices under /dev/input via evdev.

Headless-testable: everything that touches the filesystem or evdev goes
through small injectable seams (``open_device``, the directory listing).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from evdev import InputDevice, ecodes

__all__ = ["DeviceKind", "DeviceInfo", "list_devices"]

INPUT_DIR = Path("/dev/input")


class DeviceKind:
    """Classification labels for input devices."""

    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    TOUCHPAD = "touchpad"
    GAMEPAD = "gamepad"
    OTHER = "other"


@dataclass(frozen=True)
class DeviceInfo:
    """Facts about one input device, safe to serialize."""

    path: str
    name: str
    phys: str
    kinds: frozenset[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "phys": self.phys,
            "kinds": sorted(self.kinds),
        }


class DeviceLike(Protocol):
    """The surface of evdev.InputDevice that discovery needs."""

    name: str
    phys: str

    def capabilities(self, verbose: bool = False) -> dict[int, list[int]]: ...


GamepadButtons = frozenset[int]


def _gamepad_buttons() -> GamepadButtons:
    # evdev.ecodes is flat: BTN_SOUTH etc. live directly on the module
    names = (
        "BTN_SOUTH",
        "BTN_EAST",
        "BTN_C",
        "BTN_NORTH",
        "BTN_WEST",
        "BTN_Z",
        "BTN_TL",
        "BTN_TR",
        "BTN_TL2",
        "BTN_TR2",
        "BTN_SELECT",
        "BTN_START",
        "BTN_MODE",
        "BTN_THUMBL",
        "BTN_THUMBR",
    )
    return frozenset(getattr(ecodes, name) for name in names if hasattr(ecodes, name))


def classify_device(caps: dict[int, list[int]]) -> frozenset[str]:
    """Map evdev capabilities to device kinds."""
    keys = set(caps.get(ecodes.EV_KEY, []))
    has_rel = ecodes.EV_REL in caps
    has_abs = ecodes.EV_ABS in caps
    kinds: set[str] = set()

    letters = {c for c in keys if 0x01 <= c <= 0xFF}  # KEY_ESC..KEY_MICMUTE band
    if letters:
        kinds.add(DeviceKind.KEYBOARD)
    mouse_buttons = {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE}
    if has_rel and keys & mouse_buttons:
        kinds.add(DeviceKind.MOUSE)
    if has_abs and ecodes.BTN_TOUCH in keys:
        kinds.add(DeviceKind.TOUCHPAD)
    if keys & _gamepad_buttons():
        kinds.add(DeviceKind.GAMEPAD)
    if not kinds:
        kinds.add(DeviceKind.OTHER)
    return frozenset(kinds)


def device_info(path: Path, device: DeviceLike) -> DeviceInfo:
    caps = {code: list(vals) for code, vals in device.capabilities().items()}
    return DeviceInfo(
        path=str(path),
        name=device.name or "<unnamed>",
        phys=device.phys or "",
        kinds=classify_device(caps),
    )


def open_device(path: Path) -> DeviceLike:
    # cast: InputDevice.capabilities() has overloads the Protocol narrows to
    # the no-arg call we make; runtime shape is identical.
    return cast(DeviceLike, InputDevice(str(path)))


DeviceOpener = Callable[[Path], DeviceLike]
ErrorCallback = Callable[[Path, Exception], None]


def list_devices(
    directory: Path = INPUT_DIR,
    open_device_fn: DeviceOpener | None = None,
    on_error: ErrorCallback | None = None,
) -> list[DeviceInfo]:
    """List all evdev devices in ``directory``, skipping unreadable ones.

    ``on_error`` (if given) is called for every path that could not be opened,
    e.g. to warn about missing ``input`` group permissions.
    """
    opener = open_device_fn if open_device_fn is not None else open_device
    infos: list[DeviceInfo] = []
    for path in sorted(directory.glob("event*")):
        try:
            device = opener(path)
            infos.append(device_info(path, device))
        except (OSError, PermissionError) as exc:
            if on_error is not None:
                on_error(path, exc)
    return infos
