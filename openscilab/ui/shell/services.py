# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a section or a kind of documents of a plugin may ask of the window (``ui/sections.py``).

Plugins get this object instead of the window: it keeps what they rely on small and stable while the
window changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtWidgets import QWidget

    from .main_window import ShellWindow


class ShellServices(QObject):
    """The window as plugins see it."""

    #: the project of the active document changed (a :class:`~openscilab.lab.project.Project` or ``None``)
    project_changed = Signal(object)
    #: another document became active (or ``None``)
    active_document_changed = Signal(object)
    #: documents were opened or closed
    documents_changed = Signal()

    def __init__(self, shell: ShellWindow) -> None:
        super().__init__(shell)
        self._shell = shell

    # ------------------------------------------------------------ documents
    def open_file(self, path: str) -> QWidget | None:
        """Open ``path`` in the document that suits it (an open one is shown instead)."""
        return self._shell.open_file(path)

    def add_document(self, document: QWidget, activate: bool = True) -> QWidget:
        """Show a document in a tab: the shell shows its tool bar, menus, inspector and run state."""
        return self._shell.add_document(document, activate=activate)

    def activate(self, document: QWidget) -> None:
        self._shell.area.activate(document)

    def active_document(self) -> QWidget | None:
        return self._shell.active_document()

    def documents(self) -> list[QWidget]:
        return self._shell.documents()

    def remember(self, path: str) -> None:
        """Add ``path`` to the recent files."""
        self._shell._remember(path)

    # -------------------------------------------------------------- project
    def active_project(self):
        """The project of the active document (``None``: none)."""
        return self._shell.project_section.project

    def data_folder(self, document) -> str | None:
        """Where ``document`` keeps its data (the ``data/`` folder of its project)."""
        return self._shell.data_folder(document)

    # ----------------------------------------------------------------- shell
    def notify(self, text: str, timeout: int = 6000) -> None:
        """A short note in the status bar."""
        self._shell.notify(text, timeout)

    def select_section(self, key: str) -> None:
        """Show the section ``key`` of the sidebar."""
        self._shell.activity_bar.select(key)

    def show_settings(self, page: str = "") -> None:
        self._shell.show_preferences(page)

    def show_data(self, instrument) -> QWidget:
        """A data view of ``instrument`` (of the hub)."""
        return self._shell.show_data(instrument)

    @property
    def hub(self):
        """Every instrument of the lab (:class:`~openscilab.core.hub.Hub`)."""
        return self._shell.hub

    @property
    def window(self) -> QWidget:
        """The window, as the parent of dialogs only."""
        return self._shell
