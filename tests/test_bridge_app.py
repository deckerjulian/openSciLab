"""Installing the bridge app on a DHO900 (driver/rigoldho/bridge_app.py) and the offer on its device card."""

from __future__ import annotations

import subprocess

import pytest
from PySide6.QtWidgets import QApplication

from openscilab.driver.rigoldho import bridge_app


class FakeAdb:
    """adb as far as the installer uses it: answers by the subcommand, records the calls."""

    def __init__(self, connect="connected to 10.0.0.5:55555", install="Performing Streamed Install\nSuccess",
                 install_code=0) -> None:
        self.calls: list[list[str]] = []
        self.answers = {"connect": (0, connect), "install": (install_code, install),
                        "uninstall": (0, "Success"), "shell": (0, "Starting: Intent { cmp=org.openscilab.bridge }")}

    def __call__(self, arguments: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(arguments[1:])
        command = next(item for item in arguments[1:] if item in self.answers)
        code, text = self.answers[command]
        return subprocess.CompletedProcess(arguments, code, stdout=text, stderr="")


@pytest.fixture
def adb_found(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_app, "find_tool", lambda name: ["adb"])
    apk = tmp_path / "openSciLab-bridge.apk"
    apk.write_bytes(b"PK")
    return str(apk)


def test_install_connects_installs_starts_and_waits(adb_found):
    adb = FakeAdb()
    answers = iter([None, "0.1.0"])  # (the bridge needs a moment after its start)
    version = bridge_app.install("10.0.0.5", adb_found, runner=adb, wait=lambda host: next(answers), start_timeout=5)
    assert version == "0.1.0"
    target = ["-s", "10.0.0.5:55555"]
    assert adb.calls == [["connect", "10.0.0.5:55555"],
                         [*target, "install", "-r", adb_found],
                         [*target, "shell", "am", "start", "-n", "org.openscilab.bridge/.MainActivity",
                          "--ez", "start", "true"]]


def test_an_app_signed_with_another_key_is_only_replaced_when_asked(adb_found):
    failing = FakeAdb(install="Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]", install_code=1)
    with pytest.raises(bridge_app.SignatureMismatch):
        bridge_app.install("10.0.0.5", adb_found, runner=failing, wait=lambda host: "0.1.0")
    assert not any("uninstall" in call for call in failing.calls)
    adb = FakeAdb()
    assert bridge_app.install("10.0.0.5", adb_found, replace=True, runner=adb, wait=lambda host: "0.1.0") == "0.1.0"
    assert ["-s", "10.0.0.5:55555", "uninstall", "org.openscilab.bridge"] in adb.calls


def test_errors_say_what_is_missing(adb_found, monkeypatch):
    with pytest.raises(bridge_app.BridgeInstallError, match="could not reach"):
        bridge_app.install("10.0.0.5", adb_found, runner=FakeAdb(connect="failed to connect to 10.0.0.5:55555"))
    with pytest.raises(bridge_app.BridgeInstallError, match="does not answer"):
        bridge_app.install("10.0.0.5", adb_found, runner=FakeAdb(), wait=lambda host: None, start_timeout=0)
    monkeypatch.setattr(bridge_app, "find_tool", lambda name: None)
    with pytest.raises(bridge_app.AdbMissing):
        bridge_app.install("10.0.0.5", adb_found, runner=FakeAdb())
    monkeypatch.setattr(bridge_app, "find_apk", lambda: None)
    with pytest.raises(bridge_app.BridgeInstallError, match="not built"):
        bridge_app.install("10.0.0.5")


def test_the_version_of_a_running_bridge():
    from openscilab.driver.simulated.scpi_shell import ScpiShell

    shell = ScpiShell(bridge=True)
    try:
        assert bridge_app.bridge_version("127.0.0.1", shell.port) == "0.1.0-sim"
    finally:
        shell.close()
    plain = ScpiShell(bridge=False)
    try:
        assert bridge_app.bridge_version("127.0.0.1", plain.port, timeout=1.0) is None
    finally:
        plain.close()


# ------------------------------------------------------------ device card
@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def card_for(mode: str, monkeypatch):
    from openscilab.core.hub import Hub
    from openscilab.core.instrument import Instrument
    from openscilab.driver.rigoldho.driver import RigolDhoDriver
    from openscilab.driver.simulated.scpi_shell import open_shell
    from openscilab.ui.documents.device import DeviceDocument

    # the simulated DHO stands in for a real one: the offer is made for those only
    monkeypatch.setattr(RigolDhoDriver, "bridge_app_installable", property(lambda self: True))
    driver = open_shell(mode)
    instrument = Instrument.from_driver(driver, name="DHO924S")
    hub = Hub()
    hub.add(instrument)
    card = DeviceDocument(instrument, hub)
    card._update_capture()
    return card, driver


def test_the_card_offers_the_bridge_app_and_installs_it(application, monkeypatch):
    from openscilab.ui import messages

    card, driver = card_for("direct", monkeypatch)
    try:
        assert not card.bridge_banner.isHidden()
        assert card.action_bridge.isVisible() and card.action_bridge.text() == "Install bridge app..."
        installed = []
        monkeypatch.setattr(bridge_app, "find_apk", lambda: "/tmp/openSciLab-bridge.apk")
        monkeypatch.setattr(bridge_app, "install", lambda host, apk, replace=False: installed.append(
            (host, apk, replace)) or "0.1.0")
        monkeypatch.setattr(messages, "confirm", lambda *args, **kwargs: True)
        reconnected = []
        card.reconnect_requested.connect(reconnected.append)
        assert card.install_bridge_app()
        assert installed == [("127.0.0.1", "/tmp/openSciLab-bridge.apk", False)]
        assert reconnected == [card.instrument] and card.bridge_banner.isHidden()
        # closed, the offer does not come back while the card is open
        card.bridge_banner.setVisible(True)
        card.bridge_banner.close_button.click()
        card._update_capture()
        assert card.bridge_banner.isHidden()
    finally:
        card.shutdown()
        driver.dispose()


def test_with_the_bridge_running_it_can_be_updated_and_is_not_offered(application, monkeypatch):
    card, driver = card_for("bridge", monkeypatch)
    try:
        assert card.bridge_banner.isHidden()
        assert card.action_bridge.text() == "Update bridge app..."
    finally:
        card.shutdown()
        driver.dispose()


def test_a_simulated_oscilloscope_gets_no_offer():
    from openscilab.driver.simulated.scpi_shell import open_shell

    driver = open_shell("direct")
    try:
        assert not driver.bridge_app_installable
    finally:
        driver.dispose()
