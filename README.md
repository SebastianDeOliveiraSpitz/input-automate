# input-automation

Automate inputs on Linux (Wayland): a pure-Python SPS/PLC logic engine plus a
device- and vision-driven macro tile editor.

Build logic graphs by wiring typed function blocks (AND, OR, NOT, TON timers,
counters, comparators, evdev inputs, uinput outputs, screen capture) — the
engine runs cyclically (read inputs → evaluate in dependency order → write
outputs) and executes headless through the CLI. Separately, the tile editor
builds macros bound to keys/mouse buttons/screen regions that fire through
virtual uinput devices. Macros are declarative YAML, editable by hand, by the
GUI, or by recording your own input.

GNOME on Wayland is the primary target; wlroots-based compositors are
best-effort. Linux only.

## Features

- **Logic engine** — cyclic scan executor, typed pins (bool/int/float/string),
  wire graph with cycle detection, YAML/JSON project files
- **Function blocks** — AND/OR/XOR/NOT, SR flip-flop, TON timer, CTU counter,
  EQ/NE/LT/GT/THRESHOLD comparators, PASS/MUX signal blocks
- **Device IO** — evdev reading (`INPUT` blocks), virtual uinput keyboard/mouse
  output (`OUTPUT` blocks), screen-region measurement (`CAPTURE` blocks)
- **Trigger engine** — single keys, combos, sequences, holds, mouse buttons,
  visual (screen) triggers with debouncing
- **Macro engine** — sequential steps plus `if`/`else`, `while` loops,
  variables, recording & replay
- **GUI** — tile editor with drag & drop, live YAML preview, region picker on
  the real screen, dry-run test, YAML editor for power users
- **Always-on daemon** — arms macros from `~/.config/input-automate/macros/`,
  hot-reloads every 2 s, logs firings to `logs/executions.jsonl`

## Install

Fedora/RHEL (the tool ships its own system setup for uinput access):

```bash
git clone https://github.com/SebastianDeOliveiraSpitz/input-automate
cd input-automate
sudo ./install/system-setup.sh   # build deps, capture runtime deps, uinput udev rule, 'input' group
# log out and back in so the group change applies
pipx install --system-site-packages git+https://github.com/SebastianDeOliveiraSpitz/input-automate@main
# pin to a tagged release instead:
# pipx install --system-site-packages git+https://github.com/SebastianDeOliveiraSpitz/input-automate@v0.1.0
```

`--system-site-packages` is required for the Wayland screen-capture backends:
PyGObject (`gi`) and GStreamer come from the system, not from PyPI. Without
it the CLI/GUI still work, but `capture --backend mutter/portal` and the
region picker fall back to the mock backend.

For development/testing installs use an editable pipx install instead of
re-installing on every pull:

```bash
pipx uninstall input-automation && pipx install --system-site-packages -e .
```

`system-setup.sh` is idempotent — run it once per machine. It installs the
minimal build deps (gcc, matching `python3.X-devel`), loads the `uinput`
kernel module at boot, installs a udev rule making `/dev/uinput` writable by
the `input` group, and adds your user to `input`.

## GUI

```bash
input-automation gui
```

The app opens directly on the **tile editor**. The left palette offers Input
tiles (on key press / mouse button — configurable per device and key in the
panel below the tree), Logic tiles (if / while / set), Output tiles (tap,
press, release, click, move, scroll) and a Timer tile (wait). Drag a tile
onto the tree (or double-click it); `if`/`while` tiles carry `then`, `else`
and `body` branch rows — dropping tiles onto a branch nests them.
The tree can be reordered by dragging rows, the YAML preview on the right
updates live (it is the source of truth), and "Save" writes it back to
`~/.config/input-automate/macros/`.

Toolbar: switch between macro files, **Test run (dry)** validates the
current macro end-to-end without injecting anything, **YAML editor** opens
the raw YAML ↔ form editor, **Devices** lists connected input devices, and
**Pick region** grabs one frame of your real screen (GNOME/mutter backend,
mock fallback) and copies the dragged rectangle as `x,y,w,h` to the
clipboard.

## CLI

```bash
input-automation list-devices                # connected input devices
input-automation monitor                     # watch live events (Ctrl+C to stop)
input-automation test-output                 # wiggle mouse + tap a key virtually
input-automation record -o my-macro.yaml     # record input; F10 stops recording
input-automation macros list [dir]           # macros in a directory (--json)
input-automation macros validate file.yaml   # schema check
input-automation macros run file.yaml [--repeat N] [--dry-run]
input-automation daemon                      # ALWAYS-ON: fire macros from triggers
input-automation capture -o shot.png         # one screen frame (mock/mutter/portal)
input-automation analyze shot.png --color 0,0,255 [--region x,y,w,h] [--bar]
input-automation analyze shot.png --template icon.png
input-automation project new|open|validate|run file.yaml [--cycles N] [--evdev] [--uinput]
```

