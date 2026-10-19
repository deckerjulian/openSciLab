# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What every document of the shell offers.

A document is a widget in a tab of the document area (a flow, a panel, captures in a data
view, a device, ...). The shell asks it for its title, file, unsaved state, menus, actions
(for the command palette), tool bar, views and the inspector content, and drives what it runs
(:ref:`the run protocol <run>`). :class:`Document` is a mixin with defaults; widgets inherit from
their Qt base class first, e.g. ``class FlowDocument(DocumentWidget)``.

**Signals** (``DocumentWidget`` declares them): ``document_changed`` (title, path, dirty state or
selection changed), ``message(str)`` (a short note for the status bar of the shell – the shell
has the only status bar) and ``run_state_changed(str)``.

.. _run:

**The run protocol** (docs/design.md, rule 3): the header's *Run*, *Pause* and *Stop* – and the
*Run* menu, F5, Shift+F5, F10 – act on the active document when it is :attr:`Document.runnable`.
It names what runs (:attr:`run_label`: *Run*, *Capture*, *Play*), says how far it is
(:attr:`run_state`: ``idle``, ``running``, ``paused``, ``finished``, ``stopped``, ``error``) and
whether it runs in real or virtual time (:attr:`time_modes`, :meth:`virtual_time`).
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolBar, QWidget


class Document:
    """Mixin with the document interface; every method has a sensible default."""

    #: Kind of document ("data", "start", "flow", ...); used by the shell and the tests.
    document_kind = "document"

    # ------------------------------------------------------------ identity
    @property
    def title(self) -> str:
        """Text of the tab."""
        path = self.path
        return os.path.basename(path) if path else "Untitled"

    @property
    def path(self) -> Optional[str]:
        """File the document was loaded from or saved to, ``None`` before it is saved."""
        return None

    @property
    def dirty(self) -> bool:
        """``True`` while there are changes that were not saved."""
        return False

    # ------------------------------------------------------- shell content
    def document_actions(self) -> list[QAction]:
        """Actions the command palette offers beside those of :meth:`document_menus`.

        (``actions()`` is taken by ``QWidget``.)
        """
        return []

    def document_menus(self) -> list[QMenu]:
        """Menus shown in the menu bar of the shell while the document is active."""
        return []

    def document_file_actions(self) -> list[QAction]:
        """Entries the *Project* menu shows for this document (e.g. *Export*)."""
        return []

    def toolbar(self) -> Optional[QToolBar]:
        """The narrow toolbar of the document (shown inside the document)."""
        return None

    def views(self) -> list[str]:
        """Names of the views the document can switch between (``Graph``, ``YAML``, ...)."""
        return []

    def current_view(self) -> Optional[str]:
        views = self.views()
        return views[0] if views else None

    def set_view(self, name: str) -> None:
        """Switch to the view ``name`` (one of :meth:`views`)."""

    def inspector_widget(self, selection=None) -> Optional[QWidget]:
        """Properties of ``selection`` (or of the document) for the inspector, ``None`` for none."""
        return None

    def undo_stack(self):
        """The ``QUndoStack`` of the document, if it has one (Edit → Undo/Redo)."""
        return None

    # ---------------------------------------------------------------- run
    #: the document runs something the header's Run, Pause and Stop drive
    runnable = False
    #: the label of *Run* for this document: what it does ("Run", "Capture", "Play")
    run_label = "Run"
    #: the tool tip of *Run*
    run_tooltip = ""
    #: a run can pause (and continue)
    can_pause = False
    #: a paused run can go on by one step (F10)
    can_step = False
    #: whether *Run* may be pressed now (e.g. a device to capture with is chosen)
    can_start_run = True
    #: the document runs in real time or virtual time (flows and panels with simulators)
    time_modes = False
    #: the document can use simulators for every device it uses
    simulates_devices = False

    @property
    def run_state(self) -> str:
        """``idle``, ``running``, ``paused``, ``finished``, ``stopped`` or ``error``."""
        return "idle"

    def start_run(self) -> bool:
        """Run (or continue a paused run); ``True`` when it runs."""
        return False

    def pause_run(self) -> None:
        """Pause the run."""

    def step_run(self) -> None:
        """One step of a paused run."""

    def stop_run(self) -> None:
        """Stop the run."""

    def virtual_time(self) -> bool:
        """Whether the next run goes in virtual time (:attr:`time_modes`)."""
        return False

    def set_virtual_time(self, virtual: bool) -> None:
        """Run in virtual time (``True``) or in real time."""

    def simulated(self) -> bool:
        """Whether the next run uses simulators for every device (:attr:`simulates_devices`)."""
        return False

    def set_simulated(self, simulated: bool) -> None:
        """Use simulators for every device (``True``) or the devices themselves."""

    # -------------------------------------------------------------- files
    def can_save(self) -> bool:
        return False

    def save(self) -> bool:
        """Save to :attr:`path` (or ask for one); ``True`` when saved."""
        return False

    def save_as(self) -> bool:
        return False

    def shutdown(self) -> None:
        """Release devices and threads; called once before the document is closed for good."""


class Switch(QObject):
    """A setting that is on or off, without a widget of its own: the shell shows it where it belongs
    (the time of a run beside *Run* in the header, the *Run* menu) for the active document."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)

    def isChecked(self) -> bool:  # noqa: N802 - the name of QAbstractButton, which it stands in for
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        checked = bool(checked)
        if checked != self._checked:
            self._checked = checked
            self.toggled.emit(checked)


_untitled: dict[str, int] = {}


def untitled_title(kind: str) -> str:
    """``Untitled flow``, ``Untitled flow 2``, … for documents that have no name of their own yet."""
    number = _untitled.get(kind, 0) + 1
    _untitled[kind] = number
    return f"Untitled {kind}" + (f" {number}" if number > 1 else "")


class DocumentWidget(QWidget, Document):
    """A plain widget as a document."""

    #: Title, path, dirty state or selection changed: the shell updates tab, menus and inspector.
    document_changed = Signal()
    #: a short note for the status bar of the shell (saved, exported, ...)
    message = Signal(str)
    #: the state of the run changed (see :attr:`Document.run_state`)
    run_state_changed = Signal(str)
