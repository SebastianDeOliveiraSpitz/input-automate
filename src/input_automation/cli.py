"""CLI entry point for input-automation."""

import json
import time
from pathlib import Path
from typing import Annotated, Any, cast

import cv2
import typer
from evdev import ecodes

from input_automation import __version__
from input_automation.capture.factory import BACKEND_NAMES, create_backend
from input_automation.devices.discovery import list_devices
from input_automation.devices.listener import DeviceListener
from input_automation.macros.loader import (
    LoadError,
    load_macro,
    load_python_macro,
)
from input_automation.macros.orchestrator import MacroOrchestrator
from input_automation.output.virtual import (
    RecordingBackend,
    VirtualDevice,
    uinput_available,
)

app = typer.Typer(help="Automate inputs on Linux (Wayland).")
macros_app = typer.Typer(help="List, validate, and run macros.")
app.add_typer(macros_app, name="macros")


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


_DEFAULT_MACRO_DIR = Path("macros")
_DEFAULT_SCREENSHOT = Path("screenshot.png")


class _NullSink:
    """No-op output sink for --dry-run (orchestrator never touches it)."""

    def key_tap(self, code: int) -> None:
        del code

    def key_press(self, code: int) -> None:
        del code

    def key_release(self, code: int) -> None:
        del code

    def mouse_move(self, dx: int, dy: int) -> None:
        del dx, dy

    def mouse_click(self, button: str) -> None:
        del button

    def scroll(self, dy: int) -> None:
        del dy


def _load_macro_by_suffix(path: Path) -> Any:
    """Dispatch to the YAML or Python macro loader by file suffix."""
    if path.suffix in (".yaml", ".yml"):
        return load_macro(path)
    return load_python_macro(path)


@macros_app.command("list")
def macros_list(
    directory: Annotated[
        Path, typer.Argument(help="Macro directory.")
    ] = _DEFAULT_MACRO_DIR,
    json_output: bool = typer.Option(False, "--json", help="Output JSON array."),
) -> None:
    """List macros in a directory (yaml and python)."""
    macros: list[dict[str, object]] = []
    warnings: list[str] = []
    for pattern in ("*.yaml", "*.yml", "*.py"):
        for path in sorted(directory.glob(pattern)):
            try:
                if path.suffix in (".yaml", ".yml"):
                    macro = load_macro(path)
                else:
                    macro = load_python_macro(path)
            except (LoadError, FileNotFoundError) as exc:
                warnings.append(f"warning: {path.name}: {exc}")
                continue
            macros.append(
                {
                    "file": path.name,
                    "name": macro.name,
                    "description": macro.description,
                    "triggers": [str(t.kind) for t in macro.triggers],
                }
            )
    if json_output:
        typer.echo(json.dumps(macros, indent=2))
        return
    if not macros:
        typer.echo(f"no macros found in {directory}")
    else:
        for entry in macros:
            trigger_kinds = cast("list[str]", entry["triggers"])
            triggers = ",".join(trigger_kinds)
            typer.echo(
                f"{entry['file']}: {entry['name']} [{triggers}] {entry['description']}"
            )
    for warning in warnings:
        typer.echo(warning, err=True)


@macros_app.command("validate")
def macros_validate(
    path: Annotated[Path, typer.Argument(help="Macro file to validate.")],
) -> None:
    """Validate one macro file against the schema."""
    try:
        macro = _load_macro_by_suffix(path)
        typer.echo(f"{path.name}: ok ({macro.name})")
    except (LoadError, FileNotFoundError) as exc:
        typer.echo(f"{path.name}: invalid macro: {exc}", err=True)
        raise typer.Exit(1) from None


@macros_app.command("run")
def macros_run(
    path: Annotated[Path, typer.Argument(help="Macro file to run.")],
    dry_run: bool = typer.Option(False, "--dry-run", help="Walk the tree without injecting."),
    repeat: int = typer.Option(1, "--repeat", min=1, help="How often to run."),
) -> None:
    """Run a macro once (or --repeat N times)."""
    try:
        macro = _load_macro_by_suffix(path)
    except (LoadError, FileNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    if dry_run:
        orchestrator = MacroOrchestrator(sink=_NullSink(), dry_run=True)
        orchestrator.run(macro, repeat=repeat)
        typer.echo(f"dry run: {macro.name} executed without injecting")
        return
    if not uinput_available():
        typer.echo(
            "error: /dev/uinput is not writable — run install/system-setup.sh",
            err=True,
        )
        raise typer.Exit(1)
    with VirtualDevice() as device:
        orchestrator = MacroOrchestrator(sink=device)
        orchestrator.run(macro, repeat=repeat)
    typer.echo(f"ran {macro.name} x{repeat}")


def _capture_one_frame(backend_name: str, output: Path, json_output: bool) -> None:
    """Grab a single frame from a capture backend and write it as PNG."""
    try:
        backend = create_backend(backend_name)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    try:
        backend.start()
        frame = backend.grab()
    finally:
        backend.stop()
    if not cv2.imwrite(str(output), frame.image):
        typer.echo(f"error: could not write {output}", err=True)
        raise typer.Exit(1)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "path": str(output),
                    "width": frame.width,
                    "height": frame.height,
                    "backend": backend_name,
                }
            )
        )
    else:
        typer.echo(f"saved {output} ({frame.width}x{frame.height})")


@app.command("capture")
@app.command("screenshot", hidden=True)
def capture(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="PNG file to write."),
    ] = _DEFAULT_SCREENSHOT,
    backend: Annotated[
        str,
        typer.Option(
            "--backend",
            help=f"Capture backend ({', '.join(BACKEND_NAMES)}). 'mock' works everywhere.",
        ),
    ] = "mock",
    json_output: bool = typer.Option(False, "--json", help="Print metadata as JSON."),
) -> None:
    """Capture one frame and save it as PNG."""
    _capture_one_frame(backend, output, json_output)


if __name__ == "__main__":
    app()
