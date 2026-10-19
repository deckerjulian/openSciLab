"""Emulated microcontrollers (circuits/mcu): firmware files, the AVR emulator (skipped without
libopenscilab_avr), boards and their pins, a microcontroller in a running circuit, and the vendor
folder the tools are found in or installed into."""

from __future__ import annotations

import os
import subprocess
import time

import numpy as np
import pytest

from openscilab.circuits import CircuitError, engines, spice
from openscilab.circuits.catalog import Catalog
from openscilab.circuits.mcu import avr
from openscilab.circuits.mcu.boards import BOARDS, pin_of
from openscilab.circuits.mcu.firmware import read_elf, read_firmware, read_hex
from openscilab.circuits.nets import placed_pins
from openscilab.core import vendor

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "avr")
UNO_HEX = os.path.join(FIXTURES, "blink_serial-atmega328p.hex")
MEGA_ELF = os.path.join(FIXTURES, "blink_serial-atmega2560.elf")

needs_avr = pytest.mark.skipif(not avr.available(), reason="the AVR emulator is not installed")


# ---------------------------------------------------------------- firmware
def test_intel_hex_and_elf_give_the_same_flash():
    image, start = read_firmware(UNO_HEX)
    assert start == 0 and image[:4] == bytes.fromhex("0C943400")
    with open(MEGA_ELF, "rb") as handle:
        elf_image, elf_start = read_elf(handle.read())
    hex_image, hex_start = read_hex(open(MEGA_ELF.replace(".elf", ".hex")).read())
    assert (elf_start, elf_image) == (hex_start, hex_image)


def test_a_damaged_hex_file_says_why():
    with pytest.raises(CircuitError, match="checksum"):
        read_hex(":100000000C9434000C9449000C9449000C94490062\n")
    with pytest.raises(CircuitError, match="no record"):
        read_hex("hello\n")


# ------------------------------------------------------------------ boards
def test_the_pins_of_the_boards():
    uno, mega, chip = BOARDS["mcu.uno"], BOARDS["mcu.mega2560"], BOARDS["atmega328p"]
    assert pin_of(uno, "D13") == pin_of(uno, "D13/SCK") and (pin_of(uno, "D13").port, pin_of(uno, "D13").bit) == ("B", 5)
    assert pin_of(uno, "A0").adc == 0 and pin_of(BOARDS["mcu.nano"], "A7").analog_only
    assert (pin_of(mega, "D13").port, pin_of(mega, "D13").bit) == ("B", 7)
    assert (pin_of(mega, "D2").port, pin_of(mega, "D2").bit) == ("E", 4)
    assert pin_of(mega, "A15").adc == 15
    assert (pin_of(chip, "PB5").port, pin_of(chip, "PB5").bit) == ("B", 5) and pin_of(chip, "PC3").adc == 3


# ---------------------------------------------------------------- emulator
@needs_avr
def test_the_uno_runs_its_firmware():
    mcu = avr.AvrMcu("atmega328p", 16e6, 5.0)
    mcu.load_firmware(UNO_HEX)
    mcu.run_until(0.01)
    events = mcu.events()
    assert bytes(e.value for e in events if e.kind == avr.EVENT_UART).startswith(b"hello\n")
    assert any(e.kind == avr.EVENT_PORT and e.port == "B" and e.value & (1 << 5) for e in events)  # (D13 high)
    assert any(e.kind == avr.EVENT_DIRECTION and e.port == "B" and e.value & (1 << 5) for e in events)
    mcu.set_analog(0, 2.5)
    mcu.uart_write(b"abc")
    mcu.set_input("D", 2, False)
    mcu.run_until(0.08)
    said = bytes(e.value for e in mcu.events() if e.kind == avr.EVENT_UART)
    assert b"A0=511" in said and b"D2=0" in said
    assert [c for c in said.decode() if c in "ABC"][:3] == ["A", "B", "C"]
    mcu.close()


@needs_avr
def test_the_mega_runs_an_elf_file():
    mcu = avr.AvrMcu("atmega2560")
    mcu.load_firmware(MEGA_ELF)
    mcu.run_until(0.01)
    assert bytes(e.value for e in mcu.events() if e.kind == avr.EVENT_UART).startswith(b"hello")
    mcu.close()


@needs_avr
def test_an_unknown_chip_is_refused():
    with pytest.raises(CircuitError, match="does not know"):
        avr.AvrMcu("atmega9999")


