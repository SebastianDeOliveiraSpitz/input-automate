"""Pure-Python SPS logic engine: block registry, typed pins, wire graph, cyclic scan executor."""

from input_automation.engine.block import Block
from input_automation.engine.pins import Pin, PinTypeError
from input_automation.engine.registry import BlockRegistry, default_registry
from input_automation.engine.wires import (
    DuplicateBlockError,
    DuplicateWireError,
    UnknownBlockError,
    UnknownPinError,
    UnknownWireError,
    Wire,
    WireCycleError,
    WireGraph,
    WireGraphError,
    WireTypeError,
)
from input_automation.project.pins import PinDirection, PinDType

__all__ = [
    "Block",
    "Pin",
    "PinDType",
    "PinDirection",
    "PinTypeError",
    "BlockRegistry",
    "default_registry",
    "Wire",
    "WireGraph",
    "WireGraphError",
    "DuplicateBlockError",
    "UnknownBlockError",
    "UnknownPinError",
    "DuplicateWireError",
    "UnknownWireError",
    "WireCycleError",
    "WireTypeError",
]
