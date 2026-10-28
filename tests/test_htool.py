# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The HTOOL HT008 driver against a fake serial port that answers as the HT008 does (firmware
V6.0.1): the first thing after power on decides between frames and SCPI."""

from __future__ import annotations

import pytest

from openscilab.core import waveform as waves
from openscilab.core.instrument import GeneratorFacet, InstrumentError, SettingsFacet
from openscilab.driver import kinds, ports
from openscilab.driver.base import DeviceConnectionError
from openscilab.driver.htool import protocol
from openscilab.driver.htool.instrument import detect, open_ht008

IDENTITY = b"HTOOL,HT008,37373439,V6.0.1\r\n"


class FakeHt008:
    """Answers as the HT008: ``fresh`` (just switched on), ``frames`` or ``scpi``."""

    def __init__(self, mode: str = "fresh") -> None:
        self.mode = mode
        self.sent: list[bytes] = []
        self.output: tuple[float, int] | None = None
        self._answer = b""
        self.closed = False

    def __call__(self, port, baud, **_options):
        assert baud == 115200
        return self

    def reset_input_buffer(self) -> None:
        self._answer = b""

    def write(self, data: bytes) -> int:
        self.sent.append(data)
        is_frame = len(data) == 9 and data.startswith(b"\x55\x55")
        if self.mode == "fresh":
            self.mode = "frames" if is_frame else "scpi"
            if is_frame:
                self.output = protocol.parse(data)
                return len(data)
        if self.mode == "frames":
            self.output = protocol.parse(data)
            self._answer = data + b"OK"
        elif data == b"*IDN?\n":
            self._answer = IDENTITY
        return len(data)

    @property
    def in_waiting(self) -> int:
        return len(self._answer)

    def read(self, size: int = 1) -> bytes:
        data, self._answer = self._answer[:size], self._answer[size:]
        return data

    def close(self) -> None:
        self.closed = True


def test_frames_as_documented():
    # the examples of the LTDZ's protocol, with the HT008's levels (0: -4 dBm ... 3: +5 dBm)
    assert protocol.frame(100.123e6, 3) == bytes.fromhex("5555 0064 007b 03 0d0a")
    assert protocol.frame(2000.456e6, 0) == bytes.fromhex("5555 07d0 01c8 00 0d0a")
    assert protocol.parse(protocol.frame(433.92e6, 1)) == (433.92e6, 1)
    assert protocol.round_frequency(433.9204e6) == 433_920_000
    with pytest.raises(ValueError):
        protocol.frame(20e6, 0)
    with pytest.raises(ValueError):
        protocol.frame(100e6, 4)
    assert protocol.volts_to_dbm(protocol.dbm_to_volts(5.0)) == pytest.approx(5.0)
    assert protocol.dbm_to_volts(0.0) == pytest.approx(0.316, abs=1e-3)  # 1 mW into 50 ohms
    assert protocol.nearest_level(1.2) == 2


@pytest.mark.parametrize("mode", ["fresh", "frames"])
def test_opens_an_ht008_just_switched_on_or_already_listening(mode):
    device = FakeHt008(mode)
    instrument = open_ht008("/dev/cu.wchusbserial210", opener=device)
    assert instrument.uri == "ht008:/dev/cu.wchusbserial210" and instrument.capture is None
    assert device.output == (3e9, 0)  # its power on state, nothing else
    assert all(data.startswith(b"\x55\x55") for data in device.sent)  # no SCPI: it would decide
    settings = instrument.facet(SettingsFacet)
    assert settings.values()["frequency"] == 3e9 and settings.values()["power"] == "-4"
    instrument.close()
    assert device.closed


def test_an_ht008_spoken_to_with_scpi_must_be_switched_off_and_on():
    device = FakeHt008("scpi")
    with pytest.raises(DeviceConnectionError, match="Switch it off and on"):
        open_ht008("/dev/cu.wchusbserial210", opener=device)
    assert device.closed


def test_another_device_on_the_port_is_refused():
    class Silent(FakeHt008):
        def write(self, data):
            self.sent.append(data)
            return len(data)

    with pytest.raises(DeviceConnectionError, match="No HTOOL HT008"):
        open_ht008("/dev/cu.usbserial-1", opener=Silent())


def test_generator_plays_sines_at_its_power_levels():
    device = FakeHt008()
    instrument = open_ht008("/dev/x", opener=device)
    generator = instrument.facet(GeneratorFacet)
    assert [output.name for output in generator.outputs()] == ["CH1"]
    generator.start("CH1", waves.Waveform(frequency=433.92e6, amplitude=protocol.dbm_to_volts(5.0)))
    assert device.output == (433.92e6, 3) and generator.running("CH1")
    generator.start("CH1", waves.Waveform(frequency=1e9, amplitude=0.28))  # -1.0 dBm
    assert device.output == (1e9, 1)
    generator.stop("CH1")
    assert not generator.running("CH1") and device.output == (1e9, 1)  # it has no off
    for waveform, problem in [(waves.Waveform(kind=waves.SQUARE, frequency=100e6, amplitude=0.4), "sine"),
                              (waves.Waveform(frequency=1e6, amplitude=0.4), "below"),
                              (waves.Waveform(frequency=7e9, amplitude=0.4), "above"),
                              (waves.Waveform(frequency=100e6, amplitude=0.05), "dBm"),
                              (waves.Waveform(frequency=100e6, amplitude=1.0), "outside"),
                              (waves.Waveform(frequency=100e6, amplitude=0.4, offset=0.1), "offset"),
                              (waves.Waveform(frequency=100e6, amplitude=0.4, modulation=waves.SWEEP), "sweep")]:
        with pytest.raises(InstrumentError, match=problem):
            generator.start("CH1", waveform)
    assert device.output == (1e9, 1)


def test_settings_set_frequency_and_power():
    device = FakeHt008()
    settings = open_ht008("/dev/x", opener=device).facet(SettingsFacet)
    names = {setting["name"]: setting for setting in settings.settings()}
    assert names["frequency"]["unit"] == "Hz" and names["frequency"]["digits"] == 10
    assert [value for value, _label in names["power"]["choices"]] == ["-4", "-1", "2", "5"]
    assert settings.set("frequency", 2400.0004e6) == 2400e6
    assert settings.set("power", "2") == "2"
    assert device.output == (2400e6, 2)
    assert settings.values()["frequency"] == 2400e6 and settings.values()["power"] == "2"
    with pytest.raises(InstrumentError):
        settings.set("frequency", 10e6)
    with pytest.raises(InstrumentError):
        settings.set("power", "3")


def test_an_unacknowledged_frame_is_an_error():
    device = FakeHt008()
    settings = open_ht008("/dev/x", opener=device).facet(SettingsFacet)
    device.mode = "off"  # (switched off: nothing answers)
    with pytest.raises(InstrumentError, match="did not acknowledge"):
        settings.set("frequency", 100e6)
    assert settings.values()["frequency"] == 3e9


def test_detected_by_its_usb_chip_once_per_device(monkeypatch):
    class Port:
        def __init__(self, device, vid, pid, location):
            self.device, self.vid, self.pid, self.location = device, vid, pid, location
            self.product, self.serial_number = "USB Serial", None

    monkeypatch.setattr(ports, "comports", lambda: [Port("/dev/cu.wchusbserial210", 0x1A86, 0x7523, "2-1"),
                                                    Port("/dev/cu.usbserial-210", 0x1A86, 0x7523, "2-1"),
                                                    Port("/dev/cu.usbmodem1", 0x2341, 0x0042, "1-1")])
    assert detect() == [("/dev/cu.usbserial-210", "HTOOL HT008 on /dev/cu.usbserial-210 (CH340)")]


def test_kind_is_registered():
    kind = kinds.find("ht008")
    assert kind is not None and kind.builtin and kind.instrument is not None and kind.open is None
    assert kind.simulation is None
