"""Pins of connected devices in a circuit (hardware in the loop, ``circuits/device_io.py``): the netlist,
the bridge to a device, a value a device reads arriving in the running circuit, the document."""

from __future__ import annotations

import time

import pytest

from openscilab.circuits import device_io, engines, spice
from openscilab.circuits.catalog import Catalog
from openscilab.circuits.model import Schematic
from openscilab.core.hub import Hub
from openscilab.core.instrument import (
    MODE_INPUT,
    MODE_OUTPUT,
    PIN_ADC,
    PIN_DIN,
    PIN_DOUT,
    AnalogInFacet,
    AnalogOutFacet,
    GpioFacet,
    Instrument,
    PinInfo,
)


@pytest.fixture(scope="module")
def catalog():
    return Catalog(kicad=[], models=[], user=[], use_cache=False)


class FakeGpio(GpioFacet):
    """Pins D2 (input), D3 (output), A0 (analog input), D9 (reserved)."""

    def __init__(self) -> None:
        super().__init__()
        self.levels = {"D2": 1}
        self.writes: list[tuple[str, int]] = []
        self.modes: dict[str, str] = {}

    def pins(self) -> list[PinInfo]:
        return [PinInfo("D2", frozenset({PIN_DIN, PIN_DOUT}), logic_level=3.3),
                PinInfo("D3", frozenset({PIN_DIN, PIN_DOUT}), logic_level=3.3),
                PinInfo("A0", frozenset({PIN_ADC}), logic_level=3.3, analog_channel=0),
                PinInfo("D9", frozenset({PIN_DIN, PIN_DOUT}), reserved="USB")]

    def set_mode(self, pin: str, mode: str) -> None:
        self.modes[pin] = mode

    def write(self, pin: str, value: int) -> None:
        self.writes.append((pin, value))

    def read(self, pin: str) -> int:
        return self.levels.get(pin, 0)


class FakeAnalogIn(AnalogInFacet):
    def read(self, pins: list[str]) -> dict[str, float]:
        return {pin: 1.25 for pin in pins}


class FakeAnalogOut(AnalogOutFacet):
    def __init__(self) -> None:
        super().__init__()
        self.voltages: list[tuple[str, float]] = []

    def set_voltage(self, pin: str, volt: float) -> None:
        self.voltages.append((pin, volt))

    def voltage_range(self, pin: str) -> tuple[float, float]:
        return 0.0, 5.0

    def pin_names(self) -> list[str]:
        return ["AO0"]


def fake_device(name: str = "Board") -> tuple[Instrument, FakeGpio, FakeAnalogOut]:
    instrument = Instrument(name, "Test board", f"test:{name}")
    gpio, analog_out = FakeGpio(), FakeAnalogOut()
    instrument.add_facet(gpio)
    instrument.add_facet(FakeAnalogIn())
    instrument.add_facet(analog_out)
    return instrument, gpio, analog_out


def device_circuit() -> Schematic:
    """J1 (the device reads D2) drives OUT through R1 into a 10 kΩ load; J2 (the device drives D3) follows OUT."""
    s = Schematic("HIL")
    s.add_component("device.input", 0, 0, "J", "J1", {"device": "Board", "pin": "D2"})
    s.add_component("resistor", 60, 40, "R", "R1", {"value": "10k"}, rotation=90)
    s.add_component("ground", 60, 100, "GND", "GND1")
    s.add_component("probe.v", 60, 0, "P", "P1", {"name": "OUT"})
    s.add_component("device.output", 120, 0, "J", "J2", {"device": "Board", "pin": "D3"})
    s.add_wire([(0, 0), (60, 0), (60, 10)])
    s.add_wire([(60, 70), (60, 100)])
    s.add_wire([(60, 0), (120, 0)])
    return s


def test_device_pins_are_in_the_netlist(catalog):
    circuit = spice.emit(device_circuit(), catalog.spec, catalog.models)
    assert [str(problem) for problem in circuit.errors] == []
    pins = {pin.component: pin for pin in circuit.device_pins}
    assert pins["J1"].direction == "in" and pins["J1"].source == "vj1x" and pins["J1"].pin == "D2"
    assert pins["J2"].direction == "out" and pins["J2"].vector == "v(OUT)" and pins["J2"].signal == "digital"
    assert any(line.startswith("VJ1x ") and line.endswith(" external") for line in circuit.lines)
    analysis = spice.emit(device_circuit(), catalog.spec, catalog.models, analysis="op")
    assert not any(line.endswith(" external") for line in analysis.lines)  # (an analysis has no device)


def test_the_bridge_reads_inputs_and_drives_outputs():
    hub = Hub()
    instrument, gpio, analog_out = fake_device()
    hub.add(instrument, "Board")
    pins = [spice.DevicePin("J1", "in", "Board", "D2", "digital", source="vj1x"),
            spice.DevicePin("J3", "in", "", "A0", "analog", source="vj3x"),  # (the only device)
            spice.DevicePin("J2", "out", "Board", "D3", "digital", vector="v(out)"),
            spice.DevicePin("J4", "out", "Board", "AO0", "analog", vector="v(set)")]
    pushed: dict[str, float] = {}
    nets = {"v(out)": 4.0, "v(set)": 2.5}
    bridge = device_io.DeviceBridge(hub, pins, 5.0, pushed.update, nets.get)
    bridge.exchange()
    assert pushed == {"vj1x": pytest.approx(3.3), "vj3x": pytest.approx(1.25)}
    assert gpio.modes == {"D2": MODE_INPUT, "D3": MODE_OUTPUT}
    assert gpio.writes == [("D3", 1)] and analog_out.voltages == [("AO0", 2.5)]
    # only changes are written; a digital output has hysteresis around half the logic supply
    nets["v(out)"] = 2.4
    bridge.exchange()
    assert gpio.writes == [("D3", 1)]  # (2.4 V: inside the band around 2.5 V)
    nets["v(out)"] = 0.3
    nets["v(set)"] = 9.0  # (beyond the range of the analog output: limited)
    bridge.exchange()
    assert gpio.writes == [("D3", 1), ("D3", 0)] and analog_out.voltages[-1] == ("AO0", 5.0)
    assert not bridge.problems
    bridge.stop()  # (the outputs it drove are safe again)
    assert gpio.modes["D3"] == MODE_INPUT and analog_out.voltages[-1] == ("AO0", 0.0)


