# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The inspector of a circuit: the selected component (its reference, parameters, unit, how it is
simulated), a net label, or - with nothing selected - the circuit (its settings, model libraries and
analyses). What was typed wrong shows under its field (docs/design.md, rule 4)."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...circuits import values
from ...circuits.library import ComponentSpec, ParamSpec
from ...circuits.model import (
    ANALYSES,
    GROUP_COLORS,
    SETTING_CHOICES,
    SETTINGS,
    Component,
    Group,
    NetLabel,
    Schematic,
    Wire,
)
from ..theme import ICON, set_role, set_variant, size, smaller_font, space

ANALYSIS_TITLES = {"op": "Operating point", "dc": "DC sweep", "ac": "AC sweep", "tran": "Transient",
                   "sp": "S-parameters", "noise": "Noise"}
SETTING_TITLES = {"step": ("Time step", "s", "The time step of the live simulation and of transients."),
                  "max_step": ("Longest step", "s", "The longest step ngspice may take (empty: it decides)."),
                  "vcc": ("Logic supply", "V", "The supply of digital components and microcontrollers; logic thresholds."),
                  "temperature": ("Temperature", "°C", "The temperature of the components."),
                  "tolerance": ("Tolerance", "", ("The relative error ngspice allows per step (its reltol): 0.01 "
                                                  "runs switching circuits several times faster, a little less exact.")),
                  "speed": ("Speed", "", "Simulated seconds per second when it runs in real time (1: as fast as the clock)."),
                  "start": ("Start", "", ("settled: from its operating point (as if it ran for long); power on: every "
                                          "capacitor empty, as the moment it is switched on (inrush, soft start)."))}
PARAM_TITLES = {"source": "Source", "start": "Start", "stop": "Stop", "step": "Step", "points": "Points per decade",
                "scale": "Scale", "output": "Output net"}


def _section(parent: QWidget, title: str) -> QLabel:
    label = QLabel(title.upper(), parent)
    set_role(label, "section")
    label.setContentsMargins(0, space(2), 0, 0)
    return label


def _set_text(editor: QWidget, text: str) -> None:
    """Put ``text`` into an editor unless it is being typed in."""
    if isinstance(editor, QLineEdit):
        if not editor.hasFocus() and editor.text() != text:
            editor.setText(text)
    elif isinstance(editor, QComboBox):
        if not editor.hasFocus() and editor.currentText() != text:
            blocked = editor.blockSignals(True)
            if editor.findText(text) < 0:
                editor.addItem(text)
            editor.setCurrentText(text)
            editor.blockSignals(blocked)
    else:
        line = editor.findChild(QLineEdit)
        if line is not None:
            _set_text(line, text)


class CommentEdit(QPlainTextEdit):
    """A note of the author (a few lines), taken when the field is left."""

    committed = Signal(str)

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.setPlaceholderText("A note shown on the schematic (optional)")
        self.setFixedHeight(size("note"))
        self.setTabChangesFocus(True)
        self._text = text

    def set_comment(self, text: str) -> None:
        if not self.hasFocus() and text != self.toPlainText():
            self.setPlainText(text)
        self._text = text

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        text = self.toPlainText().strip()
        if text != self._text:
            self._text = text
            self.committed.emit(text)


def _comment_rows(widget: QWidget, layout, text: str, on_commit) -> CommentEdit:
    layout.addWidget(_section(widget, "Comment"))
    edit = CommentEdit(text, widget)
    edit.committed.connect(on_commit)
    layout.addWidget(edit)
    return edit


def icon_swatch(name: str):
    """A small square of a group colour (for the colour's entry)."""
    from PySide6.QtGui import QIcon, QPixmap

    from ..theme import icon_px, qcolor

    side = icon_px(ICON["small"])
    pixmap = QPixmap(side, side)
    pixmap.fill(qcolor(f"circuit.group.{name}"))
    return QIcon(pixmap)


