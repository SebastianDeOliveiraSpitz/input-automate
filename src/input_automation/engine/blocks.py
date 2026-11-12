"""Basic function blocks for the cyclic scan engine (T4/T5).

Combinational blocks: AND, OR, XOR, NOT, EQ, NE, LT, GT, THRESHOLD.
Sequential blocks: SR (SET/RESET flip-flop), TON (on-delay timer), TOF
(off-delay timer), TP (pulse timer), CTU (up counter), CLK (periodic clock).
Signal blocks: PASS (pass-through), MUX (2:1 multiplexer).

All blocks are headless-testable: no GUI or IO dependencies, all configuration
flows through typed pins, and every block registers itself in
:data:`input_automation.engine.default_registry` on import so the convenience
registry ships "batteries included".
"""

from __future__ import annotations

from input_automation.engine.block import Block
from input_automation.engine.pins import Pin
from input_automation.engine.registry import default_registry
from input_automation.project.pins import PinDirection, PinDType

__all__ = [
    "ANDBlock",
    "ClockBlock",
    "CTUBlock",
    "EQBlock",
    "TOFBlock",
    "TPBlock",
    "GTBlock",
    "LTBlock",
    "MUXBlock",
    "NEBlock",
    "NOTBlock",
    "ORBlock",
    "PassThroughBlock",
    "SRBlock",
    "ThresholdBlock",
    "TONBlock",
    "XORBlock",
]


def _pin_float(value: bool | int | float | str | None) -> float | None:
    """Return a FLOAT pin's value as a float, or ``None`` if unwired.

    FLOAT pins only ever store int/float (bool and str are rejected by
    :meth:`Pin.set`), so this narrows the pin value type for the
    comparison operators.
    """
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        return None
    return float(value)


class ANDBlock(Block):
    """Output Q is true when both inputs are true."""

    type_name = "AND"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.BOOL),
        Pin("IN2", PinDirection.INPUT, PinDType.BOOL),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        self.output("Q").set(
            bool(self.input("IN1").value) and bool(self.input("IN2").value)
        )


class ORBlock(Block):
    """Output Q is true when at least one input is true."""

    type_name = "OR"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.BOOL),
        Pin("IN2", PinDirection.INPUT, PinDType.BOOL),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        self.output("Q").set(
            bool(self.input("IN1").value) or bool(self.input("IN2").value)
        )


class XORBlock(Block):
    """Output Q is true when exactly one input is true."""

    type_name = "XOR"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.BOOL),
        Pin("IN2", PinDirection.INPUT, PinDType.BOOL),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        self.output("Q").set(
            bool(self.input("IN1").value) != bool(self.input("IN2").value)
        )


class NOTBlock(Block):
    """Output Q is the inverse of input IN."""

    type_name = "NOT"
    input_pins = (Pin("IN", PinDirection.INPUT, PinDType.BOOL),)
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        self.output("Q").set(not bool(self.input("IN").value))


class SRBlock(Block):
    """SET/RESET flip-flop.

    S sets Q, R resets Q. When both are true, reset wins (reset priority).
    Q keeps its last value when neither input is active. Starts at false.
    """

    type_name = "SR"
    input_pins = (
        Pin("S", PinDirection.INPUT, PinDType.BOOL),
        Pin("R", PinDirection.INPUT, PinDType.BOOL),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL, default=False),)

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self._q: bool = False

    def reset(self) -> None:
        super().reset()
        self._q = False

    def evaluate(self) -> None:
        if bool(self.input("R").value):
            self._q = False
        elif bool(self.input("S").value):
            self._q = True
        self.output("Q").set(self._q)


