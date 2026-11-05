# TASKS.md

## M0 — Project rework foundation
- [x] R0: New project layout: `src/input_automation/{engine, io, gui, cli, project}`; keep pytest/ruff/mypy config; all gates green on empty scaffold
- [x] R1: Pydantic project schema: blocks, pins, wires, project document, round-trip YAML/JSON tests
- [x] R2: CLI stub: `input-automation --version`, `input-automation project new|open|validate|run`

## M1 — Pure Python logic engine (SPS core)
- [x] T1: Block base class + pin model (input/output, typed), block registry
- [x] T2: Wire graph: add/remove connections, cycle detection, topological sort
- [x] T3: Cyclic scan executor: read inputs → execute blocks → write outputs, configurable cycle time, deterministic
- [x] T4: Basic function blocks: AND, OR, NOT, XOR, SET/RESET (flip-flop), TON timer, CTU counter
- [x] T5: Compare blocks: `==`, `!=`, `<`, `>`, threshold; pass-through/mux
- [x] T6: Project load/save round-trip via YAML; headless `run` executes a project file and exits

## M2 — IO layer
- [x] T7: evdev input block: read device key/button/axis state into a pin
- [x] T8: uinput output block: write pin state to virtual keyboard/mouse output
- [x] T9: Screen capture block: PipeWire portal frame → OpenCV region measurement (color/threshold) → pin
- [x] T10: Mock IO providers for headless testing (no real devices needed)

## M3 — GUI canvas (SPS editor)
- [x] T11: PySide6 `QGraphicsView` 2D canvas; block palette (draggable function blocks)
- [x] T12: Drag blocks onto canvas, position freely; block properties panel (edit parameters)
- [x] T13: Draw connections between output pins and input pins; visual wires; delete/rewire
- [x] T14: Run/stop toggle; live pin-value monitoring on the canvas (show true/false or numbers on pins)
- [x] T15: Save/open project files from the GUI; recent projects

## M5 — Legacy cleanup (remove redundant old code)
- [ ] C1: Remove legacy tile editor GUI (gui/tile_editor.py, gui/editor.py, gui/region_picker.py) — replaced by gui/canvas.py
- [ ] C2: Remove legacy macro system (macros/ directory) — replaced by engine/ + project/
- [ ] C3: Remove legacy daemon (daemon.py) — replaced by engine/scan.py
- [ ] C4: Remove legacy capture/devices/output/triggers/vision modules — replaced by io/ + engine/
- [ ] C5: Remove legacy CLI macro/device/capture/record commands — replaced by cli project commands
- [ ] C6: Remove legacy tests for removed modules — keep tests/ for new code only

## M4 — Integration & polish
- [x] T16: End-to-end: GUI-built project → headless engine runs it with real evdev/uinput; SPS canvas wired as default `gui()` entry point
- [x] T17: README: install, GUI usage, block reference with wiring examples, troubleshooting, dev section
- [x] T18: Packaging: pipx-installable from GitHub release tags; system-setup.sh idempotent
- [ ] Create GitHub release for tag v0.1.1 (needs `gh auth login`, then `gh release create v0.1.1`)

## Post-v1 (optional)
- [ ] Ladder-logic view alternative to FBD
- [ ] Block library export/import (share projects)
- [ ] Online edit (rewire while running)
