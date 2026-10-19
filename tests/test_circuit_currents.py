"""The current view (``circuits/currents.py``): the currents of the elements, measured or from
Kirchhoff's law, and along the pieces of the wires; shown as streams while a circuit runs."""

from __future__ import annotations

import time

import pytest

from openscilab.circuits import currents, engines, spice
from openscilab.circuits.catalog import Catalog
from openscilab.circuits.model import Schematic
from openscilab.circuits.nets import extract


@pytest.fixture(scope="module")
def catalog():
    return Catalog(kicad=[], models=[], user=[], use_cache=False)


def divider() -> Schematic:
    """V1 (5 V) → R1 (1 kΩ) → D1 (1N4148) → R2 (1 kΩ) → ground."""
    s = Schematic("Divider")
    s.add_component("source.dc", 0, 40, "V", "V1", {"value": "5 V"})
    s.add_component("resistor", 60, 0, "R", "R1", {"value": "1k"})
    s.add_component("diode", 150, 40, "D", "D1", {"value": "1N4148"}, rotation=270)
    s.add_component("resistor", 150, 120, "R", "R2", {"value": "1k"}, rotation=90)
    for ref, x, y in (("GND1", 0, 180), ("GND2", 150, 180)):
        s.add_component("ground", x, y, "GND", ref)
    s.add_wire([(0, 10), (0, 0), (30, 0)])           # V1+ to R1
    s.add_wire([(90, 0), (150, 0), (150, 10)])       # R1 to the anode of D1 (OUT)
    s.add_wire([(150, 70), (150, 90)])               # D1 to R2
    s.add_wire([(150, 150), (150, 180)])             # R2 to ground
    s.add_wire([(0, 70), (0, 180)])                  # V1- to ground
    return s


def test_netlist_lines_and_how_their_currents_are_known():
    lines = ["V1 a 0 DC 5", "R1 a b 1000", "C1 b 0 1e-06", "Q1 c b 0 2N3904", "XU1 b c a 0 c OPAMP",
             ".subckt OPAMP inp inn vcc vee out", "R9 inp inn 1e7", ".ends OPAMP", "AU1_i1 [b] [d_u1] osl_adc"]
    elements = {element.name: element for element in currents.parse(lines)}
    assert "R9" not in elements  # (inside a subcircuit)
    assert elements["V1"].how == ["branch", "rest"] and elements["R1"].how == ["resistor", "rest"]
    assert elements["R1"].resistance == 1000
    assert elements["Q1"].how == ["@q1[ic]", "@q1[ib]", "rest"]
    assert elements["XU1"].how == ["ammeter"] * 5 and elements["AU1_i1"].how == [None]
    assert currents.saved_vectors(lines) == ["@c1[i]", "@q1[ic]", "@q1[ib]"]
    rewritten = currents.with_ammeters(lines)
    assert "V_am_XU1_1 b am_b__XU1_1 DC 0" in rewritten
    assert any(line.startswith("XU1 am_b__XU1_1 ") and line.endswith(" OPAMP") for line in rewritten)
    assert "R9 inp inn 1e7" in rewritten  # (the subcircuit itself stays)


