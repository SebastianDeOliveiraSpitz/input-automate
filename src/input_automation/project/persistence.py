"""Project persistence: YAML/JSON round-trip for :class:`Project` documents."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from input_automation.project.document import Project

_FORMATS: dict[str, str] = {".yaml": "yaml", ".yml": "yaml", ".json": "json"}


def detect_format(path: str | Path) -> str:
    """Return ``"yaml"`` or ``"json"`` for a project file path; raise on others."""

    suffix = Path(path).suffix.lower()
    if suffix not in _FORMATS:
        raise ValueError(
            f"unsupported project file suffix {suffix!r} "
            f"(expected one of {sorted(_FORMATS)})"
        )
    return _FORMATS[suffix]


def save_project(project: Project, path: str | Path) -> None:
    """Serialize ``project`` to YAML (default) or JSON at ``path``."""

    path = Path(path)
    fmt = detect_format(path)
    data = project.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_project(path: str | Path) -> Project:
    """Load and validate a project document from YAML or JSON."""

    path = Path(path)
    fmt = detect_format(path)
    text = path.read_text(encoding="utf-8")
    if fmt == "json":
        data: object = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: project file must contain a mapping/object")
    return Project.model_validate(data)
