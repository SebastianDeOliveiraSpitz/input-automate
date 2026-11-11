"""Base class for logic blocks executed by the cyclic scan engine.

A block owns its input and output pins (typed :class:`Pin` instances) and
implements :meth:`evaluate`, which reads its inputs and writes its outputs.
Subclasses declare their pin interface through the class attributes
``input_pins`` and ``output_pins`` (tuples of template :class:`Pin`s); every
instance gets its own copy, so blocks never share pin state.

"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from itertools import chain
from typing import ClassVar, Final

from input_automation.engine.pins import Pin


class Block(ABC):
    """Abstract base class for a function block in the logic graph.

    Subclasses set the class attributes below:

    - ``type_name``: registry key, e.g. ``"AND"`` (must be overridden).
    - ``input_pins`` / ``output_pins``: template pins declaring the interface.
    """

    type_name: ClassVar[str] = "BLOCK"
    category: ClassVar[str] = "Logic"
    input_pins: ClassVar[tuple[Pin, ...]] = ()
    output_pins: ClassVar[tuple[Pin, ...]] = ()

    def __init__(self, block_id: str) -> None:
        self.id: Final[str] = block_id
        self._inputs: dict[str, Pin] = {}
        self._outputs: dict[str, Pin] = {}
        self._input_templates: dict[str, Pin] = {}
        self._output_templates: dict[str, Pin] = {}
        for template in self.input_pins:
            pin = self._copy(template)
            self._inputs[template.name] = pin
            self._input_templates[template.name] = template
        for template in self.output_pins:
            pin = self._copy(template)
            self._outputs[template.name] = pin
            self._output_templates[template.name] = template

    @staticmethod
    def _copy(template: Pin) -> Pin:
        return Pin(template.name, template.direction, template.dtype, template.value)

    @property
    def inputs(self) -> Mapping[str, Pin]:
        """Read-only view of the block's input pins."""
        return self._inputs

    @property
    def outputs(self) -> Mapping[str, Pin]:
        """Read-only view of the block's output pins."""
        return self._outputs

    def input(self, name: str) -> Pin:
        """Return the input pin ``name``; raise :exc:`KeyError` if unknown."""
        try:
            return self._inputs[name]
        except KeyError:
            raise KeyError(
                f"block {self.id!r} ({self.type_name}): no input pin {name!r}"
            ) from None

    def output(self, name: str) -> Pin:
        """Return the output pin ``name``; raise :exc:`KeyError` if unknown."""
        try:
            return self._outputs[name]
        except KeyError:
            raise KeyError(
                f"block {self.id!r} ({self.type_name}): no output pin {name!r}"
            ) from None

    @abstractmethod
    def evaluate(self) -> None:
        """Read input pins and write output pins. Called once per scan cycle."""

    def reset(self) -> None:
        """Reset every pin to its template default value."""
        for name, pin in chain(
            self._inputs.items(), self._outputs.items()
        ):
            template = self._input_templates.get(name)
            if template is None:
                template = self._output_templates[name]
            pin.set(template.value)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id!r}, type={self.type_name!r})"