# ------------------------------------------------------- in a circuit
def blink_circuit():
    from openscilab.circuits.model import Schematic
    from openscilab.circuits.nets import placed_pins

    catalog = Catalog(kicad=[], models=[], user=[], use_cache=False)
    s = Schematic("Blink")
    s.add_component("mcu.uno", 0, 0, "U", "U1", {"firmware": UNO_HEX})
    pins = {pin.name: point for pin, point in placed_pins(s.components["U1"], catalog.spec("mcu.uno"))}
    x, y = pins["D13"]
    s.add_component("resistor", x - 60, y, "R", "R1", {"value": "330"})
    s.add_wire([(x - 30, y), (x, y)])
    s.add_component("led", x - 150, y, "D", "D1", mirror=True)  # (anode right, towards the resistor)
    s.add_wire([(x - 120, y), (x - 90, y)])
    s.add_component("ground", x - 180, y + 40, "GND", "GND1")
    s.add_wire([(x - 180, y), (x - 180, y + 40)])
    s.add_component("probe.v", x - 30, y, "P", "P1", {"name": "D13"})
    return s, catalog


def test_a_microcontroller_in_the_netlist():
    s, catalog = blink_circuit()
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert not circuit.errors and not [p for p in circuit.problems if p.item == "U1"]
    assert len(circuit.mcus) == 1 and [pin.name for pin in circuit.mcus[0].pins] == ["D13"]
    assert any(line.startswith("vu1_d13_l ") and line.endswith("external") for line in circuit.lines)
    s.components["U1"].params["firmware"] = "nowhere.hex"
    assert any("not there" in p.text for p in spice.emit(s, catalog.spec, catalog.models).errors)


@needs_avr
@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_a_microcontroller_runs_with_the_circuit():
    from openscilab.circuits.engines import live

    s, catalog = blink_circuit()
    circuit = spice.emit(s, catalog.spec, catalog.models)
    simulation = live.LiveSimulation(circuit, realtime=False, mcus=circuit.mcus)
    simulation.start()
    serial = b""
    currents = []
    try:
        end = time.monotonic() + 15
        while time.monotonic() < end and (simulation.clock() < 0.05 or b"hello" not in serial
                                           or max(currents, default=0) <= 5e-3):
            time.sleep(0.002)  # (the LED blinks every millisecond: look often)
            for events in simulation.take_mcu_events():
                serial += events.get("U1", {}).get("serial", b"")
            current = simulation.history.latest("@d1[id]")
            if current is not None:
                currents.append(current)
        times, values = simulation.course("v(D13)")
    finally:
        simulation.stop()
    assert b"hello" in serial
    assert values.max() > 4.0 and values.min() < 1.0  # (D13 blinks)
    assert max(currents) > 5e-3  # (the LED lights: (5 V - 1.9 V) / 330 Ω ≈ 9 mA)


# ------------------------------------------------------------------- vendor
def test_the_vendor_folders():
    assert vendor.platform_tag() in ("macos-arm64", "macos-x64", "linux-x86_64", "linux-aarch64", "windows-x64",
                                     "windows-arm64")
    assert vendor.package_directory().endswith(os.path.join("vendor", vendor.platform_tag()))
    assert set(vendor.TOOLS) == {"ngspice", "simavr", "kicad"}


def test_tools_come_from_their_projects_never_from_openscilab(tmp_path, monkeypatch):
    # (openSciLab hands on no builds: what is missing is built on the computer, or fetched upstream)
    from openscilab.core import vendor_build

    urls = []
    monkeypatch.setattr(vendor, "directories", lambda: [str(tmp_path)])
    monkeypatch.setattr(vendor, "install_directory", lambda: str(tmp_path))
    monkeypatch.setattr(vendor, "_download", lambda url, *args, **kwargs: urls.append(url) or b"")

    def no_compiler(name, target, progress=None):
        raise vendor.VendorError("no C compiler (cc, gcc, clang) to build the AVR emulator")

    monkeypatch.setattr(vendor_build, "build", no_compiler)
    with pytest.raises(vendor.VendorError, match="compiler") as raised:
        vendor.install("simavr")
    assert vendor._hint("simavr") in str(raised.value)
    assert not [url for url in urls if "openSciLab" in url]