class TONBlock(Block):
    """On-delay timer.

    Inputs:
        IN -- enable (bool). While true, elapsed time accumulates.
        PT -- preset time in seconds (float, default 1.0).
        DT -- seconds since the last scan cycle (float, default 0.0). The
            caller (e.g. a read_inputs provider) injects the cycle time here;
            unwired, the timer never advances.
    Outputs:
        Q -- true once ET >= PT while IN is true.
        ET -- elapsed time since IN last went true, in seconds.

    The block is deliberately clockless: it advances only via the DT pin, so
    headless tests stay deterministic without sleeping on wall-clock time.
    """

    type_name = "TON"
    category = "Timer"
    input_pins = (
        Pin("IN", PinDirection.INPUT, PinDType.BOOL),
        Pin("PT", PinDirection.INPUT, PinDType.FLOAT, default=1.0),
        Pin("DT", PinDirection.INPUT, PinDType.FLOAT, default=0.0),
    )
    output_pins = (
        Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),
        Pin("ET", PinDirection.OUTPUT, PinDType.FLOAT, default=0.0),
    )

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self._elapsed: float = 0.0

    def reset(self) -> None:
        super().reset()
        self._elapsed = 0.0

    def evaluate(self) -> None:
        if bool(self.input("IN").value):
            dt = self.input("DT").value
            if isinstance(dt, (int, float)) and not isinstance(dt, bool):
                self._elapsed += dt
            pt = self.input("PT").value
            pt = pt if isinstance(pt, (int, float)) and not isinstance(pt, bool) else 0.0
            self.output("Q").set(self._elapsed >= pt)
        else:
            self._elapsed = 0.0
            self.output("Q").set(False)
        self.output("ET").set(self._elapsed)


class CTUBlock(Block):
    """Up counter.

    Inputs:
        CU -- count up; Q increments on rising edges only (level holding does
            not double-count).
        R -- reset; sets CV to 0 and Q to false, takes priority over CU.
        PV -- preset value (int, default 0).
    Outputs:
        Q -- true while CV >= PV.
        CV -- current count.
    """

    type_name = "CTU"
    category = "Timer"
    input_pins = (
        Pin("CU", PinDirection.INPUT, PinDType.BOOL),
        Pin("R", PinDirection.INPUT, PinDType.BOOL),
        Pin("PV", PinDirection.INPUT, PinDType.INT, default=0),
    )
    output_pins = (
        Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),
        Pin("CV", PinDirection.OUTPUT, PinDType.INT, default=0),
    )

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self._cv: int = 0
        self._prev_cu: bool = False

    def reset(self) -> None:
        super().reset()
        self._cv = 0
        self._prev_cu = False

    def evaluate(self) -> None:
        cu = bool(self.input("CU").value)
        if bool(self.input("R").value):
            self._cv = 0
        elif cu and not self._prev_cu:
            self._cv += 1
        self._prev_cu = cu
        pv = self.input("PV").value
        pv = pv if isinstance(pv, int) and not isinstance(pv, bool) else 0
        self.output("Q").set(self._cv >= pv)
        self.output("CV").set(self._cv)


class TOFBlock(Block):
    """Off-delay timer.

    Inputs:
        IN -- enable (bool). Q stays true while IN is true.
        PT -- preset time in seconds (float, default 1.0). After IN goes
            false, Q stays true for PT seconds.
        DT -- seconds since the last scan cycle (float, default 0.0). The
            caller (e.g. a read_inputs provider) injects the cycle time here;
            unwired, the timer never advances.
    Outputs:
        Q -- true while IN is true and for PT seconds after IN goes false.
        ET -- elapsed time since IN last went false, in seconds.

    Clockless like :class:`TONBlock`: advances only via the DT pin, so
    headless tests stay deterministic without sleeping on wall-clock time.
    """

    type_name = "TOF"
    category = "Timer"
    input_pins = (
        Pin("IN", PinDirection.INPUT, PinDType.BOOL),
        Pin("PT", PinDirection.INPUT, PinDType.FLOAT, default=1.0),
        Pin("DT", PinDirection.INPUT, PinDType.FLOAT, default=0.0),
    )
    output_pins = (
        Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),
        Pin("ET", PinDirection.OUTPUT, PinDType.FLOAT, default=0.0),
    )

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self._elapsed: float = 0.0

    def reset(self) -> None:
        super().reset()
        self._elapsed = 0.0

    def evaluate(self) -> None:
        dt = self.input("DT").value
        if not isinstance(dt, (int, float)) or isinstance(dt, bool):
            dt = 0.0
        pt = self.input("PT").value
        pt = pt if isinstance(pt, (int, float)) and not isinstance(pt, bool) else 0.0
        if bool(self.input("IN").value):
            self._elapsed = 0.0
            self.output("Q").set(True)
        else:
            self._elapsed += dt
            self.output("Q").set(self._elapsed < pt)
        self.output("ET").set(self._elapsed)


