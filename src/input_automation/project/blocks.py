"""Block instances for the project schema."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from input_automation.project.pins import Pin


class Block(BaseModel):
    """One block instance: id, registered block type, pins, parameter values.

    ``type`` names a block kind known to the engine's block registry
    (e.g. ``AND``, ``TON``). ``pins`` declares the instance's pin interface;
    ``parameters`` holds configuration constants such as a timer's preset.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    pins: list[Pin] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_pin_names(self) -> Self:
        names = [pin.name for pin in self.pins]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(
                f"block {self.id!r}: duplicate pin names: {sorted(duplicates)}"
            )
        return self
