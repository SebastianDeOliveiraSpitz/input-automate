"""Tests for saving macros back to YAML (dump_macro, T12)."""

from pathlib import Path

from input_automation.macros.loader import dump_macro, load_macro
from input_automation.macros.models import (
    Action,
    Condition,
    IfStep,
    Macro,
    Trigger,
)


def test_dump_roundtrip_preserves_else_branch(tmp_path: Path) -> None:
    """The 'else' alias survives dump -> load."""
    macro = Macro(
        name="round-trip",
        triggers=[Trigger(kind="key", code="a")],
        steps=[
            IfStep(
                kind="if",
                condition=Condition(var="x", equals=1),
                then=[Action(kind="action", op="tap", target="b")],
                **{"else": [Action(kind="action", op="tap", target="c")]},
            )
        ],
    )
    path = tmp_path / "macro.yaml"
    dump_macro(macro, path)
    loaded = load_macro(path)
    assert loaded.name == "round-trip"
    assert loaded.steps[0].else_[0].target == "c"  # type: ignore[union-attr]
