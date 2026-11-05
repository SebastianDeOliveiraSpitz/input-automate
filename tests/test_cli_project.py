"""Tests for the `project` CLI group (R2 stub)."""

from pathlib import Path

from typer.testing import CliRunner

from input_automation.cli import app
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

runner = CliRunner()


def _demo_project() -> Project:
    return Project(
        name="demo",
        blocks=[
            Block(
                id="sensor",
                type="Input",
                pins=[
                    Pin(
                        name="out",
                        direction=PinDirection.OUTPUT,
                        dtype=PinDType.BOOL,
                    )
                ],
            ),
            Block(
                id="and1",
                type="AND",
                pins=[
                    Pin(name="a", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
                    Pin(name="b", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
                    Pin(
                        name="q",
                        direction=PinDirection.OUTPUT,
                        dtype=PinDType.BOOL,
                    ),
                ],
            ),
        ],
        wires=[
            Wire(
                src_block="sensor",
                src_pin="out",
                dst_block="and1",
                dst_pin="a",
            )
        ],
    )


def _save(path: Path, project: Project | None = None) -> None:
    save_project(project or _demo_project(), path)


class TestProjectVersion:
    def test_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0, result.output
        assert "input-automation 0.1.0" in result.output


class TestProjectNew:
    def test_creates_yaml_file(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.yaml"
        result = runner.invoke(
            app, ["project", "new", str(path), "--name", "demo"]
        )
        assert result.exit_code == 0, result.output
        assert load_project(path).name == "demo"
        project = load_project(path)
        assert project.blocks == [] and project.wires == []

    def test_derives_name_from_file_stem(self, tmp_path: Path) -> None:
        path = tmp_path / "robot_cell.yaml"
        result = runner.invoke(app, ["project", "new", str(path)])
        assert result.exit_code == 0, result.output
        assert load_project(path).name == "robot_cell"

    def test_creates_json_file(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        result = runner.invoke(app, ["project", "new", str(path)])
        assert result.exit_code == 0, result.output
        assert load_project(path).name == "demo"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "deep" / "demo.yaml"
        result = runner.invoke(app, ["project", "new", str(path)])
        assert result.exit_code == 0, result.output
        assert path.exists()

    def test_fails_if_file_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.yaml"
        _save(path)
        result = runner.invoke(app, ["project", "new", str(path)])
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_rejects_name_from_bad_stem(self, tmp_path: Path) -> None:
        path = tmp_path / "1.2.yaml"
        result = runner.invoke(app, ["project", "new", str(path)])
        assert result.exit_code != 0
        result = runner.invoke(
            app, ["project", "new", str(path), "--name", "cell_1"]
        )
        assert result.exit_code == 0, result.output
        assert load_project(path).name == "cell_1"


class TestProjectOpen:
    def test_prints_summary(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.yaml"
        _save(path)
        result = runner.invoke(app, ["project", "open", str(path)])
        assert result.exit_code == 0, result.output
        assert "project 'demo'" in result.output
        assert "blocks: 2" in result.output
        assert "wires: 1" in result.output
        assert "sensor (Input): out:bool (output)" in result.output
        assert "and1 (AND):" in result.output
        assert "a:bool (input)" in result.output
        assert "b:bool (input)" in result.output
        assert "q:bool (output)" in result.output
        assert "sensor.out -> and1.a" in result.output

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["project", "open", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0


class TestProjectValidate:
    def test_valid_project_passes(self, tmp_path: Path) -> None:
        path = tmp_path / "v.yaml"
        _save(path)
        result = runner.invoke(app, ["project", "validate", str(path)])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()

    def test_invalid_project_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("name: bad name\nblocks: []\nwires: []\n", encoding="utf-8")
        result = runner.invoke(app, ["project", "validate", str(path)])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["project", "validate", str(tmp_path / "nope.yaml")]
        )
        assert result.exit_code != 0


class TestProjectRun:
    def _and_project(self, tmp_path: Path, name: str = "demo") -> Path:
        project = Project(
            name=name,
            blocks=[
                Block(
                    id="a1",
                    type="AND",
                    pins=[
                        Pin(
                            name="IN1",
                            direction=PinDirection.INPUT,
                            dtype=PinDType.BOOL,
                            default=True,
                        ),
                        Pin(
                            name="IN2",
                            direction=PinDirection.INPUT,
                            dtype=PinDType.BOOL,
                            default=False,
                        ),
                        Pin(name="Q", direction=PinDirection.OUTPUT, dtype=PinDType.BOOL),
                    ],
                ),
                Block(
                    id="a2",
                    type="AND",
                    pins=[
                        Pin(name="IN1", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
                        Pin(
                            name="IN2",
                            direction=PinDirection.INPUT,
                            dtype=PinDType.BOOL,
                            default=True,
                        ),
                        Pin(name="Q", direction=PinDirection.OUTPUT, dtype=PinDType.BOOL),
                    ],
                ),
            ],
            wires=[Wire(src_block="a1", src_pin="Q", dst_block="a2", dst_pin="IN1")],
        )
        path = tmp_path / "demo.yaml"
        save_project(project, path)
        return path

    def test_run_executes_and_reports_outputs(self, tmp_path: Path) -> None:
        path = self._and_project(tmp_path)
        result = runner.invoke(app, ["project", "run", str(path)])
        assert result.exit_code == 0, result.output
        assert "ran 1 cycle(s)" in result.output
        assert "a1: Q=False" in result.output
        assert "a2: Q=False" in result.output

    def test_run_cycles_option(self, tmp_path: Path) -> None:
        path = self._and_project(tmp_path)
        result = runner.invoke(app, ["project", "run", str(path), "--cycles", "3"])
        assert result.exit_code == 0, result.output
        assert "ran 3 cycle(s)" in result.output

    def test_run_unknown_block_type_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        save_project(
            Project(name="bad", blocks=[Block(id="x", type="NOPE", pins=[])]),
            path,
        )
        result = runner.invoke(app, ["project", "run", str(path)])
        assert result.exit_code != 0
        assert "unknown block type" in result.output

    def test_run_cycle_fails(self, tmp_path: Path) -> None:
        pins = [
            Pin(name="IN1", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
            Pin(name="IN2", direction=PinDirection.INPUT, dtype=PinDType.BOOL),
            Pin(name="Q", direction=PinDirection.OUTPUT, dtype=PinDType.BOOL),
        ]
        path = tmp_path / "cyc.yaml"
        save_project(
            Project(
                name="cyc",
                blocks=[
                    Block(id="a1", type="AND", pins=pins),
                    Block(id="a2", type="AND", pins=pins),
                ],
                wires=[
                    Wire(src_block="a1", src_pin="Q", dst_block="a2", dst_pin="IN1"),
                    Wire(src_block="a2", src_pin="Q", dst_block="a1", dst_pin="IN1"),
                ],
            ),
            path,
        )
        result = runner.invoke(app, ["project", "run", str(path)])
        assert result.exit_code != 0
        assert "cycle" in result.output.lower()

    def test_run_invalid_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("name: bad name\n", encoding="utf-8")
        result = runner.invoke(app, ["project", "run", str(path)])
        assert result.exit_code != 0
