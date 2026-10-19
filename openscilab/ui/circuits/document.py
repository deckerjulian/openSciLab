# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The document of a circuit (``*.circuit.yaml``): its schematic, netlist and results.

It keeps to the contract of ``ui/documents/base.py``: the shell shows its tool bar, *Circuit* menu,
inspector, undo and run state. *Run* (the header, F5) simulates the circuit live - in real time or
in virtual time - and *Pause*, *Stop* act on it; values changed meanwhile go in at once, a changed
circuit starts again. The analyses (operating point, sweeps, transient, S-parameters) are computed
from the *Circuit* menu or the inspector and shown under *Results*. ngspice is installed into the
vendor folder when it is missing (``core/vendor.py``); until then a banner says so.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Callable

import yaml
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QMenu,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...circuits import FOLDER, SUFFIX, CircuitError, running, spice, values, yaml_io
from ...circuits.catalog import Catalog, default_catalog
from ...circuits.library import ComponentSpec
from ...circuits.model import GROUP_COLORS, Component, Schematic, apply_moves, drag_wires
from ...circuits.nets import extract, pin_position
from ..dialogs.common import Banner
from ..documents.base import DocumentWidget, Switch, untitled_title
from ..icons import icon, known
from ..theme import size
from .catalog_widget import ComponentBrowser
from .inspector import (
    ANALYSIS_TITLES,
    CircuitInspector,
    ComponentInspector,
    GroupInspector,
    LabelInspector,
    WireInspector,
)
from .results import ResultsView
from .scene import SchematicScene
from .scope import ScopePanel, probes_of
from .serial_monitor import SerialMonitor
from .speed import SpeedControl, setting_speed
from .view import SchematicView

log = logging.getLogger(__name__)

CLIPBOARD_MIME = "application/x-openscilab-circuit"
#: how often the schematic shows what the running circuit does
LIVE_INTERVAL_MS = 33
#: the current view is computed every this many ticks of the live display
CURRENT_EVERY = 2
#: the entries of the *Probe* menu: (text, component type, tip); an empty type separates
PROBE_KINDS = (
    ("Voltage probe", "probe.v", "The voltage of a net against ground (Shift+P)"),
    ("Current probe", "probe.i", "The current through it (in a wire, in series)"),
    ("Logic probe", "probe.logic", "0 or 1: the level of a net"),
    ("Oscilloscope", "probe.scope", "Four channels on a screen below the schematic"),
    ("Terminal", "terminal", "A named output socket (OUT, COM ...): measured like a voltage probe"),
    ("", "", ""),
    ("Device input", "device.input", "A pin a connected device reads: its level or voltage drives the net"),
    ("Device output", "device.output", "A pin a connected device drives: it follows the net"),
)


class SchematicEdit(QUndoCommand):
    """One step of undo: the circuit before and after."""

    def __init__(self, document: CircuitDocument, before: Schematic, after: Schematic, text: str,
                 merge_key: str = "") -> None:
        super().__init__(text)
        self.document = document
        self.before = before
        self.after = after
        self.merge_key = merge_key
        self._first = True

    def id(self) -> int:
        return 0x5C4E if self.merge_key else -1

    def mergeWith(self, other) -> bool:
        if not isinstance(other, SchematicEdit) or other.merge_key != self.merge_key or not self.merge_key:
            return False
        self.after = other.after
        return True

    def redo(self) -> None:
        if self._first:
            self._first = False
            return
        self.document._set_schematic(self.after.copy())

    def undo(self) -> None:
        self.document._set_schematic(self.before.copy())


class _Installer(QObject):
    progress = Signal(str, float)
    done = Signal(bool, str)


