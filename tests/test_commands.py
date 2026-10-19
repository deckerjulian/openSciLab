"""Every key does one thing where it acts (docs/design.md): the shell's shortcuts are unique, a
document's shortcuts are unique in it and none takes a key of the shell (Qt fires neither of two
shortcuts of one key that both apply). Run, Pause, Step and Stop are the shell's."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu


def _keys(actions) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for action in actions:
        if isinstance(action.parent(), QMenu) and action.parent().title() == "":
            continue  # (an entry of a context menu: its key acts only while the menu is open)
        for sequence in action.shortcuts():
            if not sequence.isEmpty():
                found.setdefault(sequence.toString(), []).append(action.text().replace("&", ""))
    return found


def _window_actions(window) -> list[QAction]:
    return [action for action in window.findChildren(QAction)
            if action.shortcutContext() in (Qt.WindowShortcut, Qt.ApplicationShortcut)
            and not action.shortcuts() == [] and action.isEnabled() is not None]


def test_the_shortcuts_of_the_shell_are_unique(shell):
    shell_keys = _keys(action for action in _window_actions(shell) if action.parent() is shell
                       or (action.parent() is not None and action.parent().parent() is shell))
    doubles = {key: names for key, names in shell_keys.items() if len(set(names)) > 1}
    assert not doubles, doubles
    # one way to run (rule 3): the keys of running are the shell's
    for key in ("F5", "Shift+F5", "F10", "F6"):
        assert key in shell_keys, key


def test_documents_do_not_take_the_keys_of_the_shell(shell, make_dataview):
    from openscilab.driver.simulated import open_simulated

    shell_keys = set(_keys(action for action in shell.actions() + shell.findChildren(QAction)
                           if action.shortcutContext() == Qt.WindowShortcut))
    instrument = open_simulated("uno")
    shell.hub.add(instrument)
    documents = [shell.new_flow(), shell.new_data_view(), shell.new_panel(), shell.new_waveform(),
                 shell.open_device_card(instrument)]
    for document in documents:
        own = [action for action in document.findChildren(QAction)
               if action.shortcutContext() == Qt.WidgetWithChildrenShortcut]
        keys = _keys(own)
        doubles = {key: names for key, names in keys.items() if len(set(names)) > 1}
        assert not doubles, (document.document_kind, doubles)
        taken = sorted(set(keys) & shell_keys)
        assert not taken, (document.document_kind, taken)
