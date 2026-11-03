"""Main window for the input-automation GUI (T14).

Headless-testable: data comes from injectable provider callables, so tests
drive the window with fakes and never touch real devices or uinput. The
window itself is thin glue — macro execution goes through the same
:class:`~input_automation.macros.orchestrator.MacroOrchestrator` the CLI uses.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from input_automation.capture.base import CaptureBackend
from input_automation.capture.factory import create_backend
from input_automation.devices.discovery import DeviceInfo, list_devices
from input_automation.gui.editor import MacroEditorDialog, new_macro_template
from input_automation.gui.region_picker import RegionPickerDialog
from input_automation.macros.loader import LoadError, load_macro, load_macros
from input_automation.macros.models import Macro
from input_automation.macros.orchestrator import MacroOrchestrator, NullSink

__all__ = ["MainWindow", "run"]

MacroProvider = Callable[[Path], Sequence[tuple[Path, Macro]]]
DeviceProvider = Callable[..., Sequence[DeviceInfo]]

# Macro home per AGENTS.md: ~/.config/input-automate/macros/
DEFAULT_MACRO_DIR = Path.home() / ".config" / "input-automate" / "macros"


def _default_macro_provider(directory: Path) -> Sequence[tuple[Path, Macro]]:
    """Load all macros from ``directory`` (load_macros skips nothing — errors surface)."""
    return list(load_macros(directory))


class MainWindow(QMainWindow):
    """App shell: device list, macro list, tray, and a dry-run button."""

    def __init__(
        self,
        *,
        device_provider: DeviceProvider | None = None,
        macro_provider: MacroProvider | None = None,
        macro_dir: Path = DEFAULT_MACRO_DIR,
    ) -> None:
        super().__init__()
        self.setWindowTitle("input-automation")
        self._device_provider = device_provider or list_devices
        self._macro_provider = macro_provider or _default_macro_provider
        self._macro_dir = macro_dir
        self._macros: dict[str, Macro] = {}
        self._macro_paths: dict[str, Path] = {}

        central = QWidget()
        layout = QVBoxLayout(central)
        self._device_list = QListWidget()
        self._macro_list = QListWidget()
        buttons = QHBoxLayout()
        self._refresh_devices = QPushButton("Refresh devices")
        self._refresh_macros = QPushButton("Refresh macros")
        self._test_run = QPushButton("Test run (dry)")
        self._new_macro = QPushButton("New macro")
        self._pick_region = QPushButton("Pick region")
        for btn in (
            self._refresh_devices,
            self._refresh_macros,
            self._test_run,
            self._new_macro,
            self._pick_region,
        ):
            buttons.addWidget(btn)
        layout.addWidget(QLabel("Devices"))
        layout.addWidget(self._device_list)
        layout.addWidget(QLabel("Macros"))
        layout.addWidget(self._macro_list)
        layout.addLayout(buttons)
        self.setCentralWidget(central)

        self._refresh_devices.clicked.connect(self.refresh_devices)
        self._refresh_macros.clicked.connect(self.refresh_macros)
        self._test_run.clicked.connect(self.test_run_selected)
        self._new_macro.clicked.connect(self._open_new_macro)
        self._pick_region.clicked.connect(self._open_region_picker)
        self._macro_list.itemDoubleClicked.connect(self._edit_macro)
        self.statusBar().showMessage(
            "  |  ".join(m for m in (self._load_devices(), self._load_macros()))
        )

        self._tray = self._build_tray()

    # -- data ----------------------------------------------------------------

    def _load_devices(self) -> str:
        """Fill the device list; returns a status message."""
        self._device_list.clear()
        try:
            devices = list(self._device_provider())
        except Exception as exc:  # noqa: BLE001 - surfaced in the status bar
            return f"device refresh failed: {exc!r}"
        for device in devices:
            kinds = ", ".join(sorted(device.kinds))
            self._device_list.addItem(f"{device.path}  {device.name}  [{kinds}]")
        return f"{len(devices)} device(s)"

    def _load_macros(self) -> str:
        """Fill the macro list; returns a status message."""
        self._macro_list.clear()
        self._macros.clear()
        self._macro_paths.clear()
        try:
            entries = list(self._macro_provider(self._macro_dir))
        except Exception as exc:  # noqa: BLE001 - surfaced in the status bar
            return f"macro refresh failed: {exc!r}"
        for path, macro in entries:
            self._macros[path.name] = macro
            self._macro_paths[path.name] = path
            self._macro_list.addItem(f"{macro.name}  ({path.name})")
        return f"{len(entries)} macro(s)"

    def refresh_devices(self) -> None:
        """Reload the device list from the provider."""
        self.statusBar().showMessage(self._load_devices())

    def refresh_macros(self) -> None:
        """Reload the macro list from the provider."""
        self.statusBar().showMessage(self._load_macros())

    def test_run_selected(self) -> None:
        """Dry-run the selected macro (validates the tree, injects nothing)."""
        item = self._macro_list.currentItem()
        if item is None:
            self.statusBar().showMessage("Select a macro first.")
            return
        stem = item.text().rsplit("(", 1)[1].strip(" )")
        macro = self._macros.get(stem)
        assert macro is not None  # noqa: S101 - populated by _load_macros
        try:
            MacroOrchestrator(sink=NullSink(), dry_run=True).run(macro)
        except Exception as exc:  # noqa: BLE001 - surfaced in the status bar
            self.statusBar().showMessage(f"{macro.name}: error: {exc!r}")
            return
        self.statusBar().showMessage(
            f"{macro.name}: dry run ok ({len(macro.steps)} steps)"
        )

    # -- editor / region picker ----------------------------------------------

    def _edit_macro(self, item) -> None:  # type: ignore[no-untyped-def]
        """Open the double-clicked YAML macro in the editor dialog."""
        stem = item.text().rsplit("(", 1)[1].strip(" )")
        path = self._macro_paths.get(stem)
        if path is None or path.suffix not in (".yaml", ".yml"):
            self.statusBar().showMessage(
                "Only YAML macros can be edited (Python macros are code)."
            )
            return
        try:
            macro = load_macro(path)
        except LoadError as exc:
            self.statusBar().showMessage(f"could not load {path.name}: {exc}")
            return
        editor = MacroEditorDialog(
            macro=macro, path=path, default_dir=self._macro_dir, parent=self
        )
        editor.exec()
        self.refresh_macros()

    def _open_new_macro(self) -> None:
        """Open the editor with a template macro; saving asks for a path."""
        editor = MacroEditorDialog(
            macro=new_macro_template(), default_dir=self._macro_dir, parent=self
        )
        editor.exec()
        self.refresh_macros()

    def _open_region_picker(self) -> None:
        """Pick a screen region on live frames; copy x,y,w,h to the clipboard."""
        try:
            backend = self._start_capture_backend()
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            self.statusBar().showMessage(f"no capture backend available: {exc!r}")
            return
        picker = RegionPickerDialog(backend=backend, parent=self)
        picker.exec()
        if picker.region is not None:
            x, y, w, h = picker.region
            QApplication.clipboard().setText(f"{x},{y},{w},{h}")
            self.statusBar().showMessage(
                f"Region {x},{y},{w},{h} copied (analyze --region / watch rules)."
            )
        else:
            self.statusBar().showMessage("Region picking cancelled.")

    def _start_capture_backend(self) -> CaptureBackend:
        """Start a capture backend: real screen first, mock as fallback.

        The backend is left running — :class:`RegionPickerDialog` skips a
        redundant start and stops it when the dialog closes.
        """
        for name in ("mutter", "mock"):
            backend = create_backend(name)
            try:
                backend.start()
            except Exception:  # noqa: BLE001 - try the next backend
                continue
            return backend
        raise RuntimeError("mutter and mock backends both failed to start")

    # -- tray ----------------------------------------------------------------

    def _build_tray(self) -> QSystemTrayIcon | None:
        """Create the tray icon; None when the desktop has no system tray."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        style = self.style()
        tray = QSystemTrayIcon(style.standardIcon(style.StandardPixmap.SP_ComputerIcon))
        menu = QMenu()
        show_action = QAction("Show window", menu)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("Quit", menu)
        app = QApplication.instance()
        if app is not None:
            quit_action.triggered.connect(app.quit)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.setToolTip("input-automation")
        tray.show()
        return tray


def run() -> None:
    """Create the application, show the main window, and enter the event loop."""
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
