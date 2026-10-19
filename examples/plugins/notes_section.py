# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""An example plugin with a section of its own: *Notes*.

It adds an entry to the activity bar with a section of the sidebar that lists the notes of the active
project (its ``notes/`` folder), and a kind of documents - text notes (``*.note.txt``) - that *File →
New*, *File → Open* and the Project section know. Copy it into the ``plugins`` folder of the settings
directory (or point ``OPENSCILAB_PLUGINS`` to this folder) and restart openSciLab; docs/drivers.md
describes the hooks.
"""

from __future__ import annotations

import os
from functools import lru_cache

NOTE_ICON = "<path d='M5 3h10l4 4v14H5z'/><path d='M15 3v4h4'/><path d='M8 12h8M8 16h6'/>"


def setup_ui() -> None:
    """Only the application calls it (with Qt): the command line stays without."""
    from openscilab.ui.icons import register_icon
    from openscilab.ui.sections import register_document_type, register_section

    register_icon("note", NOTE_ICON)
    register_section("notes", "Notes", "note", NotesSection, shortcut="Ctrl+Shift+M", on_show=NotesSection.refresh)
    register_document_type("note", "Note", (".note.txt",), open_note, new_note, icon="note", folder="notes",
                           description="A text note of the project")


@lru_cache(maxsize=None)
def _classes():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFileDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget

    from openscilab.ui.documents.base import DocumentWidget, untitled_title
    from openscilab.ui.icons import icon

    class NoteDocument(DocumentWidget):
        document_kind = "note"

        def __init__(self, path: str | None = None, text: str = "") -> None:
            super().__init__()
            self._path = path
            self._untitled = untitled_title("note")
            self.editor = QPlainTextEdit(self)
            self.editor.setPlainText(text)
            self.editor.document().setUndoRedoEnabled(True)
            self.editor.document().modificationChanged.connect(lambda _m: self.document_changed.emit())
            self.editor.document().setModified(False)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.editor)

        @property
        def title(self) -> str:
            return os.path.basename(self._path) if self._path else self._untitled

        @property
        def path(self):
            return self._path

        @property
        def dirty(self) -> bool:
            return self.editor.document().isModified()

        def can_save(self) -> bool:
            return True

        def save(self) -> bool:
            if not self._path:
                return self.save_as()
            with open(self._path, "w", encoding="utf-8") as handle:
                handle.write(self.editor.toPlainText())
            self.editor.document().setModified(False)
            self.message.emit(f"Saved {self.title}")
            return True

        def save_as(self) -> bool:
            path, _ = QFileDialog.getSaveFileName(self, "Save note", self._path or "", "Notes (*.note.txt)")
            if not path:
                return False
            if not path.lower().endswith(".note.txt"):
                path += ".note.txt"
            self._path = path
            return self.save()

    class NotesSection(QWidget):
        """The notes of the active project; a click opens one."""

        def __init__(self, services) -> None:
            super().__init__()
            self.services = services
            self.list = QListWidget(self)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.list)
            self.list.itemActivated.connect(self._open)
            self.list.itemClicked.connect(self._open)
            services.project_changed.connect(lambda _project: self.refresh())
            self.refresh()

        def refresh(self) -> None:
            self.list.clear()
            project = self.services.active_project()
            paths = project.files("notes", (".note.txt",)) if project is not None else []
            for path in paths:
                item = QListWidgetItem(icon("note"), os.path.basename(path), self.list)
                item.setData(Qt.UserRole, path)
            if not paths:
                item = QListWidgetItem("No notes", self.list)
                item.setFlags(Qt.NoItemFlags)

        def _open(self, item) -> None:
            path = item.data(Qt.UserRole)
            if path:
                self.services.open_file(path)

    return NoteDocument, NotesSection


def NotesSection(services):  # noqa: N802 - a factory standing in for the class (Qt is imported late)
    return _classes()[1](services)


NotesSection.refresh = lambda widget: widget.refresh()


def open_note(path: str, services):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return _classes()[0](path, text)


def new_note(services):
    return _classes()[0]()
