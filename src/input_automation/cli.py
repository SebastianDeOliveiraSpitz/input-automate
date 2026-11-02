"""CLI entry point for input-automation."""

import json
import time
from pathlib import Path
from typing import Annotated

import cv2
import typer
from evdev import ecodes

from input_automation import __version__
from input_automation.capture.mock import MockCaptureBackend
from input_automation.devices.discovery import list_devices
from input_automation.devices.listener import DeviceListener
from input_automation.output.virtual import (
    RecordingBackend,
    VirtualDevice,
    uinput_available,
)

app = typer.Typer(help="Automate inputs on Linux (Wayland).")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"input-automation {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Automate inputs on Linux (Wayland) with device- and vision-driven macros."""


@app.command("list-devices")
def list_devices_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """List connected input devices (keyboard, mouse, touchpad, gamepad)."""
    skipped: list[tuple[str, str]] = []

    def record_error(path: Path, exc: Exception) -> None:
        skipped.append((str(path), str(exc)))

    devices = list_devices(on_error=record_error)
    if skipped:
        typer.echo(
            f"Warning: {len(skipped)} device(s) could not be opened "
            f"(first: {skipped[0][0]}: {skipped[0][1]}). "
            "Hint: add your user to the 'input' group or install the udev rule.",
            err=True,
        )
    if json_output:
        typer.echo(json.dumps([d.as_dict() for d in devices], indent=2))
        return
    if not devices:
        typer.echo("No input devices found.")
        return
    for dev in devices:
        kinds = ", ".join(sorted(dev.kinds))
        typer.echo(f"{dev.path}  {dev.name}  [{kinds}]")


@app.command()
def monitor(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON events."),
    paths: Annotated[
        list[str] | None,
        typer.Argument(help="Device paths to watch (default: all discoverable devices)."),
    ] = None,
) -> None:
    """Watch input device events in real time. Press Ctrl+C to stop."""
    skipped: list[tuple[str, str]] = []

    def record_error(path: Path, exc: Exception) -> None:
        skipped.append((str(path), str(exc)))

    with DeviceListener(paths, on_error=record_error) as listener:
        if skipped:
            typer.echo(
                f"Warning: {len(skipped)} device(s) could not be opened "
                f"(first: {skipped[0][0]}: {skipped[0][1]}). "
                "Hint: add your user to the 'input' group or install the udev rule.",
                err=True,
            )
        for event in listener:
            if json_output:
                typer.echo(json.dumps(event.as_dict()))
            else:
                typer.echo(event.describe())


@app.command("test-output")
def test_output_cmd(
    key: str = typer.Option("KEY_A", help="Key name to tap (evdev ecodes name, e.g. KEY_A)."),
    move: bool = typer.Option(True, help="Move the mouse in a small square."),
    click: str = typer.Option("", help="Mouse button to click: left, right or middle."),
    dry_run: bool = typer.Option(False, help="Record events instead of sending them."),
) -> None:
    """Send test events through a virtual device (move / click / keys)."""
    code = ecodes.ecodes.get(key)
    if not isinstance(code, int):
        typer.echo(f"error: unknown key name {key!r}", err=True)
        raise typer.Exit(2)

    if click and click not in ("left", "right", "middle"):
        typer.echo(f"error: unknown button {click!r} (use left/right/middle)", err=True)
        raise typer.Exit(2)

    if not dry_run:
        ok, hint = uinput_available()
        if not ok:
            typer.echo(f"error: {hint}", err=True)
            raise typer.Exit(1)

    backend = RecordingBackend() if dry_run else None
    with VirtualDevice(backend) as device:
        if move:
            for dx, dy in ((40, 0), (0, 40), (-40, 0), (0, -40)):
                device.mouse_move(dx, dy)
                time.sleep(0.05)
        device.key_tap(code)
        if click:
            device.mouse_click(click)

    if isinstance(backend, RecordingBackend):
        typer.echo(f"dry-run: recorded {len(backend.events)} emissions: {backend.describe()}")
    typer.echo("test-output done")


_DEFAULT_SCREENSHOT = Path("screenshot.png")


@app.command()
def screenshot(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="PNG file to write."),
    ] = _DEFAULT_SCREENSHOT,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Capture backend. 'mock' works everywhere."),
    ] = "mock",
) -> None:
    """Capture one frame and save it as PNG."""
    if backend != "mock":
        typer.echo(f"error: unknown backend {backend!r} (only 'mock' for now)", err=True)
        raise typer.Exit(2)
    capture = MockCaptureBackend()
    frame = capture.grab()
    if not cv2.imwrite(str(output), frame.image):
        typer.echo(f"error: could not write {output}", err=True)
        raise typer.Exit(1)
    typer.echo(f"saved {output} ({frame.width}x{frame.height})")


if __name__ == "__main__":
    app()
