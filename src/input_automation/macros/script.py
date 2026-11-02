"""Python macro escape hatch: build macros from Python modules (T11).

A Python macro is a module defining::

    def build(m: MacroBuilder) -> None:
        m.key_trigger("F10")
        m.tap("a")

The builder accumulates triggers and steps with the same vocabulary as the
YAML schema; validation happens once at :meth:`MacroBuilder.build`, so the
Pydantic schema (T2) stays the single source of truth. Python-only logic
(loops, arithmetic, conditionals on host state) is free to run inside
``build()`` as long as the final step tree is declarative.
"""

from __future__ import annotations

from typing import Any, Literal

from input_automation.macros.models import (
    Action,
    Condition,
    IfStep,
    Macro,
    SetStep,
    Step,
    StepKind,
    Trigger,
    TriggerKind,
    WhileStep,
)

_State = Literal["down", "up"]

__all__ = ["MacroBuilder"]


class MacroBuilder:
    """Fluent builder producing a validated :class:`Macro`.

    ``name`` defaults to the macro file's stem; assign :attr:`name` to
    override it. All mutation happens in ``build(m)``; the final
    :meth:`build` call validates the whole macro against the schema.
    """

    def __init__(self, name: str) -> None:
        self.name: str = name
        self._description: str = ""
        self._triggers: list[Trigger] = []
        self._steps: list[Step] = []

    def description(self, text: str) -> None:
        """Set the macro description."""
        self._description = text

    # -- triggers ---------------------------------------------------------

    def key_trigger(self, code: str, state: _State = "down") -> None:
        """Bind a keyboard trigger (e.g. ``code="F10"``, ``state="down"``)."""
        self._triggers.append(Trigger(kind=TriggerKind.KEY, code=code, state=state))

    def button_trigger(self, code: str, state: _State = "down") -> None:
        """Bind a mouse-button trigger (``code`` is a BTN_ name or number)."""
        self._triggers.append(Trigger(kind=TriggerKind.BUTTON, code=code, state=state))

    # -- actions ----------------------------------------------------------

    def press(self, target: str) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="press", target=target))

    def release(self, target: str) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="release", target=target))

    def tap(self, target: str) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="tap", target=target))

    def click(self, button: int) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="click", button=button))

    def move(self, dx: int | None = None, dy: int | None = None) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="move", dx=dx, dy=dy))

    def scroll(self, dx: int | None = None, dy: int | None = None) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="scroll", dx=dx, dy=dy))

    def wait(self, ms: int) -> None:
        self._steps.append(Action(kind=StepKind.ACTION, op="wait", ms=ms))

    # -- control flow -----------------------------------------------------

    def set(self, var: str, value: Any) -> None:
        self._steps.append(SetStep(kind=StepKind.SET, var=var, value=value))

    def if_(
        self,
        condition: Condition,
        then: list[Step],
        otherwise: list[Step] | None = None,
    ) -> None:
        """Append a conditional block (``otherwise`` maps to ``else``)."""
        self._steps.append(
            IfStep(
                kind=StepKind.IF,
                condition=condition,
                then=then,
                **{"else": otherwise or []},
            )
        )

    def while_(
        self,
        condition: Condition,
        body: list[Step],
        max_iterations: int = 1000,
    ) -> None:
        self._steps.append(
            WhileStep(
                kind=StepKind.WHILE,
                condition=condition,
                body=body,
                max_iterations=max_iterations,
            )
        )

    @staticmethod
    def cond(
        var: str,
        equals: str | int | float | None = None,
        not_equals: str | int | float | None = None,
        greater_than: int | float | None = None,
        less_than: int | float | None = None,
    ) -> Condition:
        """Shorthand for building a :class:`Condition`."""
        return Condition(
            var=var,
            equals=equals,
            not_equals=not_equals,
            greater_than=greater_than,
            less_than=less_than,
        )

    # -- assembly ---------------------------------------------------------

    def build(self) -> Macro:
        """Assemble and validate the macro (schema violations raise here)."""
        return Macro(
            name=self.name,
            description=self._description,
            triggers=self._triggers,
            steps=self._steps,
        )
