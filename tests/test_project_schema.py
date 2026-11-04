"""Project schema: blocks, pins, wires, documents with YAML/JSON round-trips."""

import pytest

from input_automation.project import (
    Block,
    Pin,
    PinDirection,
    PinDType,
    Project,
    Wire,
    load_project,
    save_project,
)


def _block(block_id: str, block_type: str) -> Block:
    return Block(
        id=block_id,
        type=block_type,
        pins=[
            Pin(name="IN1", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
            Pin(name="IN2", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
            Pin(name="Q", direction=PinDirection.OUTPUT, dtype=PinDType.BOOL),
        ],
    )


def _project() -> Project:
    return Project(
        name="demo",
        blocks=[
            _block("sensor", "SENSOR"),
            _block("and1", "AND"),
            _block("out", "OUTPUT"),
        ],
        wires=[
            Wire(src_block="sensor", src_pin="Q", dst_block="and1", dst_pin="IN1"),
            Wire(src_block="and1", src_pin="Q", dst_block="out", dst_pin="IN1"),
        ],
    )


def _same(a: Project, b: Project) -> bool:
    return a.model_dump(mode="json") == b.model_dump(mode="json")


def test_yaml_round_trip(tmp_path) -> None:
    project = _project()
    path = tmp_path / "sub" / "demo.yaml"  # save_project must create parents
    save_project(project, path)
    loaded = load_project(path)
    assert _same(loaded, project)
    text = path.read_text(encoding="utf-8")
    assert "AND" in text and "and1" in text and "sensor" in text


def test_json_round_trip(tmp_path) -> None:
    project = _project()
    path = tmp_path / "demo.json"
    save_project(project, path)
    assert load_project(path) and _same(load_project(path), project)


def test_empty_project_round_trip(tmp_path) -> None:
    project = Project(name="empty")
    path = tmp_path / "empty.yaml"
    save_project(project, path)
    assert _same(load_project(path), project)


def test_duplicate_block_id_rejected() -> None:
    document = {
        "name": "bad",
        "blocks": [{"id": "x", "type": "AND"}, {"id": "x", "type": "AND"}],
    }
    with pytest.raises(ValueError, match="duplicate block id"):
        Project.model_validate(document)


def test_wire_to_unknown_block_rejected() -> None:
    document = {
        "name": "bad",
        "blocks": [{"id": "a", "type": "AND"}],
        "wires": [
            {"src_block": "a", "src_pin": "Q", "dst_block": "ghost", "dst_pin": "IN1"}
        ],
    }
    with pytest.raises(ValueError, match="unknown block"):
        Project.model_validate(document)


def test_wire_requires_output_to_input() -> None:
    pins = [
        {"name": "IN1", "direction": "input", "dtype": "bool"},
        {"name": "IN2", "direction": "input", "dtype": "bool"},
        {"name": "Q", "direction": "output", "dtype": "bool"},
    ]
    document = {
        "name": "bad",
        "blocks": [
            {"id": "a", "type": "AND", "pins": pins},
            {"id": "b", "type": "AND", "pins": pins},
        ],
        "wires": [{"src_block": "a", "src_pin": "IN1", "dst_block": "b", "dst_pin": "Q"}],
    }
    with pytest.raises(ValueError, match="is an input, not an output"):
        Project.model_validate(document)
    document["wires"] = [{"src_block": "a", "src_pin": "Q", "dst_block": "b", "dst_pin": "Q"}]
    with pytest.raises(ValueError, match="is an output, not an input"):
        Project.model_validate(document)


def test_wire_requires_matching_pin_types() -> None:
    document = {
        "name": "bad",
        "blocks": [
            {
                "id": "a",
                "type": "SENSOR",
                "pins": [{"name": "Q", "direction": "output", "dtype": "int"}],
            },
            {
                "id": "b",
                "type": "AND",
                "pins": [{"name": "IN1", "direction": "input", "dtype": "bool"}],
            },
        ],
        "wires": [{"src_block": "a", "src_pin": "Q", "dst_block": "b", "dst_pin": "IN1"}],
    }
    with pytest.raises(ValueError, match="pin type mismatch"):
        Project.model_validate(document)


def test_duplicate_pin_names_rejected() -> None:
    document = {
        "id": "a",
        "type": "AND",
        "pins": [
            {"name": "IN1", "direction": "input", "dtype": "bool"},
            {"name": "IN1", "direction": "input", "dtype": "bool"},
        ],
    }
    with pytest.raises(ValueError, match="duplicate pin names"):
        Block.model_validate(document)


def test_pin_default_must_match_dtype() -> None:
    with pytest.raises(ValueError, match="is not of type bool"):
        Pin(
            name="EN",
            direction=PinDirection.INPUT,
            dtype=PinDType.BOOL,
            default=1,
        )
    with pytest.raises(ValueError, match="is not of type string"):
        Pin(
            name="MSG",
            direction=PinDirection.INPUT,
            dtype=PinDType.STRING,
            default=7,
        )


def test_detect_format_rejects_unknown_suffix() -> None:
    from input_automation.project.persistence import detect_format

    with pytest.raises(ValueError, match="unsupported project file suffix"):
        detect_format("project.toml")
