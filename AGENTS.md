# AGENTS.md

## Context

input-automation is an SPS/PLC-style visual automation tool for Linux (Wayland).
Users build logic graphs by dragging function blocks onto a 2D canvas and wiring
their inputs and outputs together, like Siemens S7 function block diagrams. A
pure-Python cyclic scan engine executes the graph: read inputs → evaluate blocks
in dependency order → write outputs, at a fixed cycle time.

Primary target is GNOME on Wayland (Fedora/RHEL); wlroots-based compositors are
best-effort via fallback backends. The GUI runs on the desktop; the same logic
engine runs headless (CLI) for testing and unattended operation.

Blocks read physical inputs (evdev keys/buttons/axes), screen state (PipeWire
capture + OpenCV analysis), and produce physical outputs (uinput virtual
keyboard/mouse) plus internal signals.

## Architecture (three layers)

1. **Logic engine** — pure Python, zero GUI/IO dependency.
   - Block registry with typed pins (bool/int/float/string)
   - Wire graph connecting block pins
   - Cyclic scan executor (topological order, cycle time configurable)
   - Project persistence: YAML/JSON (block definitions + wiring)
2. **IO layer** — evdev input, uinput output, PipeWire portal capture, OpenCV primitives.
   - Blocks declare which IO they need; the engine injects providers.
3. **GUI** — PySide6 `QGraphicsView` 2D canvas (`CanvasWindow`) is the app's main window, with block palette, drag-drop, connection drawing, properties panel, live pin-value monitoring, run/stop. The legacy tile editor has been removed in M5 cleanup.

## Stack

- Python >= 3.12
- PySide6 — GUI (Qt official LGPL binding)
- python-evdev — device reading and event capture
- python-uinput — virtual output devices (mouse/keyboard injection)
- OpenCV (opencv-python) — image analysis for screen-based blocks
- PyYAML + Pydantic — project file schema validation
- Typer — CLI (headless run/validate/export)
- pytest, ruff, mypy — quality gates

## Deployment

- Development on headless mgmt-vm: GUI tests run with `QT_QPA_PLATFORM=offscreen`;
  real-GUI behavior verified on fedora1 (GNOME Wayland).
- `install/system-setup.sh` installs build deps, udev rules for evdev/uinput,
  and the PipeWire/PipeWire portal dependencies. Idempotent, dnf-based.
- Project files live under `~/.config/input-automate/projects/`; runtime writes
  only under `~/.config/input-automate/` — gitignored with `!.gitkeep`.
- Linux-only (Wayland); no Windows/macOS support planned.

## Rules

- Use KISS and DRY.
- Commit and push small changes frequently.
- Verify before every commit: `ruff check .`, `mypy` (configured targets), `pytest`.
- Work one TASKS.md task at a time, in listed order; remove its line when done.
- Logic engine must be headless-testable: no PySide6/Qt imports in engine code,
  mock IO providers for tests.
- Never log raw input device data or keystroke contents; macro/event data may be
  logged in sanitized form only.
- Do not edit `AGENTS.md` (project constitution — read-only).
- `TASKS.md` is the living task list maintained by the dev agent: mark tasks
  done (`- [ ]` → `- [x]`), add new ones, remove obsolete ones each tick.
- No ansible playbooks (that is Basti's separate repo).