class TPBlock(Block):
    """Pulse timer.

    Inputs:
        IN -- trigger (bool). Q turns true on a rising edge of IN and stays
            true for PT seconds, regardless of IN afterwards.
        PT -- preset pulse width in seconds (float, default 1.0).
        DT -- seconds since the last scan cycle (float, default 0.0).
    Outputs:
        Q -- true for PT seconds after each IN rising edge.
        ET -- elapsed time since the last rising edge, in seconds.

    A rising edge while a pulse is active restarts the pulse. Clockless like
    :class:`TONBlock`: advances only via the DT pin.
    """

    type_name = "TP"
    category = "Timer"
    input_pins = (
        Pin("IN", PinDirection.INPUT, PinDType.BOOL),
        Pin("PT", PinDirection.INPUT, PinDType.FLOAT, default=1.0),
        Pin("DT", PinDirection.INPUT, PinDType.FLOAT, default=0.0),
    )
    output_pins = (
        Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),
        Pin("ET", PinDirection.OUTPUT, PinDType.FLOAT, default=0.0),
    )

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self._elapsed: float = 0.0
        self._active: bool = False
        self._prev_in: bool = False

    def reset(self) -> None:
        super().reset()
        self._elapsed = 0.0
        self._active = False
        self._prev_in = False

    def evaluate(self) -> None:
        dt = self.input("DT").value
        if not isinstance(dt, (int, float)) or isinstance(dt, bool):
            dt = 0.0
        pt = self.input("PT").value
        pt = pt if isinstance(pt, (int, float)) and not isinstance(pt, bool) else 0.0
        in_ = bool(self.input("IN").value)
        rising = in_ and not self._prev_in
        self._prev_in = in_
        if rising:
            self._elapsed = 0.0
            self._active = True
        if self._active:
            self._elapsed += dt
            if self._elapsed >= pt:
                self._active = False
        self.output("Q").set(self._active)
        self.output("ET").set(self._elapsed)


class ClockBlock(Block):
    """Periodic clock that fires true every cycle.

    Inputs:
        IN -- enable (bool, default true). While false, Q is false and the
            phase resets.
        PERIOD -- cycle time in seconds (float, default 1.0). Must be > 0.
        ON -- seconds within each cycle that Q is true (float, default 0.5).
        DT -- seconds since the last scan cycle (float, default 0.0).
    Outputs:
        Q -- true during the first ON seconds of every PERIOD cycle.
        ET -- phase within the current cycle, in seconds.

    Free-running clock: Q is true for ON seconds every PERIOD seconds.
    Clockless like :class:`TONBlock`: advances only via the DT pin.
    """

    type_name = "CLK"
    category = "Timer"
    input_pins = (
        Pin("IN", PinDirection.INPUT, PinDType.BOOL, default=True),
        Pin("PERIOD", PinDirection.INPUT, PinDType.FLOAT, default=1.0),
        Pin("ON", PinDirection.INPUT, PinDType.FLOAT, default=0.5),
        Pin("DT", PinDirection.INPUT, PinDType.FLOAT, default=0.0),
    )
    output_pins = (
        Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),
        Pin("ET", PinDirection.OUTPUT, PinDType.FLOAT, default=0.0),
    )

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self._phase: float = 0.0

    def reset(self) -> None:
        super().reset()
        self._phase = 0.0

    def evaluate(self) -> None:
        dt = self.input("DT").value
        if not isinstance(dt, (int, float)) or isinstance(dt, bool):
            dt = 0.0
        period = self.input("PERIOD").value
        period = (
            period
            if isinstance(period, (int, float)) and not isinstance(period, bool)
            else 0.0
        )
        on = self.input("ON").value
        on = on if isinstance(on, (int, float)) and not isinstance(on, bool) else 0.0
        if bool(self.input("IN").value) and period > 0.0 and dt > 0.0:
            self._phase += dt
            self._phase %= period
            self.output("Q").set(self._phase < on)
        else:
            self._phase = 0.0
            self.output("Q").set(False)
        self.output("ET").set(self._phase)


