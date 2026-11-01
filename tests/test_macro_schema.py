"""Round-trip and validation tests for the macro schema and loader."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from input_automation.macros.loader import LoadError, load_macro
from input_automation.macros.models import Action, Macro, StepKind

VALID = """\
name: heal_loop
description: heal when health is low
triggers:
  - kind: key
    code: KEY_F
    state: down
steps:
  - set: {var: healed, value: false}
  - while:
      condition: {var: health, greater_than: 50}
      max_iterations: 5
      body:
        - action: {op: press, target: KEY_H}
        - action: {op: wait, ms: 250}
  - if:
      condition: {var: healed, equals: false}
      then:
        - action: {op: tap, target: KEY_H}
      else: []
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "macro.yaml"
    p.write_text(text)
    return p


def test_round_trip_valid_macro(tmp_path: Path) -> None:
    macro = load_macro(_write(tmp_path, VALID))
    assert macro.name == "heal_loop"
    assert macro.triggers[0].code == "KEY_F"
    # re-serialize to JSON and reload — schema must accept its own output
    # (by_alias keeps the YAML-compatible `else` key for IfStep)
    dumped = macro.model_dump_json(by_alias=True)
    macro2 = Macro.model_validate_json(dumped)
    assert macro2 == macro


def test_invalid_action_missing_target(tmp_path: Path) -> None:
    bad = 'name: x\ntriggers: [{kind: key, code: KEY_A}]\nsteps:\n  - action: {op: press}\n'
    with pytest.raises(LoadError, match="requires 'target'"):
        load_macro(_write(tmp_path, bad))


def test_invalid_condition_multiple_operators(tmp_path: Path) -> None:
    bad = (
        "name: x\ntriggers: [{kind: key, code: KEY_A}]\nsteps:\n"
        "  - if:\n      condition: {var: h, equals: 1, less_than: 5}\n"
        "      then: [{action: {op: wait, ms: 10}}]\n"
    )
    with pytest.raises(LoadError, match="exactly one of"):
        load_macro(_write(tmp_path, bad))


def test_forbidden_extra_keys(tmp_path: Path) -> None:
    bad = (
        "name: x\ntriggers: [{kind: key, code: KEY_A}]\n"
        "steps:\n  - action: {op: wait, ms: 10, bogus: 1}\n"
    )
    with pytest.raises(LoadError, match="bogus"):
        load_macro(_write(tmp_path, bad))


def test_bad_name_pattern(tmp_path: Path) -> None:
    bad = (
        "name: Bad Name!\ntriggers: [{kind: key, code: KEY_A}]\n"
        "steps:\n  - action: {op: wait, ms: 10}\n"
    )
    with pytest.raises(LoadError, match="name"):
        load_macro(_write(tmp_path, bad))


def test_no_triggers_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        Macro(
            name="x",
            triggers=[],
            steps=[Action(kind=StepKind.ACTION, op="wait", ms=10)],
        )
