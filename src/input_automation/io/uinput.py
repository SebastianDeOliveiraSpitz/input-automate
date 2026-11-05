"""uinput output provider: write engine ``OUTPUT`` block pins to virtual
evdev devices (T8).

Pass an instance as ``write_outputs`` to
:class:`input_automation.engine.ScanExecutor`; each cycle it reads the
graph's ``OUTPUT`` blocks and emits one evdev event per changed
``(device, code)`` pair through a cached uinput device. A held state is
emitted once (transitions only), so the kernel is not flooded with repeats.

The engine never opens /dev/uinput itself: device creation goes through an
injectable opener, so tests run with a recording fake and no real device.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Protocol, cast

from evdev import ecodes

from input_automation.engine.block import Block
from input_automation.engine.wires import WireGraph
from input_automation.io.evdev import resolve_evdev_code

logger = logging.getLogger(__name__)

__all__ = ["UinputOutputProvider"]


class _DeviceWriter(Protocol):
    """The uinput device surface the provider needs: emit + close."""

    def emit(self, event: tuple[int, int], value: int, syn: bool = True) -> None: ...

    def close(self) -> None: ...


def _event_type(code_name: str) -> int:
    """Evdev event type for a code name: ``ABS_*`` -> EV_ABS, ``REL_*`` ->
    EV_REL, everything else (``KEY_*``, ``BTN_*``, short key forms) -> EV_KEY.
    """
    if code_name.startswith("ABS_"):
        return ecodes.EV_ABS
    if code_name.startswith("REL_"):
        return ecodes.EV_REL
    return ecodes.EV_KEY


def _resolve_event(code_name: str) -> tuple[int, int] | None:
    """Resolve ``code_name`` to ``(event_type, code)``; ``None`` if unknown."""
    code = resolve_evdev_code(code_name)
    if code is None:
        return None
    return _event_type(code_name), code


class UinputOutputProvider:
    """Write ``OUTPUT`` block pins to virtual uinput devices.

    Called once per scan cycle as ``write_outputs(graph)``. Each OUTPUT block
    names a virtual device (``DEVICE`` pin, e.g. ``"out-keyboard"``) and an
    evdev code (``CODE`` pin). The emitted value is ``1`` while ``Q`` is true
    and ``0`` while false; for absolute axes the raw ``VALUE`` pin is emitted
    while ``Q`` is true and ``0`` on release. Events are only emitted when the
    value changes.

    Devices are created lazily and cached for the provider's lifetime;
    :meth:`close` releases them (which also releases any stuck keys).
    """

    def __init__(
        self,
        *,
        open_device_fn: Callable[[str, list[tuple[int, object]]], _DeviceWriter]
        | None = None,
    ) -> None:
        self._opener = open_device_fn or self._open_real
        self._devices: dict[str, _DeviceWriter] = {}
        self._last: dict[tuple[str, int], int] = {}

    def close(self) -> None:
        """Close every cached virtual device (releases any stuck keys)."""
        for device in self._devices.values():
            with contextlib.suppress(OSError):
                device.close()
        self._devices.clear()
        self._last.clear()

    @staticmethod
    def _open_real(
        name: str, specs: list[tuple[int, object]]
    ) -> _DeviceWriter:
        import uinput as uinput_module

        return cast(_DeviceWriter, uinput_module.Device(specs, name=name))

    # -- write_outputs seam ------------------------------------------------

    def __call__(self, graph: WireGraph) -> None:
        """Emit one event per changed ``(device, code)`` in the graph's OUTPUT blocks."""
        needed: dict[str, list[tuple[Block, int, int]]] = {}
        for block_id in graph.blocks():
            block = graph.block(block_id)
            if getattr(block, "type_name", None) != "OUTPUT":
                continue
            name = str(block.input("DEVICE").value or "")
            event = _resolve_event(str(block.input("CODE").value or ""))
            if not name or event is None:
                continue
            needed.setdefault(name, []).append((block, event[0], event[1]))

        for name, entries in needed.items():
            device = self._devices.get(name)
            if device is None:
                codes_by_type: dict[int, list[int]] = {}
                for _block, etype, code in entries:
                    codes_by_type.setdefault(etype, []).append(code)
                try:
                    device = self._opener(name, self._build_specs(codes_by_type))
                except OSError as exc:
                    logger.debug("cannot open uinput device %s: %s", name, exc)
                    continue
                self._devices[name] = device
            for block, etype, code in entries:
                q = block.input("Q").value
                raw = block.input("VALUE").value
                new_value = self._emit_value(etype, q, raw)
                key = (name, code)
                if self._last.get(key) == new_value:
                    continue
                try:
                    device.emit((etype, code), new_value)
                except OSError as exc:
                    logger.debug("emit %s/%s failed: %s", name, code, exc)
                    continue
                self._last[key] = new_value

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _build_specs(
        codes_by_type: dict[int, list[int]],
    ) -> list[tuple[int, object]]:
        """uinput.Device capability specs from the collected (type, code) set."""
        specs: list[tuple[int, object]] = []
        for etype in sorted(codes_by_type):
            codes = sorted(set(codes_by_type[etype]))
            if etype == ecodes.EV_ABS:
                specs.append((etype, [(code, 0, 255, 0, 0) for code in codes]))
            else:
                specs.append((etype, codes))
        return specs

    @staticmethod
    def _emit_value(etype: int, q: object, raw: object) -> int:
        """evdev value to emit: gated by Q; raw value for axes, 1/0 otherwise."""
        if not q:
            return 0
        if etype == ecodes.EV_ABS:
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return int(raw)
            return 0
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return int(raw)
        return 1
