# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Inspector content of the flow document: the parameters of a node (built from its type), or the
flow itself (name, settings) when nothing is selected. Changes go to the model at once."""

from __future__ import annotations

from typing import Any, Callable, Optional

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import units
from ...lab.model import Flow, Node
from ...lab.nodes.registry import CLOSED_SUGGESTIONS, NodeSpec, ParamSpec, locked_reason
from ...lab.yaml_io import safe_load as yaml_safe_load
from ..icons import set_icon
from ..theme import DEFAULT_PICK, set_role, size, tabular_font


def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    from ...lab.yaml_io import Dumper

    return yaml.dump(value, Dumper=Dumper, default_flow_style=True, sort_keys=False,
                     allow_unicode=True).strip().removesuffix("...").strip()


def to_block_text(value: Any) -> str:
    """``value`` as YAML in block style, the innermost collections on one line (a big text field)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    from ...lab.yaml_io import Dumper

    return yaml.dump(value, Dumper=Dumper, default_flow_style=None, sort_keys=False, allow_unicode=True,
                     width=72).strip().removesuffix("...").strip()


#: values longer than this get a big text field (when they are mappings or lists)
LONG_TEXT = 48


class YamlEdit(QPlainTextEdit):
    """A big text field for a mapping or a list in YAML: ``committed(text)`` when it loses the focus
    or on Ctrl+Return."""

    committed = Signal(str)

    def __init__(self, text: str, parent: Optional[QWidget] = None, lines: int = 6) -> None:
        super().__init__(text, parent)

        self.setFont(tabular_font(self.font()))
        self.setTabStopDistance(24)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        # as high as its text (3 lines at least, ``lines`` at most; more scroll)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._lines = max(lines, 3)
        self._fit()
        self.textChanged.connect(self._fit)
        self._last = text

    def _fit(self) -> None:
        shown = min(max(self.document().blockCount(), 3), self._lines)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * shown + 2 * self.frameWidth()
                            + int(self.document().documentMargin() * 2) + 4)

    def commit(self) -> None:
        text = self.toPlainText()
        if text != self._last:
            self._last = text
            self.committed.emit(text)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.commit()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.commit()
            return
        super().keyPressEvent(event)


def from_text(text: str, kind: str) -> Any:
    """The value a text field means for a parameter of ``kind`` (``None``: not set)."""
    text = text.strip()
    if text == "":
        return None
    if kind in ("str", "path", "pin", "quantity", "address", "choice", "code"):
        if kind == "quantity":
            try:
                return yaml_safe_load(text) if not any(char.isalpha() or char == "%" for char in text) else text
            except yaml.YAMLError:
                return text
        return text
    if kind == "int":
        return int(text)
    if kind == "float":
        return float(text)
    if kind == "bool":
        return text.lower() in ("1", "true", "yes", "on")
    return yaml_safe_load(text)


def flow_path(path: str, folder: str, base: str = "") -> str:
    """``path`` as a node parameter, so that the flow finds the same file again: a plain name
    for a file in the data folder ``folder`` (where the flow looks for plain names), a path
    relative to ``base`` (the project folder, where it looks for paths with a folder) for a file
    below it, else the absolute path."""
    import os

    absolute = os.path.abspath(path)
    base = base or folder
    if folder and os.path.dirname(absolute) == os.path.abspath(folder):
        return os.path.basename(absolute)
    if base and absolute.startswith(os.path.abspath(base) + os.sep):
        relative = os.path.relpath(absolute, base).replace(os.sep, "/")
        return relative if "/" in relative else f"./{relative}"
    return path


