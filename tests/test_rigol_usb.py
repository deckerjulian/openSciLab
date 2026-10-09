"""The Rigol DHO900 over USB: the USBTMC framing (driver/usbtmc.py), the simulated DHO on its USB
port (rigol-sim:usb), choosing the oscilloscope (rigol:usb[:<serial>|:<bus>:<address>])."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from openscilab.driver import usbtmc
from openscilab.driver.base import DeviceConnectionError
from openscilab.driver.rigoldho import scpi
from openscilab.driver.rigoldho.driver import open_usb
from openscilab.driver.simulated.scpi_shell import ScpiShell, ShellTmcPort, open_shell

from test_rigoldho import make_session, run


# ------------------------------------------------------------------ framing
def test_the_transfers_follow_usbtmc():
    out = usbtmc.message_out(7, b"*IDN?\n")
    assert out[:12] == bytes([1, 7, 0xF8, 0]) + struct.pack("<I", 6) + bytes([1, 0, 0, 0])
    assert out[12:18] == b"*IDN?\n" and len(out) % 4 == 0  # (padded to whole words)
    assert usbtmc.request_in(9, 4096) == bytes([2, 9, 0xF6, 0]) + struct.pack("<I", 4096) + bytes(4)
    answer = usbtmc.header(usbtmc.DEV_DEP_MSG_IN, 9, 3, usbtmc.EOM) + b"ok\n\x00"
    assert usbtmc.parse_in(answer, 9) == (b"ok\n", True)
    with pytest.raises(usbtmc.UsbTmcError, match="does not belong"):
        usbtmc.parse_in(answer, 10)
    with pytest.raises(usbtmc.UsbTmcError, match="shorter"):
        usbtmc.parse_in(answer[:13], 9)

    class Recorder(usbtmc.UsbTmcPort):
        def __init__(self):
            self.sent = []

        def write(self, data, timeout):
            self.sent.append(data)

    link = usbtmc.UsbTmcLink(Recorder())
    tags = []
    for _ in range(300):
        link.write(b"x", 1.0)
        tags.append(link.port.sent[-1][1])
    assert 0 not in tags and tags[:2] == [1, 2] and tags[255] == 1  # (1 ... 255, never 0)
    link.write(b"y" * (usbtmc.WRITE_SIZE + 5), 1.0)  # a long command: pieces, EOM on the last only
    assert [piece[8] & usbtmc.EOM for piece in link.port.sent[-2:]] == [0, 1]


# ----------------------------------------------------------- the simulation
@pytest.fixture
def usb():
    driver = open_shell("usb")
    yield driver
    driver.dispose()


def test_a_capture_over_usb(usb):
    assert usb.over_usb and not usb.is_network and usb.address == "rigol-sim:usb"
    assert dict(usb.describe()[0][1])["Connection"].startswith("USB (USBTMC)")
    session = make_session(analog=(0,))
    result = run(usb, session)
    assert result.success, result.error
    assert len(session.capture_channels[0].samples) == 10_000
    assert np.all(np.diff(session.capture_channels[0].samples[:10].astype(int)) != 0)  # D0 toggles every sample
    volts = session.analog_channels[0].raw * session.analog_channels[0].scale + session.analog_channels[0].offset
    assert volts.max() - volts.min() > 0.5


def test_answers_split_by_libusb_are_put_together(usb):
    usb.usb_port.chunk = 5  # (every bulk read hands over 5 bytes at most)
    assert usb.connection.query("*IDN?").startswith("RIGOL TECHNOLOGIES,DHO924S")
    session = make_session(channels=(0,), post=2000, pre=0)
    assert run(usb, session).success


def test_a_late_answer_is_cleared_and_the_next_one_is_in_step(usb):
    port = usb.usb_port
    port.delay = 0.5
    with pytest.raises(scpi.ScpiTimeout):
        usb.connection.query(":ACQuire:SRATe?", timeout=0.1)
    assert port.cleared == 1  # (INITIATE_CLEAR: the instrument dropped the late answer)
    port.delay = 0.0
    assert usb.connection.query("*IDN?").startswith("RIGOL TECHNOLOGIES")
    assert usb.connection.query(":TRIGger:STATus?") in ("STOP", "RUN", "WAIT", "TD", "AUTO")


# ------------------------------------------------------- choosing the scope
def test_the_oscilloscope_is_chosen_by_serial_number_or_bus_position():
    shells = []

    def link(info):
        shell = ScpiShell()
        shells.append(shell)
        return usbtmc.UsbTmcLink(ShellTmcPort(shell), info.label)

    found = [usbtmc.UsbTmcInfo(1, 4, 0x1AB1, 0x044C, "Rigol Technologies", "DHO924S", "DHO9A0001"),
             usbtmc.UsbTmcInfo(2, 7, 0x1AB1, 0x044C, "Rigol Technologies", "DHO914", "")]
    try:
        first = open_usb("", find=lambda: found, open_link=link)
        assert first.address == "rigol:usb:DHO9A0001" and first.host.startswith("Rigol Technologies DHO924S")
        first.dispose()
        second = open_usb("2:7", find=lambda: found, open_link=link)
        assert second.address == "rigol:usb:2:7"  # (no serial number: its bus position)
        second.dispose()
        with pytest.raises(DeviceConnectionError, match="No Rigol oscilloscope found on USB with DHO9X"):
            open_usb("DHO9X", find=lambda: found, open_link=link)
        with pytest.raises(DeviceConnectionError, match="No Rigol oscilloscope found on USB"):
            open_usb("", find=lambda: [], open_link=link)

        def denied(info):
            raise usbtmc.UsbTmcError("Access to the instrument was denied")

        with pytest.raises(DeviceConnectionError, match="denied"):
            open_usb("", find=lambda: found, open_link=denied)
    finally:
        for shell in shells:
            shell.close()


def test_the_address_rigol_usb_opens_the_oscilloscope_on_usb(monkeypatch):
    from openscilab.driver import kinds
    from openscilab.driver.discovery import open_device
    from openscilab.driver.rigoldho import driver as module

    shell = ScpiShell()
    found = [usbtmc.UsbTmcInfo(1, 4, 0x1AB1, 0x044C, "Rigol Technologies", "DHO924S", "DHO9A0001")]
    monkeypatch.setattr(module, "usb_instruments", lambda: found)
    monkeypatch.setattr(usbtmc, "open_link", lambda info: usbtmc.UsbTmcLink(ShellTmcPort(shell), info.label))
    try:
        assert ("usb:DHO9A0001", found[0].label) in kinds.find("rigol").detect()
        driver = open_device("rigol:usb:DHO9A0001")
        assert driver.over_usb and driver.address == "rigol:usb:DHO9A0001"
        driver.dispose()
    finally:
        shell.close()
