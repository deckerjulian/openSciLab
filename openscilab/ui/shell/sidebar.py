# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sidebar: the section chosen in the activity bar (the project, the devices).

A click on an entry opens it, in every list (with Ctrl or Shift held, the data list selects instead,
to compare two captures).
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import recent
from .. import sections
from ..icons import icon, known
from ..theme import set_role


class Sidebar(QStackedWidget):
    """Pages by key, each with a small title."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setMinimumWidth(200)
        self._pages: dict[str, QWidget] = {}
        self._contents: dict[str, QWidget] = {}

    def add_section(self, key: str, title: str, widget: QWidget) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        heading = QLabel(title.upper(), page)
        set_role(heading, "sidebar-title")
        layout.addWidget(heading)
        widget.setParent(page)
        layout.addWidget(widget, 1)
        self._pages[key] = page
        self._contents[key] = widget
        self.addWidget(page)

    def section(self, key: str) -> QWidget:
        return self._contents[key]

    def show_section(self, key: str) -> None:
        if key in self._pages:
            self.setCurrentWidget(self._pages[key])

    @property
    def current_key(self) -> Optional[str]:
        for key, page in self._pages.items():
            if page is self.currentWidget():
                return key
        return None


def _list(parent: QWidget) -> QListWidget:
    widget = QListWidget(parent)
    widget.setFrameShape(QFrame.NoFrame)
    return widget


class _ClickOpener(QObject):
    """Opens the entry of a list that was clicked, unless Ctrl or Shift was held at the press (the
    keys of the click itself: :func:`open_on_click`)."""

    SELECTING = Qt.ControlModifier | Qt.ShiftModifier | Qt.MetaModifier

    def __init__(self, widget: QListWidget, opened: Callable[[QListWidgetItem], None]) -> None:
        super().__init__(widget)
        self.opened = opened
        self.selecting = False
        widget.viewport().installEventFilter(self)
        widget.itemActivated.connect(opened)
        widget.itemClicked.connect(self._clicked)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.MouseButtonPress:
            self.selecting = bool(event.modifiers() & self.SELECTING)
        return False

    def _clicked(self, item: QListWidgetItem) -> None:
        selecting, self.selecting = self.selecting, False
        if not selecting:
            self.opened(item)


def open_on_click(widget: QListWidget, opened: Callable[[QListWidgetItem], None]) -> None:
    """One click opens an entry (as Enter and a double click do); with Ctrl or Shift held a click
    selects (several entries for the commands of the list's menu)."""
    _ClickOpener(widget, opened)


#: kinds of files in the data folder of a project: (suffixes, kind, icon)
DATA_KINDS = (
    ((".lac", ".lac.gz", ".sr"), "capture", "channels"),
    ((".csv", ".tsv"), "table", "list"),
    ((".log", ".txt"), "log", "terminal"),
    ((".html", ".htm", ".pdf"), "report", "book"),
)


def data_kind(path: str) -> Optional[tuple[str, str]]:
    lowered = path.lower()
    for suffixes, kind, icon_name in DATA_KINDS:
        if lowered.endswith(suffixes):
            return kind, icon_name
    return None


def data_files(folder: str) -> list[tuple[str, str]]:
    """``(path, kind)`` of the captures, tables, logs and reports in ``folder`` (and below), newest first."""
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            kind = data_kind(path)
            if kind is not None:
                found.append((path, kind[0]))
    def changed(item: tuple[str, str]) -> float:
        try:
            return os.path.getmtime(item[0])
        except OSError:  # deleted while the folder was read
            return 0.0

    found.sort(key=changed, reverse=True)
    return found


def file_icon(path: str) -> str:
    """The icon of a file by what it holds (flow, panel, capture, waveform, a document of a plugin, other)."""
    added = sections.icon_for(path)
    if added is not None:
        return known(added, "file-plus")
    lowered = path.lower()
    for endings, name in (((".flow.yaml", ".py"), "nodes"), ((".panel.yaml",), "panel"),
                          ((".lac", ".lac.gz", ".sr", ".csv", ".vcd"), "channels"), ((".wave.yaml", ".sdl"), "wave")):
        if lowered.endswith(endings):
            return name
    return "file-plus"


class ProjectSection(QWidget):
    """Open documents, the data of the project (captures, tables, logs, reports) and recent files."""

    document_activated = Signal(object)
    file_requested = Signal(str)
    #: two captures to compare: (capture, reference)
    compare_requested = Signal(str, str)
    #: a capture to export as CSV
    export_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from .sections import Part, PartStack

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.parts = PartStack(self)
        layout.addWidget(self.parts, 1)

        self.documents = _list(self)
        open_on_click(self.documents, lambda item: self.document_activated.emit(item.data(Qt.UserRole)))
        self.documents_part = self.parts.add(Part("documents", "Open documents", self.documents), 2)

        # the flows, panels and waveforms of the project of the active document (also closed ones)
        self.files = _list(self)
        open_on_click(self.files, self._open_item)
        self.files_part = self.parts.add(Part("files", "Project", self.files), 2)
        self.project = None

        self.data = _list(self)
        self.data.setSelectionMode(QAbstractItemView.ExtendedSelection)
        open_on_click(self.data, self._open_item)
        self.data.setContextMenuPolicy(Qt.CustomContextMenu)
        self.data.customContextMenuRequested.connect(self._data_menu)
        self.data_part = self.parts.add(Part("data", "Data", self.data), 2)
        self.data_part.add_button("refresh", "Read the data folder again", lambda: self.refresh_data())
        self.data_folder: Optional[str] = None

        self.recent = _list(self)
        open_on_click(self.recent, self._open_item)
        self.recent_part = self.parts.add(Part("recent", "Recent", self.recent), 1)
        self.set_project(None)
        self.set_data_folder(None)

    def _open_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:  # (not a note like "No flows or panels yet")
            self.file_requested.emit(path)

    def set_project(self, project) -> None:
        """The files of ``project`` (``None``: no project, the list is hidden)."""
        self.project = project
        self.files.clear()
        visible = project is not None
        self.files_part.setVisible(visible)
        self.parts.refresh()
        if not visible:
            return
        self.files_part.set_title(f"Project · {project.name or os.path.basename(project.root.rstrip(os.sep))}")
        try:
            paths = project.flows() + project.panels() + project.waveforms() + sections.project_files(project)
        except OSError:
            paths = []
        for path in paths:
            item = QListWidgetItem(icon(file_icon(path)), os.path.relpath(path, project.root), self.files)
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
        self.files_part.set_count(len(paths))
        if not paths:
            item = QListWidgetItem("No flows or panels yet", self.files)
            item.setFlags(Qt.NoItemFlags)

    def set_documents(self, documents: list, active=None) -> None:
        self.documents.clear()
        self.documents_part.set_count(len(documents))
        for document in documents:
            text = document.title + (" ●" if document.dirty else "")
            kind = getattr(document, "document_kind", "")
            icon_name = {"flow": "nodes", "panel": "panel", "data": "channels", "device": "devices",
                         "waveform": "wave", "start": "home", "hardware": "chip"}.get(kind) \
                or known(sections.icon_for(kind) or "file-plus", "file-plus")
            item = QListWidgetItem(icon(icon_name), text, self.documents)
            item.setData(Qt.UserRole, document)
            if document.path:
                item.setToolTip(document.path)
            if document is active:
                self.documents.setCurrentItem(item)

    def set_data_folder(self, folder: Optional[str]) -> None:
        """Show the data of ``folder`` (the ``data/`` of the project of the active document)."""
        self.data_folder = folder
        self.refresh_data()

    def refresh_data(self) -> None:
        self.data.clear()
        folder = self.data_folder
        visible = bool(folder and os.path.isdir(folder))
        self.data_part.setVisible(visible)
        self.parts.refresh()
        if not visible:
            return
        self.data_part.set_title(f"Data · {os.path.basename(os.path.dirname(folder)) or folder}")
        found = data_files(folder)
        self.data_part.set_count(len(found))
        if not found:
            item = QListWidgetItem("Captures, tables and reports of the flows appear here", self.data)
            item.setFlags(Qt.NoItemFlags)
        for path, kind in found:
            icon_name = data_kind(path)[1]
            item = QListWidgetItem(icon(icon_name), os.path.relpath(path, folder), self.data)
            item.setToolTip(f"{kind}: {path}")
            item.setData(Qt.UserRole, path)
            item.setData(Qt.UserRole + 1, kind)

    def selected_data(self) -> list[tuple[str, str]]:
        return [(item.data(Qt.UserRole), item.data(Qt.UserRole + 1)) for item in self.data.selectedItems()]

    def _data_menu(self, position) -> None:
        selected = self.selected_data()
        if not selected:
            return
        menu = QMenu(self)
        menu.addAction(icon("folder"), "Open", lambda: [self.file_requested.emit(path) for path, _kind in selected])
        captures = [path for path, kind in selected if kind == "capture"]
        if len(captures) == 2:
            menu.addAction(icon("compare"), "Compare the two captures",
                           lambda: self.compare_requested.emit(captures[0], captures[1]))
        if len(captures) == 1 and len(selected) == 1:
            menu.addAction(icon("export"), "Export as CSV...", lambda: self.export_requested.emit(captures[0]))
        menu.addAction(icon("refresh"), "Refresh", self.refresh_data)
        menu.exec(self.data.viewport().mapToGlobal(position))

    def refresh_recent(self) -> None:
        self.recent.clear()
        for path in recent.entries("files"):
            item = QListWidgetItem(icon(file_icon(path)), os.path.basename(path), self.recent)
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
        self.recent_part.set_count(self.recent.count() or None)
        if not self.recent.count():
            item = QListWidgetItem("Files you open appear here", self.recent)
            item.setFlags(Qt.NoItemFlags)