class NodeInspector(QWidget):
    """Edits one node. ``changed(node_id, name, value)``; ``value`` ``None`` removes it."""

    param_changed = Signal(str, str, object)
    #: a wire from an output (``node.port``) to an input of this node, or its removal
    wire_requested = Signal(str, str)
    unwire_requested = Signal(str, str)
    renamed = Signal(str, str)
    comment_changed = Signal(str, str)
    breakpoint_toggled = Signal(str, bool)

    def __init__(self, flow: Flow, node: Node, spec: Optional[NodeSpec], breakpoint: bool = False,
                 parent: Optional[QWidget] = None, addresses: Optional[list[tuple[str, str]]] = None,
                 problems: Optional[list[str]] = None, running: bool = False,
                 suggestions: Optional[dict[str, list[str]]] = None, folder: str = "", base: str = "",
                 devices: Optional[list[str]] = None, wide: bool = False) -> None:
        super().__init__(parent)
        #: in a window of its own (the node editor): bigger text fields
        self.wide = wide
        self.flow = flow
        self.node = node
        self.spec = spec
        #: (label, address) offered for parameters of kind ``address``
        self.addresses = list(addresses or [])
        #: values to offer per parameter (pins and channels of the device the node is wired to, the
        #: channels of the capture wired to it, ...; see :mod:`openscilab.lab.hints`)
        self.suggestions = dict(suggestions or {})
        #: the device nodes of the flow (``ctx.device("…")`` in Python code)
        self.devices = list(devices or [])
        #: where relative paths start (*Browse…* of ``path`` parameters)
        self.folder = folder
        #: where the flow resolves relative paths with a folder in them (the project folder)
        self.base = base or folder
        self.editors: dict[str, QWidget] = {}
        self.errors: dict[str, str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(4)
        title = QLabel(spec.title if spec else node.type, self)
        set_role(title, "heading")
        layout.addWidget(title)
        kind = QLabel(f"{spec.group_title} · {node.type}" if spec else node.type, self)
        set_role(kind, "hint")
        kind.setWordWrap(True)
        layout.addWidget(kind)
        if spec is not None and spec.description:
            description = QLabel(spec.description, self)
            description.setWordWrap(True)
            set_role(description, "hint")
            layout.addWidget(description)
        if problems:
            # what the check found at this node: errors keep the flow from running, warnings may not
            for severity, text in (problem if isinstance(problem, tuple) else ("error", str(problem))
                                   for problem in problems):
                label = QLabel(f"{'Error' if severity == 'error' else 'Warning'}: {text}", self)
                label.setWordWrap(True)
                set_role(label, "error" if severity == "error" else "warning")
                layout.addWidget(label)
            self.problems_label = label
        if running:
            note = QLabel("The flow is running: changes apply on its next run.", self)
            note.setWordWrap(True)
            set_role(note, "warning")
            layout.addWidget(note)

        #: the input ports with a wire (a parameter they set is locked)
        self.wired = sorted({edge.target.port for edge in flow.edges if edge.target.node == node.id})
        defaults = {param.name: param.default for param in (spec.params if spec else [])}
        #: why a parameter is locked, by name
        self.locked: dict[str, str] = {}
        #: the message under each field (what is wrong with what was typed)
        self.messages: dict[str, QLabel] = {}
        params = [param for param in (spec.params if spec else []) if param.editor != "hidden"]
        basic = [param for param in params if not param.advanced]
        advanced = [param for param in params if param.advanced]
        if basic:
            layout.addWidget(self._section("Settings"))
            layout.addLayout(self._form(basic, defaults))
        # parameters the type does not know (kept, shown as text)
        unknown = [name for name in node.params if spec is None or spec.param(name) is None]
        if unknown:
            layout.addWidget(self._section("Not of this node type"))
            form = QFormLayout()
            for name in unknown:
                editor = QLineEdit(to_text(node.params[name]), self)
                editor.editingFinished.connect(lambda name=name, editor=editor: self._emit(name, editor.text(), "any"))
                editor.setToolTip("A setting this node type does not know: it is kept as it is")
                self.editors[name] = editor
                form.addRow(name, editor)
            layout.addLayout(form)
        if advanced:
            # settings most nodes leave as they are: folded away unless one of them is set
            self.advanced_button = QToolButton(self)
            self.advanced_button.setText(f"Advanced ({len(advanced)})")
            self.advanced_button.setCheckable(True)
            self.advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.advanced_button.setAutoRaise(True)
            layout.addWidget(self.advanced_button)
            self.advanced_box = QWidget(self)
            box_layout = QVBoxLayout(self.advanced_box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.addLayout(self._form(advanced, defaults))
            layout.addWidget(self.advanced_box)
            opened = any(node.params.get(param.name) is not None for param in advanced)

            def fold(shown: bool) -> None:
                self.advanced_box.setVisible(shown)
                set_icon(self.advanced_button, "down" if shown else "arrow-right")

            self.advanced_button.toggled.connect(fold)
            self.advanced_button.setChecked(opened)
            fold(opened)

        layout.addWidget(self._section("Node"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.name_edit = QLineEdit(node.id, self)
        self.name_edit.setToolTip("The name of the node in the flow: wires and panels refer to it")
        self.name_edit.editingFinished.connect(self._rename)
        form.addRow("Name", self.name_edit)
        self.comment_edit = QLineEdit(node.comment, self)
        self.comment_edit.setPlaceholderText("A note (shown in its tool tip)")
        self.comment_edit.editingFinished.connect(lambda: self.comment_changed.emit(self.node.id, self.comment_edit.text()))
        form.addRow("Comment", self.comment_edit)
        layout.addLayout(form)
        self.breakpoint_box = QCheckBox("Stop before this node (breakpoint)", self)
        self.breakpoint_box.setToolTip("The run pauses before this node handles a value; Step (F10) goes on by one")
        self.breakpoint_box.setChecked(breakpoint)
        self.breakpoint_box.toggled.connect(lambda checked: self.breakpoint_toggled.emit(self.node.id, checked))
        layout.addWidget(self.breakpoint_box)
        # (what was typed wrong shows under its field; this line says it once more for a reader)
        self.error_label = QLabel("", self)
        set_role(self.error_label, "error")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        layout.addStretch(1)

    def _section(self, title: str) -> QLabel:
        label = QLabel(title.upper(), self)
        set_role(label, "section")
        label.setContentsMargins(0, 8, 0, 0)
        return label

    def _form(self, params: list, defaults: dict) -> QFormLayout:
        """A row per parameter: its label (and unit), its editor, a message below it."""
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for param in params:
            editor = self._editor(param)
            self.editors[param.name] = editor
            label = param.display_name + (f" ({param.unit})" if param.unit else "") + (" *" if param.required else "")
            holder = QWidget(self)
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            column.addWidget(editor)
            message = QLabel(holder)
            message.setWordWrap(True)
            set_role(message, "error")
            message.setVisible(False)
            column.addWidget(message)
            self.messages[param.name] = message
            form.addRow(label, holder)
            tip = (param.description or param.display_name) + ("\nRequired." if param.required else "")
            if param.label and param.label != param.name:
                tip += f"\n(stored as '{param.name}')"
            reason = locked_reason(param, self.node.params, defaults, self.wired)
            if reason:
                self.locked[param.name] = reason
                editor.setEnabled(False)
                tip = f"Locked: {reason}.\n{tip}"
            editor.setToolTip(tip)
            label_widget = form.labelForField(holder)
            if label_widget is not None:
                label_widget.setToolTip(tip)
        return form

    def _editor(self, param: ParamSpec) -> QWidget:
        value = self.node.params.get(param.name)
        if param.kind == "bool":
            box = QCheckBox(self)
            box.setChecked(bool(value if value is not None else param.default))
            box.toggled.connect(lambda checked, name=param.name: self.param_changed.emit(self.node.id, name, checked))
            return box
        if param.kind == "address":
            combo = QComboBox(self)
            combo.setEditable(True)
            combo.setToolTip("sim:<profile>, pico:<port>, dslogic, ... – or an instrument open in the device list; "
                             "empty: the project's device of this name")
            for label, address in self.addresses:
                combo.addItem(f"{label}  –  {address}", address)
            combo.setCurrentIndex(combo.findData(value) if value is not None else -1)
            combo.setEditText(str(value) if value is not None else "")

            def commit(_index=None, name=param.name, combo=combo) -> None:
                text = combo.currentText()
                data = combo.currentData() if combo.currentIndex() >= 0 and combo.itemText(combo.currentIndex()) == text \
                    else text.strip()
                if data != self.node.params.get(name):
                    self.param_changed.emit(self.node.id, name, data)

            combo.activated.connect(commit)
            combo.lineEdit().editingFinished.connect(commit)
            return combo
        offered = self.suggestions.get(param.name)
        if param.editor in ("steps", "states"):
            from .editors import StatesEditor, StepsEditor

            editor = (StepsEditor if param.editor == "steps" else StatesEditor)(value, self)
            editor.changed.connect(lambda new, name=param.name: self.param_changed.emit(self.node.id, name, new)
                                   if self.node.params.get(name) != new else None)
            return editor
        if param.editor == "pattern":
            return self._pattern_editor(param, value, offered)
        if param.editor == "inputs":
            return self._inputs_editor(param, value, offered or [])
        if param.editor == "trigger":
            return self._trigger_editor(param, value)
        if param.editor == "color":
            return self._color_editor(param, value)
        if param.editor == "mapping":
            return self._free_mapping_editor(param, value, offered or [], self.suggestions.get(f"{param.name}#values"))
        # a closed set that is known (the channels of the device, the columns of the input): a choice
        closed = offered is not None and bool(offered) and param.suggest.split(":", 1)[0] in CLOSED_SUGGESTIONS
        if offered is not None and param.kind == "dict" and param.keys:
            return self._mapping_editor(param, value, offered, closed)
        if offered is not None and param.kind == "list":
            return self._list_editor(param, value, offered, closed)
        if param.kind == "list" and param.choices:
            return self._list_editor(param, value, [str(choice) for choice in param.choices], True)
        if param.kind == "str" and param.choices:
            # common values to pick, any other may be typed (units, "auto")
            combo = QComboBox(self)
            combo.setEditable(True)
            combo.addItems([str(choice) for choice in param.choices])
            combo.setEditText(to_text(value) if value is not None else "")
            if param.default not in (None, ""):
                combo.lineEdit().setPlaceholderText(to_text(param.default))
            combo.activated.connect(lambda _index, name=param.name, combo=combo: self._emit(name, combo.currentText(), "str"))
            combo.lineEdit().editingFinished.connect(
                lambda name=param.name, combo=combo: self._emit(name, combo.currentText(), "str"))
            return combo
        if closed and param.kind in ("str", "pin", "any"):
            return self._choice_editor(param, value, offered)
        if offered is not None and param.kind in ("str", "pin", "any"):
            combo = QComboBox(self)
            combo.setEditable(True)
            combo.addItems([str(item) for item in offered])
            combo.setEditText(to_text(value) if value is not None else "")
            if param.default is not None:
                combo.lineEdit().setPlaceholderText(to_text(param.default))
            kind = "pin" if param.kind in ("str", "pin") else param.kind
            combo.activated.connect(lambda _index, name=param.name, combo=combo, kind=kind: self._emit(
                name, combo.currentText(), kind))
            combo.lineEdit().editingFinished.connect(lambda name=param.name, combo=combo, kind=kind: self._emit(
                name, combo.currentText(), kind))
            return combo
        if param.kind == "dict" and param.keys:
            return self._mapping_editor(param, value, [])
        if param.kind == "dict" or (param.kind in ("list", "any") and isinstance(value, (dict, list))
                                    and len(to_text(value)) > LONG_TEXT):
            # a mapping (the states of a state machine, the signals of a simulator) or a long list:
            # a big text field, one entry a line
            edit = YamlEdit(to_block_text(value), self, lines=14 if self.wide else 6)
            if param.default not in (None, {}, []):
                edit.setPlaceholderText(to_block_text(param.default))
            edit.setToolTip("YAML, one entry a line; applied when the field is left or with Ctrl+Return")
            kind = "dict" if param.kind == "dict" else "any"
            edit.committed.connect(lambda text, name=param.name, kind=kind: self._emit(name, text, kind))
            return edit
        if param.kind == "path":
            holder = QWidget(self)
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(to_text(value), holder)
            if param.default is not None:
                edit.setPlaceholderText(to_text(param.default))
            edit.editingFinished.connect(lambda name=param.name, edit=edit: self._emit(name, edit.text(), "path"))
            row.addWidget(edit, 1)
            browse = QPushButton("...", holder)
            browse.setToolTip("Choose a file")
            browse.setFixedWidth(size("icon-button"))
            browse.clicked.connect(lambda _checked=False, name=param.name, edit=edit: self._browse(name, edit))
            row.addWidget(browse)
            holder.edit = edit
            return holder
        if param.kind == "choice":
            combo = QComboBox(self)
            choices = list(param.choices)
            combo.addItem("", None)
            for choice in choices:
                combo.addItem(str(choice), choice)
            if value is not None and combo.findData(value) < 0:
                combo.addItem(str(value), value)
            combo.setCurrentIndex(max(combo.findData(value), 0) if value is not None else 0)
            combo.currentIndexChanged.connect(
                lambda _index, name=param.name, combo=combo: self.param_changed.emit(self.node.id, name, combo.currentData()))
            return combo
        if param.kind == "code":
            edit = QPlainTextEdit(to_text(value if value is not None else param.default), self)
            edit.setMinimumHeight(180 if self.wide else 90)
            edit.setTabStopDistance(28)
            if param.name == "code":  # Python (a comment's text is kind code too)

                from ..widgets.completer import attach
                from .highlighter import PythonHighlighter

                edit.setFont(tabular_font(edit.font()))
                edit.setMinimumHeight(360 if self.wide else 160)
                edit.highlighter = PythonHighlighter(edit.document())
                edit.completer = attach(edit, self._python_completions)
            commit = _Debounced(lambda name=param.name, edit=edit: self.param_changed.emit(
                self.node.id, name, edit.toPlainText()))
            edit.textChanged.connect(commit)
            return edit
        edit = QLineEdit(to_text(value), self)
        if param.default is not None:
            unit = f" {param.unit}" if param.unit and isinstance(param.default, (int, float)) else ""
            edit.setPlaceholderText(f"{to_text(param.default)}{unit}")
        if param.kind == "int":
            from PySide6.QtGui import QIntValidator

            edit.setValidator(QIntValidator(int(param.minimum) if param.minimum is not None else -2**31,
                                            int(param.maximum) if param.maximum is not None else 2**31 - 1, edit))
        edit.editingFinished.connect(lambda name=param.name, edit=edit, kind=param.kind: self._emit(name, edit.text(), kind))
        return edit

    def _python_completions(self, text: str):
        """Completions in the node's Python code: ctx, np, signals, units and its port names."""
        from ...lab.completion import python_completions

        inputs = [str(name) for name in self.node.params.get("inputs") or []]
        outputs = [str(name) for name in self.node.params.get("outputs", ["out"]) or []]
        if self.spec is not None:  # the ports as the type makes them (also those it always has)
            spec_inputs, spec_outputs = self.spec.resolve_ports(self.node.params)
            inputs = list(dict.fromkeys(inputs + [port.name for port in spec_inputs]))
            outputs = list(dict.fromkeys(outputs + [port.name for port in spec_outputs]))
        return python_completions(text, inputs=inputs, outputs=outputs, devices=self.devices)

    def _choice_editor(self, param: ParamSpec, value: Any, offered: list[str]) -> QWidget:
        """One of the values the context knows (the channels of the device, the columns of the
        input): a choice, no free text; a value that is not among them stays, marked."""
        combo = QComboBox(self)
        combo.addItem("", None)
        for item in offered:
            combo.addItem(str(item), str(item))
        current = to_text(value) if value not in (None, "") else None
        if current is not None and combo.findData(current) < 0:
            combo.addItem(f"{current} (not offered)", current)
        combo.setCurrentIndex(max(combo.findData(current), 0) if current is not None else 0)
        if param.default not in (None, ""):
            combo.setItemText(0, f"({to_text(param.default)})")
        kind = "pin" if param.kind in ("str", "pin") else param.kind

        def commit(_index: int, name=param.name) -> None:
            data = combo.currentData()
            if data is None:
                if self.node.params.get(name) is not None:
                    self.param_changed.emit(self.node.id, name, None)
                return
            self._emit(name, str(data), kind)

        combo.currentIndexChanged.connect(commit)
        return combo

    def _wired_from(self, names: list[str]) -> dict[str, list[str]]:
        """The outputs (``node.port``) wired into each of the inputs ``names`` of this node."""
        found: dict[str, list[str]] = {name: [] for name in names}
        for edge in self.flow.edges:
            if edge.target.node == self.node.id and edge.target.port in found:
                found[edge.target.port].append(str(edge.source))
        return found

    def _inputs_editor(self, param: ParamSpec, value: Any, offered: list[str]) -> QWidget:
        """Inputs by name (a strip chart's traces, the columns of a table): ticking an output of the
        flow adds an input named after it and wires it; unticking removes both. The names can also
        be typed (an input wired later)."""
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        names = [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        edit = QLineEdit(to_text(value) if names else "", holder)
        edit.setPlaceholderText("choose outputs of the flow ▾")
        edit.editingFinished.connect(lambda name=param.name, edit=edit: self._emit(name, edit.text(), "list"))
        row.addWidget(edit, 1)
        button = QToolButton(holder)
        button.setText("▾")
        button.setToolTip("Outputs of the flow: a tick adds an input for it and wires it")
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        wired = self._wired_from(names)
        sources = {source: port for port, items in wired.items() for source in items}

        def toggled(checked: bool, source: str, name=param.name) -> None:
            current = [str(item) for item in self.node.params.get(name) or []]
            if checked:
                port = source.split(".", 1)[1]
                taken = set(current) | {spec_port.name for spec_port in (self.spec.inputs if self.spec else [])}
                if port in taken:
                    port = source.replace(".", "_")
                self.param_changed.emit(self.node.id, name, current + [port])
                self.wire_requested.emit(source, f"{self.node.id}.{port}")
                return
            port = sources.get(source)
            if port is None:
                return
            self.unwire_requested.emit(source, f"{self.node.id}.{port}")
            if len(wired.get(port, [])) <= 1:  # its only wire: the input goes too
                self.param_changed.emit(self.node.id, name, [item for item in current if item != port] or None)

        for source in offered:
            action = menu.addAction(source)
            action.setCheckable(True)
            action.setChecked(source in sources)
            action.toggled.connect(lambda checked, source=source: toggled(checked, source))
        if not offered:
            menu.addAction("no outputs in the flow yet").setEnabled(False)
        button.setMenu(menu)
        row.addWidget(button)
        holder.edit, holder.menu = edit, menu
        return holder

    def _trigger_editor(self, param: ParamSpec, value: Any) -> QWidget:
        """A capture's trigger: none, an edge of a channel, or a pattern from a channel on."""
        holder = QWidget(self)
        form = QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        trigger = value if isinstance(value, dict) else {}
        kind = QComboBox(holder)
        kind.addItem("none (at once)", "none")
        kind.addItem("edge", "edge")
        kind.addItem("pattern", "pattern")
        kind.setCurrentIndex(2 if "pattern" in trigger else 1 if trigger else 0)
        edge = QComboBox(holder)
        edge.addItems(["rising", "falling"])
        edge.setCurrentText(str(trigger.get("edge", "rising")))
        source = QComboBox(holder)
        channels = [str(item) for item in self.node.params.get("channels") or []]
        source.addItems(channels)
        current = str(trigger.get("source", channels[0] if channels else ""))
        if current and source.findText(current) < 0:
            source.addItem(current)
        source.setCurrentText(current)
        source.setToolTip("one of the captured channels (add it to 'channels' first)")
        pattern = QLineEdit(str(trigger.get("pattern", "")), holder)
        pattern.setPlaceholderText("0, 1 or x per channel from the source on, e.g. 10x1")
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QRegularExpressionValidator

        pattern.setValidator(QRegularExpressionValidator(QRegularExpression("[01xX]*"), pattern))
        form.addRow("kind", kind)
        form.addRow("edge", edge)
        form.addRow("source", source)
        form.addRow("pattern", pattern)

        def update_enabled() -> None:
            chosen = kind.currentData()
            edge.setEnabled(chosen == "edge")
            source.setEnabled(chosen != "none")
            pattern.setEnabled(chosen == "pattern")

        def commit(*_args, name=param.name) -> None:
            update_enabled()
            chosen = kind.currentData()
            if chosen == "none":
                result = None
            elif chosen == "edge":
                result = {"edge": edge.currentText(), "source": source.currentText()}
            else:
                result = {"pattern": pattern.text().lower() or "1", "source": source.currentText()}
            if self.node.params.get(name) != result:
                self.param_changed.emit(self.node.id, name, result)

        update_enabled()
        kind.currentIndexChanged.connect(commit)
        edge.currentIndexChanged.connect(commit)
        source.currentIndexChanged.connect(commit)
        pattern.editingFinished.connect(commit)
        holder.kind, holder.edge, holder.source, holder.pattern = kind, edge, source, pattern
        return holder

    def _color_editor(self, param: ParamSpec, value: Any) -> QWidget:
        """A colour: a button that shows it and opens the colour dialog."""
        from PySide6.QtGui import QColor

        button = QPushButton(to_text(value) if value else "default", self)
        if value and QColor(str(value)).isValid():
            button.setStyleSheet(f"QPushButton {{ border-left: 14px solid {QColor(str(value)).name()}; }}")

        def choose(_checked: bool = False, name=param.name) -> None:
            from PySide6.QtWidgets import QColorDialog

            color = QColorDialog.getColor(QColor(str(value) if value else DEFAULT_PICK), self, name)
            if color.isValid():
                self.param_changed.emit(self.node.id, name, color.name())

        button.clicked.connect(choose)
        return button

    def _free_mapping_editor(self, param: ParamSpec, value: Any, keys: list[str],
                             values: Optional[list[str]]) -> QWidget:
        """A mapping whose keys come from the flow (the channels of a capture wired in) and whose
        values are chosen too (pins of the device): a choice per key; keys kept that are not offered."""
        holder = QWidget(self)
        form = QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        mapping = {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}
        shown = list(dict.fromkeys(keys + list(mapping)))
        holder.boxes = {}

        def commit(name=param.name) -> None:
            result = {key: box.currentText().strip() for key, box in holder.boxes.items() if box.currentText().strip()}
            if self.node.params.get(name) != (result or None):
                self.param_changed.emit(self.node.id, name, result or None)

        for key in shown:
            box = QComboBox(holder)
            box.setEditable(values is None)
            box.addItem("")
            box.addItems([str(item) for item in values or []])
            current = to_text(mapping.get(key)) if mapping.get(key) is not None else ""
            if current and box.findText(current) < 0:
                box.addItem(current)
            box.setCurrentText(current)
            box.currentIndexChanged.connect(lambda _index: commit())
            if box.isEditable():
                box.lineEdit().editingFinished.connect(commit)
            holder.boxes[key] = box
            form.addRow(key, box)
        if not shown:
            hint = QLabel("wire a capture to 'capture' to map its channels", holder)
            set_role(hint, "hint")
            form.addRow(hint)
        return holder

    def _pattern_editor(self, param: ParamSpec, value: Any, offered: Optional[list[str]]) -> QWidget:
        """The tracks of a pattern: what they are, and the pattern editor (blocks per pin)."""
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        tracks = value if isinstance(value, dict) else {}
        summary = QLabel(f"{len(tracks)} pin{'s' if len(tracks) != 1 else ''}: {', '.join(map(str, tracks))}"
                         if tracks else "no pins yet", holder)
        summary.setWordWrap(True)
        row.addWidget(summary, 1)
        button = QPushButton("Edit pattern…", holder)
        button.setToolTip("A block per pin (clock, square wave, bytes, ...) with a preview; the SDL text behind it")

        def edit(_checked: bool = False, name=param.name) -> None:
            from .pattern_editor import PatternEditor

            dialog = PatternEditor(tracks, self.node.params.get("rate", "1 MHz"), pins=offered, parent=self)
            if dialog.exec() != PatternEditor.Accepted:
                return
            if dialog.result_rate != str(self.node.params.get("rate", "")):
                self.param_changed.emit(self.node.id, "rate", dialog.result_rate)
            if dialog.result_tracks != tracks:
                self.param_changed.emit(self.node.id, name, dialog.result_tracks)

        button.clicked.connect(edit)
        row.addWidget(button)
        holder.button, holder.summary = button, summary
        return holder

    def _list_editor(self, param: ParamSpec, value: Any, offered: list[str], closed: bool = False) -> QWidget:
        """A list parameter: its text, and a menu to tick the values the device has (only the
        menu for a closed set)."""
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(to_text(value), holder)
        edit.setReadOnly(closed)
        edit.setPlaceholderText(to_text(param.default) if param.default is not None else "choose from the list ▾")
        edit.editingFinished.connect(lambda name=param.name, edit=edit: self._emit(name, edit.text(), "list"))
        row.addWidget(edit, 1)
        button = QToolButton(holder)
        button.setText("▾")
        button.setToolTip(f"Choose {param.name}")
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        current = [str(item) for item in value] if isinstance(value, (list, tuple)) else []

        def toggled(_checked: bool, name=param.name) -> None:
            chosen = [action.text() for action in menu.actions() if action.isChecked()]
            kept = [item for item in current if item not in offered]  # values the device does not tell of
            values = kept + chosen
            edit.setText(to_text(values) if values else "")
            if self.node.params.get(name) != (values or None):
                self.param_changed.emit(self.node.id, name, values or None)

        for item in offered:
            action = menu.addAction(str(item))
            action.setCheckable(True)
            action.setChecked(str(item) in current)
            action.toggled.connect(toggled)
        button.setMenu(menu)
        row.addWidget(button)
        holder.edit, holder.menu = edit, menu
        return holder

    def _mapping_editor(self, param: ParamSpec, value: Any, offered: list[str], closed: bool = False) -> QWidget:
        """A mapping of fixed names (a decoder's channels) to values: a field for each name."""
        holder = QWidget(self)
        form = QFormLayout(holder)
        form.setContentsMargins(0, 0, 0, 0)
        mapping = dict(value) if isinstance(value, dict) else {}
        holder.boxes = {}

        def commit(name=param.name) -> None:
            values = {key: box.currentText().strip() for key, box in holder.boxes.items() if box.currentText().strip()}
            values.update({key: item for key, item in mapping.items() if key not in holder.boxes})
            if self.node.params.get(name) != (values or None):
                self.param_changed.emit(self.node.id, name, values or None)

        for key in param.keys:
            box = QComboBox(holder)
            box.setEditable(True)
            box.addItem("")
            box.addItems([str(item) for item in offered])
            current = to_text(mapping.get(key)) if mapping.get(key) is not None else ""
            if closed and current and box.findText(current) < 0:
                box.addItem(current)  # (kept: a channel the input does not tell of)
            box.setEditText(current)
            box.lineEdit().setPlaceholderText("not wired")
            box.activated.connect(lambda _index: commit())
            box.lineEdit().editingFinished.connect(commit)
            if closed:
                box.lineEdit().setReadOnly(True)  # a choice of the channels there are
            holder.boxes[key] = box
            form.addRow(key, box)
        return holder

    def show_message(self, name: str, text: str) -> None:
        """What is wrong with what was typed into ``name``, under its field ("" clears it)."""
        message = self.messages.get(name)
        if message is not None:
            message.setText(text)
            message.setVisible(bool(text))
        if text:
            self.errors[name] = text
        else:
            self.errors.pop(name, None)
        label = self.spec.param(name).display_name if self.spec is not None and self.spec.param(name) else name
        self.error_label.setText("\n".join(f"{key}: {value}" for key, value in self.errors.items()))
        self.error_label.setVisible(bool(self.errors) and message is None)
        del label

    def _emit(self, name: str, text: str, kind: str) -> None:
        try:
            value = from_text(text, kind)
            spec = self.spec.param(name) if self.spec else None
            if spec is not None and spec.kind == "quantity" and isinstance(value, str):
                units.parse(value, spec.unit)
            if spec is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
                if spec.minimum is not None and value < spec.minimum:
                    raise ValueError(f"at least {spec.minimum:g}")
                if spec.maximum is not None and value > spec.maximum:
                    raise ValueError(f"at most {spec.maximum:g}")
        except (ValueError, yaml.YAMLError, units.UnitError) as error:
            self.show_message(name, str(error).strip() or "not a valid value")
            return
        self.show_message(name, "")
        if self.node.params.get(name) != value:
            self.param_changed.emit(self.node.id, name, value)

    def _browse(self, name: str, edit: QLineEdit) -> None:
        import os

        from PySide6.QtWidgets import QFileDialog

        start = os.path.join(self.folder, edit.text()) if self.folder else edit.text()
        path, _ = QFileDialog.getSaveFileName(self, f"File for {name}", start, options=QFileDialog.DontConfirmOverwrite)
        if not path:
            return
        path = flow_path(path, self.folder, self.base)
        edit.setText(path)
        self._emit(name, path, "path")

    def _rename(self) -> None:
        new = self.name_edit.text().strip()
        if new and new != self.node.id:
            self.renamed.emit(self.node.id, new)


class _Debounced:
    """Calls ``function`` when edits pause (the code editor sends every keystroke)."""

    def __init__(self, function: Callable[[], None]) -> None:
        from PySide6.QtCore import QTimer

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(function)

    def __call__(self, *args) -> None:
        self.timer.start()


class FlowInspector(QWidget):
    """The flow when no node is selected: name, description, run settings."""

    def has_focus(self) -> bool:
        """Whether the user is typing in one of its fields."""
        focused = QApplication.focusWidget()
        return focused is not None and (focused is self or self.isAncestorOf(focused))


    name_changed = Signal(str)
    description_changed = Signal(str)
    setting_changed = Signal(str, object)
    #: a panel of the flow to open (its document or its file), a new panel for it
    panel_open_requested = Signal(object)
    new_panel_requested = Signal()

    def __init__(self, flow: Flow, parent: Optional[QWidget] = None, panels: Optional[list] = None) -> None:
        """``panels``: ``(title, target)`` of the panels that use the flow."""
        super().__init__(parent)
        self.flow = flow
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 10)
        heading = QLabel("Flow", self)
        set_role(heading, "heading")
        layout.addWidget(heading)
        form = QFormLayout()
        self.name_edit = QLineEdit(flow.name, self)
        self.name_edit.editingFinished.connect(lambda: self.name_changed.emit(self.name_edit.text().strip() or "Flow"))
        form.addRow("Name", self.name_edit)
        # several lines are kept (a single line field would drop them)
        self.description_edit = QPlainTextEdit(flow.description.strip(), self)
        self.description_edit.setFixedHeight(size("note"))
        self._description = _Debounced(lambda: self.description_changed.emit(self.description_edit.toPlainText()))
        self.description_edit.textChanged.connect(self._description)
        form.addRow("Description", self.description_edit)
        self.seed_edit = QLineEdit(to_text(flow.settings.get("seed")), self)
        self.seed_edit.setPlaceholderText("1")
        self.seed_edit.editingFinished.connect(self._seed)
        form.addRow("Seed", self.seed_edit)
        self.duration_edit = QLineEdit(to_text(flow.settings.get("duration")), self)
        self.duration_edit.setPlaceholderText("until the flow ends")
        self.duration_edit.editingFinished.connect(self._duration)
        form.addRow("Duration", self.duration_edit)
        layout.addLayout(form)
        self.error_label = QLabel("", self)
        set_role(self.error_label, "error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        panels_heading = QLabel("Panels", self)
        set_role(panels_heading, "heading")
        layout.addWidget(panels_heading)
        #: a button for every panel that uses the flow
        self.panel_buttons = []
        for title, target in panels or []:
            button = QPushButton(title, self)
            set_icon(button, "panel")
            button.setToolTip(f"Open the panel {target}" if isinstance(target, str) else "Show the panel")
            button.setProperty("align", "left")
            button.clicked.connect(lambda _checked=False, target=target: self.panel_open_requested.emit(target))
            layout.addWidget(button)
            self.panel_buttons.append(button)
        if not panels:
            none = QLabel("No panel uses this flow yet.", self)
            set_role(none, "hint")
            layout.addWidget(none)
        new_panel = QPushButton("New panel for this flow", self)
        set_icon(new_panel, "plus")
        new_panel.clicked.connect(self.new_panel_requested.emit)
        layout.addWidget(new_panel)

        hint = QLabel("Devices are nodes: drag an open instrument, a simulator or a device of the project from "
                      "the palette (Devices) and wire its 'device' output to the nodes that use it.", self)
        set_role(hint, "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

    def _seed(self) -> None:
        text = self.seed_edit.text().strip()
        if text and not text.lstrip("-").isdigit():
            self.error_label.setText(f"Seed: {text!r} is not a whole number")
            return
        self.error_label.setText("")
        self.setting_changed.emit("seed", int(text) if text else None)

    def _duration(self) -> None:
        text = self.duration_edit.text().strip()
        if text:
            try:
                units.parse(text, "s")
            except (ValueError, units.UnitError) as error:
                self.error_label.setText(f"Duration: {error}")
                return
        self.error_label.setText("")
        self.setting_changed.emit("duration", text or None)


class NodeEditor(QDialog):
    """A node's parameters in a window of their own (a double-click on the node): the same editors
    as the inspector, with bigger text fields. Changes apply at once; the window stays open while
    the flow is edited."""

    def __init__(self, node_id: str, inspector_factory: Callable[[str], "NodeInspector"], title: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        #: the node it edits (follows a rename)
        self.node_id = node_id
        self.setWindowTitle(title)
        self.resize(720, 640)
        self._factory = inspector_factory
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        row.setContentsMargins(10, 0, 10, 0)
        hint_label = QLabel("Changes apply at once (text fields when you leave them, Ctrl+Return in big ones).", self)
        set_role(hint_label, "hint")
        row.addWidget(hint_label, 1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.inspector: Optional[NodeInspector] = None
        self.rebuild()

    def rebuild(self) -> None:
        """The editors again (after the node changed outside, e.g. an undo)."""
        self.inspector = self._factory(self.node_id)
        self.scroll.setWidget(self.inspector)

    def has_focus(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and self.isAncestorOf(focus)
