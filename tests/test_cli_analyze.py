"""Tests for the `analyze` CLI command (T13 slice D)."""

import json
from pathlib import Path

import cv2
import numpy as np
from typer.testing import CliRunner

from input_automation.cli import app

runner = CliRunner()

RED = (0, 0, 255)  # BGR


def _write_image(path: Path, image: np.ndarray) -> None:
    cv2.imwrite(str(path), image)


def _gradient_image() -> np.ndarray:
    """100x80 image: left half red, right half black, plus a blue square."""
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    image[:, :50] = RED
    image[10:30, 60:80] = (255, 0, 0)  # blue square at (60,10)-(79,29)
    return image


class TestColorFraction:
    def test_color_fraction_half_red(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        _write_image(image, _gradient_image())
        result = runner.invoke(app, ["analyze", str(image), "--color", "0,0,255"])
        assert result.exit_code == 0, result.output
        assert "0.5000" in result.output

    def test_region_restricts(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        _write_image(image, _gradient_image())
        # region x=60,y=10,w=20,h=20 is the solid blue square
        result = runner.invoke(
            app, ["analyze", str(image), "--color", "255,0,0", "--region", "60,10,20,20"]
        )
        assert result.exit_code == 0, result.output
        assert "1.0000" in result.output

    def test_bar_mode(self, tmp_path: Path) -> None:
        image = np.zeros((20, 100, 3), dtype=np.uint8)
        image[:, :40] = RED  # bar 40% filled
        path = tmp_path / "bar.png"
        _write_image(path, image)
        result = runner.invoke(app, ["analyze", str(path), "--color", "0,0,255", "--bar"])
        assert result.exit_code == 0, result.output
        assert "0.4000" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        _write_image(image, _gradient_image())
        result = runner.invoke(
            app, ["analyze", str(image), "--color", "0,0,255", "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["mode"] == "color" and abs(data["fraction"] - 0.5) < 1e-3


class TestTemplateMatch:
    def test_finds_exact_template(self, tmp_path: Path) -> None:
        haystack = _gradient_image()
        template = haystack[10:30, 60:80].copy()  # the blue square
        image = tmp_path / "img.png"
        template_path = tmp_path / "tpl.png"
        _write_image(image, haystack)
        _write_image(template_path, template)
        result = runner.invoke(
            app, ["analyze", str(image), "--template", str(template_path), "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["mode"] == "template"
        assert (data["x"], data["y"]) == (60, 10)
        assert data["score"] > 0.99

    def test_template_larger_than_image_fails(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        big = tmp_path / "big.png"
        _write_image(image, _gradient_image())
        _write_image(big, np.zeros((200, 200, 3), dtype=np.uint8))
        result = runner.invoke(app, ["analyze", str(image), "--template", str(big)])
        assert result.exit_code == 2


class TestArgumentValidation:
    def test_color_and_template_conflict(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        tpl = tmp_path / "tpl.png"
        result = runner.invoke(
            app,
            ["analyze", str(image), "--color", "0,0,255", "--template", str(tpl)],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output

    def test_neither_color_nor_template(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["analyze", str(tmp_path / "img.png")])
        assert result.exit_code == 2

    def test_bad_color_spec(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        _write_image(image, _gradient_image())
        result = runner.invoke(app, ["analyze", str(image), "--color", "red"])
        assert result.exit_code == 2
        assert "B,G,R" in result.output

    def test_bad_region_spec(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        _write_image(image, _gradient_image())
        result = runner.invoke(
            app, ["analyze", str(image), "--color", "0,0,255", "--region", "1,2,3"]
        )
        assert result.exit_code == 2
        assert "x,y,w,h" in result.output

    def test_out_of_bounds_region(self, tmp_path: Path) -> None:
        image = tmp_path / "img.png"
        _write_image(image, _gradient_image())
        result = runner.invoke(
            app, ["analyze", str(image), "--color", "0,0,255", "--region", "90,90,50,50"]
        )
        assert result.exit_code != 0

    def test_missing_image_file(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["analyze", str(tmp_path / "nope.png"), "--color", "0,0,0"])
        assert result.exit_code == 1
