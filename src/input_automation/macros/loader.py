"""Load macro YAML files into validated Pydantic models with clear errors."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator

from pydantic import ValidationError

from input_automation.macros.models import Macro

__all__ = ["LoadError", "load_macro", "load_macros"]


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


def load_macros(directory: Path) -> Iterator[tuple[Path, Macro]]:
    """Yield (path, macro) for each valid *.yaml/*.yml file in the directory."""
    for pattern in ("*.yaml", "*.yml"):
        for path in sorted(directory.glob(pattern)):
            yield path, load_macro(path)
