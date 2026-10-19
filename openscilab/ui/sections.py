# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sections and kinds of documents of plugins.

A plugin adds an area of its own to the application: an entry of the activity bar with a section of
the sidebar (beside *Project* and *Devices*), and kinds of documents - files with their suffixes,
*File → New* entries, icons and a folder of every project. It registers them in its ``setup_ui()``
(see :mod:`openscilab.plugins`, docs/drivers.md)::

    def setup_ui():
        from openscilab.ui.sections import register_document_type, register_section

        register_section("notes", "Notes", "book", NotesSection, shortcut="Ctrl+Shift+M")
        register_document_type("note", "Note", (".note.txt",), open_note, new_note, folder="notes")

The window gives the factories a :class:`~openscilab.ui.shell.services.ShellServices`, never itself:
what a section may ask of the shell (open a file, show a document, the active project ...).
Windows created afterwards show what was registered.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from PySide6.QtWidgets import QWidget

    from .documents.base import DocumentWidget
    from .shell.services import ShellServices

#: keys of the sections of openSciLab itself (and of the entries of the activity bar)
RESERVED = ("project", "devices", "start", "settings", "nodes")
_KEY = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True)
class SectionSpec:
    """A section of the sidebar with its entry in the activity bar."""

    key: str
    title: str
    #: the name of an icon (``ui/icons.py``, or one the plugin registered)
    icon: str
    #: builds the section's widget for one window: ``factory(services) -> QWidget``
    factory: Callable[[ShellServices], QWidget]
    shortcut: str | None = None
    #: called with the widget whenever the section is shown (to list what changed meanwhile)
    on_show: Callable[[QWidget], None] | None = None
    #: the module that registered it
    module: str = ""

    @property
    def builtin(self) -> bool:
        return self.module.startswith("openscilab.")


@dataclass(frozen=True)
class DocumentType:
    """A kind of documents of a plugin: the files it opens and the documents it creates."""

    #: the ``document_kind`` of its documents
    kind: str
    #: its name in *File → New*, in filters and messages ("Circuit")
    title: str
    #: the endings of its files, lower case (".circuit.yaml")
    suffixes: tuple[str, ...]
    #: ``open(path, services) -> DocumentWidget``
    open: Callable[[str, ShellServices], DocumentWidget]
    #: ``new(services) -> DocumentWidget``: a new, empty document (``None``: not in *File → New*)
    new: Callable[[ShellServices], DocumentWidget] | None = None
    icon: str = "file-plus"
    #: the entry of the open dialog ("Circuits (*.circuit.yaml)"); made from the title when empty
    filter: str = ""
    #: the folder of a project that holds its files (listed in the Project section)
    folder: str | None = None
    #: what ``open`` raises for a file it cannot read (reported, the window goes on)
    errors: tuple[type, ...] = (OSError, ValueError)
    module: str = ""
    #: a tooltip for *File → New*
    description: str = ""

    @property
    def builtin(self) -> bool:
        return self.module.startswith("openscilab.")

    def file_filter(self) -> str:
        if self.filter:
            return self.filter
        patterns = " ".join(f"*{suffix}" for suffix in self.suffixes)
        return f"{self.title}s ({patterns})"

    def matches(self, path: str) -> str | None:
        """The suffix of ``path`` this kind opens, if any."""
        lowered = path.lower()
        found = [suffix for suffix in self.suffixes if lowered.endswith(suffix)]
        return max(found, key=len) if found else None


_sections: dict[str, SectionSpec] = {}
_types: dict[str, DocumentType] = {}


def _module_of(*functions: Callable | None) -> str:
    for function in functions:
        module = getattr(function, "__module__", None)
        if module:
            return module
    return ""


def register_section(key: str, title: str, icon: str, factory: Callable[[ShellServices], QWidget],
                     shortcut: str | None = None, on_show: Callable[[QWidget], None] | None = None,
                     module: str = "") -> SectionSpec:
    """Add a section to the sidebar of windows created afterwards (see the module's description)."""
    if not _KEY.match(key or ""):
        raise ValueError(f"{key!r} is no key of a section: lower case letters, digits, '_', '-', '.'")
    if key in RESERVED:
        raise ValueError(f"the section {key!r} is one of openSciLab's own")
    if not callable(factory):
        raise TypeError("the factory of a section must be callable")
    spec = SectionSpec(key, title or key, icon, factory, shortcut, on_show,
                       module or _module_of(factory))
    existing = _sections.get(key)
    if existing is not None and existing.builtin and not spec.builtin:
        raise ValueError(f"the section {key!r} is one of openSciLab's own")
    _sections[key] = spec
    return spec