`analyze` measures what the visual trigger engine sees: fraction of pixels
matching a BGR color (with tolerance), a horizontal bar's fill fraction, or
the location and similarity score of a template image. All commands accept
`--json` where useful. Capturing the real screen needs a GNOME Wayland
session (`--backend mutter` works unattended; `--backend portal` shows the
standard GNOME share dialog); `--backend mock` works everywhere.

`project run` executes an SPS project file headless for `N` scan cycles
(default 1). `--evdev`/`--uinput` wire the engine's INPUT/OUTPUT blocks to
real devices; without them the run is fully mock-safe (inputs keep defaults,
outputs discarded).

## The daemon: triggers that fire automatically

```bash
input-automation daemon      # foreground; Ctrl+C stops
```

The daemon arms **every macro** in `~/.config/input-automate/macros/` and
watches all input devices: when any macro's trigger matches a device event
(triggers within a macro are OR-combined; a trigger's `device:` field
restricts it to that device), the macro's full step tree runs. Macro files
are hot-reloaded every 2 seconds — edit or add macros while it runs, no
restart needed. Every firing is logged to
`~/.config/input-automate/logs/executions.jsonl`.

For an always-on setup, install the systemd user unit (ships with
`system-setup.sh`) and enable it:

```bash
sudo ./install/system-setup.sh          # installs the unit (idempotent)
systemctl --user enable --now input-automation-daemon
journalctl --user -u input-automation-daemon -f
```

Note: triggers are additive — the daemon does **not** grab or consume your
keystrokes. Pressing the trigger key still sends that key to the focused
application *and* fires the macro (key remapping is a different feature).

## Recording

```bash
input-automation record -o snipe.yaml        # press F10 (default) to stop
input-automation macros run snipe.yaml --repeat 3
```

The recorder converts raw events into clean steps: quick taps stay `tap`,
long holds become `press`/`wait`/`release`, mouse motion accumulates,
wheels de-duplicate. Playback speed is adjustable (`--speed 2.0` = twice as
fast). Mouse drags (button held while moving) are deliberately not
recordable as linear macros — use a Python macro for those.

## Macros

YAML files in `~/.config/input-automate/macros/` are the source of truth;
the GUI reads and writes them. Minimal example:

```yaml
name: heal
description: heal when the health bar drops
triggers:
  - kind: key          # key | button
    device: any        # or a specific /dev/input device path
    code: F10          # evdev key name without KEY_ prefix
    state: down        # down | up
steps:
  - action: { op: wait, ms: 500 }
  - action: { op: tap, target: H }
  - if:
      condition: { var: hp, less_than: 5 }
      then:
        - action: { op: press, target: Q }
        - action: { op: wait, ms: 200 }
        - action: { op: release, target: Q }
      else:
        - action: { op: scroll, dy: -1 }
  - set: { var: hp, value: 10 }
```

Steps can be written in the shorthand `action:`/`if:`/`while:`/`set:` form
(shown above) or the explicit `kind: action` tagged form — both load
identically. Ops: `tap`, `press`, `release` (target = key name), `click`
(button = 1 left / 2 middle / 3 right), `move` (dx/dy relative), `scroll`
(dy), `wait` (ms). Validate any file with `input-automation macros validate`.

**Python escape hatch:** a `*.py` file next to the YAML macros defines
`build(builder)` using the `MacroBuilder` API for anything the schema can't
express (drags, dynamic logic). Macros are hot-reloaded from the directory.
**Loading arbitrary Python from your macro directory executes that code with
your user's privileges — only put files there that you wrote or trust.**

## Logic engine: block reference & wiring examples

The engine layer (`input_automation.engine`, CLI `project ...`) is a cyclic
scan executor: each cycle reads inputs, evaluates every block once in
topological (dependency) order, then writes outputs. Cycle time is
configurable (default 20 ms); if a cycle overruns, the next starts
immediately. Pins are typed (bool/int/float/string) and wired output → input;
a wire graph containing a cycle is rejected at load time.

IO blocks never open devices themselves — an injectable provider drives them,
which keeps the engine headless-testable:

| provider | blocks it drives | real devices |
|---|---|---|
| `EvdevInputProvider` | `INPUT` | `/dev/input/event*` via evdev |
| `UinputOutputProvider` | `OUTPUT` | virtual uinput keyboard/mouse |
| `CaptureInputProvider` | `CAPTURE` | one screen frame per cycle (mutter/portal/mock) |

Mock providers (`MockEvdevInputProvider`, `MockUinputOutputProvider`,
`MockCaptureBackend`) replace the real ones in tests — no `/dev/input`,
`/dev/uinput`, or display needed.

