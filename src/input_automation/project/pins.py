"""Typed block pins for the project schema."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PinDirection(StrEnum):
    """Direction of a pin: inputs carry data into a block, outputs out of it."""

    INPUT = "input"
    OUTPUT = "output"


class PinDType(StrEnum):
    """Supported pin data types."""

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"


class Pin(BaseModel):
    """A typed pin: name, direction, data type, and an optional default value."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    direction: PinDirection
    dtype: PinDType
    default: bool | int | float | str | None = None

    @model_validator(mode="after")
    def _default_matches_dtype(self) -> Self:
        value = self.default
        if value is None:
            return self
        if self.dtype is PinDType.BOOL:
            ok = isinstance(value, bool)
        elif self.dtype is PinDType.INT:
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif self.dtype is PinDType.FLOAT:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:  # PinDType.STRING
            ok = isinstance(value, str)
        if not ok:
            raise ValueError(
                f"pin {self.name!r}: default {value!r} is not of type {self.dtype.value}"
            )
        return self
