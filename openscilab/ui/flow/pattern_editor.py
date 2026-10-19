# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The pattern editor of ``generator.pattern``: a track per pin made of a block (clock, square wave,
constant, counter bit, bytes, UART text or SDL text) with a few settings and a preview; behind it
the SDL per pin the node stores (``core/pattern_blocks.py``), which *SDL text* shows and edits."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import pattern_blocks as blocks
from ...core import units
from ...core import waveform as waves
from ..theme import set_role, tabular_font

#: what the editor calls the kinds of blocks
KIND_LABELS = {"clock": "Clock", "square": "Square wave", "constant": "Constant", "counter": "Counter bit",
               "bits": "Bytes", "uart": "UART text", "sdl": "SDL text"}
#: the settings as the editor shows them (with their choices)
SETTING_LABELS = {"period": "period (samples)", "start": "starts", "high": "high (samples)", "low": "low (samples)",
                  "level": "level", "bit": "bit", "step": "samples per count", "data": "data", "order": "order",
                  "idle": "then", "baud": "baud", "text": "SDL"}
#: labels that depend on the kind (the bit of a counter, the samples a bit of bytes takes)
KIND_SETTING_LABELS = {("counter", "bit"): "bit of the count", ("bits", "bit"): "samples per bit",
                       ("uart", "data"): "text", ("bits", "data"): "bytes (hex)"}
CHOICES = {"start": ("low", "high"), "level": ("low", "high"), "idle": ("low", "high"), "order": ("msb", "lsb")}
#: the smallest value of a number setting
MINIMUM = {"period": 2, "high": 1, "low": 1, "step": 1, "bit": 0, "baud": 1}
#: what ``data`` means for each kind
DATA_HINTS = {"bits": "bytes in hex, e.g. 55 AA", "uart": "text, 8N1 frames"}


class _Row:
    """The widgets of one track: pin, kind, settings, remove."""

    def __init__(self, editor: "PatternEditor", pin: str, block: blocks.Block) -> None:
        self.editor = editor
        self.pin = QComboBox(editor)
        self.pin.setEditable(not editor.pins)
        self.pin.addItems(editor.pins)
        if pin and self.pin.findText(pin) < 0:
            self.pin.addItem(pin)
        self.pin.setCurrentText(pin)
        self.kind = QComboBox(editor)
        for kind in blocks.KINDS:
            self.kind.addItem(KIND_LABELS[kind], kind)
        self.kind.setCurrentIndex(self.kind.findData(block.kind))
        self.settings = QWidget(editor)
        self.form = QHBoxLayout(self.settings)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.fields: dict[str, QWidget] = {}
        self.remove = QToolButton(editor)
        self.remove.setText("✕")
        self.remove.setToolTip("Remove this pin")
        self._fill(block)
        self.pin.currentTextChanged.connect(lambda _text: editor.changed())
        self.kind.currentIndexChanged.connect(lambda _index: (self._fill(blocks.Block(self.kind.currentData())),
                                                              editor.changed()))

    def _fill(self, block: blocks.Block) -> None:
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.fields = {}
        for name, default in blocks.DEFAULTS[block.kind].items():
            value = block.value(name)
            label = QLabel(KIND_SETTING_LABELS.get((block.kind, name), SETTING_LABELS.get(name, name)), self.settings)
            set_role(label, "hint")
            self.form.addWidget(label)
            if name in CHOICES:
                field = QComboBox(self.settings)
                field.addItems(CHOICES[name])
                field.setCurrentText(str(value))
                field.currentTextChanged.connect(lambda _text: self.editor.changed())
            elif isinstance(default, int):
                field = QSpinBox(self.settings)
                field.setRange(1 if (block.kind, name) == ("bits", "bit") else MINIMUM.get(name, 0), 100_000_000)
                field.setValue(int(value))
                field.valueChanged.connect(lambda _value: self.editor.changed())
            else:
                field = QLineEdit(str(value), self.settings)
                field.setPlaceholderText(DATA_HINTS.get(block.kind, ""))
                field.setMinimumWidth(160 if block.kind == "sdl" else 100)
                field.textChanged.connect(lambda _text: self.editor.changed())
            self.fields[name] = field
            self.form.addWidget(field)
        self.form.addStretch(1)

    def block(self) -> blocks.Block:
        settings: dict[str, Any] = {}
        for name, field in self.fields.items():
            if isinstance(field, QSpinBox):
                settings[name] = field.value()
            elif isinstance(field, QComboBox):
                settings[name] = field.currentText()
            else:
                settings[name] = field.text()
        return blocks.Block(self.kind.currentData(), settings)

    def widgets(self) -> list[QWidget]:
        return [self.pin, self.kind, self.settings, self.remove]


