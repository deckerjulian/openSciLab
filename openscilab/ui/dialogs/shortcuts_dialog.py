# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""*Help → Keyboard shortcuts*: the shortcuts of the menus and the active documents, and the keys
and gestures that work inside the views; a search field narrows the list."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QDialog, QHeaderView, QLineEdit, QTableWidget, QTableWidgetItem, QWidget

from .common import button_box, dialog_layout

#: keys and gestures inside the views (they are no menu entries): (where, input, what it does)
VIEW_INPUT = (
    ("Flow graph", "Two fingers / pinch", "Move the canvas / zoom at the fingers"),
    ("Flow graph", "Mouse wheel / Ctrl+wheel / Shift+wheel", "Zoom / move sideways / move up and down"),
    ("Flow graph", "Space + drag, middle button", "Move the canvas"),
    ("Flow graph", "Tab, or type a name", "Add a node"),
    ("Flow graph", "Delete, Backspace", "Delete the selection"),
    ("Flow graph", "+ / -", "Zoom in / out"),
    ("Flow graph", "Drag a wire onto the canvas", "Add a node it fits"),
    ("Circuit", "Type a name", "Search the catalog and place a component (Ctrl+Shift+A opens the search too)"),
    ("Circuit", "Press on a pin, or Shift+W", "Draw a wire: click its corners, click a pin or double-click to end it"),
    ("Circuit", "/", "Turn the corner of the wire being drawn the other way"),
    ("Circuit", "Shift+L / Shift+G / Shift+P", "Place a net label / a ground / a voltage probe"),
    ("Circuit", "R / X", "Rotate / mirror the selection"),
    ("Circuit", "Ctrl+G / Ctrl+Shift+G", "Group the selection / take the group apart"),
    ("Circuit", "Delete, Backspace", "Delete the selection (a group frame: asks about its members)"),
    ("Circuit", "Drag a knob up or down (Alt while it is not running)", "Turn a potentiometer, a variac"),
    ("Circuit", "Esc", "Stop drawing a wire or placing a component"),
    ("Data view (traces)", "Two fingers / pinch / double tap", "Move / zoom at the fingers / fit"),
    ("Data view (traces)", "Mouse wheel / Ctrl+wheel / Alt+wheel", "Zoom / move / channel height"),
    ("Data view (traces)", "A / B / M", "Cursor A / cursor B / marker at the pointer"),
    ("Data view (traces)", "← → / Shift+← →", "Scroll 10 % / one sample"),
    ("Data view (traces)", "Drag a channel name", "Move the channel"),
    ("Charts", "Wheel, pinch, drag / double-click", "Zoom and move / automatic range"),
    ("Panel editor", "Drag a widget / a handle", "Move it / resize it (snaps; Alt: freely)"),
    ("Panel editor", "Shift+click / drag on the free surface / Ctrl+A", "Add to the selection / select a frame / all"),
    ("Panel editor", "← → ↑ ↓ / Shift+arrows", "Move the selection by a pixel / by the raster"),
    ("Panel", "F11, Esc", "Full screen, leave it"),
    ("Device", "F5", "Run (capture)"),
    ("Device", "Esc (Pins tab)", "All outputs safe"),
)


class ShortcutsDialog(QDialog):
    def __init__(self, rows: list[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(720, 560)
        layout = dialog_layout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search shortcuts")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        entries = [(title, shortcut) for title, shortcut in rows] + [
            (f"{where} › {what}", keys) for where, keys, what in VIEW_INPUT]
        self.table = QTableWidget(len(entries), 2, self)
        self.table.setHorizontalHeaderLabels(["Command", "Keys"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for row, (title, keys) in enumerate(entries):
            self.table.setItem(row, 0, QTableWidgetItem(title))
            self.table.setItem(row, 1, QTableWidgetItem(keys))
        layout.addWidget(self.table, 1)
        buttons = button_box(self, None)
        layout.addWidget(buttons)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for row in range(self.table.rowCount()):
            line = f"{self.table.item(row, 0).text()} {self.table.item(row, 1).text()}".lower()
            self.table.setRowHidden(row, not all(word in line for word in words))

    def visible_rows(self) -> list[tuple[str, str]]:
        return [(self.table.item(row, 0).text(), self.table.item(row, 1).text())
                for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]
