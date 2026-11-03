# TASKS.md

## M1 — Foundation

- [x] T1: Scaffold project: pyproject.toml, src/input_automation layout, pytest/ruff/mypy config, CLI stub (`input-automation --version`), all gates green
- [x] T2: Macro schema: Pydantic models for triggers/actions, YAML loader + validation with clear error messages, round-trip tests
- [x] T3: Device discovery: enumerate /dev/input via evdev, device info API, CLI `list-devices`

## M2 — Input capture & output

- [x] T4: Device listener: evdev event loop, normalized event model (key/mouse/axis), CLI `monitor`
- [x] T5: Virtual output device: uinput wrapper (move/click/scroll/keys), udev rule + permission check, CLI `test-output`
- [x] T6: Trigger engine: event pattern matching (sequences, holds, combos) wired to macro defs, synthetic-event tests

## M3 — Visual layer

- [x] T7: Capture abstraction: Frame model (numpy BGR), CaptureBackend protocol, mock backend with animated bar pattern, CLI `screenshot`
- [x] T7b: PipeWire portal backend: GNOME portal screencast → continuous frames; needs a real GNOME session (mgmt-vm GNOME install or fedora1) — mutter (unattended) flow verified live (pipeline reaches PLAYING, 3/3 repro green); portal (dialog) variant implemented but needs one interactive grant
- [x] T8: OpenCV primitives: region crop, template match, color thresholds, bar-fraction measurement — pure functions + fixture tests
- [x] T9: Visual trigger engine: watch rules (region + condition → trigger), debouncing, wired to trigger engine — color rules with rising-edge firing + per-rule debounce_ms, injectable clock; callback contract matches TriggerEngine (T10 wires both to actions)

## M4 — Macros end-to-end

- [x] T10: Macro orchestrator: step-tree execution (action/if/while/set), dry-run mode, JSONL execution log, repeat replay; MacroRunner binds macro triggers to device events; examples/volume.yaml E2E-tested
- [x] T11: Python escape hatch: MacroBuilder API + load_python_macro; build(m) convention, stem default name, schema validated at build(); load_macros picks up *.py alongside YAML
- [x] T12: Recording & replay: Recorder converts evdev events -> macro (tap/hold inferred from gaps, motion accumulation, click, speed factor); dump_macro YAML round-trip; drag recordings fail loudly (not expressible)
- [x] T13: CLI polish: full surface (`macros list/validate/run`, `devices`, `capture`, `analyze`, `record`), `--json` outputs

## M5 — GUI

- [x] T14: PySide6 app shell: main window, tray, device/macro lists (pytest-qt testable) — MainWindow with injectable providers, NullSink moved to orchestrator, CLI `gui` command; default macro dir ~/.config/input-automate/macros
- [x] T15: Macro editor: YAML ↔ form editor, visual region picker on live frames, test-run button — MacroEditorDialog (YAML pane is source of truth; form regenerates YAML; complex if/while/set steps locked to the YAML pane), RegionPickerDialog — freeze-frame fullscreen overlay: grabs ONE real-screen frame (grab_first_frame: mutter first, mock fallback) then covers the screen, drag → frame-pixel x,y,w,h; editor Save validates + writes, Test run (dry) via NullSink; main window: double-click edits, New macro, Pick region. Note: D-Bus screencast flows run on a thread-default GLib context in a worker thread — Qt's GLib integration owns the default main context, so a background MainLoop on the default context deadlocks signal delivery (fixed by pumping the thread-default context; verified live with Qt running)
- [x] T16: Packaging + docs: README (install, CLI/GUI usage, macro YAML reference incl. schema-validated example, recording, troubleshooting, dev section), desktop entry install/input-automation.desktop installed idempotently by system-setup.sh (step 4), udev rule script pre-existing

## M6 — Visual macro editor (post-v1, decided 2026-11-03)

Concept decision: the declarative macro model (YAML) stays the source of truth;
editors are generators for it. A free-form 2D node graph is out of scope —
macros are control flow (a tree), not dataflow, so wire-routing/auto-layout
would buy nothing. Tile taxonomy maps 1:1 onto the existing schema
(triggers, Action, IfStep/WhileStep/SetStep, WatchRule).

- [x] T17: Tile outline editor: the app's main window opens on the tile tree — palette (Input/Logic/Timer/Output) with drag & drop (custom MIME) + double-click insert; QTreeWidget with Triggers/Steps section rows, if/while rendered with then/else/body branch rows, internal-move reordering + nesting, invalid mutations revert to the last good macro; inline config panel per tile (input tiles: device combo + key code + state); live YAML preview; Save (name follows file stem), Test run (dry), macro file combo, Devices dialog (moved off the main window), Pick region, YAML editor retained for power users; pytest-qt offscreen testable (test_gui_tile_editor.py)
- [ ] T18 (optional, after dogfooding): Flowchart view of the same model — auto-laid-out tile graph on QGraphicsScene (top-down, if-branches fan out, edges computed, never hand-routed). Only if the outline view proves insufficient in real use.
- Explicitly rejected: manual node-graph with free tile placement and wire dragging (high effort, wrong fit for control flow).

## M7 — Always-on macro daemon

The trigger bindings are currently only stored; nothing listens to devices
between manual runs. Basti wants triggers to actually fire macros.

- [ ] T19: Always-on macro daemon: DeviceListener + MacroRunner (both built in T4/T10) wired into a long-running process that arms every macro in ~/.config/input-automate/macros (triggers are OR-combined per macro; any firing macro executes its full step tree), hot-reload on macro file changes, systemd user unit (AGENTS.md deployment), GUI shows armed/stopped state.
