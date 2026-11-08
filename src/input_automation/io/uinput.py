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
        open_device_fn: Callable[[str, list[tuple[int, ...]]], _DeviceWriter]
        | None = None,
    ) -> None:
        self._opener = open_device_fn or self._open_real
        self._devices: dict[str, _DeviceWriter] = {}
        self._last: dict[tuple[str, int], int] = {}
        self._open_failures: set[str] = set()

    def close(self) -> None:
        """Close every cached virtual device (releases any stuck keys).

        python-uinput's :class:`uinput.Device` exposes :meth:`destroy` rather
        than :meth:`close`; fakes expose :meth:`close`. Accept either so real
        devices are always torn down on stop.
        """
        for device in self._devices.values():
            closer = getattr(device, "close", None) or getattr(device, "destroy", None)
            if closer is not None:
                with contextlib.suppress(OSError):
                    closer()
        self._devices.clear()
        self._last.clear()
        self._open_failures.clear()

    @staticmethod
    def _open_real(
        name: str, specs: list[tuple[int, ...]]
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
                logger.debug(
                    "OUTPUT block %s: DEVICE/CODE not configured, skipping",
                    block_id,
                )
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
                except Exception as exc:  # noqa: BLE001 - a bad device must never abort a scan cycle
                    if name not in self._open_failures:
                        self._open_failures.add(name)
                        logger.warning("cannot open uinput device %s: %s", name, exc)
                    else:
                        logger.debug("cannot open uinput device %s: %s", name, exc)
                    continue
                self._devices[name] = device
                logger.debug(
                    "uinput device %s opened with %d capabilities",
                    name,
                    len(codes_by_type),
                )
            for block, etype, code in entries:
                q = block.input("Q").value
                raw = block.input("VALUE").value
                new_value = self._emit_value(etype, q, raw)
                logger.debug(
                    "uinput %s [%s]: Q=%s -> event %s/%s value=%d",
                    name,
                    block.id,
                    q,
                    etype,
                    code,
                    new_value,
                )
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
    ) -> list[tuple[int, ...]]:
        """uinput.Device capability specs from the collected (type, code) set.

        The python-uinput binding wants one ``(type, code)`` tuple per
        capability -- and ``(type, code, min, max, fuzz, flat)`` for absolute
        axes -- not one tuple per type carrying a list of codes.
        """
        specs: list[tuple[int, ...]] = []
        for etype in sorted(codes_by_type):
            for code in sorted(set(codes_by_type[etype])):
                if etype == ecodes.EV_ABS:
                    specs.append((etype, code, 0, 255, 0, 0))
                else:
                    specs.append((etype, code))
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