### Function blocks

**Combinational**

| block | inputs | outputs | behavior |
|---|---|---|---|
| `AND` | IN1, IN2 (bool) | Q (bool) | Q true when both inputs are true |
| `OR` | IN1, IN2 (bool) | Q (bool) | Q true when at least one input is true |
| `XOR` | IN1, IN2 (bool) | Q (bool) | Q true when exactly one input is true |
| `NOT` | IN (bool) | Q (bool) | Q is the inverse of IN |

**Sequential**

| block | inputs | outputs | behavior |
|---|---|---|---|
| `SR` | S, R (bool) | Q (bool) | SET/RESET flip-flop; reset wins when both are true; Q holds otherwise; starts false |
| `TON` | IN (bool), PT (float, default 1.0 s), DT (float, default 0.0) | Q (bool), ET (float) | on-delay timer: while IN is true, elapsed accumulates by DT each cycle; Q true once ET >= PT; unwired DT never advances |
| `CTU` | CU (bool), R (bool), PV (int, default 0) | Q (bool), CV (int) | up counter: CV increments on rising edges of CU only; R resets; Q true while CV >= PV |

**Compare** (unwired input → Q false)

| block | inputs | outputs | behavior |
|---|---|---|---|
| `EQ` | IN1, IN2 (float) | Q (bool) | Q = IN1 == IN2 |
| `NE` | IN1, IN2 (float) | Q (bool) | Q = IN1 != IN2 |
| `LT` | IN1, IN2 (float) | Q (bool) | Q = IN1 < IN2 |
| `GT` | IN1, IN2 (float) | Q (bool) | Q = IN1 > IN2 |
| `THRESHOLD` | IN (float), LIMIT (float) | Q (bool) | Q = IN >= LIMIT |

**Signal**

| block | inputs | outputs | behavior |
|---|---|---|---|
| `PASS` | IN (float) | Q (float) | Q follows IN (buffer / pass-through) |
| `MUX` | SEL (bool), IN1, IN2 (float) | Q (float) | Q = IN2 while SEL is true, otherwise IN1 |

**IO blocks**

| block | inputs | outputs | behavior |
|---|---|---|---|
| `INPUT` | DEVICE (string), CODE (string) | Q (bool), VALUE (int) | evdev state: CODE is a name like `KEY_Q`, `BTN_LEFT`, `ABS_X` (short `q` works too); Q true while down/nonzero, VALUE is the raw evdev value |
| `OUTPUT` | DEVICE (string), CODE (string), Q (bool), VALUE (int, default 1) | — | emits one evdev event per changed value through virtual device `DEVICE` (keys/buttons: 1/0; axes: raw VALUE) |
| `CAPTURE` | REGION (`x,y,w,h`, default whole frame), MODE (`color`\|`threshold`, default `color`), COLOR (`b,g,r`), TOLERANCE (int, default 40), LIMIT (float, default 0.5) | VALUE (float), Q (bool) | VALUE = measured fraction in [0, 1]; Q true while VALUE >= LIMIT |

Every block above registers under its type name in the default registry, so a
project file can reference types `AND`, `TON`, `INPUT`, etc. directly.

### Wiring examples

Project files live under `~/.config/input-automate/projects/` and use the
schema `blocks` + `wires` (`src_block.src_pin → dst_block.dst_pin`). Pin
defaults come from the schema; `input-automation project validate file.yaml`
checks a file before running.

**1. One key press → one virtual key tap**

`/dev/input/event0`'s Q key drives virtual keyboard `out-keyboard`'s H key:

```yaml
name: q-to-h
blocks:
  - id: src
    type: INPUT
    pins:
      - { name: DEVICE, default: /dev/input/event0 }
      - { name: CODE, default: KEY_Q }
  - id: dst
    type: OUTPUT
    pins:
      - { name: DEVICE, default: out-keyboard }
      - { name: CODE, default: KEY_H }
      - { name: Q }
wires:
  - { src_block: src, src_pin: Q, dst_block: dst, dst_pin: Q }
```

Hold Q on the real keyboard → H is injected; release Q → H released.

**2. Two-key combination (AND gate)**

Both Q and W held → inject H:

```yaml
name: q-and-w-to-h
blocks:
  - id: q,   type: INPUT, pins: [{name: DEVICE, default: /dev/input/event0}, {name: CODE, default: KEY_Q}]
  - id: w,   type: INPUT, pins: [{name: DEVICE, default: /dev/input/event0}, {name: CODE, default: KEY_W}]
  - id: gate, type: AND
  - id: dst, type: OUTPUT, pins: [{name: DEVICE, default: out-keyboard}, {name: CODE, default: KEY_H}, {name: Q}]
wires:
  - { src_block: q,   src_pin: Q, dst_block: gate, dst_pin: IN1 }
  - { src_block: w,   src_pin: Q, dst_block: gate, dst_pin: IN2 }
  - { src_block: gate, src_pin: Q, dst_block: dst, dst_pin: Q }
```

