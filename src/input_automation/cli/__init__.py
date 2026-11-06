"""CLI entry point for input-automation."""

import contextlib
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Annotated

import cv2
import typer
from evdev import ecodes

from input_automation import __version__
from input_automation.capture.base import CaptureError
from input_automation.capture.factory import BACKEND_NAMES, create_backend
from input_automation.devices.discovery import list_devices
from input_automation.devices.listener import DeviceListener
from input_automation.engine import ScanExecutor
from input_automation.engine.scan import InputReader, OutputWriter
from input_automation.io.evdev import EvdevInputProvider
from input_automation.io.uinput import UinputOutputProvider
from input_automation.output.virtual import (
    RecordingBackend,
    VirtualDevice,
    uinput_available,
)
from input_automation.project import Project, load_project, save_project
from input_automation.project.runner import (
    ProjectRunError,
    build_graph,
    run_project,
)
from input_automation.vision.primitives import (
    bar_fill_fraction,
    color_fraction,
    region_crop,
    template_match,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Automate inputs on Linux (Wayland).")

project_app = typer.Typer(
    help="Create, inspect, validate, and run SPS project files (.yaml/.yml/.json)."
)
app.add_typer(project_app, name="project")


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
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable debug logging to console (stderr) for troubleshooting.",
    ),
) -> None:
    """Automate inputs on Linux (Wayland) with device- and vision-driven macros."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )


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


@project_app.command("new")
def project_new(
    path: Annotated[
        Path,
        typer.Argument(help="Project file to create (.yaml/.yml/.json)."),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="Project name (default: derived from the file name)."),
    ] = None,
) -> None:
    """Create a new empty project file."""
    project_name = name or path.stem
    try:
        project = Project(name=project_name)
    except ValueError as exc:
        typer.echo(
            f"error: invalid project name {project_name!r}: {exc}", err=True
        )
        raise typer.Exit(2) from None
    if path.exists():
        typer.echo(f"error: {path} already exists", err=True)
        raise typer.Exit(1)
    try:
        save_project(project, path)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"created {path} (project {project_name!r})")


@project_app.command("open")
def project_open(
    path: Annotated[Path, typer.Argument(help="Project file to open.")],
) -> None:
    """Load a project file and print a summary of blocks and wires."""
    try:
        project = load_project(path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"error: {path}: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{path}: project {project.name!r} (v{project.version})")
    typer.echo(f"  blocks: {len(project.blocks)}")
    for block in project.blocks:
        pins = ", ".join(
            f"{pin.name}:{pin.dtype.value} ({pin.direction.value})"
            for pin in block.pins
        ) or "-"
        typer.echo(f"  - {block.id} ({block.type}): {pins}")
    typer.echo(f"  wires: {len(project.wires)}")
    for wire in project.wires:
        typer.echo(
            f"  - {wire.src_block}.{wire.src_pin} -> "
            f"{wire.dst_block}.{wire.dst_pin}"
        )


@project_app.command("validate")
def project_validate(
    path: Annotated[Path, typer.Argument(help="Project file to validate.")],
) -> None:
    """Validate a project file against the schema."""
    try:
        project = load_project(path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"{path}: invalid project: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"{path}: ok ({project.name}, "
        f"{len(project.blocks)} blocks, {len(project.wires)} wires)"
    )


@project_app.command("run")
def project_run(
    path: Annotated[Path, typer.Argument(help="Project file to run headless.")],
    cycles: Annotated[
        int,
        typer.Option(
            "--cycles",
            min=1,
            help="Number of scan cycles to execute before exiting. "
            "Ignored when --forever is given.",
        ),
    ] = 1,
    forever: Annotated[
        bool,
        typer.Option(
            "--forever",
            "-f",
            help="Run as a background scan engine: repeat cycles at the fixed "
            "cycle time until interrupted (Ctrl+C). With --evdev/--uinput the "
            "engine reads real devices and writes to virtual uinput devices.",
        ),
    ] = False,
    evdev: Annotated[
        bool,
        typer.Option(
            "--evdev",
            help="Read INPUT block state from real evdev devices "
            "(DEVICE/CODE pins of each INPUT block).",
        ),
    ] = False,
    uinput: Annotated[
        bool,
        typer.Option(
            "--uinput",
            help="Write OUTPUT blocks to real uinput virtual devices "
            "(DEVICE/CODE pins of each OUTPUT block).",
        ),
    ] = False,
) -> None:
    """Headless run: load the project and execute it through the scan engine.

    By default the run is one-shot: execute ``--cycles`` scan cycles (default 1)
    and exit, printing resulting output pin values. Without --evdev/--uinput the
    run is purely combinational (inputs keep defaults, outputs discarded), safe
    on any box.

    With --forever the engine runs as a background scan engine: cycles repeat at
    the fixed cycle time until interrupted, reading evdev inputs and writing
    uinput outputs when those flags are given. Use --verbose to log every cycle.
    """
    try:
        project = load_project(path)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"error: {path}: {exc}", err=True)
        raise typer.Exit(1) from None
    if forever and cycles != 1:
        typer.echo(
            "error: --forever and --cycles are mutually exclusive", err=True
        )
        raise typer.Exit(1)
    read_inputs: EvdevInputProvider | None = EvdevInputProvider() if evdev else None
    write_outputs: UinputOutputProvider | None = UinputOutputProvider() if uinput else None
    try:
        if forever:
            _run_forever(project, read_inputs, write_outputs)
            return
        results = run_project(
            project,
            cycles=cycles,
            read_inputs=read_inputs,
            write_outputs=write_outputs,
        )
    except ProjectRunError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(1) from None
    finally:
        for provider in (read_inputs, write_outputs):
            if provider is not None:
                with contextlib.suppress(Exception):
                    provider.close()
    if not forever:
        typer.echo(
            f"project {project.name!r}: {len(project.blocks)} blocks, "
            f"{len(project.wires)} wires — ran {cycles} cycle(s)"
        )
        for block_id in sorted(results):
            outputs = results[block_id]
            if not outputs:
                continue
            pin_values = ", ".join(
                f"{name}={value!r}" for name, value in outputs.items()
            )
            typer.echo(f"  {block_id}: {pin_values}")


def _run_forever(
    project: Project,
    read_inputs: InputReader | None,
    write_outputs: OutputWriter | None,
) -> None:
    """Run ``project`` as a background scan engine until interrupted.

    Repeats scan cycles at the executor's fixed cycle time: each cycle reads
    inputs (evdev when wired), evaluates every block in dependency order, and
    writes outputs (uinput when wired). Every cycle is logged at DEBUG level
    (visible with --verbose). Ctrl+C stops the engine gracefully.
    """
    graph = build_graph(project)
    executor = ScanExecutor(
        graph,
        read_inputs=read_inputs,
        write_outputs=write_outputs,
    )
    stop_event = threading.Event()
    cycle = 0
    typer.echo(
        f"project {project.name!r}: background scan engine running "
        f"(Ctrl+C to stop)..."
    )
    try:
        while not stop_event.is_set():
            cycle += 1
            elapsed = executor.run_once()
            logger.debug("scan cycle %d in %.4fs", cycle, elapsed)
            remaining = executor.cycle_time_s - elapsed
            if remaining > 0:
                stop_event.wait(remaining)
    except KeyboardInterrupt:
        stop_event.set()
    typer.echo(
        f"project {project.name!r}: scan engine stopped after {cycle} cycle(s)"
    )


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
    """Open the SPS/PLC visual programming GUI (drag-and-drop canvas)."""
    from input_automation.gui.canvas import run

    run()





def _capture_one_frame(backend_name: str, output: Path, json_output: bool) -> None:
    """Grab a single frame from a capture backend and write it as PNG."""
    try:
        backend = create_backend(backend_name)
    except (ValueError, ModuleNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    try:
        frame = backend.grab()  # manages its own start/stop lifecycle
    except (CaptureError, RuntimeError, ModuleNotFoundError) as exc:
        # mutter/portal need a GNOME session + system GStreamer/PyGObject
        typer.echo(f"error: capture backend {backend_name!r} failed: {exc}", err=True)
        raise typer.Exit(1) from None
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