class EQBlock(Block):
    """Output Q is true when IN1 == IN2 (numeric comparison).

    Unwired inputs read as having no value: if either input is unwired,
    Q is false.
    """

    type_name = "EQ"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.FLOAT),
        Pin("IN2", PinDirection.INPUT, PinDType.FLOAT),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        a = self.input("IN1").value
        b = self.input("IN2").value
        self.output("Q").set(a is not None and b is not None and bool(a == b))


class NEBlock(Block):
    """Output Q is true when IN1 != IN2 (numeric comparison).

    Unwired inputs read as having no value: if either input is unwired,
    Q is false.
    """

    type_name = "NE"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.FLOAT),
        Pin("IN2", PinDirection.INPUT, PinDType.FLOAT),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        a = self.input("IN1").value
        b = self.input("IN2").value
        self.output("Q").set(a is not None and b is not None and bool(a != b))


class LTBlock(Block):
    """Output Q is true when IN1 < IN2 (numeric comparison).

    Unwired inputs read as having no value: if either input is unwired,
    Q is false.
    """

    type_name = "LT"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.FLOAT),
        Pin("IN2", PinDirection.INPUT, PinDType.FLOAT),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        a = _pin_float(self.input("IN1").value)
        b = _pin_float(self.input("IN2").value)
        if a is None or b is None:
            self.output("Q").set(False)
            return
        self.output("Q").set(bool(a < b))


class GTBlock(Block):
    """Output Q is true when IN1 > IN2 (numeric comparison).

    Unwired inputs read as having no value: if either input is unwired,
    Q is false.
    """

    type_name = "GT"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.FLOAT),
        Pin("IN2", PinDirection.INPUT, PinDType.FLOAT),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        a = _pin_float(self.input("IN1").value)
        b = _pin_float(self.input("IN2").value)
        if a is None or b is None:
            self.output("Q").set(False)
            return
        self.output("Q").set(bool(a > b))


class ThresholdBlock(Block):
    """Output Q is true when IN >= LIMIT (threshold crossed).

    Unwired inputs read as having no value: if either input is unwired,
    Q is false.
    """

    type_name = "THRESHOLD"
    input_pins = (
        Pin("IN", PinDirection.INPUT, PinDType.FLOAT),
        Pin("LIMIT", PinDirection.INPUT, PinDType.FLOAT),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        value = _pin_float(self.input("IN").value)
        limit = _pin_float(self.input("LIMIT").value)
        if value is None or limit is None:
            self.output("Q").set(False)
            return
        self.output("Q").set(bool(value >= limit))


class PassThroughBlock(Block):
    """Output Q follows IN unchanged (buffer / signal pass-through)."""

    type_name = "PASS"
    input_pins = (Pin("IN", PinDirection.INPUT, PinDType.FLOAT),)
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.FLOAT),)

    def evaluate(self) -> None:
        self.output("Q").set(self.input("IN").value)


class MUXBlock(Block):
    """2:1 multiplexer: Q is IN2 while SEL is true, otherwise IN1."""

    type_name = "MUX"
    input_pins = (
        Pin("SEL", PinDirection.INPUT, PinDType.BOOL),
        Pin("IN1", PinDirection.INPUT, PinDType.FLOAT),
        Pin("IN2", PinDirection.INPUT, PinDType.FLOAT),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.FLOAT),)

    def evaluate(self) -> None:
        sel = bool(self.input("SEL").value)
        self.output("Q").set(
            self.input("IN2").value if sel else self.input("IN1").value
        )


default_registry.register("AND", ANDBlock)
default_registry.register("OR", ORBlock)
default_registry.register("NOT", NOTBlock)
default_registry.register("XOR", XORBlock)
default_registry.register("SR", SRBlock)
default_registry.register("TON", TONBlock)
default_registry.register("CTU", CTUBlock)
default_registry.register("TOF", TOFBlock)
default_registry.register("TP", TPBlock)
default_registry.register("CLK", ClockBlock)
default_registry.register("EQ", EQBlock)
default_registry.register("NE", NEBlock)
default_registry.register("LT", LTBlock)
default_registry.register("GT", GTBlock)
default_registry.register("THRESHOLD", ThresholdBlock)
default_registry.register("PASS", PassThroughBlock)
default_registry.register("MUX", MUXBlock)