def register_document_type(kind: str, title: str, suffixes, open: Callable[[str, ShellServices], DocumentWidget],
                           new: Callable[[ShellServices], DocumentWidget] | None = None, *,
                           icon: str = "file-plus", filter: str = "", folder: str | None = None,
                           errors: tuple[type, ...] = (OSError, ValueError), description: str = "",
                           module: str = "") -> DocumentType:
    """Add a kind of documents: ``open_file`` opens its files, *File → New* creates one, the Project
    section lists the files in ``folder`` of a project."""
    if not _KEY.match(kind or ""):
        raise ValueError(f"{kind!r} is no kind of document")
    if isinstance(suffixes, str):
        suffixes = (suffixes,)
    suffixes = tuple(suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}" for suffix in suffixes)
    if not suffixes:
        raise ValueError("a kind of document needs the endings of its files")
    if not callable(open):
        raise TypeError("open must be callable")
    spec = DocumentType(kind, title or kind, suffixes, open, new, icon, filter, folder, tuple(errors),
                        module or _module_of(open, new), description)
    existing = _types.get(kind)
    if existing is not None and existing.builtin and not spec.builtin:
        raise ValueError(f"the kind of document {kind!r} is one of openSciLab's own")
    for other in _types.values():
        if other.kind != kind and set(other.suffixes) & set(suffixes):
            raise ValueError(f"the endings {sorted(set(other.suffixes) & set(suffixes))} are opened by "
                             f"{other.title} already")
    _types[kind] = spec
    if folder:
        from ..lab import project

        project.register_folder(folder, spec.module)
    return spec


def sections() -> list[SectionSpec]:
    """The sections plugins added, openSciLab's own plugins first, then in the order registered."""
    specs = list(_sections.values())
    return [spec for spec in specs if spec.builtin] + [spec for spec in specs if not spec.builtin]


def section(key: str) -> SectionSpec | None:
    return _sections.get(key)


def document_types() -> list[DocumentType]:
    specs = list(_types.values())
    return [spec for spec in specs if spec.builtin] + [spec for spec in specs if not spec.builtin]


def document_type_of_kind(kind: str) -> DocumentType | None:
    return _types.get(kind)


def document_type_for(path: str) -> DocumentType | None:
    """The kind of documents that opens ``path`` (the longest matching ending wins)."""
    best: DocumentType | None = None
    length = 0
    for spec in _types.values():
        suffix = spec.matches(path)
        if suffix and len(suffix) > length:
            best, length = spec, len(suffix)
    return best


def icon_for(path_or_kind: str) -> str | None:
    """The icon of a file or of a ``document_kind`` of a plugin."""
    spec = _types.get(path_or_kind) or document_type_for(path_or_kind)
    return spec.icon if spec is not None else None


def project_files(project) -> list[str]:
    """The files of ``project`` in the folders of the kinds of documents of plugins."""
    found: list[str] = []
    for spec in document_types():
        if spec.folder:
            try:
                found.extend(project.files(spec.folder, spec.suffixes))
            except OSError:
                continue
    return found


def of_module(module_name: str) -> tuple[list[SectionSpec], list[DocumentType]]:
    """What the module ``module_name`` (and its submodules) registered."""
    def owned(spec) -> bool:
        return spec.module == module_name or spec.module.startswith(module_name + ".")

    return [spec for spec in _sections.values() if owned(spec)], [spec for spec in _types.values() if owned(spec)]


def unregister_module(module_name: str) -> None:
    """Forget what ``module_name`` registered (``plugins.reset``, tests)."""
    from ..lab import project

    added_sections, added_types = of_module(module_name)
    for spec in added_sections:
        _sections.pop(spec.key, None)
    for spec in added_types:
        _types.pop(spec.kind, None)
        project.unregister_folders(spec.module)


def open_dialog_filter(builtin: str) -> str:
    """The filter of the open dialog: ``builtin`` (its first entry the files of openSciLab, its last
    *All files*) with the kinds of documents of plugins added."""
    entries = builtin.split(";;")
    types = document_types()
    if not types:
        return builtin
    patterns = " ".join(f"*{suffix}" for spec in types for suffix in spec.suffixes)
    first = entries[0]
    if first.endswith(")"):
        entries[0] = first[:-1] + " " + patterns + ")"
    last = entries.pop() if entries and entries[-1].lower().startswith("all files") else None
    entries.extend(spec.file_filter() for spec in types)
    if last is not None:
        entries.append(last)
    return ";;".join(entries)


def default_folder(project, spec: DocumentType) -> str | None:
    """Where a new file of ``spec`` is saved first: its folder in ``project``."""
    if project is None or not spec.folder:
        return None
    return os.path.join(project.root, spec.folder)