**3. Hold for 2 seconds, then act (TON timer)**

IN wired from an INPUT block; PT = 2.0 s. DT is the seconds since the last
scan cycle — unwired it stays 0.0 so the timer never advances on its own; a
`read_inputs` provider writes DT each cycle (or wire it from a clock block).
Q goes true once the key has been held for 2 s:

```yaml
name: hold-2s
blocks:
  - id: src, type: INPUT, pins: [{name: DEVICE, default: /dev/input/event0}, {name: CODE, default: KEY_Q}]
  - id: ton, type: TON, pins: [{name: PT, default: 2.0}]
  - id: dst, type: OUTPUT, pins: [{name: DEVICE, default: out-keyboard}, {name: CODE, default: KEY_F12}, {name: Q}]
wires:
  - { src_block: src, src_pin: Q, dst_block: ton, dst_pin: IN }
  - { src_block: ton, src_pin: Q, dst_block: dst, dst_pin: Q }
```

**4. Nth press fires (CTU counter)**

Every press of Q increments the counter; on the 5th press H is injected once:

```yaml
name: fifth-press
blocks:
  - id: src, type: INPUT, pins: [{name: DEVICE, default: /dev/input/event0}, {name: CODE, default: KEY_Q}]
  - id: ctu, type: CTU, pins: [{name: PV, default: 5}]
  - id: dst, type: OUTPUT, pins: [{name: DEVICE, default: out-keyboard}, {name: CODE, default: KEY_H}, {name: Q}]
wires:
  - { src_block: src, src_pin: Q, dst_block: ctu, dst_pin: CU }
  - { src_block: ctu, src_pin: Q, dst_block: dst, dst_pin: Q }
```

Note: CTU counts rising edges, so holding Q down counts once; release and
press again for the next count.

**5. Screen region color → virtual key (CAPTURE)**

Measure a region of the captured frame each cycle; if the share of matching
pixels exceeds the limit, tap H:

```yaml
name: red-pixel-alert
blocks:
  - id: cap, type: CAPTURE, pins: [{name: REGION, default: 100,100,200,150}, {name: COLOR, default: 0,0,255}, {name: LIMIT, default: 0.3}]
  - id: dst, type: OUTPUT, pins: [{name: DEVICE, default: out-keyboard}, {name: CODE, default: KEY_H}, {name: Q}]
wires:
  - { src_block: cap, src_pin: Q, dst_block: dst, dst_pin: Q }
```

Use `input-automation capture -o shot.png --backend mutter` to grab a real
frame and `input-automation analyze shot.png --color 0,0,255 --region
100,100,200,150` to tune REGION/COLOR/TOLERANCE.

### Running headless

```bash
input-automation project run q-to-h.yaml --cycles 1 --evdev --uinput
```

Equivalent in Python (mock IO keeps it hardware-free):

```python
from input_automation.project import load_project
from input_automation.project.runner import run_project
from input_automation.io.mock import (
    MockEvdevInputProvider,
    MockUinputOutputProvider,
)

project = load_project("q-to-h.yaml")
run_project(
    project,
    cycles=1,
    read_inputs=MockEvdevInputProvider(),
    write_outputs=MockUinputOutputProvider(),
)
```

With real devices pass `EvdevInputProvider()` / `UinputOutputProvider()`
instead; both are closed automatically, and teardown errors from a
misbehaving device are suppressed so a scan cycle never aborts.

## Troubleshooting

- **"device(s) could not be opened"** — you're not in the `input` group:
  run `system-setup.sh`, then re-login.
- **"/dev/uinput is not writable"** — the udev rule from `system-setup.sh`
  is missing, or the `uinput` module isn't loaded: re-run the script,
  re-login.
- **Capture fails on non-GNOME compositors** — only the mock backend is
  guaranteed there; GNOME (Wayland) is the supported target.
- **Recording misses keys** — check `list-devices` shows your device and
  `monitor` prints its events; if not, it's the `input` group again.
- **"0 macros armed" despite a valid trigger file** — trigger codes accept
  bare and prefixed forms (`KEY_Q` and `q` both resolve to `ecodes.KEY_Q`);
  an unknown name silently skips the macro. Check `logs/executions.jsonl`.

## Development

```bash
pip install -e ".[dev]"       # or use the repo .venv
ruff check . && mypy src && pytest
```

GUI tests run headless with `QT_QPA_PLATFORM=offscreen` (pytest-qt).
`AGENTS.md` documents architecture decisions and repo rules; `TASKS.md`
tracks the milestone plan.

## License

Apache-2.0 — see [LICENSE](LICENSE).
