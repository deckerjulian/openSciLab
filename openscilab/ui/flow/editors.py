# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Editors of the inspector for the parameters that are programs of their own: the steps of a
sequence (``control.sequence``) and the states of a state machine (``control.state_machine``).

Both are tables – a step or a state a row – with a second view, *YAML*, that shows and edits what
the node stores. Every change goes to the node at once (``changed(value)``).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...lab.yaml_io import safe_load as yaml_safe_load
from ..icons import set_icon
from ..theme import set_role

#: the actions of a step: (what is stored, what the table says, what its two cells are for)
STEP_ACTIONS = (
    ("set", "Set", "output", "value"),
    ("emit", "Send event", "output", ""),
    ("wait", "Wait", "time", ""),
    ("wait_for", "Wait for", "input", "timeout"),
)


def step_row(step: Any) -> tuple[str, str, str]:
    """``(action, first, second)`` of a step of a sequence as the table shows it."""
    if not isinstance(step, dict):
        return "set", "", str(step)
    for key, _title, _first, _second in STEP_ACTIONS:
        if key in step:
            second = step.get("value" if key == "set" else "timeout" if key == "wait_for" else "", "")
            return key, _text(step.get(key)), _text(second)
    return "set", "", ""


def row_step(action: str, first: str, second: str) -> Optional[dict]:
    """The step a row of the table means (``None``: an empty row)."""
    first, second = first.strip(), second.strip()
    if not first and not second:
        return None
    if action == "set":
        return {"set": first, "value": _value(second)}
    if action == "emit":
        return {"emit": first}
    if action == "wait":
        return {"wait": first or second}
    step = {"wait_for": first}
    if second:
        step["timeout"] = second
    return step


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _value(text: str) -> Any:
    """A value typed into a cell: a number, true/false, or the text (``1.5 V`` stays text)."""
    if text == "":
        return None
    try:
        value = yaml_safe_load(text)
    except Exception:  # noqa: BLE001 - not YAML: the text itself
        return text
    return value if isinstance(value, (int, float, bool)) else text


class _TableEditor(QWidget):
    """A table with buttons to add, remove and move rows, and a YAML view of the same value."""

    #: the new value of the parameter
    changed = Signal(object)
    HEADERS: tuple[str, ...] = ()

    def __init__(self, value: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._filling = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.add_button = self._tool("plus", "Add a row", self.add_row)
        self.remove_button = self._tool("trash", "Remove the selected row", self.remove_row)
        self.up_button = self._tool("up", "Move the row up", lambda: self.move_row(-1))
        self.down_button = self._tool("down", "Move the row down", lambda: self.move_row(1))
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            bar.addWidget(button)
        bar.addStretch(1)
        self.yaml_button = QToolButton(self)
        self.yaml_button.setText("YAML")
        self.yaml_button.setCheckable(True)
        self.yaml_button.setToolTip("Show and edit what the node stores, as YAML")
        self.yaml_button.toggled.connect(self.show_yaml)
        bar.addWidget(self.yaml_button)
        layout.addLayout(bar)
        self.stack = QStackedWidget(self)
        self.table = QTableWidget(0, len(self.HEADERS), self)
        self.table.setHorizontalHeaderLabels(list(self.HEADERS))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemChanged.connect(lambda _item: self._commit())
        self.stack.addWidget(self.table)
        from .inspector import YamlEdit, to_block_text

        self.yaml = YamlEdit(to_block_text(value), self, lines=10)
        self.yaml.committed.connect(self._yaml_committed)
        self.stack.addWidget(self.yaml)
        layout.addWidget(self.stack)
        self.message = QLabel(self)
        set_role(self.message, "error")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)
        self.fill(value)

    def _tool(self, icon_name: str, tip: str, slot) -> QToolButton:
        button = QToolButton(self)
        set_icon(button, icon_name)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setAutoRaise(True)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    # ------------------------------------------------------------ rows
    def fill(self, value: Any) -> None:
        raise NotImplementedError

    def value(self) -> Any:
        raise NotImplementedError

    def _fit(self) -> None:
        rows = max(self.table.rowCount(), 2)
        height = self.table.horizontalHeader().height() + rows * self.table.verticalHeader().defaultSectionSize() + 4
        self.table.setMinimumHeight(min(height, 320))

    def add_row(self) -> None:
        self._filling = True
        self.table.insertRow(self.table.rowCount())
        self._init_row(self.table.rowCount() - 1)
        self._filling = False
        self._fit()
        self.table.setCurrentCell(self.table.rowCount() - 1, 1)

    def _init_row(self, row: int, cells: tuple = ()) -> None:
        for column in range(self.table.columnCount()):
            text = cells[column] if column < len(cells) else ""
            self.table.setItem(row, column, QTableWidgetItem(text))

    def remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._fit()
            self._commit()

    def move_row(self, step: int) -> None:
        row = self.table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        cells = [self._cell(row, column) for column in range(self.table.columnCount())]
        others = [self._cell(target, column) for column in range(self.table.columnCount())]
        self._filling = True
        self._set_row(row, others)
        self._set_row(target, cells)
        self._filling = False
        self.table.setCurrentCell(target, self.table.currentColumn())
        self._commit()

    def _cell(self, row: int, column: int) -> str:
        widget = self.table.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            return str(widget.currentData())
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def _set_row(self, row: int, cells: list[str]) -> None:
        for column, text in enumerate(cells):
            widget = self.table.cellWidget(row, column)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(widget.findData(text), 0))
            else:
                self.table.setItem(row, column, QTableWidgetItem(text))

    def _commit(self) -> None:
        if self._filling:
            return
        try:
            value = self.value()
        except ValueError as error:
            self.message.setText(str(error))
            self.message.setVisible(True)
            return
        self.message.setVisible(False)
        from .inspector import to_block_text

        self.yaml._last = to_block_text(value)
        self.yaml.setPlainText(self.yaml._last)
        self.changed.emit(value)

    # ------------------------------------------------------------ YAML
    def show_yaml(self, shown: bool) -> None:
        self.stack.setCurrentWidget(self.yaml if shown else self.table)

    def _yaml_committed(self, text: str) -> None:
        try:
            value = yaml_safe_load(text) if text.strip() else None
        except Exception as error:  # noqa: BLE001 - shown under the field
            self.message.setText(f"Not YAML: {error}")
            self.message.setVisible(True)
            return
        self.message.setVisible(False)
        self.fill(value)
        self.changed.emit(value)


