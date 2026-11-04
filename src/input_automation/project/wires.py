"""Wires: connections between block pins in the project schema."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Wire(BaseModel):
    """A connection from one block's output pin to another block's input pin."""

    model_config = ConfigDict(extra="forbid")

    src_block: str
    src_pin: str
    dst_block: str
    dst_pin: str
