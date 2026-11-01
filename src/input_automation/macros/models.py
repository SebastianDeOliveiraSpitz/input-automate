"""Macro data model: declarative YAML schema validated with Pydantic.

A macro is a named collection of trigger bindings and a step tree that the
orchestrator executes. Conditional logic (if/else, loops) and variables are
expressed with step kinds, not arbitrary code.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


class StepKind(StrEnum):
    ACTION = "action"
    IF = "if"
    WHILE = "while"
    SET = "set"


class TriggerKind(StrEnum):
    KEY = "key"
    BUTTON = "button"
    AXIS = "axis"


class Trigger(BaseModel):
    """A device binding that fires a macro (e.g. key X down, mouse button 2)."""

    model_config = ConfigDict(extra="forbid")

    kind: TriggerKind
    device: str = "any"
    code: str
    state: Literal["down", "up"] = "down"


class Action(BaseModel):
    """A single output action: inject a key/mouse event, or wait."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[StepKind.ACTION]
    op: Literal["press", "release", "tap", "move", "click", "scroll", "wait"]
    target: str | None = None
    button: int | None = None
    dx: int | None = None
    dy: int | None = None
    x: int | None = None
    y: int | None = None
    ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _require_fields(self) -> Self:
        if self.op in ("press", "release", "tap") and self.target is None:
            raise ValueError(f"op {self.op!r} requires 'target'")
        if self.op == "click" and self.button is None:
            raise ValueError("op 'click' requires 'button'")
        if self.op in ("move", "scroll") and self.dx is None and self.dy is None:
            raise ValueError(f"op {self.op!r} requires 'dx' and/or 'dy'")
        if self.op == "scroll" and (self.dx is None and self.dy is None):
            raise ValueError("op 'scroll' requires a direction")
        if self.op == "wait" and self.ms <= 0:
            raise ValueError("op 'wait' requires ms > 0")
        return self


class Condition(BaseModel):
    """A condition evaluated against variables and/or screen state."""

    model_config = ConfigDict(extra="forbid")

    var: str
    equals: str | int | float | None = None
    not_equals: str | int | float | None = None
    greater_than: int | float | None = None
    less_than: int | float | None = None

    @model_validator(mode="after")
    def _one_operator(self) -> Self:
        ops = [self.equals, self.not_equals, self.greater_than, self.less_than]
        if sum(op is not None for op in ops) != 1:
            raise ValueError("exactly one of equals/not_equals/greater_than/less_than")
        return self


class IfStep(BaseModel):
    """Conditional block: execute 'then' if the condition holds, else 'else'."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[StepKind.IF]
    condition: Condition
    then: list[Step]
    else_: list[Step] = Field(default=[], alias="else")


class WhileStep(BaseModel):
    """Loop: execute 'body' as long as the condition holds (safety-capped)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[StepKind.WHILE]
    condition: Condition
    body: list[Step]
    max_iterations: int = Field(default=1000, ge=1)


class SetStep(BaseModel):
    """Assign a value to a variable in the execution scope."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[StepKind.SET]
    var: str
    value: Any


class StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


_STEP_TAGS = ("action", "if", "while", "set")


def _normalize_step(value: Any) -> Any:
    """Accept shorthand YAML (``- action: {...}``) alongside tagged form
    (``kind: action``) by flattening the shorthand into a tagged dict."""
    if isinstance(value, dict) and "kind" not in value:
        tags = [t for t in _STEP_TAGS if t in value]
        if not tags:
            return value  # let the tagged union produce a real error
        if len(tags) > 1:
            raise ValueError(f"step has multiple tags: {tags}")
        payload = value[tags[0]]
        if not isinstance(payload, dict):
            raise ValueError(
                f"step tag {tags[0]!r} must map to a dict, got {type(payload).__name__}"
            )
        return {"kind": tags[0], **payload}
    return value


Step = Annotated[
    Annotated[Action | IfStep | WhileStep | SetStep, Field(discriminator="kind")],
    BeforeValidator(_normalize_step),
]


class Macro(BaseModel):
    """A complete macro: trigger bindings + a step tree."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str = ""
    triggers: list[Trigger] = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