def test_ngspice_on_windows_is_its_own_dll_package(tmp_path, monkeypatch):
    work = tmp_path / "package"
    (work / "Spice64_dll" / "dll-vs").mkdir(parents=True)
    (work / "Spice64_dll" / "lib" / "ngspice").mkdir(parents=True)
    (work / "Spice64_dll" / "dll-vs" / "ngspice.dll").write_bytes(b"new ngspice")
    (work / "Spice64_dll" / "dll-vs" / "libomp140.x86_64.dll").write_bytes(b"openmp")
    (work / "Spice64_dll" / "lib" / "ngspice" / "analog.cm").write_bytes(b"code model")
    (work / "Spice64_dll" / "lib" / "ngspice" / "table.cm").write_bytes(b"GPL: left out")
    (work / "Spice64_dll" / "lib" / "ngspice" / "psp103.osdi").write_bytes(b"not taken")
    archive = tmp_path / "ngspice.7z"
    made = subprocess.run(["tar", "--format", "7zip", "-cf", str(archive), "-C", str(work), "Spice64_dll"],
                          capture_output=True, check=False)
    if made.returncode != 0:
        pytest.skip("this tar writes no 7-Zip archives (bsdtar does: macOS, Windows)")
    seen = {}

    def download(url, progress, what, share=(0, 1), sha256=""):
        seen.update(url=url, sha256=sha256)
        return archive.read_bytes()

    monkeypatch.setattr(vendor, "_download", download)
    monkeypatch.setattr(vendor, "platform_tag", lambda: "windows-x64")
    target = tmp_path / "vendor"
    vendor._install_ngspice_windows(str(target), None)
    assert seen["url"].startswith("https://downloads.sourceforge.net/project/ngspice/")
    assert seen["sha256"] == vendor.NGSPICE_WINDOWS_SHA256
    assert (target / "ngspice" / "bin" / "ngspice.dll").read_bytes() == b"new ngspice"
    assert (target / "ngspice" / "bin" / "libomp140.x86_64.dll").is_file()
    assert os.listdir(target / "ngspice" / "lib" / "ngspice") == ["analog.cm"]
    from openscilab.circuits.engines.ngspice import code_model_folders

    assert code_model_folders(str(target / "ngspice" / "bin" / "ngspice.dll")) == \
        [os.path.normpath(target / "ngspice" / "lib" / "ngspice")]
    assert not [name for name in os.listdir(target) if name.startswith("vendor-")]  # (no staging folder left)


# ------------------------------------------------------------ review fixes
def _run_for(simulation, seconds: float, limit: float = 20.0) -> None:
    mark = simulation.clock()
    end = time.monotonic() + limit
    while simulation.clock() < mark + seconds and time.monotonic() < end:
        time.sleep(0.005)
    assert simulation.clock() >= mark + seconds, "the simulation did not get on"


def _blinks(simulation, since: float) -> int:
    times, values = simulation.course("v(D13)", since)
    high = values[times > since] > 2.5
    return int(np.count_nonzero(high[1:] != high[:-1]))


@needs_avr
@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_a_restart_keeps_the_microcontroller_running_and_takes_its_new_wiring():
    from openscilab.circuits.engines import live

    s, catalog = blink_circuit()
    circuit = spice.emit(s, catalog.spec, catalog.models)
    simulation = live.LiveSimulation(circuit, realtime=False, mcus=circuit.mcus)
    simulation.start()
    try:
        _run_for(simulation, 0.05)
        before = _blinks(simulation, simulation.clock() - 0.02)
        assert before >= 10  # (D13 toggles every millisecond)
        # a topology change restarts ngspice: the chip must go on at once, not freeze for the time it has run
        s.add_component("resistor", 300, 300, "R", "R9")
        s.add_component("ground", 330, 340, "GND", "GND9")
        s.add_wire([(330, 300), (330, 340)])
        changed = spice.emit(s, catalog.spec, catalog.models)
        assert simulation.change(changed) == "restarted"
        mark = simulation.clock()
        _run_for(simulation, 0.03)
        assert _blinks(simulation, mark + 0.005) >= 10
        # a new wire to a pin: the pin drives after the restart (the sources are bound anew)
        pins = {pin.name: point for pin, point in placed_pins(s.components["U1"], catalog.spec("mcu.uno"))}
        x, y = pins["D12"]
        s.add_component("resistor", x - 60, y, "R", "R12", {"value": "10k"})
        s.add_wire([(x - 30, y), (x, y)])
        s.add_component("ground", x - 90, y + 40, "GND", "GND12")
        s.add_wire([(x - 90, y), (x - 90, y + 40)])
        s.add_component("probe.v", x - 30, y, "P", "P12", {"name": "D12"})
        changed = spice.emit(s, catalog.spec, catalog.models)
        assert len(changed.mcus[0].pins) == 2
        assert simulation.change(changed) == "restarted"
        _run_for(simulation, 0.03)
        assert simulation.history.latest("d12") is not None  # (the pin is in the run)
    finally:
        simulation.stop()


@needs_avr
@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_reset_and_serial_input_reach_the_firmware_between_steps():
    from openscilab.circuits.engines import live

    s, catalog = blink_circuit()
    circuit = spice.emit(s, catalog.spec, catalog.models)
    simulation = live.LiveSimulation(circuit, realtime=False, mcus=circuit.mcus)
    simulation.start()
    try:
        _run_for(simulation, 0.05)
        for _ in simulation.take_mcu_events():
            pass
        simulation.mcu("U1", "reset")
        _run_for(simulation, 0.1)
        serial = b"".join(events.get("U1", {}).get("serial", b"") for events in simulation.take_mcu_events())
        assert b"hello" in serial  # (the firmware started again and greeted)
        assert _blinks(simulation, simulation.clock() - 0.03) >= 10  # (and blinks on - no catch-up run froze it)
        simulation.mcu("U1", "serial", b"abc")
        _run_for(simulation, 0.1)
        serial = b"".join(events.get("U1", {}).get("serial", b"") for events in simulation.take_mcu_events())
        assert b"ABC" in serial  # (echoed, in capitals: what the test firmware does)
    finally:
        simulation.stop()


