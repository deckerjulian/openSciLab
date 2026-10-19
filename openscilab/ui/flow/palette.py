# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The node palette beside a flow: search, groups, drag onto the canvas or double-click.

The groups come in the order of ``GROUPS`` (where values come from, what works on them, where they
go), each with its icon in its colour; which groups are folded away is kept (``palette.json``; the
133 decoders start folded). The search finds a node by its title, type, group, keywords and
description; the matches stay in their groups, the group of the best match first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QFrame, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ...core import fuzzy, settings
from ...lab.model import DEVICE_NODE
from ...lab.nodes.registry import GROUP_ICONS, GROUPS, NodeSpec, Registry, default_registry
from ..icons import icon, known
from ..theme import group_color
from .canvas import NODE_MIME, encode_node

#: the groups folded away (kept between runs)
PALETTE_FILE = "palette.json"
#: folded until opened the first time
FOLDED_AT_FIRST = ("decoder",)


def search_text(spec: NodeSpec) -> str:
    """What the search of the palette (and of the canvas) looks through for a node type."""
    return " ".join((spec.title, spec.type, spec.group_title, " ".join(spec.keywords), spec.description))


def folded_groups() -> set[str]:
    data = settings.get_settings(PALETTE_FILE)
    if isinstance(data, dict) and isinstance(data.get("folded"), list):
        return {str(key) for key in data["folded"]}
    return set(FOLDED_AT_FIRST)


def set_folded(key: str, folded: bool) -> None:
    groups = folded_groups()
    if folded:
        groups.add(key)
    else:
        groups.discard(key)
    settings.persist_settings(PALETTE_FILE, {"folded": sorted(groups)})


#: a group whose nodes are loaded when it is opened: its name
PENDING_ROLE = Qt.UserRole + 1
#: the key of a group (``measure``)
GROUP_ROLE = Qt.UserRole + 2


class _Tree(QTreeWidget):
    def startDrag(self, actions) -> None:  # noqa: N802 - Qt naming
        item = self.currentItem()
        type_name = item.data(0, Qt.UserRole) if item is not None else None
        if not type_name:
            return
        mime = QMimeData()
        mime.setData(NODE_MIME, type_name.encode("utf-8"))
        mime.setText(item.text(0))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


@dataclass(frozen=True)
class DevicePreset:
    """A device ready to be placed: an open instrument, a simulator, a device of the project."""

    title: str
    node_id: str
    address: str
    #: "open", "project" or "simulator"
    kind: str = "open"


class NodePalette(QWidget):
    #: a node to add (:func:`~.canvas.encode_node`: a type, or a preset with id and parameters)
    node_requested = Signal(str)

    def __init__(self, registry: Optional[Registry] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.registry = registry or default_registry
        self.presets: list[DevicePreset] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search nodes")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.tree = _Tree(self)
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setDragEnabled(True)
        # a double-click activates the item (Enter too); connecting both would add two nodes
        self.tree.itemActivated.connect(self._activated)
        self.tree.itemExpanded.connect(self._expanded)
        self.tree.itemCollapsed.connect(lambda item: self._folded(item, True))
        # a click on a group's title opens or closes it
        self.tree.itemClicked.connect(lambda item, _column: item.childCount() and item.setExpanded(
            not item.isExpanded()))
        self._filling = False
        layout.addWidget(self.tree, 1)
        #: also the groups that take a moment to load (the decoders): once one was opened
        self._all_groups = False
        self.refresh()

    def set_registry(self, registry: Registry) -> None:
        self.registry = registry
        self.refresh()

    def set_device_presets(self, presets: list[DevicePreset]) -> None:
        """The devices the palette offers as ready device nodes (above the node types)."""
        if presets != self.presets:
            self.presets = list(presets)
            self.refresh()

    def _group(self, key: str, searching: bool, folded: set[str]) -> QTreeWidgetItem:
        group = QTreeWidgetItem(self.tree, [GROUPS.get(key, key.capitalize())])
        group.setFlags(Qt.ItemIsEnabled)
        group.setData(0, GROUP_ROLE, key)
        group.setIcon(0, icon(known(GROUP_ICONS.get(key, "nodes")), group_color(key)))
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        group.setExpanded(searching or key not in folded)
        return group

    def refresh(self) -> None:
        self._filling = True
        self.tree.clear()
        query = self.search.text()
        searching = bool(query.strip())
        folded = folded_groups()
        # The decoders (133 modules) are only imported when they are looked at or searched for.
        specs = self.registry.specs(lazy=self._all_groups or searching or "decoder" not in folded)
        if searching:
            specs = fuzzy.rank(query, specs, key=search_text)
        groups: dict[str, QTreeWidgetItem] = {}
        presets = self.presets
        if searching:
            presets = fuzzy.rank(query, presets, key=lambda preset: f"{preset.title} {preset.address}")
        if presets:
            group = self._group("device", searching, folded)
            groups["device"] = group
            hints = {"open": "open in the device list", "project": "device of the project",
                     "simulator": "simulator"}
            for preset in presets:
                item = QTreeWidgetItem(group, [preset.title])
                item.setData(0, Qt.UserRole, encode_node(DEVICE_NODE, preset.node_id, {"address": preset.address}))
                item.setToolTip(0, f"{hints.get(preset.kind, preset.kind)}: {preset.address or preset.node_id}\n"
                                   "A device node; wire its 'device' output to the nodes that use it.")
                item.setIcon(0, icon("chip" if preset.kind != "simulator" else "devices"))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        # the groups in their order; while searching, the group of the best match first and the
        # matches in their groups, best first
        order = list(GROUPS)
        if searching:
            order = list(dict.fromkeys(spec.group for spec in specs))
        for spec in sorted(specs, key=lambda spec: order.index(spec.group) if spec.group in order else len(order)):
            group = groups.get(spec.group)
            if group is None:
                group = self._group(spec.group, searching, folded)
                groups[spec.group] = group
            item = QTreeWidgetItem(group, [spec.title])
            item.setData(0, Qt.UserRole, spec.type)
            item.setToolTip(0, f"<b>{spec.full_title}</b> · {spec.type}<br>{spec.description}"
                               "<br><i>Drag it onto the flow, or double-click it</i>")
            item.setIcon(0, icon(known(spec.icon)))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        for key, group in groups.items():
            group.setToolTip(0, f"{group.childCount()} node{'s' if group.childCount() != 1 else ''}; a click opens "
                                "or closes the group")
        for name in self.registry.pending_groups():
            group = self._group(name, False, folded | {name})
            group.setData(0, PENDING_ROLE, name)
            group.setToolTip(0, "Open to load these nodes")
            QTreeWidgetItem(group, ["Loading..."]).setFlags(Qt.NoItemFlags)
        self._filling = False

    def _folded(self, item: QTreeWidgetItem, folded: bool) -> None:
        key = item.data(0, GROUP_ROLE)
        if key and not self._filling and not self.search.text().strip():
            set_folded(str(key), folded)

    def _expanded(self, item: QTreeWidgetItem) -> None:
        self._folded(item, False)
        if item.data(0, PENDING_ROLE) and not self._all_groups:
            self._all_groups = True
            QTimer.singleShot(0, self.refresh)  # not while the tree handles the click

    def load_all(self) -> None:
        """Show every group now (also those loaded on demand)."""
        if not self._all_groups:
            self._all_groups = True
            self.refresh()

    def types(self) -> list[str]:
        result = []
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            for child in range(group.childCount()):
                result.append(group.child(child).data(0, Qt.UserRole))
        return result

    def _activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        type_name = item.data(0, Qt.UserRole)
        if type_name:
            self.node_requested.emit(type_name)
