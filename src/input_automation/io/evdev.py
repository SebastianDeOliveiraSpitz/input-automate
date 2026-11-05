"""evdev input provider: polls device state into engine ``INPUT`` blocks (T7).

Pass an instance as ``read_inputs`` to
:class:`input_automation.engine.ScanExecutor`; each cycle it polls the devices
referenced by the graph's ``INPUT`` blocks and writes their ``Q``/``VALUE``
output pins.

Never blocks: key/button state comes from EVIOCGKEY
(:meth:`evdev.InputDevice.active_keys`) and axis values from EVIOCGABS
(:meth:`evdev.InputDevice.absinfo`). A device that cannot be opened or a code
that cannot be resolved yields ``Q=False, VALUE=0`` plus a debug log line — a
misbehaving device must never abort a scan cycle.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from evdev import ecodes

from input_automation.devices.discovery import open_device
from input_automation.engine.block import Block
from input_automation.engine.wires import WireGraph

logger = logging.getLogger(__name__)

__all__ = ["EvdevInputProvider", "resolve_evdev_code"]


class _AbsInfo(Protocol):
    value: int


class DeviceReader(Protocol):
    """The evdev device surface the provider needs: state queries only."""

    def active_keys(self) -> set[int]: ...

    def absinfo(self, axis: int) -> _AbsInfo: ...

    def capabilities(self, verbose: bool = False) -> dict[int, list[int]]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class DeviceState:
    device: DeviceReader
    key_codes: frozenset[int]
    abs_codes: frozenset[int]


def resolve_evdev_code(name: object) -> int | None:
    """Resolve an evdev code name to its integer code.

    Accepts canonical names (``KEY_Q``, ``BTN_LEFT``, ``ABS_X``) and short
    key forms (``"q"`` -> ``KEY_Q``). Return ``None`` for anything unknown.
    """
    if not isinstance(name, str) or not name:
        return None
    code = getattr(ecodes, name, None)
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    if len(name) == 1 and name.isalpha():
        code = getattr(ecodes, f"KEY_{name.upper()}", None)
        if isinstance(code, int) and not isinstance(code, bool):
            return code
    return None


class EvdevInputProvider:
    """Poll evdev devices and drive ``INPUT`` block output pins.

    Called once per scan cycle as ``read_inputs(graph)``. Devices are opened
    lazily and kept open for the provider's lifetime.
    """

    def __init__(
        self,
        *,
        open_device_fn: Callable[[str], DeviceReader] | None = None,
    ) -> None:
        self._opener = open_device_fn or (
            lambda path: cast(DeviceReader, open_device(Path(path)))
        )
        self._devices: dict[str, DeviceState] = {}

    def close(self) -> None:
        """Close every cached device fd."""
        for state in self._devices.values():
            with contextlib.suppress(OSError):
                state.device.close()
        self._devices.clear()

    # -- read_inputs seam -------------------------------------------------

    def __call__(self, graph: WireGraph) -> None:
        """Poll every wanted (device, code) and write the matching blocks."""
        by_device: dict[str, list[tuple[Block, int]]] = {}
        for block_id in graph.blocks():
            block = graph.block(block_id)
            if getattr(block, "type_name", None) != "INPUT":
                continue
            path = str(block.input("DEVICE").value or "")
            code = resolve_evdev_code(block.input("CODE").value)
            if not path or code is None:
                self._write(block, False, 0)
                continue
            by_device.setdefault(path, []).append((block, code))

        for path, entries in by_device.items():
            state = self._device_state(path)
            if state is None:
                for block, _code in entries:
                    self._write(block, False, 0)
                continue
            pressed = set(state.device.active_keys()) & state.key_codes
            for block, code in entries:
                if code in state.abs_codes:
                    try:
                        value = int(state.device.absinfo(code).value)
                    except OSError as exc:
                        logger.debug("absinfo %s/%s failed: %s", path, code, exc)
                        value = 0
                    self._write(block, value != 0, value)
                else:
                    down = code in pressed
                    self._write(block, down, 1 if down else 0)

    # -- internals --------------------------------------------------------

    def _device_state(self, path: str) -> DeviceState | None:
        """Open and cache ``path``; return ``None`` if it cannot be opened."""
        state = self._devices.get(path)
        if state is not None:
            return state
        try:
            device = self._opener(path)
            caps = device.capabilities()
        except OSError as exc:
            logger.debug("cannot open evdev device %s: %s", path, exc)
            return None
        state = DeviceState(
            device=device,
            key_codes=frozenset(caps.get(ecodes.EV_KEY, [])),
            abs_codes=frozenset(caps.get(ecodes.EV_ABS, [])),
        )
        self._devices[path] = state
        return state

    @staticmethod
    def _write(block: Block, pressed: bool, value: int) -> None:
        block.output("Q").set(bool(pressed))
        block.output("VALUE").set(int(value))