class _Field(QWidget):
    """An editor with its message below."""

    def __init__(self, editor: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(editor)
        self.editor = editor
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        set_role(self.message, "error")
        self.message.setVisible(False)
        column.addWidget(self.message)

    def show_message(self, text: str | None) -> None:
        self.message.setText(text or "")
        self.message.setVisible(bool(text))


class ComponentInspector(QWidget):
    #: (component, parameter, value)
    param_changed = Signal(str, str, str)
    #: (reference, text): its comment
    comment_changed = Signal(str, str)
    #: (old reference, new reference)
    renamed = Signal(str, str)
    #: (component, unit)
    unit_changed = Signal(str, int)

    def __init__(self, component: Component, spec: ComponentSpec | None, base_folder: str = "",
                 parent: QWidget | None = None, options: Callable[[str, Component], list[str]] | None = None) -> None:
        super().__init__(parent)
        self.component = component
        self.spec = spec
        #: what a "device" / "device_pin" parameter offers (the connected devices, their pins)
        self.options = options
        self.base_folder = base_folder
        self.fields: dict[str, _Field] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(2), space(2), space(2), space(2))
        title = QLabel(spec.title if spec is not None else component.type, self)
        set_role(title, "heading")
        title.setWordWrap(True)
        layout.addWidget(title)
        if spec is not None and spec.description:
            text = QLabel(spec.description, self)
            text.setWordWrap(True)
            set_role(text, "hint")
            text.setFont(smaller_font(text.font()))
            layout.addWidget(text)
        status = QLabel(self._simulation_text(), self)
        status.setWordWrap(True)
        set_role(status, "hint")
        status.setFont(smaller_font(status.font()))
        layout.addWidget(status)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.reference = QLineEdit(component.id, self)
        self.reference.setToolTip("Its reference: its name on the schematic and in the netlist")
        reference_field = _Field(self.reference, self)
        self.fields["#reference"] = reference_field
        self.reference.editingFinished.connect(self._rename)
        form.addRow("Reference", reference_field)
        if spec is not None and spec.symbol.units > 1:
            self.unit = QSpinBox(self)
            self.unit.setRange(1, spec.symbol.units)
            self.unit.setValue(component.unit)
            self.unit.setToolTip("Which unit of the component it is")
            self.unit.valueChanged.connect(lambda value: self.unit_changed.emit(self.component.id, value))
            form.addRow("Unit", self.unit)
        for param in spec.params if spec is not None else ():
            editor = self._editor(param)
            field = _Field(editor, self)
            self.fields[param.name] = field
            label = param.label + (f" ({param.unit})" if param.unit and param.kind == "quantity" else "")
            form.addRow(label, field)
            editor.setToolTip(param.description or param.label)
        layout.addLayout(form)
        if spec is not None and spec.controls:
            hint = QLabel("While the circuit runs, operate it on the schematic: "
                          + {"knob": "drag up and down or turn the wheel over it.",
                             "switch": "click it.", "button": "hold it down."}.get(spec.controls[0].kind, ""), self)
            hint.setWordWrap(True)
            set_role(hint, "hint")
            hint.setFont(smaller_font(hint.font()))
            layout.addWidget(hint)
        if spec is not None and spec.datasheet and spec.datasheet not in ("~", ""):
            link = QLabel(f"<a href='{spec.datasheet}'>Data sheet</a>", self)
            link.setOpenExternalLinks(True)
            layout.addWidget(link)
        source = QLabel(f"{spec.type}" if spec is not None else "", self)
        set_role(source, "hint")
        source.setFont(smaller_font(source.font()))
        source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        source.setWordWrap(True)
        layout.addWidget(source)
        self.comment = _comment_rows(self, layout, component.comment,
                                     lambda text: self.comment_changed.emit(self.component.id, text))
        layout.addStretch(1)

    def sync(self, component: Component) -> None:
        """Show ``component`` as it is now (the field being typed in stays as it is)."""
        self.component = component
        if not self.reference.hasFocus():
            self.reference.setText(component.id)
        for param in self.spec.params if self.spec is not None else ():
            field = self.fields.get(param.name)
            if field is None:
                continue
            if param.kind in ("device", "device_pin") and isinstance(field.editor, QComboBox) \
                    and not field.editor.hasFocus():
                self._fill_options(field.editor, param, self._value(param))  # (another device: its pins)
                continue
            _set_text(field.editor, self._value(param))
        self.comment.set_comment(component.comment)
        unit = getattr(self, "unit", None)
        if unit is not None and not unit.hasFocus():
            blocked = unit.blockSignals(True)
            unit.setValue(component.unit)
            unit.blockSignals(blocked)

    def _fill_options(self, combo: QComboBox, param: ParamSpec, value: str) -> None:
        """The connected devices or the pins of the chosen one, and ``value`` (also when it is not there)."""
        offered = self.options(param.kind, self.component) if self.options is not None else []
        items = [combo.itemText(index) for index in range(combo.count())]
        wanted = list(offered) + ([value] if value and value not in offered else [])
        blocked = combo.blockSignals(True)
        if items != wanted:
            combo.clear()
            combo.addItems(wanted)
        combo.setCurrentText(value)
        combo.blockSignals(blocked)

    def _simulation_text(self) -> str:
        spec = self.spec
        if spec is None:
            return "This kind of component is not in the catalog (a library is missing)."
        if spec.role == "mcu":
            return "An emulated microcontroller: it runs the firmware file below."
        if spec.model is None:
            return "Drawn only: no simulation model." if not spec.simulated else ""
        return {"generic": "Simulated with a standard model of its kind.", "none": "No simulation model was found.",
                "exact": ""}.get(spec.model.quality, "")

    def _value(self, param: ParamSpec) -> str:
        return str(self.component.params.get(param.name, param.default))

    def _editor(self, param: ParamSpec) -> QWidget:
        value = self._value(param)
        if param.kind in ("device", "device_pin"):
            combo = QComboBox(self)
            combo.setEditable(True)  # (a device not connected now can be named too)
            combo.lineEdit().setPlaceholderText("(the only connected device)" if param.kind == "device" else "(choose)")
            self._fill_options(combo, param, value)
            combo.lineEdit().editingFinished.connect(lambda name=param.name, box=combo: self._changed(name, box.currentText()))
            combo.activated.connect(lambda _index, name=param.name, box=combo: self._changed(name, box.currentText()))
            return combo
        if param.kind == "choice" or param.choices:
            combo = QComboBox(self)
            combo.setEditable(param.kind != "choice")
            for choice in param.choices:
                combo.addItem(choice)
            if combo.findText(value) < 0:
                combo.addItem(value)
            combo.setCurrentText(value)
            if combo.isEditable():
                combo.lineEdit().editingFinished.connect(lambda name=param.name, box=combo: self._changed(name, box.currentText()))
            combo.activated.connect(lambda _index, name=param.name, box=combo: self._changed(name, box.currentText()))
            return combo
        if param.kind == "file":
            holder = QWidget(self)
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(value, holder)
            edit.setPlaceholderText("(none)")
            button = QPushButton("Choose...", holder)
            set_variant(button, "tool")
            row.addWidget(edit, 1)
            row.addWidget(button)
            edit.editingFinished.connect(lambda name=param.name, box=edit: self._changed(name, box.text()))

            def choose(name=param.name, box=edit, suffixes=param.suffixes) -> None:
                patterns = " ".join(f"*{suffix}" for suffix in suffixes) or "*"
                start = self._absolute(box.text()) or self.base_folder
                path, _ = QFileDialog.getOpenFileName(self, param.label, start, f"{param.label} ({patterns});;All files (*)")
                if path:
                    stored = self._relative(path)
                    box.setText(stored)
                    self._changed(name, stored)

            button.clicked.connect(lambda _checked=False: choose())
            return holder
        edit = QLineEdit(value, self)
        if param.default:
            edit.setPlaceholderText(param.default)
        edit.editingFinished.connect(lambda name=param.name, box=edit: self._changed(name, box.text()))
        return edit

    def _absolute(self, path: str) -> str:
        if not path:
            return ""
        return path if os.path.isabs(path) or not self.base_folder else os.path.join(self.base_folder, path)

    def _relative(self, path: str) -> str:
        if self.base_folder:
            try:
                relative = os.path.relpath(path, self.base_folder)
                if not relative.startswith(".."):
                    return relative
            except ValueError:
                pass
        return path

    def _changed(self, name: str, text: str) -> None:
        text = text.strip()
        param = self.spec.param(name) if self.spec is not None else None
        problem = param.check(text) if param is not None and text != "" else None
        if param is not None and param.kind == "quantity" and text == "" and param.default == "":
            problem = None
        self.fields[name].show_message(problem)
        if problem is None and text != self._value(param) if param is not None else False:
            self.param_changed.emit(self.component.id, name, text)

    def _rename(self) -> None:
        new = self.reference.text().strip()
        if new and new != self.component.id:
            self.renamed.emit(self.component.id, new)

    def show_rename_problem(self, text: str) -> None:
        self.fields["#reference"].show_message(text)


