# input-automation

Automate inputs on Linux (Wayland) with device- and vision-driven macros.

Bind macros to your physical input devices (keyboard, mouse, gamepads), or
fire them from screen events (pixel colors in a region, template matches).
Output is injected through virtual uinput devices, so games and applications
receive it as if it came from real hardware. Macros are declarative YAML
files — editable by hand, by the desktop GUI, or by recording your own
input and replaying it.

GNOME on Wayland is the primary target; wlroots-based compositors are
best-effort. Linux only.

## Features

- **Device capture** — raw evdev reading of `/dev/input`, system-wide
- **Output injection** — virtual uinput keyboard/mouse (keys, clicks, moves, scroll)
- **Trigger engine** — single keys, combos, sequences, holds, mouse buttons
- **Macro engine** — sequential steps plus `if`/`else`, `while` loops and variables
- **Recording & replay** — record your input, replay at original or faster speed
- **Screen analysis** — GNOME screencast capture (PipeWire), OpenCV region/color
  analysis, template matching, progress-bar measurement
- **Visual triggers** — fire on rising-edge color conditions inside a screen
  region, with debouncing
- **GUI** — tile editor (drag & drop macro building), visual region picker on
  the real screen, dry-run testing, YAML editor for power users
- **Python escape hatch** — plain `.py` macro files for logic the schema can't express

## Install

Fedora/RHEL (the tool ships its own system setup for uinput access):

```bash
git clone https://github.com/SebastianDeOliveiraSpitz/input-automate
cd input-automate
sudo ./install/system-setup.sh      # build deps, capture runtime deps, uinput udev rule, 'input' group
# log out and back in so the group change applies
pipx install --system-site-packages git+https://github.com/SebastianDeOliveiraSpitz/input-automate@main
```

`--system-site-packages` is required for the Wayland screen-capture backends:
PyGObject (`gi`) and GStreamer come from the system, not from PyPI. Without
it the CLI/GUI still work, but `capture --backend mutter/portal` and the
region picker fall back to the mock backend.

For development/testing installs use an editable pipx install instead of
re-installing on every pull:

```bash
pipx install -e .
```

`system-setup.sh` is idempotent — run it once per machine. It installs the
minimal build deps (gcc, matching `python3.X-devel`, not the whole dev tools
group), loads the `uinput` kernel module at boot, installs a udev rule making
`/dev/uinput` writable by the `input` group, and adds your user to `input`.

## Usage

### GUI

```bash
input-automation gui
```

The app opens directly on the **tile editor**. The left palette offers
Input tiles (on key press / mouse button — configurable per device and key
in the panel below the tree), Logic tiles (if / while / set), Output tiles
(tap, press, release, click, move, scroll) and a Timer tile (wait). Drag a
tile onto the tree (or double-click it); `if`/`while` tiles carry `then`,
`else` and `body` branch rows — dropping tiles onto a branch nests them.
The tree can be reordered by dragging rows, the YAML preview on the right
updates live (it is the source of truth), and "Save" writes it back to
`~/.config/input-automate/macros/`.

Toolbar: switch between macro files, **Test run (dry)** validates the
current macro end-to-end without injecting anything, **YAML editor** opens
the raw YAML ↔ form editor, **Devices** lists connected input devices, and
**Pick region** grabs one frame of your real screen (GNOME/mutter backend,
mock fallback) and copies the dragged rectangle as `x,y,w,h` to the
clipboard.

### CLI

```bash
input-automation list-devices                # connected input devices
input-automation monitor                     # watch live events (Ctrl+C to stop)
input-automation test-output                 # wiggle mouse + tap a key virtually
input-automation record -o my-macro.yaml     # record input; F10 stops recording
input-automation macros list [dir]           # macros in a directory (--json)
input-automation macros validate file.yaml   # schema check
input-automation macros run file.yaml [--repeat N] [--dry-run]
input-automation capture -o shot.png         # one screen frame (backend: mock/mutter/portal)
input-automation analyze shot.png --color 0,0,255 [--region x,y,w,h] [--bar]
input-automation analyze shot.png --template icon.png
```

`analyze` measures what the visual trigger engine sees: fraction of pixels
matching a BGR color (with tolerance), a horizontal bar's fill fraction, or
the location and similarity score of a template image. All commands accept
`--json` where useful. Capturing the real screen needs a GNOME Wayland
session (`--backend mutter` works unattended; `--backend portal` shows the
standard GNOME share dialog); `--backend mock` works everywhere.

### Recording

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

## Troubleshooting

- **"device(s) could not be opened"** — you're not in the `input` group:
  run `system-setup.sh`, then re-login.
- **"/dev/uinput is not writable"** — the udev rule from `system-setup.sh`
  is missing, or the `uinput` module isn't loaded: re-run the script, re-login.
- **Capture fails on non-GNOME compositors** — only the mock backend is
  guaranteed there; GNOME (Wayland) is the supported target.
- **Recording misses keys** — check `list-devices` shows your device and
  `monitor` prints its events; if not, it's the `input` group again.

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
