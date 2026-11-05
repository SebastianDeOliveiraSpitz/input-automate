# TASKS.md — ARCHIVED BACKLOG REFERENCE

**GitHub issues are the single source of truth (SSOT) for this project.**
This file is kept only as a historical record of the M0–M5 rework tasks.
New work goes in GitHub issues with `type:*`, `priority:*`, and `status:*` labels.

## M0 — Project rework foundation (complete)
- [x] R0: New project layout
- [x] R1: Pydantic project schema
- [x] R2: CLI stub

## M1 — Pure Python logic engine (SPS core) (complete)
- [x] T1–T6: Block base, wire graph, scan executor, function blocks, compare blocks, project run

## M2 — IO layer (complete)
- [x] T7–T10: evdev input, uinput output, screen capture, mock IO providers

## M3 — GUI canvas (SPS editor) (complete)
- [x] T11–T15: QGraphicsView canvas, drag-drop, properties panel, wires, run/stop, live monitoring, save/open

## M4 — Integration & polish (complete)
- [x] T16: End-to-end; SPS canvas as default `gui()` entry point
- [x] T17: README
- [x] T18: Packaging

## M5 — Legacy cleanup (pending, convert to issues)
- [x] C1: Remove legacy tile editor GUI
- [ ] C2: Remove legacy macro system
- [ ] C3: Remove legacy daemon
- [ ] C4: Remove legacy capture/devices/output/triggers/vision modules
- [ ] C5: Remove legacy CLI commands
- [ ] C6: Remove legacy tests for removed modules

Create GitHub issues for C1–C6 if you want the agent to complete them.