def test_kirchhoff_fills_in_what_is_not_measured():
    model = currents.CurrentModel(["V1 a 0 DC 5", "R1 a b 1000", "AU1_o1 [d_x] [b] osl_dac"])
    found = model.terminal_currents({"a": 5.0, "b": 3.0, "v1#branch": -0.002})
    assert found["R1"] == pytest.approx([0.002, -0.002])
    assert found["AU1_o1"] == pytest.approx([0.002])  # (all that R1 brings to b goes into the bridge)
    assert found["V1"] == pytest.approx([-0.002, 0.002])


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_the_current_flows_along_the_wires(catalog):
    from openscilab.circuits.engines import live

    s = divider()
    circuit = spice.emit(s, catalog.spec, catalog.models)
    circuit.show_currents = True
    # (in an engine process, as the application runs it: ngspice in the test process outlives the tests)
    simulation = live.LiveSimulation(circuit, realtime=False)
    simulation.start()
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end and simulation.clock() < 0.05:
            time.sleep(0.01)
        latest = simulation.history.latest()  # (the batches bring the values of one moment)
    finally:
        simulation.stop()
    model = currents.WireCurrents(s, extract(s, catalog.spec), circuit, catalog.spec)
    wires, through, _pins = model.compute(latest)
    expected = (5.0 - 0.6) / 2000  # (about: the diode drops about 0.6 V)
    by_wire = {wire_id: pieces for wire_id, pieces in wires.items()}
    first = by_wire["w1"]  # from V1+ up and right to R1: the current goes that way
    assert [piece.start for piece in first] == [(0, 10), (0, 0)] and all(
        piece.current == pytest.approx(expected, rel=0.05) for piece in first)
    assert all(piece.current == pytest.approx(expected, rel=0.05) for piece in by_wire["w2"])  # (R1 → D1)
    assert by_wire["w5"][0].current == pytest.approx(-expected, rel=0.05)  # (ground → V1-: drawn from V1- down)
    assert through["R1"] == pytest.approx(expected, rel=0.05)  # (in at pin 1, out at pin 2)
    assert through["D1"] * through["R2"] != 0


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_the_current_flows_into_the_pins_of_a_potentiometer(catalog):
    from openscilab.circuits.engines import live

    s = Schematic("Potentiometer")
    s.add_component("source.dc", 0, 40, "V", "V1", {"value": "5 V"})
    s.add_component("potentiometer", 60, 0, "RV", "RV1", {"value": "10k"})
    for ref, x, y in (("GND1", 0, 180), ("GND2", 150, 180)):
        s.add_component("ground", x, y, "GND", ref)
    s.add_wire([(0, 10), (0, 0), (30, 0)])           # V1+ to pin 1
    s.add_wire([(90, 0), (150, 0), (150, 180)])      # pin 3 to ground
    s.add_wire([(0, 70), (0, 180)])                  # V1- to ground
    circuit = spice.emit(s, catalog.spec, catalog.models)
    circuit.show_currents = True
    simulation = live.LiveSimulation(circuit, realtime=False)
    simulation.start()
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end and simulation.clock() < 0.05:
            time.sleep(0.01)
        latest = simulation.history.latest()
    finally:
        simulation.stop()
    model = currents.WireCurrents(s, extract(s, catalog.spec), circuit, catalog.spec)
    _wires, through, pins = model.compute(latest)
    assert "RV1" not in through  # (three pins: the current of each)
    assert pins["RV1"]["1"] == pytest.approx(5 / 10000, rel=0.05)
    assert pins["RV1"]["3"] == pytest.approx(-5 / 10000, rel=0.05)
    assert abs(pins["RV1"].get("2", 0.0)) < 1e-6  # (the wiper leads nowhere)
    assert "V1" in through and "V1" not in pins


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_a_running_circuit_shows_its_current(shell):
    from PySide6.QtWidgets import QApplication

    document = shell.new_document("circuit")
    document._set_schematic(divider())
    document.action_current.setChecked(True)
    assert document.start_run()
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end and not document.scene.flows_item.flows:
            QApplication.processEvents()
            time.sleep(0.01)
        assert document.scene.flows_item.flows  # (dots along the wires)
        assert document.scene.wires["w1"].flows and "mA along it" in document.scene.wires["w1"].toolTip()
        assert document.scene.components["R1"].flows  # (and through the resistor)
        before = [flow.offset for flow in document.scene.flows_item.flows]
        time.sleep(0.1)
        for _ in range(5):
            QApplication.processEvents()
        assert [flow.offset for flow in document.scene.flows_item.flows] != before  # (they move)
        # wires and pins in the colours of their voltages: the 5 V wire green, the ground wire grey
        from openscilab.ui.circuits.items import voltage_color

        end = time.monotonic() + 5
        while time.monotonic() < end and document.scene.wires["w1"].voltage is None:
            QApplication.processEvents()
            time.sleep(0.01)
        supply, ground = document.scene.wires["w1"].voltage, document.scene.wires["w5"].voltage
        assert supply.green() > supply.red() and ground.rgb() == voltage_color(0.0, 5.0).rgb()
        assert document.scene.components["R1"].pin_colors
        document.action_current.trigger()  # (off: no dots, the run goes on without measuring)
        QApplication.processEvents()
        assert not document.scene.flows_item.flows and not document.circuit.show_currents
        assert document.scene.wires["w1"].voltage is None  # (the colour of wires again)
    finally:
        document.stop_run()
        document.action_current.setChecked(True)
    assert not document.scene.flows_item.flows
