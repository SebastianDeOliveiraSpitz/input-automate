"""Device input/output blocks for the scan engine (T7/T8).

These blocks declare the pin interface between physical devices and the
pure-Python engine. The engine layer stays IO-free: blocks never open devices themselves. An IO
read provider (:class:`input_automation.io.evdev.EvdevInputProvider`) polls
devices and writes input-block output pins before each cycle; an IO write
provider (:class:`input_automation.io.uinput.UinputOutputProvider`) reads
output-block input pins after each cycle and injects the events;
:meth:`Block.evaluate` is a pass-through so wired values are current.
"""

from __future__ import annotations

from input_automation.engine.block import Block
from input_automation.engine.pins import Pin
from input_automation.engine.registry import default_registry
from input_automation.project.pins import PinDirection, PinDType

__all__ = ["InputBlock", "OutputBlock"]


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


class OutputBlock(Block):
    """Write a pin state to a virtual uinput device (T8).

    Pins:

    - ``DEVICE`` (string, in) -- virtual uinput device name, e.g.
      ``"out-keyboard"``
    - ``CODE`` (string, in) -- evdev code name, e.g. ``"KEY_Q"``,
      ``"BTN_LEFT"``, ``"ABS_X"`` (resolved by
      :func:`input_automation.io.evdev.resolve_evdev_code`)
    - ``Q`` (bool, in) -- state to write: true asserts, false deasserts
    - ``VALUE`` (int, in) -- raw evdev value emitted while Q is true
      (1/0 for keys and buttons, axis position for absolute axes; default 1)

    The block never touches uinput. An IO write provider
    (:class:`input_automation.io.uinput.UinputOutputProvider`) reads ``Q``
    and ``VALUE`` after each cycle and injects the event;
    :meth:`evaluate` is a pass-through so values wired from predecessors
    are current.
    """

    type_name = "OUTPUT"
    input_pins = (
        Pin("DEVICE", PinDirection.INPUT, PinDType.STRING, default=""),
        Pin("CODE", PinDirection.INPUT, PinDType.STRING, default=""),
        Pin("Q", PinDirection.INPUT, PinDType.BOOL, default=False),
        Pin("VALUE", PinDirection.INPUT, PinDType.INT, default=1),
    )
    output_pins = ()

    def evaluate(self) -> None:
        """Pass-through: nothing to compute; the provider drives the device."""


default_registry.register("INPUT", InputBlock)
default_registry.register("OUTPUT", OutputBlock)
