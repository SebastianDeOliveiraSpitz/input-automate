"""Runtime typed pins: named, typed value holders for the scan engine.

The engine's :class:`input_automation.engine.block.Block` instances own their
pins; each pin stores one value of its declared dtype. Direction (input/
output) pins who may write it: the scan engine writes input pins, blocks write
output pins in :meth:`Block.evaluate`.

"""

from __future__ import annotations

from input_automation.project.pins import PinDirection, PinDType


class PinTypeError(TypeError):
    """Raised when a value stored on a pin does not match its :class:`PinDType`."""


class Pin:
    """One typed pin carrying a single value.

    ``direction`` and ``dtype`` are fixed at construction. :meth:`set`
    validates every value against ``dtype`` (booleans are not integers for
    ``INT``/``FLOAT`` pins, matching the project schema). ``None`` always
    reads back as "no value yet" and is accepted by the setter.
    """

    __slots__ = ("_name", "_direction", "_dtype", "_value")

    def __init__(
        self,
        name: str,
        direction: PinDirection,
        dtype: PinDType,
        default: bool | int | float | str | None = None,
    ) -> None:
        self._name = name
        self._direction = direction
        self._dtype = dtype
        self._value: bool | int | float | str | None = None
        if default is not None:
            self.set(default)

    @property
    def name(self) -> str:
        return self._name

    @property
    def direction(self) -> PinDirection:
        return self._direction

    @property
    def dtype(self) -> PinDType:
        return self._dtype

    @property
    def value(self) -> bool | int | float | str | None:
        return self._value

    def set(self, value: bool | int | float | str | None) -> None:
        """Store ``value`` on the pin; raise :class:`PinTypeError` on mismatch."""
        if value is not None and not self._matches(value):
            raise PinTypeError(
                f"pin {self._name!r}: {value!r} is not of type {self._dtype.value}"
            )
        self._value = value

    def _matches(self, value: object) -> bool:
        if self._dtype is PinDType.BOOL:
            return isinstance(value, bool)
        if self._dtype is PinDType.INT:
            return isinstance(value, int) and not isinstance(value, bool)
        if self._dtype is PinDType.FLOAT:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return isinstance(value, str)

    def __repr__(self) -> str:
        return (
            f"Pin(name={self._name!r}, direction={self._direction.value}, "
            f"dtype={self._dtype.value}, value={self._value!r})"
        )
