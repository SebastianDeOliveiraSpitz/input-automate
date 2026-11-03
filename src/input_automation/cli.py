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
from input_automation.macros import record_cli
from input_automation.macros.loader import (
    LoadError,
    load_macro,
    load_python_macro,
)
from input_automation.macros.orchestrator import MacroOrchestrator, NullSink
from input_automation.output.virtual import (
    RecordingBackend,
    VirtualDevice,
    uinput_available,
)
from input_automation.vision.primitives import (
    bar_fill_fraction,
    color_fraction,
    region_crop,
    template_match,
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


@app.command()
def record(
    output: Annotated[Path, typer.Option("--output", "-o", help="YAML file to write.")],
    from_events: Annotated[
        Path | None,
        typer.Option(
            help="Convert a JSONL event dump (e.g. from `monitor --json`)"
            " instead of recording live."
        ),
    ] = None,
    trigger: str = typer.Option("F10", help="Key that stops a live recording (e.g. F10)."),
    speed: float = typer.Option(1.0, min=0.1, help="Playback speed factor."),
    min_gap_ms: int = typer.Option(50, min=1, help="Hold threshold in ms."),
    device: Annotated[
        list[str] | None, typer.Option(help="Device paths to record from (default: all).")
    ] = None,
    duration: float | None = typer.Option(
        None, min=0.1, help="Stop recording after N seconds."
    ),
) -> None:
    """Record input events into a replayable macro YAML file.

    Press the --trigger key to stop a live recording. Use --from-events to
    convert a previously captured JSONL event dump instead of a live device.
    """
    try:
        macro = record_cli.record_macro(
            output=output,
            from_events=from_events,
            trigger=trigger,
            speed=speed,
            min_gap_ms=min_gap_ms,
            devices=device,
            duration=duration,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"recorded {macro.name} -> {output} ({len(macro.steps)} steps)")


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
        orchestrator = MacroOrchestrator(sink=NullSink(), dry_run=True)
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


def _parse_color(spec: str) -> tuple[int, int, int]:
    """Parse a ``B,G,R`` color string into a BGR triple."""
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 3 or not all(p.lstrip("-").isdigit() for p in parts):
        raise ValueError(f"color must be B,G,R integers, got {spec!r}")
    values = tuple(int(p) for p in parts)
    if not all(0 <= v <= 255 for v in values):
        raise ValueError(f"color channels must be 0-255, got {spec!r}")
    return values[0], values[1], values[2]


def _bgr_range(
    bgr: tuple[int, int, int], tolerance: int
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Clamp a BGR color +/- per-channel tolerance into an inclusive range."""
    lower = (
        max(0, bgr[0] - tolerance),
        max(0, bgr[1] - tolerance),
        max(0, bgr[2] - tolerance),
    )
    upper = (
        min(255, bgr[0] + tolerance),
        min(255, bgr[1] + tolerance),
        min(255, bgr[2] + tolerance),
    )
    return lower, upper


def _parse_region(spec: str) -> tuple[int, int, int, int] | None:
    """Parse an ``x,y,w,h`` region string; empty means the whole image."""
    if not spec.strip():
        return None
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
        raise ValueError(f"region must be x,y,w,h integers, got {spec!r}")
    return (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))


@app.command()
def analyze(
    image: Annotated[Path, typer.Argument(help="Image file to analyze (BGR).")],
    region: str = typer.Option("", help="Restrict analysis to x,y,w,h."),
    color: str = typer.Option("", help="BGR color to measure (B,G,R)."),
    tolerance: int = typer.Option(
        25, min=0, help="Per-channel tolerance around --color."
    ),
    bar: bool = typer.Option(
        False,
        help="With --color: report a horizontal bar's fill fraction"
        " instead of the pixel fraction.",
    ),
    template: Annotated[
        Path | None,
        typer.Option(help="Locate this template image instead of measuring a color."),
    ] = None,
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Analyze an image: color fraction, bar fill, or template location."""
    if color and template is not None:
        typer.echo("error: --color and --template are mutually exclusive", err=True)
        raise typer.Exit(2)
    if not color and template is None:
        typer.echo("error: give --color or --template", err=True)
        raise typer.Exit(2)
    try:
        region_box = _parse_region(region)
        lower_upper: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None
        if color:
            lower_upper = _bgr_range(_parse_color(color), tolerance)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    source = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if source is None:
        typer.echo(f"error: could not read {image}", err=True)
        raise typer.Exit(1)

    result: dict[str, object]
    if lower_upper is not None:
        target = (
            region_crop(source, *region_box)
            if region_box is not None
            else source
        )
        value = (
            bar_fill_fraction(target, *lower_upper)
            if bar
            else color_fraction(target, *lower_upper)
        )
        mode = "bar" if bar else "color"
        result = {
            "mode": mode,
            "fraction": round(value, 4),
            "color": color,
            "region": list(region_box) if region_box else None,
        }
        typer.echo(json.dumps(result) if json_output else f"{mode}: {value:.4f}")
        return
    assert template is not None
    needle = cv2.imread(str(template), cv2.IMREAD_COLOR)
    if needle is None:
        typer.echo(f"error: could not read template {template}", err=True)
        raise typer.Exit(1)
    try:
        match = template_match(source, needle)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    result = {"mode": "template", "x": match.x, "y": match.y, "score": round(match.score, 4)}
    typer.echo(
        json.dumps(result)
        if json_output
        else f"template: ({match.x}, {match.y}) score={match.score:.4f}"
    )


@app.command()
def gui() -> None:
    """Open the PySide6 desktop GUI."""
    from input_automation.gui import run

    run()


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
