# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The *Circuits* section of the sidebar: the circuits of the project, the catalog of components and
the circuits that run."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...circuits import FOLDER, SUFFIX
from ..icons import icon
from ..shell.sections import Part, PartStack
from ..shell.sidebar import open_on_click
from .catalog_widget import ComponentBrowser


class CircuitsSection(QWidget):
    def __init__(self, services, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.parts = PartStack(self)
        layout.addWidget(self.parts, 1)
        self.files = QListWidget(self)
        open_on_click(self.files, self._open)
        self.files_part = self.parts.add(Part("circuits-files", "Circuits", self.files), 1)
        self.files_part.add_button("plus", "A new circuit", self.new_circuit)
        self.browser = ComponentBrowser(parent=self)
        self.browser.chosen.connect(self._place)
        self.parts.add(Part("circuits-components", "Components", self.browser), 4)
        self.runs = QListWidget(self)
        open_on_click(self.runs, self._show_run)
        self.runs_part = self.parts.add(Part("circuits-running", "Running", self.runs), 1)
        services.project_changed.connect(lambda _project: self.refresh())
        services.documents_changed.connect(self.refresh_runs)
        services.active_document_changed.connect(lambda _document: self.refresh_runs())
        self.refresh()

    def refresh(self) -> None:
        self.files.clear()
        project = self.services.active_project()
        paths = project.files(FOLDER, (SUFFIX,)) if project is not None else []
        for document in self.services.documents():
            if getattr(document, "document_kind", "") == "circuit" and document.path and document.path not in paths:
                paths.append(document.path)
        for path in paths:
            shown = os.path.relpath(path, project.root) if project is not None and path.startswith(project.root) \
                else os.path.basename(path)
            item = QListWidgetItem(icon("circuit" if _known("circuit") else "file-plus"), shown, self.files)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
        self.files_part.set_count(len(paths))
        if not paths:
            item = QListWidgetItem("No circuits yet: + makes one", self.files)
            item.setFlags(Qt.NoItemFlags)
        self.refresh_runs()

    def refresh_runs(self) -> None:
        self.runs.clear()
        count = 0
        for document in self.services.documents():
            if getattr(document, "document_kind", "") != "circuit":
                continue
            state = document.run_state
            if state not in ("running", "paused"):
                continue
            count += 1
            item = QListWidgetItem(icon("play" if state == "running" else "pause"),
                                   f"{document.title} ({state})", self.runs)
            item.setData(Qt.UserRole, document)
        self.runs_part.set_count(count)
        if not count:
            item = QListWidgetItem("None: Run (F5) simulates the active circuit", self.runs)
            item.setFlags(Qt.NoItemFlags)

    def _open(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.services.open_file(path)

    def _show_run(self, item: QListWidgetItem) -> None:
        document = item.data(Qt.UserRole)
        if document is not None:
            self.services.activate(document)

    def new_circuit(self) -> None:
        from .document import new_circuit

        self.services.add_document(new_circuit(self.services))
        self.refresh()

    def _place(self, type_name: str, unit: int) -> None:
        """A component chosen in the catalog: it follows the pointer on the active circuit (a new one when
        no circuit is active)."""
        document = self.services.active_document()
        if getattr(document, "document_kind", "") != "circuit":
            from .document import new_circuit

            document = self.services.add_document(new_circuit(self.services))
        document.set_view("Schematic")
        center = document.view.mapToScene(document.view.viewport().rect().center())
        document.scene.place(type_name, unit, center)
        document.view.setFocus()


def _known(name: str) -> bool:
    from ..icons import known

    return known(name, "") == name