def test_kicad_modules_supply_five_volts():
    from openscilab.circuits.mcu.boards import BOARDS, netlist_lines

    board = BOARDS["mcu.uno"]
    lines, info = netlist_lines("U1", board, {"1": ("+5V", "VCC"), "2": ("3V3", "V33"), "3": ("D13", "N1")}, 5.0, "")
    assert any(line.startswith("VU1_s1 ") and line.endswith("DC 5") for line in lines)
    assert any(line.startswith("VU1_s2 ") and line.endswith("DC 3.3") for line in lines)
    assert [pin.name for pin in info.pins] == ["D13"]


def test_malformed_firmware_files_are_reported():
    from openscilab.circuits import CircuitError
    from openscilab.circuits.mcu.firmware import read_elf, read_hex

    with pytest.raises(CircuitError, match="address"):
        read_hex(":00000004FC\n")  # (a type 4 record without its two address bytes - the checksum fits)
    with pytest.raises(CircuitError, match="cut short"):
        read_elf(b"\x7fELF\x01\x01\x01" + b"\x00" * 11 + b"\x53\x00")  # (an AVR file that ends after e_machine)
    with pytest.raises(CircuitError, match="not built for an AVR"):
        read_elf(b"\x7fELF\x01\x01\x01" + b"\x00" * 9 + b"\x02\x00\x03\x00" + b"\x00" * 60)


def test_the_serial_monitor_keeps_lines_whole(shell):
    from openscilab.ui.circuits.serial_monitor import SerialMonitor

    monitor = SerialMonitor(lambda ref, data: None, lambda ref: None)
    monitor.set_devices(["U1", "U2"])
    monitor.append("U1", b"hel")
    assert monitor.output.toPlainText() == ""  # (no line end yet)
    monitor.append("U1", b"lo\r")
    monitor.append("U1", b"\nworld\n")
    assert monitor.output.toPlainText() == "[U1] hello\n[U1] world\n"
    monitor.append("U2", b"x\n", "1")
    assert monitor.output.toPlainText().endswith("[U2 Serial1] x\n")
    monitor.deleteLater()


def test_a_bare_name_in_the_settings_must_be_a_library(monkeypatch):
    tool = vendor.TOOLS["ngspice"]
    assert not vendor._usable("no-such-library-xyz", tool)
    monkeypatch.setattr(vendor.shutil, "which", lambda name: "/usr/bin/" + name if name == "ngspice" else None)
    assert vendor._usable("ngspice", tool)


def test_downloads_are_tried_again(monkeypatch):
    import urllib.error

    attempts = []

    class Response:
        headers = {"Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            data, self._data = getattr(self, "_data", b"hello"), b""
            return data

    def urlopen(request, timeout=0):
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise urllib.error.URLError("dropped")
        return Response()

    monkeypatch.setattr(vendor.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(vendor.time, "sleep", lambda seconds: None)
    assert vendor._download("https://example.invalid/x", None, "a file") == b"hello" and len(attempts) == 3
    attempts.clear()

    def not_found(request, timeout=0):
        attempts.append(1)
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(vendor.urllib.request, "urlopen", not_found)
    with pytest.raises(vendor.VendorError, match="not found"):
        vendor._download("https://example.invalid/y", None, "a file")
    assert len(attempts) == 1  # (a 404 is not tried again)


@needs_avr
@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_an_engine_that_ran_a_microcontroller_runs_other_circuits():
    # (ngspice keeps calling the step callback of a co-simulation once it had one: setting it to none made the
    # next run of that engine process - from the pool - crash)
    from openscilab.circuits.engines import live

    from test_circuit_devices import device_circuit

    s, catalog = blink_circuit()
    circuit = spice.emit(s, catalog.spec, catalog.models)
    first = live.LiveSimulation(circuit, realtime=False, mcus=circuit.mcus)
    first.start()
    _run_for(first, 0.02)
    first.stop()
    second = live.LiveSimulation(spice.emit(device_circuit(), catalog.spec, catalog.models), realtime=False)
    second.start()
    try:
        second.set_inputs({"vj1x": 3.3})
        end = time.monotonic() + 10
        while time.monotonic() < end and (second.value("v(OUT)") or 0) < 3.0 and second.state == "running":
            time.sleep(0.01)
        assert second.state == "running" and second.value("v(OUT)") == pytest.approx(3.2967, rel=1e-3)
    finally:
        second.stop()
