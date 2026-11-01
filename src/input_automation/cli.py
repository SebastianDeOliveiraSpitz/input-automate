"""CLI entry point for input-automation."""

import json
from pathlib import Path

import typer

from input_automation import __version__
from input_automation.devices.discovery import list_devices

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


if __name__ == "__main__":
    app()