class StepsEditor(_TableEditor):
    """The steps of a sequence: an action and its two cells per row (*Set* output to value, *Send
    event* on output, *Wait* a time, *Wait for* input with a timeout)."""

    HEADERS = ("Action", "Output, input or time", "Value or timeout")

    def _action_box(self, action: str) -> QComboBox:
        box = QComboBox(self.table)
        for key, title, _first, _second in STEP_ACTIONS:
            box.addItem(title, key)
        box.setCurrentIndex(max(box.findData(action), 0))
        box.currentIndexChanged.connect(lambda _index: self._commit())
        return box

    def _init_row(self, row: int, cells: tuple = ()) -> None:
        action = cells[0] if cells else "set"
        self.table.setCellWidget(row, 0, self._action_box(action))
        for column in (1, 2):
            text = cells[column] if column < len(cells) else ""
            self.table.setItem(row, column, QTableWidgetItem(text))

    def fill(self, value: Any) -> None:
        self._filling = True
        self.table.setRowCount(0)
        for step in value or []:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._init_row(row, step_row(step))
        self._filling = False
        self._fit()

    def value(self) -> list:
        steps = []
        for row in range(self.table.rowCount()):
            step = row_step(self._cell(row, 0), self._cell(row, 1), self._cell(row, 2))
            if step is not None:
                steps.append(step)
        return steps


#: a way out of a state as the table writes it: ``after 1 s → green``, ``button = 1 → green``,
#: ``button → green`` (any value)
_WAY = re.compile(r"^\s*(?:(?P<after>after)\s+(?P<time>.+?)|(?P<input>[\w.]+)(?:\s*=\s*(?P<when>.+?))?)\s*(?:→|->)\s*"
                  r"(?P<to>[\w.-]+)\s*$")


def way_text(way: Any) -> str:
    if not isinstance(way, dict):
        return str(way)
    target = way.get("to", "")
    if "after" in way:
        return f"after {way['after']} → {target}"
    if "input" in way:
        when = f" = {_text(way['when'])}" if "when" in way else ""
        return f"{way['input']}{when} → {target}"
    return f"→ {target}"


def parse_way(text: str) -> dict:
    match = _WAY.match(text)
    if match is None:
        raise ValueError(f"'{text.strip()}': write 'after 1 s → green', 'button = 1 → green' or 'button → green'")
    if match.group("after"):
        return {"after": match.group("time").strip(), "to": match.group("to")}
    way: dict = {"input": match.group("input")}
    if match.group("when") is not None:
        way["when"] = _value(match.group("when").strip())
    way["to"] = match.group("to")
    return way


def enter_text(enter: Any) -> str:
    if not isinstance(enter, dict):
        return ""
    return ", ".join(f"{name} = {_text(value)}" for name, value in enter.items())


def parse_enter(text: str) -> dict:
    values: dict = {}
    for part in (part.strip() for part in text.split(",")):
        if not part:
            continue
        name, separator, value = part.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"'{part}': write 'output = value' (several: 'red = 1, green = 0')")
        values[name.strip()] = _value(value.strip())
    return values


class StatesEditor(_TableEditor):
    """The states of a state machine: per row its name, what it sets on entering it (``red = 1,
    green = 0``) and its ways out, separated by ``;`` (``after 1 s → green; button = 1 → yellow``)."""

    HEADERS = ("State", "Sets on entering", "Ways out")

    def fill(self, value: Any) -> None:
        self._filling = True
        self.table.setRowCount(0)
        for name, state in (value or {}).items() if isinstance(value, dict) else []:
            row = self.table.rowCount()
            self.table.insertRow(row)
            state = state if isinstance(state, dict) else {}
            self._init_row(row, (str(name), enter_text(state.get("enter")),
                                 "; ".join(way_text(way) for way in state.get("on") or [])))
        self._filling = False
        self._fit()

    def value(self) -> dict:
        states: dict = {}
        for row in range(self.table.rowCount()):
            name = self._cell(row, 0).strip()
            if not name:
                continue
            state: dict = {}
            enter = parse_enter(self._cell(row, 1))
            if enter:
                state["enter"] = enter
            ways = [parse_way(part) for part in self._cell(row, 2).split(";") if part.strip()]
            if ways:
                state["on"] = ways
            states[name] = state
        return states