class CircuitDocument(DocumentWidget):
    document_kind = "circuit"
    runnable = True
    run_label = "Run"
    run_tooltip = "Simulate the circuit live (ngspice): probes, LEDs and switches on the schematic"
    can_pause = True
    time_modes = True

    def __init__(self, schematic: Schematic | None = None, path: str | None = None, project=None,
                 catalog: Catalog | None = None, services=None) -> None:
        super().__init__()
        self.schematic = schematic or Schematic()
        self._path = path
        self.project = project
        self.services = services
        self.catalog = catalog or default_catalog()
        self._untitled = untitled_title("circuit")
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self._undo_changed)
        self.undo.indexChanged.connect(self._undo_changed)
        self.fast_box = Switch(False, self)
        self.live = None
        #: the pins of connected devices while the circuit runs (circuits/device_io.py)
        self.devices = None
        self._run_state = "idle"
        self._models = None
        self._models_key: tuple = ()
        self.circuit: spice.Circuit | None = None
        self._selection: list[str] = []
        self._installing = False
        # ---------------------------------------------------------- layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.banner = Banner("warning", self)
        self.banner.setVisible(False)
        if hasattr(self.banner, "set_closable"):
            self.banner.set_closable(True)
        self.banner.button.clicked.connect(self._banner_action)
        self._banner_callback: Callable[[], None] | None = None
        self._toolbar_slot = layout.count()
        layout.addWidget(self.banner)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack, 1)
        self.scene = SchematicScene(self, self)
        self.schematic_page = QSplitter(Qt.Vertical, self.stack)
        self.view = SchematicView(self.scene, self.schematic_page)
        self.view.search = self.open_search
        self.schematic_page.addWidget(self.view)
        #: below the schematic: the serial monitor of its microcontrollers and a screen for each oscilloscope
        self.instruments = QTabWidget(self.schematic_page)
        self.instruments.setDocumentMode(True)
        # (a tab only while the circuit has microcontrollers: kept hidden by the document otherwise - a child
        # of the tabs that is no tab of them is drawn over them)
        self.serial = SerialMonitor(self._serial_send, self._mcu_reset, self)
        self.serial.hide()
        self.scopes: dict[str, ScopePanel] = {}
        self.schematic_page.addWidget(self.instruments)
        self.schematic_page.setStretchFactor(0, 3)
        self.schematic_page.setStretchFactor(1, 2)
        self.instruments.setVisible(False)
        self.stack.addWidget(self.schematic_page)
        self.netlist_view = QPlainTextEdit(self.stack)
        self.netlist_view.setReadOnly(True)
        self.netlist_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.stack.addWidget(self.netlist_view)
        self.results = ResultsView(self.stack)
        self.stack.addWidget(self.results)
        self._views = {"Schematic": self.schematic_page, "Netlist": self.netlist_view, "Results": self.results}
        self.scene.selection_changed_ids.connect(self._selection_changed)
        self.scene.tool_changed.connect(self._sync_tool_actions)
        self.timer = QTimer(self)
        self.timer.setInterval(LIVE_INTERVAL_MS)
        self.timer.timeout.connect(self._show_live)
        self._build_actions()
        layout.insertWidget(self._toolbar_slot, self.toolbar())
        # (letters type into the catalog search; R and X act on a selection or a part being placed, else
        # they are typed too; the tools have Shift+letter shortcuts, see _build_actions)
        self.view.actions_by_key = {
            Qt.Key_R: self.rotate, Qt.Key_X: self.mirror, Qt.Key_Y: self.mirror,
            Qt.Key_Delete: self.delete_selection, Qt.Key_Backspace: self.delete_selection,
            Qt.Key_Slash: self.scene.toggle_route,
        }
        self._popup: QWidget | None = None
        self.refresh()
        QTimer.singleShot(0, self.view.fit)
        _warm_up_engine()

    # ------------------------------------------------------------ identity
    @property
    def title(self) -> str:
        return os.path.basename(self._path) if self._path else self._untitled

    @property
    def path(self) -> str | None:
        return self._path

    def undo_stack(self):
        return self.undo

    @property
    def base_folder(self) -> str:
        if self._path:
            return os.path.dirname(os.path.abspath(self._path))
        if self.project is not None:
            return os.path.join(self.project.root, FOLDER)
        return ""

    # ---------------------------------------------------------------- views
    def views(self) -> list[str]:
        return list(self._views)

    def current_view(self) -> str:
        current = self.stack.currentWidget()
        return next((name for name, widget in self._views.items() if widget is current), "Schematic")

    def set_view(self, name: str) -> None:
        widget = self._views.get(name)
        if widget is not None:
            if name == "Netlist":
                self._show_netlist()
            self.stack.setCurrentWidget(widget)
            for view, item in getattr(self, "view_actions", {}).items():
                item.setChecked(view == name)
            for item in (self.action_select, self.action_wire, self.action_label, self.action_search,
                         self.action_ground, self.action_supply, self.action_probe, self.action_rotate,
                         self.action_mirror, self.action_delete, self.action_fit, self.action_group,
                         self.action_ungroup):
                item.setEnabled(name == "Schematic")
            self.document_changed.emit()

    # ----------------------------------------------------------- actions
    def _build_actions(self) -> None:
        def action(text: str, slot, icon_name: str = "", shortcut=None, tip: str = "", checkable: bool = False) -> QAction:
            item = QAction(text, self)
            if icon_name:
                item.setIcon(icon(icon_name))
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
                item.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                self.addAction(item)
            item.setToolTip(tip or text)
            item.setCheckable(checkable)
            item.triggered.connect(lambda _checked=False: slot())
            return item

        self.action_select = action("Select", lambda: self.scene.set_tool("select"), "pointer" if self._icon("pointer") else "",
                                    tip="Select and move (Esc)", checkable=True)
        self.action_wire = action("Wire", lambda: self.scene.set_tool("wire"), "wire" if self._icon("wire") else "",
                                  "Shift+W", "Draw a wire: click its corners, double-click to end (Shift+W); press on a "
                                  "pin to start one", checkable=True)
        self.action_label = action("Net label", lambda: self.scene.set_tool("label"), shortcut="Shift+L",
                                   tip="Name a net (Shift+L)", checkable=True)
        self.action_ground = action("Ground", lambda: self.scene.place("ground"), shortcut="Shift+G",
                                    tip="Place a ground (Shift+G)")
        self.action_supply = action("Supply", lambda: self.scene.place("power"), tip="Place a supply rail (VCC)")
        self.probe_menu = QMenu("Probe", self)
        for text, type_name, tip in PROBE_KINDS:
            if not type_name:
                self.probe_menu.addSeparator()
                continue
            entry = self.probe_menu.addAction(text)
            entry.setToolTip(tip)
            entry.triggered.connect(lambda _checked=False, kind=type_name: self.scene.place(kind))
        self.probe_menu.setToolTipsVisible(True)
        self.action_probe = action("Probe", lambda: self.scene.place("probe.v"), shortcut="Shift+P",
                                   tip="Place a voltage probe (Shift+P)")
        self.action_rotate = action("Rotate", self.rotate, "refresh", tip="Rotate the selection (R)")
        self.action_mirror = action("Mirror", self.mirror, tip="Mirror the selection (X)")
        self.action_delete = action("Delete", self.delete_selection, "close", tip="Delete the selection (Delete)")
        self.action_fit = action("Fit", lambda: self.view.fit(), "search", tip="Show the whole circuit")
        self.action_current = action("Current", lambda: self._current_view_changed(), "wave" if self._icon("wave") else "",
                                     tip="Show where the current flows while the circuit runs: a stream along the wires "
                                         "and through the components, in the direction of the (conventional) current, "
                                         "faster and wider where more flows", checkable=True)
        from ...core import preferences as _preferences

        self.action_current.setChecked(bool(_preferences.get("circuits.show_current")))
        self.action_group = action("Group", lambda: self.group_selection(), shortcut="Ctrl+G",
                                   tip="Put the selection into a group: a frame with a name and a comment (Ctrl+G)")
        self.action_ungroup = action("Ungroup", lambda: self.ungroup(), shortcut="Ctrl+Shift+G",
                                     tip="Take the selected groups apart: their members stay (Ctrl+Shift+G)")
        self.action_search = action("Add component...", lambda: self.open_search(), "plus", "Ctrl+Shift+A",
                                    "Search the catalog and place a component")
        self.action_copy = action("Copy", self.copy_selection, shortcut=QKeySequence.Copy, tip="Copy the selection")
        self.action_cut = action("Cut", self.cut_selection, shortcut=QKeySequence.Cut, tip="Cut the selection")
        self.action_paste = action("Paste", self.paste, shortcut=QKeySequence.Paste, tip="Paste components and wires")
        self.action_select_all = action("Select all", lambda: self.scene.select_ids(self.schematic.items()),
                                        shortcut=QKeySequence.SelectAll)
        self.analysis_actions = {kind: action(title, lambda kind=kind: self.compute(kind),
                                              tip=f"Compute the {title.lower()} (results under Results)")
                                 for kind, title in ANALYSIS_TITLES.items()}
        self.action_export = action("Export results as CSV...", self.export_results, tip="The shown result as a table")
        self.action_export_netlist = action("Export netlist...", self.export_netlist, tip="The SPICE netlist of the circuit")
        self._sync_tool_actions()

    @staticmethod
    def _icon(name: str) -> bool:
        from ..icons import known

        return known(name, "") == name

    def _undo_changed(self, *_args) -> None:
        self._changed_soon()

    def _changed_soon(self) -> None:
        """``document_changed`` once for everything that changed in one turn of the event loop (an edit
        changes the circuit, the undo stack and its clean state: the window updates once)."""
        if not getattr(self, "_change_pending", False):
            self._change_pending = True
            QTimer.singleShot(0, self._emit_changed)

    def _emit_changed(self) -> None:
        self._change_pending = False
        self.document_changed.emit()

    def _sync_tool_actions(self, *_args) -> None:
        tool = self.scene.tool
        self.action_select.setChecked(tool == "select")
        self.action_wire.setChecked(tool == "wire")
        self.action_label.setChecked(tool == "label")

    def toolbar(self) -> QToolBar | None:
        """The views (Schematic, Netlist, Results) and the tools of the schematic, shown above it."""
        if getattr(self, "_toolbar", None) is None:
            from ..widgets.toolbar import HIGH, LOW, NORMAL, AdaptiveToolBar

            bar = AdaptiveToolBar("circuit", "Circuit", self)
            self.view_actions = {}
            for index, (name, icon_name) in enumerate(zip(self._views, ("circuit", "file-plus", "channels"))):
                item = QAction(icon(known(icon_name, "file-plus")), name, self)
                item.setCheckable(True)
                item.setChecked(index == 0)
                item.setToolTip(f"Show the {name.lower()} of the circuit")
                item.triggered.connect(lambda _checked=False, name=name: self.set_view(name))
                bar.add_action(f"view-{name.lower()}", item, "view", HIGH)
                self.view_actions[name] = item
            for key, item, group, priority in (
                    ("select", self.action_select, "tools", HIGH), ("wire", self.action_wire, "tools", HIGH),
                    ("label", self.action_label, "tools", NORMAL), ("add", self.action_search, "place", HIGH),
                    ("ground", self.action_ground, "place", NORMAL), ("supply", self.action_supply, "place", LOW),
                    ("probe", None, "place", NORMAL), ("rotate", self.action_rotate, "edit", NORMAL),
                    ("mirror", self.action_mirror, "edit", LOW), ("delete", self.action_delete, "edit", LOW),
                    ("group", self.action_group, "edit", LOW), ("fit", self.action_fit, "view-tools", NORMAL),
                    ("current", self.action_current, "view-tools", NORMAL)):
                if key == "probe":
                    # (which probe: a voltage, current or logic probe, an oscilloscope, a terminal, a pin of a
                    # connected device - chosen from its menu)
                    button = QToolButton(bar)
                    button.setText("Probe")
                    button.setToolTip("Place a probe: voltage (Shift+P), current, logic, an oscilloscope, a terminal "
                                      "or a pin of a connected device")
                    button.setMenu(self.probe_menu)
                    button.setPopupMode(QToolButton.InstantPopup)
                    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
                    button.setAutoRaise(True)
                    self.probe_button = button
                    bar.add_widget(key, button, "Probe", group, priority,
                                   overflow=lambda menu: menu.addMenu(self.probe_menu))
                    continue
                bar.add_action(key, item, group, priority)
            # (how fast it runs in real time: the circuit's speed setting, taken at once while it runs)
            self.speed_control = SpeedControl(bar)
            self.speed_control.set_speed(setting_speed(self.schematic.setting("speed")))
            self.speed_control.setEnabled(not self.virtual_time())
            self.speed_control.changed.connect(self._speed_chosen)
            self.fast_box.toggled.connect(lambda virtual: self.speed_control.setEnabled(not virtual))
            bar.add_widget("speed", self.speed_control, "Speed", "run", NORMAL,
                           overflow=self.speed_control.fill_menu)
            bar.finish()
            self._toolbar = bar
        return self._toolbar

    def document_menus(self) -> list[QMenu]:
        menu = QMenu("&Circuit", self)
        for item in (self.action_search, self.action_wire, self.action_label, self.action_ground, self.action_supply):
            menu.addAction(item)
        menu.addMenu(self.probe_menu)
        menu.addSeparator()
        for item in (self.action_rotate, self.action_mirror, self.action_delete, self.action_copy, self.action_cut,
                     self.action_paste, self.action_select_all):
            menu.addAction(item)
        menu.addSeparator()
        menu.addAction(self.action_group)
        menu.addAction(self.action_ungroup)
        menu.addSeparator()
        analyses = menu.addMenu("&Analyses")
        for item in self.analysis_actions.values():
            analyses.addAction(item)
        views = menu.addMenu("&View")
        for name in self._views:
            views.addAction(name, lambda name=name: self.set_view(name))
        views.addSeparator()
        views.addAction(self.action_current)
        return [menu]

    def document_actions(self) -> list[QAction]:
        return [self.action_search, self.action_copy, self.action_paste, *self.analysis_actions.values()]

    def document_file_actions(self) -> list[QAction]:
        return [self.action_export_netlist, self.action_export]

    # ---------------------------------------------------------- inspector
    def inspector_widget(self, selection=None) -> QWidget | None:
        """The inspector of the selection - the same widget while the selection stays, its fields
        brought up to date (built again it would lose the field being typed in, and take long)."""
        import shiboken6

        ids = tuple(self._selection)
        cached = getattr(self, "_inspector", None)
        if cached is not None and cached[0] == ids and shiboken6.isValid(cached[1]):
            widget = cached[1]
            synced = self._sync_inspector(widget, ids)
            if synced:
                return widget
        if cached is not None and shiboken6.isValid(cached[1]):
            cached[1].deleteLater()  # (not kept: the old inspectors piled up as hidden children)
        widget = self._build_inspector()
        self._inspector = (ids, widget) if widget is not None else None
        return widget

    def _sync_inspector(self, widget, ids: tuple) -> bool:
        """Bring the cached inspector up to date; False when it does not fit the selection any more."""
        sync = getattr(widget, "sync", None)
        if sync is None:
            return False
        if len(ids) == 1:
            item = ids[0]
            if item in self.schematic.components and hasattr(widget, "component"):
                component = self.schematic.components[item]
                if component.type != widget.component.type:
                    return False
                sync(component)
                return True
            if item in self.schematic.wires and hasattr(widget, "wire"):
                netlist = self.scene.netlist
                sync(self.schematic.wires[item], netlist.wire_net.get(item, "") if netlist else "")
                return True
            if item in self.schematic.labels and hasattr(widget, "label"):
                sync(self.schematic.labels[item])
                return True
            if item in self.schematic.groups and hasattr(widget, "group"):
                sync(self.schematic.groups[item])
                return True
            return False
        if not ids and hasattr(widget, "schematic"):
            sync(self.schematic)
            return True
        return False

    def _build_inspector(self) -> QWidget | None:
        ids = self._selection
        if len(ids) == 1 and ids[0] in self.schematic.components:
            component = self.schematic.components[ids[0]]
            widget = ComponentInspector(component, self.spec(component.type), self.base_folder,
                                        options=self._param_options)
            widget.param_changed.connect(self.set_param)
            widget.renamed.connect(self.rename)
            widget.unit_changed.connect(self.set_unit)
            widget.comment_changed.connect(self.set_comment)
            return widget
        if len(ids) == 1 and ids[0] in self.schematic.labels:
            widget = LabelInspector(self.schematic.labels[ids[0]])
            widget.renamed.connect(self.rename_label)
            widget.comment_changed.connect(self.set_comment)
            return widget
        if len(ids) == 1 and ids[0] in self.schematic.wires:
            netlist = self.scene.netlist
            widget = WireInspector(self.schematic.wires[ids[0]], netlist.wire_net.get(ids[0], "") if netlist else "")
            widget.comment_changed.connect(self.set_comment)
            return widget
        if len(ids) == 1 and ids[0] in self.schematic.groups:
            widget = GroupInspector(self.schematic.groups[ids[0]])
            widget.changed.connect(self.set_group)
            widget.ungroup.connect(lambda group_id: self.ungroup([group_id]))
            return widget
        if not ids:
            widget = CircuitInspector(self.schematic, self.base_folder)
            widget.changed.connect(self._circuit_changed)
            widget.libraries_changed.connect(self._libraries_changed)
            widget.compute.connect(self.compute)
            return widget
        return None

    def _selection_changed(self, ids: list) -> None:
        if ids != self._selection:
            self._selection = list(ids)
            self._changed_soon()

    # ----------------------------------------------------------- the model
    def spec(self, type_name: str) -> ComponentSpec | None:
        return self.catalog.spec(type_name)

    def models(self):
        """The model libraries of this circuit (its own first)."""
        paths = tuple(os.path.join(self.base_folder, path) if not os.path.isabs(path) and self.base_folder else path
                      for path in self.schematic.libraries)
        if self._models is None or paths != self._models_key:
            self._models = self.catalog.models_for(paths) if paths else self.catalog.models
            self._models_key = paths
        return self._models

    def _edit(self, text: str, change: Callable[[Schematic], None], merge_key: str = "",
              only: str | None = None) -> bool:
        """Change the circuit with undo; ``only``: just the parameters of that component changed (its
        item is drawn again, nothing else)."""
        before = self.schematic.copy()
        after = self.schematic.copy()
        try:
            change(after)
        except CircuitError as error:
            self.message.emit(str(error))
            return False
        if yaml_io.to_data(after) == yaml_io.to_data(before):
            return False  # (nothing changed: no undo step, the file stays as saved)
        self._set_schematic(after, only)
        self.undo.push(SchematicEdit(self, before, after.copy(), text, merge_key))
        return True

    def _set_schematic(self, schematic: Schematic, only: str | None = None) -> None:
        self.schematic = schematic
        if only is not None and only in schematic.components and only in self.scene.components:
            self._refresh_component(only)
        else:
            self.refresh()
        self._changed_soon()

    def _refresh_component(self, ref: str) -> None:
        """Only parameters of ``ref`` changed: its item, its oscilloscope screen, the running simulation."""
        component = self.schematic.components[ref]
        spec = self.spec(component.type)
        self.scene.components[ref].update_from(component, spec)
        if spec is not None and spec.role == "probe.scope" and ref in self.scopes:
            params = spec.defaults()
            params.update(component.params)
            self.scopes[ref].set_params(params)
            return  # (an oscilloscope changes nothing of the netlist)
        if self.stack.currentWidget() is self.netlist_view:
            self._show_netlist()
        if self.live is not None:
            self._update_live_circuit()

    def refresh(self) -> None:
        """The scene (and a running simulation) after the circuit changed."""
        netlist = extract(self.schematic, self.spec)
        self.scene.sync(self.schematic, netlist)
        self._sync_instruments()
        self._sync_speed()
        if self.stack.currentWidget() is self.netlist_view:
            self._show_netlist()
        if self.live is not None:
            self._update_live_circuit()

    def _speed_chosen(self, speed: float) -> None:
        """The slider moved: the running circuit takes the speed at once, the setting keeps it (undo)."""
        if self.live is not None and not self.virtual_time():
            self.live.set_speed(speed)
        text = f"{speed:g}"
        self._circuit_changed("setting.speed", "" if text == "1" else text)

    def _sync_speed(self) -> None:
        """The slider and a running circuit at the speed setting (changed in the inspector, undone)."""
        speed = setting_speed(self.schematic.setting("speed"))
        control = getattr(self, "speed_control", None)
        if control is not None and not math.isclose(control.speed, speed):
            control.set_speed(speed)
        if self.live is not None and not self.virtual_time() and not math.isclose(self.live.speed, speed):
            self.live.set_speed(speed)

    def _netlist(self, analysis: str | None = None) -> spice.Circuit:
        circuit = spice.emit(self.schematic, self.spec, self.models(), analysis=analysis, title=self.title,
                             base_folder=self.base_folder)
        circuit.show_currents = self.show_current and analysis is None
        return circuit

    @property
    def show_current(self) -> bool:
        """The current view is on (*Current* in the tool bar): a running circuit shows where it flows."""
        action = getattr(self, "action_current", None)
        return bool(action is not None and action.isChecked())

    def _current_view_changed(self) -> None:
        from ...core import preferences

        preferences.update({"circuits.show_current": self.show_current})
        if not self.show_current:
            self._wire_currents = None
            self.scene.set_flows({}, {})
            self.scene.set_voltages({})
        if self.live is not None:
            # (the currents are measured only while shown: the run takes the other netlist, its time going on)
            self._update_live_circuit()

    def _measure_currents(self, circuit) -> None:
        """Where the current flows in ``circuit`` (its wires, its components) - ``circuits/currents.py``."""
        self._wire_currents = None
        if not circuit.show_currents or circuit.netlist is None:
            return
        from ...circuits.currents import WireCurrents

        try:
            self._wire_currents = WireCurrents(self.schematic, circuit.netlist, circuit, self.spec)
        except Exception:  # noqa: BLE001 - the view is a help: the circuit runs on without it
            log.exception("the current view")

    def _show_currents(self, simulation) -> None:
        model = getattr(self, "_wire_currents", None)
        if model is None:
            return
        self._current_tick = getattr(self, "_current_tick", 0) + 1
        if self._current_tick % CURRENT_EVERY:
            return
        try:
            wires, through, pins = model.compute(simulation.history.latest())
        except Exception:  # noqa: BLE001
            log.exception("the current view")
            self._wire_currents = None
            return
        self.scene.set_flows({wire_id: [(flow.start, flow.end, flow.current) for flow in pieces]
                              for wire_id, pieces in wires.items()}, through, pins)

    def _show_netlist(self) -> None:
        circuit = self._netlist()
        problems = "".join(f"* {problem.severity}: {problem}\n" for problem in circuit.problems)
        self.netlist_view.setPlainText(problems + circuit.text)

    # ------------------------------------------------------- edits (scene)
    @property
    def running(self) -> bool:
        return self._run_state in ("running", "paused")

    def add_component(self, type_name: str, x: int, y: int, rotation: int = 0, mirror: bool = False,
                      unit: int = 1) -> str | None:
        spec = self.spec(type_name)
        if spec is None:
            self.message.emit(f"{type_name} is not in the catalog")
            return None
        added: list[str] = []

        def change(schematic: Schematic) -> None:
            ref = ""
            if spec.symbol.units > 1:
                base = schematic.free_id(spec.prefix)
                taken = set(schematic.components)
                number = int("".join(ch for ch in base if ch.isdigit()) or 1)
                while any(f"{spec.prefix}{number}{chr(64 + u)}" in taken for u in range(1, spec.symbol.units + 1)):
                    number += 1
                ref = f"{spec.prefix}{number}{chr(64 + unit)}"
            component = schematic.add_component(type_name, x, y, spec.prefix, ref, rotation=rotation, mirror=mirror,
                                                unit=unit)
            added.append(component.id)

        if self._edit(f"Add {spec.title}", change):
            self.scene.select_ids(added)
            return added[0]
        return None

    def add_wire(self, points: list[tuple[int, int]]) -> None:
        self._edit("Add wire", lambda schematic: schematic.add_wire(points))

    def add_label(self, x: int, y: int) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Net label", "The name of the net:")
        if ok and name.strip():
            self._edit("Add net label", lambda schematic: schematic.add_label(name.strip(), x, y))

    def _pins_of(self, component) -> list[tuple[int, int]]:
        spec = self.spec(component.type)
        return [pin_position(component, pin) for pin in spec.symbol.pins_of(component.unit)] if spec else []

    def move_items(self, moves: dict[str, tuple[int, int]], wires: dict | None = None) -> None:
        """Components and labels moved (and ``wires`` as a whole, by (dx, dy)): their wires follow,
        connections stay (``model.apply_moves``)."""
        self._edit("Move", lambda schematic: apply_moves(schematic, moves, self._pins_of, wires))

    def preview_moves(self, moves: dict[str, tuple[int, int]], wires: dict | None = None) -> None:
        """While components are dragged: the wires as they will follow."""
        preview = self.schematic.copy()
        apply_moves(preview, moves, self._pins_of, wires)
        self.scene.preview_wires(preview)

    def delete_items(self, ids: list[str], groups: str = "frame") -> None:
        """Delete components, wires, labels and groups; a group among ``ids`` loses only its frame
        (``groups="frame"``) or goes with its members (``"members"``)."""
        ids = list(ids)
        if groups == "members":
            for group_id in list(ids):
                group = self.schematic.groups.get(group_id)
                if group is not None:
                    ids.extend(member for member in group.members if member not in ids)
        if ids:
            self._edit("Delete", lambda schematic: schematic.remove(ids))

    def delete_selection(self) -> None:
        """Delete the selection; with a group frame in it, ask whether its members go too."""
        ids = self.scene.selected_ids()
        frames = [item for item in ids if item in self.schematic.groups]
        groups = "frame"
        if frames:
            from .. import messages

            count = sum(len(self.schematic.groups[item].members) for item in frames)
            what = "the group" if len(frames) == 1 else f"the {len(frames)} groups"
            choice = messages.choose(self, "Delete the group", f"Delete {what} with its {count} members, or only the frame?",
                                     ["Delete the group and its members", "Only the frame (keep the members)"])
            if choice is None:
                return
            groups = "members" if choice == 0 else "frame"
        self.delete_items(ids, groups)

    # ------------------------------------------------------ comments, groups
    def set_comment(self, item_id: str, text: str) -> None:
        """The comment of a component, wire or label (shown beside it)."""
        def change(schematic: Schematic) -> None:
            for items in (schematic.components, schematic.wires, schematic.labels):
                if item_id in items:
                    items[item_id].comment = text.strip()

        self._edit(f"Comment of {item_id}", change)

    def group_selection(self, name: str = "", comment: str = "") -> str | None:
        """Put the selected components, wires and labels into a group (a frame); its id, None without any."""
        selected = self.scene.selected_ids()
        ids = [item for item in selected if item in self.schematic.components
               or item in self.schematic.wires or item in self.schematic.labels]
        frames = [self.schematic.groups[item] for item in selected if item in self.schematic.groups]
        if frames and not ids and len(frames) == 1:
            self.message.emit(f"{frames[0].name or 'The group'} is a group already")
            return frames[0].id
        for group in frames:  # (a selected group's members join; the first group's name, comment and colour stay)
            ids.extend(member for member in group.members if member not in ids)
        if not ids:
            self.message.emit("Select components, wires or labels to group")
            return None
        if frames and not name:
            name, comment = frames[0].name, comment or frames[0].comment
        color = frames[0].color if frames else ""
        made = []

        def change(schematic: Schematic) -> None:
            group = schematic.add_group(ids, name, comment)
            group.color = color
            made.append(group.id)

        self._edit("Group", change)
        if made:
            self.scene.select_ids([made[0]])
        return made[0] if made else None

    def ungroup(self, group_ids=None) -> None:
        """Take groups apart (the selected ones, or those of the selection); the members stay."""
        if group_ids is None:
            selected = self.scene.selected_ids()
            group_ids = {item for item in selected if item in self.schematic.groups}
            group_ids |= {group.id for group in self.schematic.groups.values() if set(group.members) & set(selected)}
        group_ids = [group_id for group_id in group_ids if group_id in self.schematic.groups]
        if not group_ids:
            self.message.emit("Select a group (its frame) to take it apart")
            return
        self._edit("Ungroup", lambda schematic: schematic.remove(group_ids))

    def set_group(self, group_id: str, key: str, value: str) -> None:
        def change(schematic: Schematic) -> None:
            group = schematic.groups.get(group_id)
            if group is None:
                return
            if key == "color":
                if value and value not in GROUP_COLORS:
                    raise CircuitError(f"{value!r} is no colour of a group")
                group.color = value
            elif key in ("name", "comment"):
                setattr(group, key, value.strip())

        self._edit(f"Group {key}", change, merge_key=f"group:{group_id}:{key}")

    def item_activated(self, ref: str) -> None:
        panel = self.scopes.get(ref)
        if panel is not None:  # (an oscilloscope: its screen)
            self.instruments.setCurrentWidget(panel)
            return
        if self.services is not None:
            window = self.services.window
            dock = getattr(window, "inspector_dock", None)
            if dock is not None:
                dock.setVisible(True)

    def rotate(self) -> bool:
        """Rotate the selection (or the part being placed); False when there is nothing to rotate."""
        if self.scene.rotate_ghost():
            return True
        ids = self._selection_with_members(labels=True)
        if not ids:
            return False

        def change(schematic: Schematic) -> None:
            pin_moves = {}
            for ref in ids:
                if ref in schematic.components:
                    component = schematic.components[ref]
                    spec = self.spec(component.type)
                    pins = spec.symbol.pins_of(component.unit) if spec else ()
                    old = {pin.number: pin_position(component, pin) for pin in pins}
                    component.rotation = (component.rotation + 90) % 360
                    for pin in pins:
                        pin_moves[old[pin.number]] = pin_position(component, pin)
                else:
                    schematic.labels[ref].rotation = (schematic.labels[ref].rotation + 90) % 360
            drag_wires(schematic, pin_moves)

        self._edit("Rotate", change)
        return True

    def mirror(self) -> bool:
        if self.scene.mirror_ghost():
            return True
        ids = self._selection_with_members(labels=False)
        if not ids:
            return False

        def change(schematic: Schematic) -> None:
            pin_moves = {}
            for ref in ids:
                component = schematic.components[ref]
                spec = self.spec(component.type)
                pins = spec.symbol.pins_of(component.unit) if spec else ()
                old = {pin.number: pin_position(component, pin) for pin in pins}
                component.mirror = not component.mirror
                for pin in pins:
                    pin_moves[old[pin.number]] = pin_position(component, pin)
            drag_wires(schematic, pin_moves)

        self._edit("Mirror", change)
        return True

    def _param_options(self, kind: str, component: Component) -> list[str]:
        """What a ``device`` / ``device_pin`` parameter offers: the connected devices, the pins of the chosen
        one that fit the component (an input pin, an analog output ...)."""
        from ...circuits import device_io

        hub = self.services.hub if self.services is not None else None
        if hub is None:
            return []
        if kind == "device":
            return list(hub.names())
        spec = self.spec(component.type)
        params = spec.defaults() if spec is not None else {}
        params.update(component.params)
        direction = "in" if spec is not None and spec.role == "device.in" else "out"
        return device_io.pin_names(device_io.resolve(hub, params.get("device", "")), direction,
                                   params.get("signal", "digital"))

    def _selection_with_members(self, labels: bool) -> list[str]:
        """The selected components (and labels) - a selected group frame stands for its members."""
        ids: list[str] = []
        for item in self.scene.selected_ids():
            candidates = self.schematic.groups[item].members if item in self.schematic.groups else [item]
            for ref in candidates:
                if ref in ids:
                    continue
                if ref in self.schematic.components or (labels and ref in self.schematic.labels):
                    ids.append(ref)
        return ids

    def set_param(self, ref: str, name: str, value: str) -> None:
        def change(schematic: Schematic) -> None:
            component = schematic.components[ref]
            spec = self.spec(component.type)
            default = spec.param(name).default if spec is not None and spec.param(name) is not None else None
            if value == "" or value == default:
                component.params.pop(name, None)
            else:
                component.params[name] = value

        self._edit(f"Change {name} of {ref}", change, merge_key=f"param:{ref}:{name}", only=ref)

    def operate(self, ref: str, param: str, value: str, final: bool) -> None:
        """A switch, button or knob on the schematic (while the circuit runs)."""
        component = self.schematic.components.get(ref)
        if component is None:
            return
        spec = self.spec(component.type)
        current = str(component.params.get(param, spec.param(param).default if spec and spec.param(param) else ""))
        if current == value and not final:
            return
        control = spec.controls[0].kind if spec and spec.controls else ""
        if control == "button":
            # held while pressed: not a change of the file
            component.params[param] = value
            if value == (spec.param(param).default if spec.param(param) else "0"):
                component.params.pop(param, None)
            self.refresh()
            return
        self.set_param(ref, param, value)

    def rename(self, old: str, new: str) -> str | None:
        """Rename a component; returns what is wrong with the new name (None: renamed)."""
        try:
            self.schematic.copy().rename_component(old, new)
        except CircuitError as error:
            self._say_rename_problem(str(error))
            return str(error)
        if self._edit(f"Rename {old}", lambda schematic: schematic.rename_component(old, new)):
            self.scene.select_ids([new])
        return None

    def _say_rename_problem(self, text: str) -> None:
        """Under the field (rule 4: a problem next to its cause), when the inspector shows it."""
        cached = getattr(self, "_inspector", None)
        widget = cached[1] if cached is not None else None
        if widget is not None and hasattr(widget, "show_rename_problem"):
            widget.show_rename_problem(text)
        else:
            self.message.emit(text)

    def rename_label(self, label_id: str, name: str) -> str | None:
        name = name.strip()
        if not name:
            self._say_rename_problem("a net label needs a name")
            return "a net label needs a name"

        def change(schematic: Schematic) -> None:
            schematic.labels[label_id].name = name

        self._edit("Rename net label", change)
        return None

    def set_unit(self, ref: str, unit: int) -> None:
        def change(schematic: Schematic) -> None:
            component = schematic.components[ref]
            component.unit = unit
            if len(ref) > 1 and ref[-1].isalpha() and ref[-2].isdigit():
                new = ref[:-1] + chr(64 + unit)
                if new != ref and new not in schematic.components:
                    schematic.rename_component(ref, new)
                    renamed.append(new)

        renamed: list[str] = []
        if self._edit(f"Unit of {ref}", change) and renamed:
            self.scene.select_ids(renamed)

    def _circuit_changed(self, key: str, value: str) -> None:
        def change(schematic: Schematic) -> None:
            if key == "name":
                schematic.name = value
            elif key == "description":
                schematic.description = value
            elif key.startswith("setting."):
                name = key.split(".", 1)[1]
                if value:
                    schematic.settings[name] = value
                else:
                    schematic.settings.pop(name, None)
            elif key.startswith("analysis."):
                _prefix, kind, param = key.split(".", 2)
                params = schematic.analyses.setdefault(kind, {})
                if value:
                    params[param] = value
                else:
                    params.pop(param, None)

        self._edit("Change the circuit", change, merge_key=f"circuit:{key}")

    def _libraries_changed(self, libraries: list) -> None:
        def change(schematic: Schematic) -> None:
            schematic.libraries = list(libraries)

        self._edit("Model libraries", change)
        self.document_changed.emit()

    # --------------------------------------------------------- clipboard
    def _selection_data(self) -> dict | None:
        ids = set(self.scene.selected_ids())
        if not ids:
            return None
        for group_id in list(ids):  # (a selected frame stands for its members)
            group = self.schematic.groups.get(group_id)
            if group is not None:
                ids.update(group.members)
        part = Schematic(self.schematic.name)
        part.components = {ref: c for ref, c in self.schematic.components.items() if ref in ids}
        part.wires = {wid: w for wid, w in self.schematic.wires.items() if wid in ids}
        part.labels = {lid: label for lid, label in self.schematic.labels.items() if lid in ids}
        # (groups whose members are all copied go along)
        part.groups = {gid: group for gid, group in self.schematic.groups.items()
                       if group.members and all(member in ids for member in group.members)}
        return yaml_io.to_data(part)

    def copy_selection(self) -> bool:
        data = self._selection_data()
        if data is None:
            return False
        from PySide6.QtCore import QMimeData

        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        mime = QMimeData()
        mime.setText(text)
        mime.setData(CLIPBOARD_MIME, text.encode("utf-8"))
        QApplication.clipboard().setMimeData(mime)
        return True

    def cut_selection(self) -> None:
        if self.copy_selection():
            self.delete_selection()

    def paste(self) -> None:
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return
        text = bytes(mime.data(CLIPBOARD_MIME)).decode("utf-8") if mime.hasFormat(CLIPBOARD_MIME) else mime.text()
        try:
            part = yaml_io.loads(text)
        except CircuitError:
            return
        offset = 40
        added: list[str] = []

        def change(schematic: Schematic) -> None:
            renamed: set[str] = set()
            new_ids: dict[str, str] = {}  # (the ids in the clipboard -> here)
            for ref, component in part.components.items():
                spec = self.spec(component.type)
                new = ref if ref not in schematic.components else schematic.free_id(spec.prefix if spec else "U", renamed)
                renamed.add(new)
                placed = schematic.add_component(component.type, component.x + offset, component.y + offset,
                                                 component_id=new, params=dict(component.params),
                                                 rotation=component.rotation, mirror=component.mirror,
                                                 unit=component.unit, comment=component.comment)
                added.append(placed.id)
                new_ids[ref] = placed.id
            for wire_id, wire in part.wires.items():
                made = schematic.add_wire([(x + offset, y + offset) for x, y in wire.points], wire.comment)
                if made is not None:
                    added.append(made.id)
                    new_ids[wire_id] = made.id
            for label_id, label in part.labels.items():
                made_label = schematic.add_label(label.name, label.x + offset, label.y + offset, label.rotation,
                                                 label.comment)
                added.append(made_label.id)
                new_ids[label_id] = made_label.id
            for group in part.groups.values():
                members = [new_ids[member] for member in group.members if member in new_ids]
                if members:
                    schematic.add_group(members, group.name, group.comment).color = group.color

        if part.items() and self._edit("Paste", change):
            self.scene.select_ids(added)

    # ---------------------------------------------------------- the catalog
    def open_search(self, text: str = "") -> None:
        """The catalog at the pointer; the chosen component follows the pointer to be placed."""
        if self._popup is not None:
            self._popup.close()
        popup = QFrame(self, Qt.Popup)
        popup.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(4, 4, 4, 4)
        browser = ComponentBrowser(self.catalog, popup, preview=True)
        layout.addWidget(browser)
        popup.resize(size("search-popup-width"), size("search-popup-height"))
        cursor = self.view.mapFromGlobal(self.view.cursor().pos())
        position = self.view.mapToScene(cursor) if self.view.viewport().rect().contains(cursor) else \
            self.view.mapToScene(self.view.viewport().rect().center())

        def chosen(type_name: str, unit: int) -> None:
            popup.close()
            self.scene.place(type_name, unit, position)
            self.view.setFocus()

        browser.chosen.connect(chosen)
        global_point = self.view.viewport().mapToGlobal(self.view.mapFromScene(position))
        popup.move(global_point)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        popup.show()
        browser.focus_search(text)
        self._popup = popup
        popup.destroyed.connect(lambda *_args: setattr(self, "_popup", None))

    # ---------------------------------------------------------------- files
    def can_save(self) -> bool:
        return True

    def commit_pending(self) -> None:
        """What is being typed in the inspector goes into the circuit now (before a save, a close)."""
        focused = QApplication.focusWidget()
        cached = getattr(self, "_inspector", None)
        widget = cached[1] if cached is not None else None
        if focused is not None and widget is not None and widget.isAncestorOf(focused):
            focused.clearFocus()
            QApplication.processEvents()

    @property
    def dirty(self) -> bool:
        self.commit_pending()
        return not self.undo.isClean()

    def save(self) -> bool:
        self.commit_pending()
        if not self._path:
            return self.save_as()
        try:
            yaml_io.save(self.schematic, self._path)
        except OSError as error:
            self.message.emit(f"{self.title} could not be saved: {error}")
            return False
        self.undo.setClean()
        self.message.emit(f"Saved {self.title}")
        self.document_changed.emit()
        return True

    def save_as(self) -> bool:
        from ...core import preferences

        folder = self.base_folder or preferences.default_folder()
        name = (self.schematic.name or "circuit").replace(os.sep, "-")
        start = os.path.join(folder, name + SUFFIX) if folder else name + SUFFIX
        path, _ = QFileDialog.getSaveFileName(self, "Save circuit", start, f"Circuits (*{SUFFIX})")
        if not path:
            return False
        if not path.lower().endswith(SUFFIX):
            path = os.path.splitext(path)[0] + SUFFIX if path.lower().endswith(".yaml") else path + SUFFIX
        self._path = path
        if self.services is not None:
            self.services.remember(path)
        return self.save()

    def export_netlist(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export netlist", os.path.splitext(self._path or self.title)[0] + ".cir",
                                              "SPICE netlists (*.cir *.sp *.net);;All files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(self._netlist().text)
            except OSError as error:
                self.message.emit(f"The netlist could not be written: {error}")
                return
            self.message.emit(f"Netlist written to {os.path.basename(path)}")

    def _export(self, writer: Callable[[str], bool], path: str) -> bool:
        try:
            return writer(path)
        except OSError as error:
            self.message.emit(f"The results could not be written: {error}")
            return False

    def export_results(self) -> None:
        if self.results.current() is None:
            self.message.emit("Compute an analysis first (Circuit → Analyses)")
            return
        result = self.results.current()
        filters = "CSV (*.csv)" + (";;Touchstone (*.s2p *.s1p *.snp)" if result.kind == "sp" else "")
        path, chosen = QFileDialog.getSaveFileName(self, "Export results", "", filters)
        if not path:
            return
        if "Touchstone" in chosen or path.lower().endswith((".s1p", ".s2p", ".s3p", ".s4p", ".snp")):
            done = self._export(self.results.export_touchstone, path)
        else:
            done = self._export(self.results.export_csv, path)
        if done:
            self.message.emit(f"Results written to {os.path.basename(path)}")

    # ------------------------------------------------------------- banner
    def show_banner(self, text: str, button: str | None = None, action: Callable[[], None] | None = None,
                    kind: str = "warning") -> None:
        from ..theme import set_role

        set_role(self.banner, f"banner-{kind}")
        self.banner.set_message(text, button)
        self._banner_callback = action

    def hide_banner(self) -> None:
        self.banner.set_message("")
        self._banner_callback = None

    def _banner_action(self) -> None:
        if self._banner_callback is not None:
            self._banner_callback()

    # ------------------------------------------------------------ ngspice
    def _missing_tools(self) -> list[tuple[str, str]]:
        """(tool, what it is) of the tools this circuit needs and that are not installed."""
        from ...circuits import engines

        missing = []
        if not engines.available():
            missing.append(("ngspice", "ngspice, the circuit simulator"))
        if any((self.spec(component.type) or ComponentSpec("", "")).role == "mcu"
               for component in self.schematic.components.values()):
            from ...circuits.mcu import avr

            if not avr.available():
                missing.append(("simavr", "the AVR emulator"))
        return missing

    def _engine_ready(self, then: Callable[[], None]) -> bool:
        """The tools the circuit needs are there (ngspice; the AVR emulator with microcontrollers); else
        they are installed in the background and ``then`` runs afterwards."""
        from ...core import preferences

        missing = self._missing_tools()
        if not missing:
            return True
        if self._installing:
            return False
        names = " and ".join(what for _tool, what in missing)
        if not preferences.get("circuits.install_tools"):
            self.show_banner(f"{names[0].upper()}{names[1:]} was not found. Install it (ngspice: brew install "
                             "libngspice, apt install libngspice0, ngspice.org) or allow openSciLab to install the "
                             "tools in Settings.", "Settings...", lambda: self.services and self.services.show_settings())
            return False
        self._installing = True
        installer = _Installer(self)
        self._installer = installer
        installer.progress.connect(lambda text, share: self.show_banner(f"Installing {names}: {text} "
                                                                         f"({share * 100:.0f} %)", kind="info"))

        def done(ok: bool, error: str) -> None:
            self._installing = False
            if ok:
                self.hide_banner()
                then()
            else:
                self.show_banner(f"{names[0].upper()}{names[1:]} could not be installed: {error}", "Try again",
                                 lambda: self._engine_ready(then))

        installer.done.connect(done)

        def work() -> None:
            from ...core import vendor

            try:
                for tool, _what in missing:
                    vendor.install(tool, lambda text, share: installer.progress.emit(text, share))
                installer.done.emit(True, "")
            except Exception as error:  # noqa: BLE001 - reported in the banner
                installer.done.emit(False, str(error))

        self.show_banner(f"Installing {names} ...", kind="info")
        threading.Thread(target=work, name="openscilab-install-tools", daemon=True).start()
        return False

    # --------------------------------------------------------------- run
    @property
    def run_state(self) -> str:
        return self._run_state

    def virtual_time(self) -> bool:
        return self.fast_box.isChecked()

    def set_virtual_time(self, virtual: bool) -> None:
        self.fast_box.setChecked(virtual)

    def _set_run_state(self, state: str) -> None:
        self._run_state = state
        self.run_state_changed.emit(state)
        self.document_changed.emit()

    def start_run(self) -> bool:
        if self._run_state == "paused" and self.live is not None:
            self.live.resume()
            self._set_run_state("running")
            self.timer.start()
            self.scene.freeze_flows(False)
            return True
        if self.running:
            return True
        if not self._engine_ready(self.start_run):
            return False
        circuit = self._netlist()
        errors = circuit.errors
        self.scene.set_problems({problem.item: problem.text for problem in errors if problem.item})
        if errors:
            self.show_banner("The circuit cannot be simulated: " + "; ".join(str(p) for p in errors[:4])
                             + (" ..." if len(errors) > 4 else ""))
            return False
        if not circuit.lines or len(circuit.elements) == 0:
            self.show_banner("There is nothing to simulate: place components and wire them.", kind="info")
            return False
        from ...circuits.engines import EngineBusy, live
        from ...circuits.engines.ngspice import NgSpiceError

        try:
            step = values.parse(self.schematic.setting("step"))
            max_step = values.parse_optional(self.schematic.setting("max_step"))
            speed = values.parse_optional(self.schematic.setting("speed")) or 1.0
        except ValueError as error:
            self.show_banner(f"The settings of the circuit: {error}")
            return False
        simulation = live.LiveSimulation(circuit, step, realtime=not self.virtual_time(), speed=speed,
                                         owner=self.title, max_step=max_step)
        self._prepare_cosimulation(simulation)
        try:
            simulation.start()
        except EngineBusy as error:
            self.show_banner(f"ngspice is busy: {error}. Stop it there first.")
            return False
        except NgSpiceError as error:
            self.show_banner(f"ngspice did not take the circuit: {error}")
            return False
        self.hide_banner()
        self.live = simulation
        self.circuit = circuit
        running.register(self._path or self.title, simulation)
        self.results.probes = {spice.data_name(probe.vector): probe.name for probe in circuit.probes}
        self._measure_currents(circuit)
        self._start_devices(circuit)
        self.timer.start()
        self._set_run_state("running")
        self.message.emit(f"{self.title} runs ({'virtual' if self.virtual_time() else 'real'} time)")
        return True

    # ------------------------------------------------- connected devices
    def _start_devices(self, circuit) -> None:
        """Pins of connected devices in the circuit (``device.input`` / ``device.output``): exchanged with
        their devices while it runs (``circuits/device_io.py``)."""
        if not circuit.device_pins or self.live is None:
            return
        from ...circuits import device_io

        simulation = self.live
        hub = self.services.hub if self.services is not None else None
        self.devices = device_io.DeviceBridge(hub, circuit.device_pins, circuit.vcc, simulation.set_inputs,
                                              simulation.value)
        self._device_problems: dict[str, str] = {}
        self.devices.start()

    def _stop_devices(self) -> None:
        bridge, self.devices = self.devices, None
        if bridge is not None:
            bridge.stop()  # (the outputs it drove go back to safe inputs)
            if getattr(self, "_device_problems", None):
                self._device_problems = {}
                self.scene.set_problems({})
                self.hide_banner()

    def _show_device_pins(self, circuit, texts: dict[str, str]) -> None:
        """The values of the device pins on the schematic; their problems at them and in the banner."""
        bridge = self.devices
        if bridge is None:
            return
        for pin in circuit.device_pins:
            value = bridge.values.get(pin.component)
            if value is not None:
                texts[pin.component] = values.display(value, "V") if pin.signal == "analog" else (
                    "1" if value > 0 else "0")
        problems = dict(bridge.problems)
        if problems != getattr(self, "_device_problems", {}):
            self._device_problems = problems
            self.scene.set_problems(problems)
            if problems:
                self.show_banner("Pins of connected devices: " + "; ".join(f"{ref}: {text}"
                                                                           for ref, text in sorted(problems.items())),
                                 kind="warning")
            else:
                self.hide_banner()

    def _prepare_cosimulation(self, simulation) -> None:
        """Microcontrollers of the circuit run with it (``circuits/mcu``)."""
        mcus = [ref for ref, component in self.schematic.components.items()
                if (self.spec(component.type) or ComponentSpec("", "")).role == "mcu"]
        if not mcus:
            return
        try:
            from ...circuits.mcu import cosim
        except ImportError:
            return
        problems = cosim.attach(simulation, self.schematic, self.spec, self.base_folder)
        if problems:
            self.show_banner("; ".join(problems), kind="warning")

    def pause_run(self) -> None:
        if self.live is not None and self._run_state == "running":
            self.live.pause()
            self._set_run_state("paused")
            self._show_live()
            self.timer.stop()  # (nothing changes while it pauses)
            self.scene.freeze_flows(True)

    def stop_run(self) -> None:
        if self.live is None:
            return
        simulation = self.live
        self.live = None
        self.timer.stop()
        self._stop_devices()
        self._wire_currents = None
        self.scene.set_flows({}, {})
        self.scene.set_voltages({})
        try:
            simulation.stop()
        finally:
            running.unregister(simulation)
        self.scene.set_live({}, {}, {})
        for ref, panel in self.scopes.items():  # (the screens show nothing of a run that is over)
            panel.clear()
            item = self.scene.components.get(ref)
            if item is not None and item.scope_traces:
                item.scope_traces = []
                item.update()
        self._set_run_state("stopped")

    def _update_live_circuit(self) -> None:
        circuit = self._netlist()
        if circuit.errors:
            self.scene.set_problems({problem.item: problem.text for problem in circuit.errors if problem.item})
            self.show_banner("The circuit has problems; it runs on as it was: "
                             + "; ".join(str(p) for p in circuit.errors[:3]))
            return
        self.scene.set_problems({})
        # (without waiting for ngspice: quick changes - a knob turned - are taken the newest first)
        self.live.submit(circuit)
        self._measure_currents(circuit)
        if self.devices is not None:
            self.devices.vcc = circuit.vcc
            self.devices.set_pins(circuit.device_pins)
        elif circuit.device_pins:
            self._start_devices(circuit)
        self.circuit = circuit
        self.results.probes = {spice.data_name(probe.vector): probe.name for probe in circuit.probes}
        if not self.banner.isHidden() and self.banner.label.text().startswith("The circuit has problems"):
            self.hide_banner()

    def _show_live(self) -> None:
        simulation = self.live
        circuit = self.circuit
        if simulation is None or circuit is None:
            return
        latest = simulation.history.latest()
        texts: dict[str, str] = {}
        glows: dict[str, float] = {}
        vcc = circuit.vcc
        for probe in circuit.probes:
            if probe.kind == "scope":
                continue  # (an oscilloscope draws its screen: _show_scopes)
            value = latest.get(spice.data_name(probe.vector))
            if value is None:
                continue
            number = float(value.real if isinstance(value, complex) else value)
            if probe.kind == "logic":
                texts[probe.component] = "1" if number > vcc / 2 else "0"
            elif probe.kind == "current":
                texts[probe.component] = values.display(number, "A")
            else:
                texts[probe.component] = values.display(number, "V")
        for ref, vector in circuit.currents.items():
            component = self.schematic.components.get(ref)
            spec = self.spec(component.type) if component is not None else None
            if spec is None or not (spec.extra.get("lights") or spec.type == "led"):
                continue
            value = latest.get(spice.data_name(vector))
            if value is None:
                continue
            full = values.parse_optional(component.params.get("full", "10 mA")) or 0.01
            glows[ref] = max(0.0, min(1.0, float(value.real if isinstance(value, complex) else value) / full))
        nets = {}
        volts: dict[str, float] = {}
        for name, safe in circuit.nets.items():
            value = latest.get(safe.lower()) if safe != "0" else 0.0
            if value is not None:
                volts[name] = float(value.real if isinstance(value, complex) else value)
                nets[name] = values.display(volts[name], "V")
        if self.show_current and getattr(self, "_current_tick", 0) % CURRENT_EVERY == 0:
            self.scene.set_voltages(volts)  # (wires and pins in the colours of their voltages)
        self._show_device_pins(circuit, texts)
        self._show_currents(simulation)
        self.scene.set_live(texts, glows, nets)
        self._show_scopes(simulation, circuit)
        for events in simulation.take_mcu_events():
            for ref, item in events.items():
                if ref == "#problems":
                    self.show_banner("; ".join(item))
                    continue
                self.serial.append(ref, item.get("serial", b""))
                for uart in ("1", "2", "3"):
                    if item.get(f"serial{uart}"):
                        self.serial.append(ref, item[f"serial{uart}"], uart)
                self.serial.set_state(f"{ref}: {item.get('state', '')}, {values.format_value(item.get('time', 0.0), 's', 4)}")
        if self.stack.currentWidget() is self.results:
            series = {}
            for probe in circuit.probes:
                times, data = simulation.course(probe.vector)
                series[probe.name] = (times, data)
            self.results.set_live(series)

    def _roles(self, role: str) -> list[str]:
        return [ref for ref, component in self.schematic.components.items()
                if (self.spec(component.type) or ComponentSpec("", "")).role == role]

    def _sync_instruments(self) -> None:
        """The tabs below the schematic follow the circuit: the serial monitor while it has microcontrollers,
        a screen for each oscilloscope."""
        mcus = self._roles("mcu")
        scopes = self._roles("probe.scope")
        tabs = self.instruments
        index = tabs.indexOf(self.serial)
        if mcus and index < 0:
            tabs.insertTab(0, self.serial, "Serial monitor")
        elif not mcus and index >= 0:
            tabs.removeTab(index)
            self.serial.setParent(self)
            self.serial.hide()
        self.serial.set_devices(mcus)
        for ref in list(self.scopes):
            if ref not in scopes:
                panel = self.scopes.pop(ref)
                tabs.removeTab(tabs.indexOf(panel))
                panel.deleteLater()
        for ref in scopes:
            panel = self.scopes.get(ref)
            if panel is None:
                panel = ScopePanel(ref, self._scope_changed, tabs)
                self.scopes[ref] = panel
                tabs.addTab(panel, f"{ref} · Oscilloscope")
            component = self.schematic.components[ref]
            spec = self.spec(component.type)
            params = spec.defaults() if spec is not None else {}
            params.update(component.params)
            panel.set_params(params)
        tabs.setVisible(bool(mcus or scopes))

    def _scope_changed(self, ref: str, name: str, value: str) -> None:
        if ref in self.schematic.components:
            self.set_param(ref, name, value)

    def _show_scopes(self, simulation, circuit) -> None:
        for ref, panel in self.scopes.items():
            picture = panel.show_live(simulation, probes_of(circuit, ref))
            item = self.scene.components.get(ref)
            if item is None:
                continue
            traces = []
            if picture is not None:
                from .scope import CHANNELS, DIVISIONS_Y, settings_of

                settings = settings_of(panel.params)
                for index, channel in enumerate(CHANNELS):
                    data = picture.channels.get(channel)
                    if data is None:
                        continue
                    step = max(len(data) // 400, 1)
                    shown = data[::step]
                    points = [(k / max(len(shown) - 1, 1),
                               0.5 - (float(v) - settings["offset"]) / (settings["volts_div"] * DIVISIONS_Y))
                              for k, v in enumerate(shown) if not math.isnan(float(v))]
                    traces.append((index, points))
            item.scope_traces = traces
            item.update()

    def _serial_send(self, ref: str, data: bytes) -> None:
        if self.live is None:
            self.message.emit("Run the circuit to talk to its microcontrollers")
            return
        try:
            self.live.mcu(ref, "serial", data)
        except Exception as error:  # noqa: BLE001 - said in the status bar
            self.message.emit(str(error))

    def _mcu_reset(self, ref: str) -> None:
        if self.live is not None:
            try:
                self.live.mcu(ref, "reset")
            except Exception as error:  # noqa: BLE001
                self.message.emit(str(error))

    # ------------------------------------------------------------ analyses
    def compute(self, kind: str) -> None:
        """Compute the analysis ``kind`` and show it under *Results*."""
        if self.running:
            self.show_banner("Stop the live simulation (Stop in the header) to compute an analysis: ngspice does one "
                             "thing at a time.", kind="info")
            return
        if not self._engine_ready(lambda: self.compute(kind)):
            return
        if kind == "sp":
            from ...circuits import engines

            if 0 < engines.version() < 44:
                self.show_banner(f"S-parameters need ngspice 44 or newer (this one is {engines.version()}).")
                return
        circuit = self._netlist(kind)
        errors = circuit.errors
        self.scene.set_problems({problem.item: problem.text for problem in errors if problem.item})
        if errors:
            self.show_banner(f"The {ANALYSIS_TITLES[kind].lower()} cannot be computed: "
                             + "; ".join(str(p) for p in errors[:4]))
            return
        from ...circuits.engines import EngineBusy
        from ...circuits.engines.analysis import run_analysis
        from ...circuits.engines.ngspice import NgSpiceError
        from .. import background

        try:
            result = background.run(self, f"Computing the {ANALYSIS_TITLES[kind].lower()} ...",
                                    lambda: run_analysis(circuit, kind, owner=self.title))
        except background.Cancelled:
            return
        except (EngineBusy, NgSpiceError) as error:
            self.show_banner(f"The {ANALYSIS_TITLES[kind].lower()} failed: {error}")
            return
        self.hide_banner()
        self.results.probes = {spice.data_name(probe.vector): probe.name for probe in circuit.probes}
        wanted = [spice.data_name(probe.vector) for probe in circuit.probes]
        self.results.add(result, wanted)
        self.set_view("Results")
        self.message.emit(f"{ANALYSIS_TITLES[kind]} computed")

    # ------------------------------------------------------------- closing
    def shutdown(self) -> None:
        self.stop_run()
        if self._popup is not None:
            self._popup.close()


_warmed = False


def _warm_up_engine() -> None:
    """Start a process of ngspice in the background when the first circuit opens: the first *Run* starts
    at once."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    from ...circuits import engines

    if engines.available():
        from ...circuits.engines import process

        process.warm_up()


def open_circuit_file(path: str, services=None) -> CircuitDocument:
    """The document of the circuit in ``path`` (raises CircuitError, OSError)."""
    from ...lab.project import Project

    schematic = yaml_io.load(path)
    root = Project.find(path)
    project = Project.open(root) if root else None
    return CircuitDocument(schematic, path, project=project, services=services)


def new_circuit(services=None) -> CircuitDocument:
    project = services.active_project() if services is not None else None
    return CircuitDocument(Schematic("Circuit"), None, project=project, services=services)
