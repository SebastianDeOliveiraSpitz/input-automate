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
- [ ] T9: Visual trigger engine: watch rules (region + condition → trigger), debouncing, wired to trigger engine

## M4 — Macros end-to-end

- [ ] T10: Macro orchestrator: bind device/visual triggers to macro actions (sequences, delays, loops, conditionals), dry-run mode, JSONL execution log under logs/, example YAML macro
- [ ] T11: Python escape hatch: macro scripts as Python modules with a small API
- [ ] T12: Recording & replay: capture evdev events into a recorded macro, replay through the orchestrator (timing, speed factor, loop)
- [ ] T13: CLI polish: full surface (`macros list/validate/run`, `devices`, `capture`, `analyze`, `record`), `--json` outputs

## M5 — GUI

- [ ] T14: PySide6 app shell: main window, tray, device/macro lists (pytest-qt testable)
- [ ] T15: Macro editor: YAML ↔ form editor, visual region picker on live frames, test-run button
- [ ] T16: Packaging + docs: README, udev rule install script, desktop entry, usage docs
