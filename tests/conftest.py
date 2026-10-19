"""Shared test fixtures."""

from __future__ import annotations

import os
import sys

import pytest

from openscilab import qt_plugins

# Before any test creates a QApplication: iCloud Drive hides the plugin files of
# a virtual environment inside ~/Documents, which Qt would not load.
qt_plugins.ensure_loadable_plugins()

FIXTURE_DECODERS = os.path.join(os.path.dirname(__file__), "fixtures", "decoders")


def pytest_configure(config):
    """The typeface (fetched like the application does, core/typeface.py) once for every run, in pytest's
    cache: the settings of each test are a folder of their own. Without a connection it stays missing."""
    if not os.environ.get("OPENSCILAB_FONTS_DIR"):
        os.environ["OPENSCILAB_FONTS_DIR"] = str(config.cache.mkdir("fonts"))
    from openscilab.core import typeface

    typeface.download()


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep the tests away from the real settings directory."""
    from openscilab.core import preferences

    monkeypatch.setenv("OPENSCILAB_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.delenv("OPENSCILAB_PLUGINS", raising=False)  # (the plugins of the developer)
    # fake drivers live in this process: devices are opened here (tests/test_device_process.py opens
    # them in device processes on purpose)
    monkeypatch.setitem(preferences.DEFAULTS, "devices.process", False)
    monkeypatch.setitem(preferences.DEFAULTS, "devices.simulator_process", False)
    preferences.reload()
    yield
    preferences.reload()


@pytest.fixture(autouse=True)
def no_usb_devices(monkeypatch):
    """The tests never touch real devices: no DSLogic and no USBTMC instrument through libusb, no
    oscilloscope whose bridge app announces itself on the network."""
    from openscilab.driver import usbtmc
    from openscilab.driver.dslogic import usb
    from openscilab.driver.rigoldho import beacon

    monkeypatch.setattr(usb, "list_devices", lambda: [])
    monkeypatch.setattr(usbtmc, "list_instruments", lambda vendor=None: [])
    monkeypatch.setattr(beacon, "seen", lambda: [])


@pytest.fixture(autouse=True)
def no_unanswered_message_boxes(monkeypatch):
    """A message box nobody answers would block the test run forever; fail instead.

    Tests that expect a question or a message patch ``openscilab.ui.messages``.
    """
    from openscilab.ui import messages

    def unexpected(box):
        raise AssertionError(f"Unexpected message box '{box.windowTitle()}': {box.text()}")

    monkeypatch.setattr(messages, "_exec", unexpected)

    from openscilab.ui.dialogs import board_warning_dialog

    def unexpected_warning(dialog):
        raise AssertionError(f"Unexpected board warning: {dialog.finding.message}")

    monkeypatch.setattr(board_warning_dialog, "_exec", unexpected_warning)


@pytest.fixture(autouse=True)
def empty_clipboard():
    """Data a test left on the clipboard crashes Qt when the application ends on the offscreen
    platform (the run ends with a segmentation fault after every test passed): emptied after each test."""
    yield
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is not None:
        application.clipboard().clear()


@pytest.fixture
def decoder_registry():
    from openscilab.sigrok.engine import DecoderRegistry

    registry = DecoderRegistry([FIXTURE_DECODERS])
    registry.load(force=True)
    yield registry
    sys.modules.pop("testdec", None)
    sys.modules.pop("testdec.pd", None)


@pytest.fixture
def shell():
    """The openSciLab shell, shown on the offscreen platform; closed without questions afterwards."""
    from PySide6.QtWidgets import QApplication

    from openscilab.ui.shell.main_window import ShellWindow

    application = QApplication.instance() or QApplication([])
    from openscilab.ui.theme import apply_font

    apply_font(application)  # (as the application does)
    window = ShellWindow()
    window.resize(1600, 1000)
    window.show()
    yield window
    window.force_close()


@pytest.fixture
def make_dataview(shell):
    """Creates analyzer documents in the shell: ``make_dataview()`` (as the template does)."""

    def make(**kwargs):
        from openscilab.ui.documents.dataview import DataView

        kwargs.setdefault("provider", shell.decoders())
        kwargs.setdefault("hub", shell.hub)
        document = DataView(**kwargs)
        shell.add_document(document)
        return document

    return make
