"""Project document: blocks wired into a logic graph."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from input_automation.project.blocks import Block
from input_automation.project.pins import PinDirection
from input_automation.project.wires import Wire


class Project(BaseModel):
    """A complete project: named blocks wired into a logic graph."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    version: str = "1"
    blocks: list[Block] = Field(default_factory=list)
    wires: list[Wire] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_wires(self) -> Self:
        by_id: dict[str, Block] = {}
        for block in self.blocks:
            if block.id in by_id:
                raise ValueError(f"duplicate block id {block.id!r} in project")
            by_id[block.id] = block
        for wire in self.wires:
            src = by_id.get(wire.src_block)
            dst = by_id.get(wire.dst_block)
            if src is None:
                raise ValueError(
                    f"wire {wire.src_block!r}.{wire.src_pin!r}: unknown block "
                    f"{wire.src_block!r}"
                )
            if dst is None:
                raise ValueError(
                    f"wire {wire.dst_block!r}.{wire.dst_pin!r}: unknown block "
                    f"{wire.dst_block!r}"
                )
            src_pins = {pin.name: pin for pin in src.pins}
            dst_pins = {pin.name: pin for pin in dst.pins}
            src_pin = src_pins.get(wire.src_pin)
            dst_pin = dst_pins.get(wire.dst_pin)
            if src_pin is None:
                raise ValueError(
                    f"wire: block {wire.src_block!r} has no output pin "
                    f"{wire.src_pin!r}"
                )
            if dst_pin is None:
                raise ValueError(
                    f"wire: block {wire.dst_block!r} has no input pin "
                    f"{wire.dst_pin!r}"
                )
            if src_pin.direction is not PinDirection.OUTPUT:
                raise ValueError(
                    f"wire: source pin {wire.src_block!r}.{wire.src_pin!r} is "
                    f"an input, not an output"
                )
            if dst_pin.direction is not PinDirection.INPUT:
                raise ValueError(
                    f"wire: destination pin {wire.dst_block!r}.{wire.dst_pin!r} "
                    f"is an output, not an input"
                )
            if src_pin.dtype is not dst_pin.dtype:
                raise ValueError(
                    f"wire: pin type mismatch {wire.src_block!r}.{wire.src_pin!r} "
                    f"({src_pin.dtype.value}) -> {wire.dst_block!r}.{wire.dst_pin!r} "
                    f"({dst_pin.dtype.value})"
                )
        return self
