"""Properties Panel integration for CAPTURE blocks (issue #18, Layer 4).

Verifies the Select region button, the MODE combobox, and the TEMPLATE
show/hide behaviour. Headless with QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox, QPushButton

from input_automation.gui.canvas import BlockCanvas, PropertiesPanel

CAPTURE_MODES = ("color", "threshold", "template", "bar")


def test_capture_properties_panel(qtbot) -> None:  # type: ignore[no-untyped-def]
    canvas = BlockCanvas()
    qtbot.addWidget(canvas)
    panel = PropertiesPanel()
    qtbot.addWidget(panel)

    item = canvas.add_block("CAPTURE", "cap1")
    panel.set_block(item)

    # MODE combobox lists all four modes, defaulting to color.
    assert isinstance(panel._mode_combo, QComboBox)
    assert [
        panel._mode_combo.itemText(i) for i in range(panel._mode_combo.count())
    ] == list(CAPTURE_MODES)
    assert panel._mode_combo.currentText() == "color"

    # REGION has a Select region… button.
    assert any(
        button.text() == "Select region…"
        for button in panel.findChildren(QPushButton)
    )

    # TEMPLATE is hidden unless MODE == template.
    assert panel._template_row is not None and panel._template_edit is not None
    assert panel._template_row.isHidden()
    assert panel._template_edit.isHidden()

    # Switching to template reveals TEMPLATE; switching back hides it.
    panel._mode_combo.setCurrentText("template")
    assert panel._mode_combo.currentText() == "template"
    assert not panel._template_row.isHidden()
    assert not panel._template_edit.isHidden()

    panel._mode_combo.setCurrentText("color")
    assert panel._template_row.isHidden()
    assert panel._template_edit.isHidden()


def test_non_capture_block_shows_no_capture_rows(qtbot) -> None:  # type: ignore[no-untyped-def]
    canvas = BlockCanvas()
    qtbot.addWidget(canvas)
    panel = PropertiesPanel()
    qtbot.addWidget(panel)

    item = canvas.add_block("AND", "and1")
    panel.set_block(item)

    assert panel._mode_combo is None
    assert panel._template_row is None
    assert panel._region_edit is None
