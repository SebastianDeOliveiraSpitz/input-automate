"""Device input/output blocks for the scan engine (T7/T8).

These blocks declare the pin interface between physical devices and the
pure-Python engine. The engine layer stays IO-free: an :class:`InputBlock`
never opens an evdev device itself. An IO read provider
(:class:`input_automation.io.evdev.EvdevInputProvider`) polls the devices and
writes the output pins before each cycle; :meth:`Block.evaluate` is a
pass-through so wired successors receive the current state.
"""

from __future__ import annotations

from input_automation.engine.block import Block
from input_automation.engine.pins import Pin
from input_automation.engine.registry import default_registry
from input_automation.project.pins import PinDirection, PinDType

__all__ = ["InputBlock"]


class InputBlock(Block):
    """Reads evdev device state into output pins (T7).

    Pins:

    - ``DEVICE`` (string, in) -- evdev device path, e.g. ``"/dev/input/event0"``
    - ``CODE`` (string, in) -- evdev code name, e.g. ``"KEY_Q"``, ``"BTN_LEFT"``,
      ``"ABS_X"`` (resolved by :func:`input_automation.io.evdev.resolve_evdev_code`)
    - ``Q`` (bool, out) -- true while the key/button is down, or while the
      axis value is nonzero
    - ``VALUE`` (int, out) -- raw evdev value (1/0 for keys and buttons, axis
      position for absolute axes)

    The block never touches evdev. An IO provider writes ``Q`` and ``VALUE``
    before each cycle; :meth:`evaluate` is a pass-through.
    """

    type_name = "INPUT"
    input_pins = (
        Pin("DEVICE", PinDirection.INPUT, PinDType.STRING, default=""),
        Pin("CODE", PinDirection.INPUT, PinDType.STRING, default=""),
    )
    output_pins = (
        Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),
        Pin("VALUE", PinDirection.OUTPUT, PinDType.INT, default=0),
    )

    def evaluate(self) -> None:
        """Pass-through: output pins hold the state the IO provider wrote."""


default_registry.register("INPUT", InputBlock)