class LabelInspector(QWidget):
    renamed = Signal(str, str)
    comment_changed = Signal(str, str)

    def __init__(self, label: NetLabel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(2), space(2), space(2), space(2))
        title = QLabel("Net label", self)
        set_role(title, "heading")
        layout.addWidget(title)
        hint = QLabel("Labels of the same name are one net: the wires between them are not needed.", self)
        hint.setWordWrap(True)
        set_role(hint, "hint")
        layout.addWidget(hint)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name = QLineEdit(label.name, self)
        self.name_field = _Field(self.name, self)
        self.name.editingFinished.connect(self._rename)
        form.addRow("Name", self.name_field)
        layout.addLayout(form)
        self.comment = _comment_rows(self, layout, label.comment, lambda text: self.comment_changed.emit(self.label.id, text))
        layout.addStretch(1)

    def _rename(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.name_field.show_message("a net label needs a name")
            return
        self.name_field.show_message(None)
        if name != self.label.name:
            self.renamed.emit(self.label.id, name)

    def show_rename_problem(self, text: str) -> None:
        self.name_field.show_message(text)

    def sync(self, label: NetLabel) -> None:
        """Show ``label`` as it is now (the field being typed in stays as it is)."""
        self.label = label
        _set_text(self.name, label.name)
        self.comment.set_comment(label.comment)


class WireInspector(QWidget):
    """A wire: its net and its comment."""

    comment_changed = Signal(str, str)

    def __init__(self, wire: Wire, net: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.wire = wire
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(2), space(2), space(2), space(2))
        title = QLabel("Wire", self)
        set_role(title, "heading")
        layout.addWidget(title)
        hint = QLabel(f"On the net {net}." if net else "Not on a net yet.", self)
        hint.setWordWrap(True)
        set_role(hint, "hint")
        layout.addWidget(hint)
        self.hint = hint
        self.comment = _comment_rows(self, layout, wire.comment, lambda text: self.comment_changed.emit(self.wire.id, text))
        layout.addStretch(1)

    def sync(self, wire: Wire, net: str = "") -> None:
        self.wire = wire
        self.hint.setText(f"On the net {net}." if net else "Not on a net yet.")
        self.comment.set_comment(wire.comment)


class GroupInspector(QWidget):
    """A group: its name, its comment, its members; *Ungroup*."""

    #: (group id, "name", "comment" or "color", text)
    changed = Signal(str, str, str)
    ungroup = Signal(str)

    def __init__(self, group: Group, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.group = group
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(2), space(2), space(2), space(2))
        title = QLabel("Group", self)
        set_role(title, "heading")
        layout.addWidget(title)
        hint = QLabel(f"{len(group.members)} members, in a frame with its name and comment; drag the frame to move "
                      "them. Ctrl+G groups the selection, Ctrl+Shift+G takes the group apart.", self)
        hint.setWordWrap(True)
        set_role(hint, "hint")
        layout.addWidget(hint)
        form = QFormLayout()
        self.name = QLineEdit(group.name, self)
        self.name.setPlaceholderText("(no name)")
        self.name.editingFinished.connect(lambda: self.name.text().strip() != self.group.name
                                          and self.changed.emit(self.group.id, "name", self.name.text().strip()))
        form.addRow("Name", self.name)
        self.color = QComboBox(self)
        self.color.addItem("None", "")
        for name in GROUP_COLORS:
            self.color.addItem(icon_swatch(name), name.capitalize(), name)
        self.color.setCurrentIndex(max(self.color.findData(group.color), 0))
        self.color.setToolTip("A light background behind the group (optional)")
        self.color.activated.connect(lambda _index: self.changed.emit(self.group.id, "color", self.color.currentData()))
        form.addRow("Colour", self.color)
        layout.addLayout(form)
        self.comment = _comment_rows(self, layout, group.comment,
                                     lambda text: self.changed.emit(self.group.id, "comment", text))
        button = QPushButton("Ungroup", self)
        button.setToolTip("Take the group apart: its members stay (Ctrl+Shift+G)")
        button.clicked.connect(lambda: self.ungroup.emit(self.group.id))
        layout.addWidget(button)
        layout.addStretch(1)

    def sync(self, group: Group) -> None:
        """Show ``group`` as it is now (the field being typed in stays as it is)."""
        self.group = group
        _set_text(self.name, group.name)
        self.comment.set_comment(group.comment)
        if not self.color.hasFocus():
            self.color.setCurrentIndex(max(self.color.findData(group.color), 0))


class CircuitInspector(QWidget):
    """The circuit: name, settings, model libraries, analyses."""

    #: (key, value): "name", "description", "setting.<name>", "analysis.<kind>.<param>"
    changed = Signal(str, str)
    libraries_changed = Signal(list)
    #: compute an analysis
    compute = Signal(str)

    def __init__(self, schematic: Schematic, base_folder: str = "", busy: Callable[[], bool] = lambda: False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.schematic = schematic
        self.base_folder = base_folder
        self.fields: dict[str, _Field] = {}
        self.choices: dict[str, QComboBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(2), space(2), space(2), space(2))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        name = QLineEdit(schematic.name, self)
        self.name_edit = name
        name.editingFinished.connect(lambda: self.changed.emit("name", name.text().strip() or "Circuit"))
        form.addRow("Name", name)
        description = QLineEdit(schematic.description, self)
        self.description_edit = description
        description.editingFinished.connect(lambda: self.changed.emit("description", description.text()))
        form.addRow("Description", description)
        layout.addLayout(form)
        layout.addWidget(_section(self, "Simulation"))
        settings = QFormLayout()
        settings.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for key in SETTINGS:
            title, unit, tip = SETTING_TITLES.get(key, (key, "", ""))
            if key in SETTING_CHOICES:
                choice = QComboBox(self)
                choice.addItems(SETTING_CHOICES[key])
                choice.setCurrentText(schematic.setting(key))
                choice.setToolTip(tip)
                self.choices[key] = choice
                choice.currentTextChanged.connect(lambda text, key=key: self._setting(key, text))
                settings.addRow(title, choice)
                continue
            edit = QLineEdit(str(schematic.settings.get(key, "")), self)
            edit.setPlaceholderText(SETTINGS[key] or "(ngspice decides)")
            edit.setToolTip(tip)
            field = _Field(edit, self)
            self.fields[f"setting.{key}"] = field
            edit.editingFinished.connect(lambda key=key, box=edit: self._setting(key, box.text()))
            settings.addRow(title + (f" ({unit})" if unit else ""), field)
        layout.addLayout(settings)
        layout.addWidget(_section(self, "Model libraries"))
        hint = QLabel("SPICE models of makers (.lib, .mod, .sub) used by this circuit before the others.", self)
        hint.setWordWrap(True)
        set_role(hint, "hint")
        hint.setFont(smaller_font(hint.font()))
        layout.addWidget(hint)
        self.libraries = QListWidget(self)
        self.libraries.addItems(schematic.libraries)
        self.libraries.setMaximumHeight(size("note-tall"))
        layout.addWidget(self.libraries)
        row = QHBoxLayout()
        add = QPushButton("Add...", self)
        remove = QPushButton("Remove", self)
        for button in (add, remove):
            set_variant(button, "tool")
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        add.clicked.connect(self._add_library)
        remove.clicked.connect(self._remove_library)
        layout.addWidget(_section(self, "Analyses"))
        for kind, defaults in ANALYSES.items():
            box = QWidget(self)
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, space(1))
            header = QHBoxLayout()
            title = QLabel(ANALYSIS_TITLES[kind], box)
            header.addWidget(title, 1)
            button = QPushButton("Compute", box)
            set_variant(button, "tool")
            button.setToolTip(f"Compute the {ANALYSIS_TITLES[kind].lower()} and show it under Results")
            button.clicked.connect(lambda _checked=False, kind=kind: self.compute.emit(kind))
            header.addWidget(button)
            column.addLayout(header)
            params = QFormLayout()
            params.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            current = schematic.analyses.get(kind, {})
            for param, default in defaults.items():
                if param == "scale":
                    combo = QComboBox(box)
                    combo.addItems(["dec", "oct", "lin"])
                    combo.setCurrentText(current.get(param, default))
                    combo.activated.connect(lambda _i, kind=kind, param=param, combo=combo:
                                            self.changed.emit(f"analysis.{kind}.{param}", combo.currentText()))
                    params.addRow(PARAM_TITLES.get(param, param), combo)
                    continue
                edit = QLineEdit(current.get(param, ""), box)
                edit.setPlaceholderText(default or "(none)")
                field = _Field(edit, box)
                self.fields[f"analysis.{kind}.{param}"] = field
                edit.editingFinished.connect(lambda kind=kind, param=param, box=edit: self._analysis(kind, param, box.text()))
                params.addRow(PARAM_TITLES.get(param, param), field)
            column.addLayout(params)
            layout.addWidget(box)
        layout.addStretch(1)

    def sync(self, schematic: Schematic) -> None:
        """Show the circuit as it is now (the field being typed in stays as it is)."""
        self.schematic = schematic
        _set_text(self.name_edit, schematic.name)
        _set_text(self.description_edit, schematic.description)
        for key in SETTINGS:
            field = self.fields.get(f"setting.{key}")
            if field is not None:
                _set_text(field.editor, str(schematic.settings.get(key, "")))
            choice = self.choices.get(key)
            if choice is not None and choice.currentText() != schematic.setting(key):
                blocked = choice.blockSignals(True)
                choice.setCurrentText(schematic.setting(key))
                choice.blockSignals(blocked)
        for kind, defaults in ANALYSES.items():
            for param in defaults:
                field = self.fields.get(f"analysis.{kind}.{param}")
                if field is not None:
                    _set_text(field.editor, schematic.analyses.get(kind, {}).get(param, ""))
        if [self.libraries.item(i).text() for i in range(self.libraries.count())] != list(schematic.libraries):
            self.libraries.clear()
            self.libraries.addItems(schematic.libraries)

    def _setting(self, key: str, text: str) -> None:
        text = text.strip()
        if key in SETTING_CHOICES:
            if text != self.schematic.setting(key):
                self.changed.emit(f"setting.{key}", "" if text == SETTINGS[key] else text)
            return
        problem = None
        if text and key != "speed" and values.parse_optional(text) is None:
            problem = f"{text!r} is no value"
        if key in ("speed", "tolerance") and text and (values.parse_optional(text) or 0) <= 0:
            problem = "more than 0"
        self.fields[f"setting.{key}"].show_message(problem)
        if problem is None and text != self.schematic.settings.get(key, ""):
            self.changed.emit(f"setting.{key}", text)

    def _analysis(self, kind: str, param: str, text: str) -> None:
        text = text.strip()
        problem = None
        if text and param not in ("source", "output") and values.parse_optional(text) is None:
            problem = f"{text!r} is no value"
        self.fields[f"analysis.{kind}.{param}"].show_message(problem)
        if problem is None and text != self.schematic.analyses.get(kind, {}).get(param, ""):
            self.changed.emit(f"analysis.{kind}.{param}", text)

    def _add_library(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Model libraries", self.base_folder,
                                                "SPICE models (*.lib *.mod *.sub *.cir *.sp *.spice *.txt);;All files (*)")
        if not paths:
            return
        current = list(self.schematic.libraries)
        for path in paths:
            stored = path
            if self.base_folder:
                try:
                    relative = os.path.relpath(path, self.base_folder)
                    if not relative.startswith(".."):
                        stored = relative
                except ValueError:
                    pass
            if stored not in current:
                current.append(stored)
        self.libraries_changed.emit(current)

    def _remove_library(self) -> None:
        item = self.libraries.currentItem()
        if item is None:
            return
        self.libraries_changed.emit([path for path in self.schematic.libraries if path != item.text()])