def test_the_bridge_says_what_is_wrong_at_the_component():
    hub = Hub()
    instrument, _gpio, _analog_out = fake_device()
    hub.add(instrument, "Board")
    pins = [spice.DevicePin("J1", "in", "Other", "D2", "digital", source="vj1x"),
            spice.DevicePin("J2", "in", "Board", "D7", "digital", source="vj2x"),
            spice.DevicePin("J3", "out", "Board", "A0", "digital", vector="v(a)"),
            spice.DevicePin("J4", "in", "Board", "D9", "digital", source="vj4x"),
            spice.DevicePin("J5", "in", "Board", "", "digital", source="vj5x")]
    bridge = device_io.DeviceBridge(hub, pins, 5.0, lambda values: None, lambda vector: 1.0)
    bridge.exchange()
    assert "no device 'Other'" in bridge.problems["J1"]
    assert "no pin D7" in bridge.problems["J2"]
    assert "cannot drive a level" in bridge.problems["J3"]
    assert "in use: USB" in bridge.problems["J4"]
    assert "choose the pin" in bridge.problems["J5"]
    second, _g, _a = fake_device("Second")
    hub.add(second, "Second")
    other = device_io.DeviceBridge(hub, [spice.DevicePin("J6", "in", "", "D2", "digital", source="x")], 5.0,
                                   lambda values: None, lambda vector: None)
    other.exchange()
    assert "several are connected" in other.problems["J6"]
    assert device_io.pin_names(instrument, "in", "analog") == ["A0"]
    assert device_io.pin_names(instrument, "out", "digital") == ["D2", "D3"]  # (D9 is in use: not offered)
    assert device_io.pin_names(instrument, "out", "analog") == ["AO0"]


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_what_a_device_reads_drives_the_running_circuit(catalog):
    from openscilab.circuits.engines import live

    circuit = spice.emit(device_circuit(), catalog.spec, catalog.models)
    simulation = live.LiveSimulation(circuit, realtime=False)
    simulation.start()
    try:
        simulation.set_inputs({"vj1x": 3.3})
        end = time.monotonic() + 10
        while time.monotonic() < end and abs((simulation.value("v(OUT)") or 0) - 3.3) > 0.01:
            time.sleep(0.01)
        assert simulation.value("v(OUT)") == pytest.approx(3.3 * 10_000 / 10_010, rel=1e-3), (
            simulation.state, simulation.error, simulation.clock())
        simulation.set_inputs({"vj1x": 0.0})
        end = time.monotonic() + 10
        while time.monotonic() < end and abs(simulation.value("v(OUT)") or 0) > 0.01:
            time.sleep(0.01)
        assert abs(simulation.value("v(OUT)")) < 0.01
    finally:
        simulation.stop()


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_a_circuit_with_device_pins_runs_with_the_device(shell):
    instrument, gpio, _analog_out = fake_device()
    shell.hub.add(instrument, "Board")
    try:
        circuit = shell.new_document("circuit")
        circuit._set_schematic(device_circuit())
        assert circuit.start_run()
        try:
            end = time.monotonic() + 10
            while time.monotonic() < end and (("D3", 1) not in gpio.writes
                                              or circuit.scene.components["J1"].live_text != "1"):
                from PySide6.QtWidgets import QApplication

                QApplication.processEvents()
                time.sleep(0.01)
            # D2 of the device reads 1 → J1 gives 3.3 V → OUT is high → D3 of the device is driven high
            assert ("D3", 1) in gpio.writes
            assert circuit.scene.components["J1"].live_text == "1"
        finally:
            circuit.stop_run()
        assert gpio.modes["D3"] == MODE_INPUT  # (safe again when it stopped)
        # the inspector offers the connected devices and the pins that fit
        circuit.scene.select_ids(["J2"])
        widget = circuit.inspector_widget()
        device_box, pin_box = widget.fields["device"].editor, widget.fields["pin"].editor
        assert "Board" in [device_box.itemText(index) for index in range(device_box.count())]
        assert [pin_box.itemText(index) for index in range(pin_box.count())] == ["D2", "D3"]
    finally:
        shell.hub.remove("Board", close=False)


def test_the_probe_button_offers_every_kind_of_probe(shell):
    circuit = shell.new_document("circuit")
    circuit.toolbar()
    entries = [action.text() for action in circuit.probe_button.menu().actions() if action.text()]
    assert entries == ["Voltage probe", "Current probe", "Logic probe", "Oscilloscope", "Terminal", "Device input",
                       "Device output"]
    next(action for action in circuit.probe_menu.actions() if action.text() == "Logic probe").trigger()
    assert circuit.scene.tool == "place" and circuit.scene._ghost_type == "probe.logic"
    circuit.scene.cancel()
    assert circuit.probe_menu in [action.menu() for menu in circuit.document_menus() for action in menu.actions()]
