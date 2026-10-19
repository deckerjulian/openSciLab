"""Sections of plugins (ui/sections.py): a plugin adds an area of its own to the window - an entry of
the activity bar with a section of the sidebar - and kinds of documents that File → New, File → Open,
the Project section, the session and Help → Plugins know."""

from __future__ import annotations

import os
import shutil

import pytest

from openscilab import plugins
from openscilab.lab import project as project_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "plugins", "notes_section.py")


@pytest.fixture(autouse=True)
def fresh_plugins():
    plugins.reset()
    yield
    plugins.reset()


@pytest.fixture
def folder():
    from openscilab.core import settings

    path = os.path.join(settings.settings_directory(), plugins.FOLDER)
    os.makedirs(path, exist_ok=True)
    shutil.copy(EXAMPLE, path)
    return path


@pytest.fixture
def notes(folder, shell):
    """A window built after the notes plugin was loaded (windows show what was registered before)."""
    from PySide6.QtWidgets import QApplication

    from openscilab.ui.shell.main_window import ShellWindow

    plugins.reset()
    plugins.load(ui=True)
    QApplication.instance()
    window = ShellWindow(restore_state=False)
    window.resize(1600, 1000)
    window.show()
    yield window
    window.force_close()


def test_the_registry_checks_what_it_gets():
    from openscilab.ui import sections

    with pytest.raises(ValueError, match="openSciLab's own"):
        sections.register_section("devices", "Devices", "devices", lambda services: None)
    with pytest.raises(ValueError, match="no key"):
        sections.register_section("Bad Key", "Bad", "book", lambda services: None)
    spec = sections.register_document_type("demo", "Demo", ("demo",), lambda path, services: None,
                                           module="tests.demo")
    try:
        assert spec.suffixes == (".demo",) and sections.document_type_for("A.DEMO") is spec
        assert spec.file_filter() == "Demos (*.demo)"
        with pytest.raises(ValueError, match="opened by Demo"):
            sections.register_document_type("other", "Other", (".demo",), lambda path, services: None,
                                            module="tests.other")
    finally:
        sections.unregister_module("tests.demo")
    assert sections.document_type_for("a.demo") is None


def test_a_plugin_adds_a_section_and_a_kind_of_documents(notes, tmp_path, monkeypatch):
    from openscilab.ui import sections

    window = notes
    assert window.activity_bar.keys() == ["project", "devices", "circuits", "notes"]
    widget = window.plugin_section("notes")
    assert widget is not None and window.sidebar.section("notes") is not None
    # View → Sidebar section lists it with the others
    view = next(action.menu() for action in window.view_menu.actions() if action.menu()
                and action.menu().title() == "Side&bar section")
    assert window.activity_bar.section_action("notes") in view.actions()
    # shown: on_show lists what changed
    called = []
    monkeypatch.setattr(type(widget), "refresh", lambda self: called.append(self))
    window.activity_bar.select("notes")
    assert window.sidebar.current_key == "notes" and called == [widget]
    # File → New → Note
    assert "note" in window.plugin_new_actions
    window.plugin_new_actions["note"].trigger()
    document = window.active_document()
    assert document.document_kind == "note" and document.title.startswith("Untitled note")
    # File → Open: the dialog offers them, open_file opens them in their document
    assert "Notes (*.note.txt)" in sections.open_dialog_filter("All (*.x);;All files (*)").split(";;")
    path = tmp_path / "todo.note.txt"
    path.write_text("measure the ripple", encoding="utf-8")
    opened = window.open_file(str(path))
    assert opened.document_kind == "note" and opened.editor.toPlainText() == "measure the ripple"
    assert window.open_file(str(path)) is opened  # (shown again, not opened twice)
    icons = [window.project_section.documents.item(row).icon().cacheKey()
             for row in range(window.project_section.documents.count())]
    assert len(icons) == 2


def test_the_project_has_a_folder_for_them_and_lists_them(notes, tmp_path):
    from openscilab.lab.project import Project
    from openscilab.lab.templates import copy_project

    assert "notes" in project_module.folders()
    root = tmp_path / "lab"
    Project.create(str(root), "Lab")
    assert (root / "notes").is_dir()
    (root / "notes" / "idea.note.txt").write_text("x", encoding="utf-8")
    window = notes
    window.project_section.set_project(Project.open(str(root)))
    listed = [window.project_section.files.item(row).text() for row in range(window.project_section.files.count())]
    assert os.path.join("notes", "idea.note.txt") in listed
    source = tmp_path / "template"
    source.mkdir()
    (source / "project.yaml").write_text("project: T\n", encoding="utf-8")
    copy_project(str(source), str(tmp_path / "copy"))
    assert (tmp_path / "copy" / "notes").is_dir()


def test_the_session_reopens_them(notes, tmp_path):
    from openscilab.ui.shell import session

    path = tmp_path / "a.note.txt"
    path.write_text("a", encoding="utf-8")
    notes.open_file(str(path))
    state = session.session_state(notes)
    notes.close_all_documents(force=True)
    session.restore(notes, state, reconnect_devices=False)
    assert any(document.path == str(path) for document in notes.documents())


def test_help_plugins_lists_sections_and_kinds_of_documents(notes, monkeypatch):
    from openscilab.ui import messages

    shown = []
    monkeypatch.setattr(messages, "info", lambda parent, title, text, details=None: shown.append(details))
    notes.show_plugins()
    assert "notes: Notes (notes_section)" in shown[0]
    assert "note: Note (.note.txt; notes_section)" in shown[0]


def test_reset_forgets_them(folder):
    from openscilab.ui import sections

    plugins.load(ui=True)
    assert sections.section("notes") is not None and "notes" in project_module.folders()
    plugins.reset()
    assert sections.section("notes") is None and sections.document_type_of_kind("note") is None
    assert "notes" not in project_module.folders()


def test_a_section_that_cannot_be_built_does_not_stop_the_window(folder, shell):
    from PySide6.QtWidgets import QApplication

    from openscilab.ui.shell.main_window import ShellWindow

    with open(os.path.join(folder, "broken_section.py"), "w") as handle:
        handle.write(
            "def build(services):\n"
            "    raise RuntimeError('no widget today')\n"
            "def setup_ui():\n"
            "    from openscilab.ui.sections import register_section\n"
            "    register_section('broken', 'Broken', 'book', build)\n")
    plugins.reset()
    plugins.load(ui=True)
    QApplication.instance()
    window = ShellWindow(restore_state=False)
    try:
        assert "broken" not in window.activity_bar.keys() and "notes" in window.activity_bar.keys()
        failed = [plugin for plugin in plugins.problems() if plugin.name == "broken_section"]
        assert failed and "no widget today" in failed[0].error
    finally:
        window.force_close()


def test_a_plugin_icon_is_drawn(shell):
    from openscilab.ui import icons

    icons.register_icon("test-square", "<rect x='4' y='4' width='16' height='16'/>")
    assert not icons.icon("test-square").isNull() and icons.known("test-square") == "test-square"
    with pytest.raises(ValueError, match="openSciLab's own"):
        icons.register_icon("play", "<rect x='4' y='4' width='16' height='16'/>")


def test_the_services_tell_about_documents(shell):
    seen = []
    shell.services.documents_changed.connect(lambda: seen.append("documents"))
    shell.services.active_document_changed.connect(lambda document: seen.append(("active", document)))
    document = shell.new_document("circuit")
    assert "documents" in seen and any(item[0] == "active" and item[1] is document for item in seen if item != "documents")
    assert shell.services.active_document() is document
    folder = shell.services.data_folder(document)
    assert folder is None or isinstance(folder, str)
