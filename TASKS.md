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
- [ ] T5: Compare blocks: `==`, `!=`, `<`, `>`, threshold; pass-through/mux
- [ ] T6: Project load/save round-trip via YAML; headless `run` executes a project file and exits

## M2 — IO layer
- [ ] T7: evdev input block: read device key/button/axis state into a pin
- [ ] T8: uinput output block: write pin state to virtual keyboard/mouse output
- [ ] T9: Screen capture block: PipeWire portal frame → OpenCV region measurement (color/threshold) → pin
- [ ] T10: Mock IO providers for headless testing (no real devices needed)

## M3 — GUI canvas (SPS editor)
- [ ] T11: PySide6 `QGraphicsView` 2D canvas; block palette (draggable function blocks)
- [ ] T12: Drag blocks onto canvas, position freely; block properties panel (edit parameters)
- [ ] T13: Draw connections between output pins and input pins; visual wires; delete/rewire
- [ ] T14: Run/stop toggle; live pin-value monitoring on the canvas (show true/false or numbers on pins)
- [ ] T15: Save/open project files from the GUI; recent projects

## M4 — Integration & polish
- [ ] T16: End-to-end: GUI-built project → headless engine runs it with real evdev/uinput
- [ ] T17: README: install, GUI usage, block reference with wiring examples, troubleshooting, dev section
- [ ] T18: Packaging: pipx-installable from GitHub release tags; system-setup.sh idempotent

## Post-v1 (optional)
- [ ] Ladder-logic view alternative to FBD
- [ ] Block library export/import (share projects)
- [ ] Online edit (rewire while running)