class PatternEditor(QDialog):
    """Edits the ``tracks`` (SDL per pin) and the ``rate`` of a pattern as blocks."""

    def __init__(self, tracks: Optional[dict], rate: Any, pins: Optional[list[str]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pattern")
        self.resize(900, 560)
        #: the pins the device can play the pattern on (empty: not known, any name)
        self.pins = [str(pin) for pin in pins or []]
        self.rows: list[_Row] = []
        self._loading = True
        layout = QVBoxLayout(self)
        head = QFormLayout()
        self.rate = QLineEdit(str(rate if rate not in (None, "") else "1 MHz"), self)
        self.rate.setToolTip("Samples per second, e.g. 200 Hz or 1 MHz")
        self.rate.editingFinished.connect(self.changed)
        head.addRow("Rate", self.rate)
        length_row = QHBoxLayout()
        self.length = QSpinBox(self)
        self.length.setRange(1, 10_000_000)
        self.length.setSuffix(" samples")
        self.length.valueChanged.connect(lambda _value: self.changed())
        length_row.addWidget(self.length)
        fit = QPushButton("Fit", self)
        fit.setToolTip("As long as the longest block needs: a period, its bytes or its text")
        fit.clicked.connect(self.fit_length)
        length_row.addWidget(fit)
        self.duration = QLabel("", self)
        set_role(self.duration, "hint")
        length_row.addWidget(self.duration, 1)
        head.addRow("Length", length_row)
        layout.addLayout(head)

        self.pages = QStackedWidget(self)
        # blocks
        page = QWidget(self.pages)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        holder = QWidget(scroll)
        self.grid = QGridLayout(holder)
        for column, title in enumerate(("Pin", "Kind", "Settings")):
            label = QLabel(title, holder)
            set_role(label, "hint")
            self.grid.addWidget(label, 0, column)
        self.grid.setColumnStretch(2, 1)
        self.grid.setRowStretch(1000, 1)
        scroll.setWidget(holder)
        page_layout.addWidget(scroll, 1)
        add = QPushButton("+ Pin", page)
        add.clicked.connect(lambda: self.add_row())
        page_layout.addWidget(add, 0, Qt.AlignLeft)
        self.pages.addWidget(page)
        # text
        self.text = QPlainTextEdit(self.pages)

        self.text.setFont(tabular_font(self.text.font()))
        self.text.setToolTip("The tracks as the node stores them: a pin and its SDL per entry (YAML)")
        self.pages.addWidget(self.text)
        layout.addWidget(self.pages, 1)

        from ..documents.waveform import WavePreview

        self.preview = WavePreview(self)
        self.preview.setMinimumHeight(140)
        layout.addWidget(self.preview, 1)
        self.error = QLabel("", self)
        set_role(self.error, "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.text_button = buttons.addButton("SDL text", QDialogButtonBox.ActionRole)
        self.text_button.setCheckable(True)
        self.text_button.setToolTip("The SDL behind the blocks (what the node stores); edit it and switch back")
        self.text_button.toggled.connect(self.show_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load(tracks or {})
        self._loading = False
        self.changed()

    # ------------------------------------------------------------- reading
    def rate_value(self) -> float:
        try:
            value = units.parse(self.rate.text(), "Hz")
        except units.UnitError:
            return 0.0
        return float(value) if value else 0.0

    def load(self, tracks: dict) -> None:
        for row in list(self.rows):
            self.remove_row(row)
        found, length = blocks.read_tracks({str(pin): str(text) for pin, text in tracks.items()},
                                           self.rate_value() or 1.0)
        self.length.setValue(length)
        for pin, block in found.items():
            self.add_row(pin, block)
        if not self.rows:
            self.add_row()

    def add_row(self, pin: str = "", block: Optional[blocks.Block] = None) -> None:
        if not pin:
            used = {row.pin.currentText() for row in self.rows}
            pin = next((name for name in self.pins if name not in used), "")
        row = _Row(self, pin, block or blocks.Block("clock"))
        row.remove.clicked.connect(lambda _checked=False, row=row: (self.remove_row(row), self.changed()))
        index = len(self.rows) + 1
        for column, widget in enumerate(row.widgets()):
            self.grid.addWidget(widget, index, column)
        self.rows.append(row)
        self.changed()

    def remove_row(self, row: _Row) -> None:
        self.rows.remove(row)
        for widget in row.widgets():
            self.grid.removeWidget(widget)
            widget.deleteLater()

    def blocks(self) -> dict[str, blocks.Block]:
        return {row.pin.currentText().strip(): row.block() for row in self.rows if row.pin.currentText().strip()}

    def tracks(self) -> dict[str, str]:
        """SDL per pin (what the node stores); from the text while it is shown."""
        if self.text_button.isChecked():
            from ...lab.yaml_io import safe_load

            data = safe_load(self.text.toPlainText()) or {}
            if not isinstance(data, dict):
                raise blocks.BlockError("the text is a mapping of pins to SDL, e.g. 'D0: l5;h5;'")
            return {str(pin): str(text) for pin, text in data.items()}
        return blocks.tracks(self.blocks(), self.length.value(), self.rate_value() or 1.0)

    # ------------------------------------------------------------- actions
    def fit_length(self) -> None:
        try:
            self.length.setValue(blocks.pattern_length(self.blocks(), self.rate_value() or 1.0))
        except blocks.BlockError as error:
            self.error.setText(str(error))

    def show_text(self, shown: bool) -> None:
        from .inspector import to_block_text

        try:
            if shown:
                self.text.setPlainText(to_block_text(blocks.tracks(self.blocks(), self.length.value(),
                                                                   self.rate_value() or 1.0)))
            else:
                from ...lab.yaml_io import safe_load

                self.load(safe_load(self.text.toPlainText()) or {})
        except (blocks.BlockError, ValueError) as error:
            self.error.setText(str(error))
        self.pages.setCurrentIndex(1 if shown else 0)
        self.changed()

    def changed(self) -> None:
        if self._loading:
            return
        rate = self.rate_value()
        self.duration.setText(f"= {units.format_quantity(self.length.value() / rate, 's')}" if rate else "")
        try:
            if not rate:
                raise blocks.BlockError("the rate is a frequency, e.g. 200 Hz")
            tracks = self.tracks()
            if not tracks:
                raise blocks.BlockError("no pin yet")
            pins = list(tracks)
            if len(set(pins)) != len(pins) or len(pins) != len(self.rows) and not self.text_button.isChecked():
                raise blocks.BlockError("every pin once")
            self.preview.set_waveform(waves.pattern_from_sdl(tracks, rate))
            self.error.setText("")
        except (blocks.BlockError, waves.WaveformError, ValueError) as error:
            self.preview.set_waveform(None, str(error))
            self.error.setText(str(error))

    def accept(self) -> None:
        try:
            self.result_tracks = self.tracks()
            if not self.rate_value():
                raise blocks.BlockError("the rate is a frequency, e.g. 200 Hz")
            if len({row.pin.currentText() for row in self.rows}) != len(self.rows) and not self.text_button.isChecked():
                raise blocks.BlockError("every pin once")
        except (blocks.BlockError, ValueError) as error:
            self.error.setText(str(error))
            return
        self.result_rate = self.rate.text().strip()
        super().accept()
