# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The serial monitor of the emulated microcontrollers: what their UART sends, a line to send them,
and *Reset* - below the schematic while a circuit with microcontrollers is shown."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import set_role, set_variant, smaller_font, space

ENDINGS = (("Newline", "\n"), ("Carriage return", "\r"), ("Both", "\r\n"), ("None", ""))
#: characters kept in the monitor
LIMIT = 200_000


class SerialMonitor(QWidget):
    def __init__(self, send: Callable[[str, bytes], None], reset: Callable[[str], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._send = send
        self._reset = reset
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(1), space(1), space(1), space(1))
        layout.setSpacing(space(1))
        header = QHBoxLayout()
        title = QLabel("SERIAL MONITOR", self)
        set_role(title, "section")
        header.addWidget(title)
        self.device = QComboBox(self)
        self.device.setToolTip("The microcontroller")
        header.addWidget(self.device)
        self.state = QLabel("", self)
        set_role(self.state, "hint")
        self.state.setFont(smaller_font(self.state.font()))
        header.addWidget(self.state, 1)
        self.autoscroll = QCheckBox("Follow", self)
        self.autoscroll.setChecked(True)
        header.addWidget(self.autoscroll)
        clear = QPushButton("Clear", self)
        set_variant(clear, "tool")
        clear.clicked.connect(lambda: self.output.clear())
        header.addWidget(clear)
        reset = QPushButton("Reset", self)
        set_variant(reset, "tool")
        reset.setToolTip("Reset the microcontroller (its firmware starts again)")
        reset.clicked.connect(lambda: self.device.currentText() and self._reset(self.device.currentText()))
        header.addWidget(reset)
        layout.addLayout(header)
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        layout.addWidget(self.output, 1)
        row = QHBoxLayout()
        self.line = QLineEdit(self)
        self.line.setPlaceholderText("Send to the microcontroller (Enter)")
        self.line.returnPressed.connect(self.send_line)
        row.addWidget(self.line, 1)
        self.ending = QComboBox(self)
        for title_text, _value in ENDINGS:
            self.ending.addItem(title_text)
        row.addWidget(self.ending)
        button = QPushButton("Send", self)
        set_variant(button, "tool")
        button.clicked.connect(self.send_line)
        row.addWidget(button)
        layout.addLayout(row)
        #: the piece of a line that has no end yet, by (microcontroller, UART)
        self._texts: dict[tuple[str, str], str] = {}

    def set_devices(self, refs: list[str]) -> None:
        current = self.device.currentText()
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItems(refs)
        if current in refs:
            self.device.setCurrentText(current)
        self.device.blockSignals(False)

    def append(self, ref: str, data: bytes, uart: str = "0") -> None:
        """Output of ``ref``'s UART (``"0"``: Serial, ``"1"``: Serial1 ...); it arrives in pieces, so lines
        are kept until their end before they are shown - with the sender in front when there are several."""
        if not data:
            return
        key = (ref, uart)
        pending = self._texts.get(key, "") + data.decode("latin-1")
        if pending.endswith("\r"):  # (a "\r\n" cut in two: wait for the "\n")
            pending, tail = pending[:-1], "\r"
        else:
            tail = ""
        pending = pending.replace("\r\n", "\n").replace("\r", "\n")
        lines = pending.split("\n")
        self._texts[key] = lines.pop() + tail  # (the last piece has no end yet)
        if not lines:
            return
        prefix = ""
        if self.device.count() > 1 or uart != "0":
            prefix = f"[{ref}{' Serial' + uart if uart != '0' else ''}] "
        text = "".join(f"{prefix}{line}\n" for line in lines)
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        if self.output.document().characterCount() > LIMIT:  # (the oldest lines go)
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 200)
            cursor.removeSelectedText()
        if self.autoscroll.isChecked():
            self.output.moveCursor(QTextCursor.End)
            self.output.ensureCursorVisible()

    def set_state(self, text: str) -> None:
        self.state.setText(text)

    def send_line(self) -> None:
        ref = self.device.currentText()
        if not ref:
            return
        ending = ENDINGS[self.ending.currentIndex()][1]
        self._send(ref, (self.line.text() + ending).encode("utf-8"))
        self.line.clear()

    def text(self) -> str:
        return self.output.toPlainText()
