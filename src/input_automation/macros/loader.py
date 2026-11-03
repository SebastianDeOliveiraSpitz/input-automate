"""Load macro YAML files and Python scripts into validated models.

YAML macros are validated directly against the schema. Python macros
(T11 escape hatch) define a ``build(builder)`` function; see
:mod:`input_automation.macros.script`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

from pydantic import ValidationError

from input_automation.macros.models import Macro
from input_automation.macros.script import MacroBuilder

__all__ = [
    "LoadError",
    "dump_macro",
    "load_macro",
    "load_macros",
    "load_python_macro",
    "macro_to_yaml_text",
]


class LoadError(Exception):
    """A macro file could not be parsed or validated."""


def load_macro(path: Path) -> Macro:
    """Load and validate a single macro YAML file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LoadError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise LoadError(f"{path.name}: top level must be a mapping, got {type(raw).__name__}")
    try:
        return Macro.model_validate(raw)
    except ValidationError as exc:
        raise LoadError(f"{path.name}: invalid macro: {exc}") from exc


def load_python_macro(path: Path) -> Macro:
    """Load a Python macro module: it must define ``build(builder)``."""
    spec = importlib.util.spec_from_file_location(
        f"input_automation._user_macro_{path.stem}", path
    )
    assert spec is not None and spec.loader is not None  # noqa: S101 - path exists
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise LoadError(f"{path.name}: error during import: {exc}") from exc
    build = getattr(module, "build", None)
    if not callable(build):
        raise LoadError(
            f"{path.name}: module must define a 'build(builder)' function"
        )
    builder = MacroBuilder(name=path.stem)
    try:
        build(builder)
        return builder.build()
    except ValidationError as exc:
        raise LoadError(f"{path.name}: invalid macro: {exc}") from exc
    except Exception as exc:
        raise LoadError(f"{path.name}: error in build(): {exc}") from exc


def macro_to_yaml_text(macro: Macro) -> str:
    """Serialize a macro to YAML text (round-trips through :func:`load_macro`)."""
    # mode="json" converts enum members (TriggerKind, StepKind) to plain
    # strings that yaml.safe_dump can represent.
    data = macro.model_dump(by_alias=True, exclude_none=True, mode="json")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def dump_macro(macro: Macro, path: Path) -> None:
    """Save a macro back to YAML (round-trips through :func:`load_macro`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(macro_to_yaml_text(macro), encoding="utf-8")


def load_macros(directory: Path) -> Iterator[tuple[Path, Macro]]:
    """Yield (path, macro) for each macro file (*.yaml, *.yml, *.py)."""
    for pattern in ("*.yaml", "*.yml"):
        for path in sorted(directory.glob(pattern)):
            yield path, load_macro(path)
    for path in sorted(directory.glob("*.py")):
        yield path, load_python_macro(path)
