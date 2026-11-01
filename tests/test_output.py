"""Tests for the virtual output device (headless, RecordingBackend)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from evdev import ecodes
from typer.testing import CliRunner

import input_automation.cli as cli_module
import input_automation.output.virtual as virtual_module
from input_automation.output.virtual import (
    RecordingBackend,
    VirtualDevice,
    uinput_available,
)


def test_key_tap_emits_press_release() -> None:
    backend = RecordingBackend()
    with VirtualDevice(backend) as device:
        device.key_tap(ecodes.KEY_A)

    events = backend.events
    assert len(events) == 2
    (etype, code), value, syn = events[0]
    assert (etype, code) == (ecodes.EV_KEY, ecodes.KEY_A)
    assert value == 1 and syn is True
    (etype, code), value, syn = events[1]
    assert value == 0 and syn is True


def test_key_tap_multiple_times() -> None:
    backend = RecordingBackend()
    with VirtualDevice(backend) as device:
        device.key_tap(ecodes.KEY_B, times=3)

    presses = [e for e in backend.events if e[1] == 1]
    releases = [e for e in backend.events if e[1] == 0]
    assert len(presses) == 3
    assert len(releases) == 3


def test_mouse_move_single_syn() -> None:
    backend = RecordingBackend()
    with VirtualDevice(backend) as device:
        device.mouse_move(10, -5)

    assert len(backend.events) == 2
    first_event = backend.events[0]
    assert first_event[0] == (ecodes.EV_REL, ecodes.REL_X)
    assert first_event[1] == 10
    assert first_event[2] is False  # no SYN yet — one report for the whole move
    second_event = backend.events[1]
    assert second_event[0] == (ecodes.EV_REL, ecodes.REL_Y)
    assert second_event[1] == -5
    assert second_event[2] is True  # final SYN


def test_mouse_move_zero_deltas() -> None:
    backend = RecordingBackend()
    with VirtualDevice(backend) as device:
        device.mouse_move(0, 0)

    assert backend.events == []


def test_mouse_click_buttons() -> None:
    for button, code in (("left", ecodes.BTN_LEFT), ("right", ecodes.BTN_RIGHT),
                         ("middle", ecodes.BTN_MIDDLE)):
        backend = RecordingBackend()
        with VirtualDevice(backend) as device:
            device.mouse_click(button)
        assert [e[1] for e in backend.events] == [1, 0]
        assert backend.events[0][0] == (ecodes.EV_KEY, code)


def test_mouse_click_invalid_button() -> None:
    with VirtualDevice(RecordingBackend()) as device, pytest.raises(
        ValueError, match="unknown mouse button"
    ):
        device.mouse_click("back")


def test_scroll() -> None:
    backend = RecordingBackend()
    with VirtualDevice(backend) as device:
        device.scroll(-2)

    assert len(backend.events) == 1
    (etype, code), value, _syn = backend.events[0]
    assert (etype, code) == (ecodes.EV_REL, ecodes.REL_WHEEL)
    assert value == -2


def test_operations_after_close_raise() -> None:
    device = VirtualDevice(RecordingBackend())
    device.close()
    device.close()  # idempotent

    with pytest.raises(RuntimeError, match="closed"):
        device.key_press(ecodes.KEY_A)


def test_recording_backend_describe() -> None:
    backend = RecordingBackend()
    with VirtualDevice(backend) as device:
        device.mouse_move(10, 0)
        device.key_tap(ecodes.KEY_A)

    text = backend.describe()
    assert "REL_X=10" in text
    assert "KEY_A=1" in text
    assert "KEY_A=0" in text


def test_uinput_available_missing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(virtual_module, "UINPUT_PATH", Path("/nonexistent/uinput"))
    ok, hint = uinput_available()
    assert ok is False
    assert "modprobe" in hint


def test_uinput_available_no_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = tmp_path / "uinput"
    node.write_bytes(b"")
    node.chmod(0o000)
    monkeypatch.setattr(virtual_module, "UINPUT_PATH", node)
    ok, hint = uinput_available()
    assert ok is False
    assert "permission" in hint or "udev" in hint


def test_uinput_available_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = tmp_path / "uinput"
    node.write_bytes(b"")
    node.chmod(0o666)
    monkeypatch.setattr(virtual_module, "UINPUT_PATH", node)
    ok, hint = uinput_available()
    assert ok is True
    assert hint == ""


def _fake_listener_iter(events: list[str]) -> Iterator[str]:  # helper for CLI fakes
    yield from events


def test_cli_test_output_dry_run() -> None:
    result = CliRunner().invoke(cli_module.app, ["test-output", "--dry-run"])

    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert "REL_X" in result.output  # the square move happened
    assert "KEY_A=1" in result.output


def test_cli_test_output_no_move_no_click() -> None:
    result = CliRunner().invoke(
        cli_module.app, ["test-output", "--dry-run", "--no-move", "--click", ""]
    )
    assert result.exit_code == 0
    assert "REL_X" not in result.output


def test_cli_test_output_unknown_key() -> None:
    result = CliRunner().invoke(cli_module.app, ["test-output", "--dry-run", "--key", "NOT_A_KEY"])
    assert result.exit_code == 2
    assert "unknown key name" in result.output


def test_cli_test_output_invalid_button() -> None:
    result = CliRunner().invoke(
        cli_module.app, ["test-output", "--dry-run", "--no-move", "--click", "back"]
    )
    assert result.exit_code == 2
    assert "unknown button" in result.output


def test_cli_test_output_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module, "uinput_available", lambda: (False, "no permission on /dev/uinput")
    )
    result = CliRunner().invoke(cli_module.app, ["test-output"])
    assert result.exit_code == 1
    assert "no permission" in result.output
