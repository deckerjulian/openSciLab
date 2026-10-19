"""Circuits simulated with ngspice (skipped where ngspice is not installed): the analyses, the live
simulation in its engine process (values changed while it runs, a changed circuit, pause), the circuit
as a device (``circuit:``) for the Python API and flows, and parts of KiCad's libraries."""

from __future__ import annotations

import time

import numpy as np
import pytest
from test_circuit_model import low_pass

from openscilab.circuits import engines, spice, yaml_io
from openscilab.circuits.catalog import Catalog
from openscilab.circuits.engines import analysis, live
from openscilab.circuits.model import Schematic
from openscilab.circuits.nets import extract

pytestmark = pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")


@pytest.fixture(scope="module")
def catalog():
    return Catalog(kicad=[], models=[], user=[], use_cache=False)


def logic_low_pass(r: str = "1k", c: str = "159n"):
    """The low pass driven between 0 and 5 V (its probe crosses the logic threshold of 2.5 V)."""
    s = low_pass(r, c)
    s.components["V1"].params.update({"amplitude": "2.5 V", "offset": "2.5 V"})
    return s


def wait_until(condition, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_analyses_of_a_low_pass(catalog):
    s = low_pass()
    result = analysis.run_analysis(spice.emit(s, catalog.spec, catalog.models, analysis="ac",
                                              analysis_params={"start": "10 Hz", "stop": "100 kHz", "points": "20"}), "ac")
    assert analysis.corner_frequency(result.x, result.traces["out"]) == pytest.approx(1001, rel=0.03)
    result = analysis.run_analysis(spice.emit(s, catalog.spec, catalog.models, analysis="tran",
                                              analysis_params={"stop": "5 ms", "step": "10 us"}), "tran")
    assert result.x[-1] == pytest.approx(5e-3) and np.max(result.traces["out"][len(result.x) // 2:]) == \
        pytest.approx(1 / np.sqrt(2), rel=0.05)
    divider = low_pass()
    divider.components["V1"].type = "source.dc"
    divider.components["V1"].params = {"value": "5 V"}
    divider.components["C1"].type = "resistor"
    divider.components["C1"].params = {"value": "1k"}
    result = analysis.run_analysis(spice.emit(divider, catalog.spec, catalog.models, analysis="op"), "op")
    assert result.values["out"] == pytest.approx(2.5)


def test_the_live_simulation_runs_in_real_time_and_takes_changes(catalog):
    simulation = live.LiveSimulation(spice.emit(low_pass(), catalog.spec, catalog.models), realtime=True)
    simulation.start()
    try:
        started = time.monotonic()
        assert wait_until(lambda: simulation.clock() > 0.3)
        assert simulation.clock() == pytest.approx(time.monotonic() - started, abs=0.25)
        assert abs(simulation.value("v(out)") or 0) <= 1.0
        changed = low_pass(r="10k")
        assert simulation.change(spice.emit(changed, catalog.spec, catalog.models)) == "altered"
        mark = simulation.clock()
        assert wait_until(lambda: simulation.clock() > mark + 0.05)
        times, values = simulation.course("v(out)")
        recent = values[times > times[-1] - 0.002]
        assert np.max(np.abs(recent)) == pytest.approx(0.0995, rel=0.1)  # (1 / sqrt(1 + 10²))
        simulation.pause()
        paused = simulation.clock()
        time.sleep(0.15)
        assert simulation.clock() == pytest.approx(paused, abs=0.01)
        simulation.resume()
        rewired = low_pass(r="10k")
        rewired.add_component("resistor", 200, -30, "R", "R2")
        rewired.add_wire([(120, -30), (170, -30)])
        before = simulation.clock()
        assert simulation.change(spice.emit(rewired, catalog.spec, catalog.models)) == "restarted"
        assert wait_until(lambda: simulation.clock() > before + 0.05)  # (its time goes on)
    finally:
        simulation.stop()
    assert simulation.state == "stopped"


def test_the_wiper_of_a_variac_turns_while_it_runs(catalog):
    s = Schematic("Variac")
    s.add_component("source.sine", 0, 0, "V", "V1", {"amplitude": "100 V", "frequency": "50 Hz"})
    s.add_component("transformer.variable", 100, 0, "T", "T1", {"output": "50 V", "input": "100 V"})
    s.add_component("resistor", 200, 0, "R", "R1", {"value": "1k"}, rotation=270)
    s.add_component("ground", 0, 60, "GND", "GND1")
    s.add_component("probe.v", 160, -30, "P", "P1", {"name": "OUT"})
    for points in ([(0, -30), (70, -30)], [(130, -30), (200, -30)], [(0, 30), (0, 60)], [(70, 30), (0, 30)],
                   [(200, 30), (70, 30)]):
        s.add_wire(points)
    assert [p for p in extract(s, catalog.spec).problems if p.severity == "error"] == []

    def peak(simulation, since):
        times, data = simulation.course("v(out)", since)
        return float(np.max(np.abs(data[times > since])))

    simulation = live.LiveSimulation(spice.emit(s, catalog.spec, catalog.models), step=1e-4, realtime=False)
    simulation.start()
    try:
        assert wait_until(lambda: simulation.clock() > 0.1)
        assert peak(simulation, simulation.clock() - 0.04) == pytest.approx(50, rel=0.03)
        s.components["T1"].params["output"] = "100 V"
        assert simulation.change(spice.emit(s, catalog.spec, catalog.models)) == "altered"
        # (in virtual time the engine is ahead of the clock seen here: measured well after the change)
        mark = simulation.clock() + 0.3
        assert wait_until(lambda: simulation.clock() > mark + 0.04)
        assert peak(simulation, simulation.clock() - 0.04) == pytest.approx(100, rel=0.03)
        # (live, ngspice computes only the currents of parts that light: each saved one slows it down)
        assert not [vector for vector in simulation.history.tracked if vector.startswith("@")]
    finally:
        simulation.stop()


def test_the_speed_changes_while_it_runs(catalog):
    simulation = live.LiveSimulation(spice.emit(low_pass(), catalog.spec, catalog.models), realtime=True)
    simulation.start()
    try:
        assert wait_until(lambda: simulation.clock() > 0.1)
        simulation.set_speed(0.1)
        time.sleep(0.05)  # (the engine process takes it)
        mark, started = simulation.clock(), time.monotonic()
        time.sleep(0.5)
        slow = (simulation.clock() - mark) / (time.monotonic() - started)
        assert slow == pytest.approx(0.1, abs=0.06)
        simulation.pause()
        simulation.set_speed(1.0)  # (paused: the pause does not count at the new speed)
        time.sleep(0.2)
        simulation.resume()
        mark, started = simulation.clock(), time.monotonic()
        assert wait_until(lambda: simulation.clock() > mark + 0.3)
        assert (simulation.clock() - mark) / (time.monotonic() - started) == pytest.approx(1.0, abs=0.35)
    finally:
        simulation.stop()


def test_the_speed_slider_sets_the_speed_of_a_running_circuit(shell):
    from PySide6.QtWidgets import QApplication

    from openscilab.ui.circuits.speed import SPEEDS

    document = shell.new_document("circuit")
    document._set_schematic(low_pass())
    control = document.speed_control
    assert control.speed == 1.0 and control.label.text() == "1×"
    assert document.start_run()
    try:
        control.slider.setValue(SPEEDS.index(0.1))
        QApplication.processEvents()
        assert document.schematic.setting("speed") == "0.1" and document.live.speed == 0.1
        assert control.label.text() == "0.1×"
        document.undo.undo()  # (the setting back: the running circuit too)
        QApplication.processEvents()
        assert control.speed == 1.0 and document.live.speed == 1.0
        assert control.slider.value() == SPEEDS.index(1.0)
    finally:
        document.stop_run()
    document.set_virtual_time(True)
    assert not control.isEnabled()  # (virtual time runs as fast as it can)
    document.set_virtual_time(False)
    assert control.isEnabled()


def test_virtual_time_runs_as_fast_as_it_can(catalog):
    simulation = live.LiveSimulation(spice.emit(low_pass(), catalog.spec, catalog.models), realtime=False)
    simulation.start()
    try:
        assert wait_until(lambda: simulation.clock() > 0.2)
    finally:
        simulation.stop()


def test_a_circuit_is_a_device(catalog, tmp_path):
    from openscilab import api

    path = tmp_path / "rc.circuit.yaml"
    yaml_io.save(logic_low_pass(r="1k", c="10n"), str(path))
    from openscilab.circuits import running

    try:
        with api.open(f"circuit:{path}") as device:
            assert device.driver.channel_count == 1  # (OUT_L: the probe OUT, thresholded)
            capture = device.capture(channels=[0], rate="100k", samples=1000)
        samples = capture.samples(0)
        assert 0 < np.count_nonzero(samples) < len(samples)
    finally:
        simulation = running.find(str(path))
        if simulation is not None:
            simulation.stop()
            running.unregister(simulation)


def test_a_capture_is_not_before_the_time_of_the_flow(tmp_path):
    from openscilab.circuits.source import open_circuit_instrument

    path = tmp_path / "rc.circuit.yaml"
    yaml_io.save(low_pass(), str(path))
    # (in virtual time a flow's timer passes at once: the circuit's device keeps to the flow's time)
    instrument = open_circuit_instrument(str(path), clock=lambda: 1.5, fast=True)
    try:
        driver = instrument.simulated_driver
        assert driver.clock() == pytest.approx(1.5)
        samples = driver.sample_analog(0, driver.clock(), 10_000, 100)  # (waits until the circuit is there)
        assert len(samples) == 100 and np.all(np.isfinite(samples))
    finally:
        instrument.close()


def test_a_flow_captures_a_probe(catalog, tmp_path):
    from openscilab.circuits import running
    from openscilab.lab import yaml_io as flow_io
    from openscilab.lab.engine import Engine

    path = tmp_path / "rc.circuit.yaml"
    yaml_io.save(logic_low_pass(), str(path))
    flow = flow_io.loads(f"""
flow: Circuit
nodes:
  rc: {{type: device.device, address: "circuit:{path}"}}
  cap: {{type: device.capture, channels: [OUT_L], rate: 100 kHz, samples: 2000}}
edges:
  - rc.device -> cap.device
""")
    engine = Engine(flow)
    captures = []
    engine.subscribe(lambda event: event.kind == "value" and event.port == "capture" and captures.append(event.value))
    try:
        assert engine.run(timeout=30).ok
    finally:
        simulation = running.find(str(path))
        if simulation is not None:
            simulation.stop()
            running.unregister(simulation)
    samples = captures[0].session.capture_channels[0].samples
    assert 0 < np.count_nonzero(samples) < len(samples)  # (a 1 kHz sine, thresholded: high half of the time)


@pytest.mark.skipif(not Catalog().kicad_libraries(), reason="KiCad's symbol libraries are not installed")
def test_parts_of_kicad_are_simulated():
    catalog = Catalog(models=[], user=[], use_cache=False)
    spec = catalog.spec("kicad:Amplifier_Operational:LM358")
    assert spec is not None and spec.model is not None and spec.symbol.units == 3
    from openscilab.circuits.model import Schematic
    from openscilab.circuits.nets import placed_pins

    s = Schematic("Inverting amplifier")
    s.add_component("kicad:Amplifier_Operational:LM358", 0, 0, "U", "U1A", unit=1)
    s.add_component("kicad:Amplifier_Operational:LM358", 0, 200, "U", "U1C", unit=3)
    pins = {pin.name or pin.number: point for pin, point in placed_pins(s.components["U1A"], spec)}
    supply = {pin.name: point for pin, point in placed_pins(s.components["U1C"], spec)}
    s.add_component("power", supply["V+"][0], supply["V+"][1], "PWR", "PWR1", {"net": "VCC", "voltage": "12 V"})
    s.add_component("ground", supply["V-"][0], supply["V-"][1], "GND", "GND1")
    s.add_component("ground", pins["+"][0], pins["+"][1], "GND", "GND2")
    s.add_label("IN", pins["-"][0], pins["-"][1])
    s.add_label("OUT", *pins["1"]) if "1" in pins else s.add_label("OUT", *[p for n, p in pins.items() if n not in "+-"][0])
    circuit = spice.emit(s, catalog.spec, catalog.models_for([]), analysis="op")
    assert not circuit.errors, [str(p) for p in circuit.errors]
    assert any(line.startswith("XU1A ") and line.endswith(" LM358") for line in circuit.lines)
    assert any(line.startswith(".subckt LM358") for line in circuit.lines)


def rf_circuit(stub: bool):
    """Two ports joined by a 50 Ω line; with ``stub`` an open quarter-wave stub at port 2 (1 GHz)."""
    from openscilab.circuits.model import Schematic

    s = Schematic("RF")
    s.add_component("rf.port", 0, 0, "P", "P1", {"number": "1"})
    s.add_component("rf.port", 200, 0, "P", "P2", {"number": "2"})
    s.add_component("rf.tline", 100, -30, "TL", "TL1", {"length": "0.2", "velocity": "1"})
    s.add_component("ground", 0, 60, "GND", "GND1")
    s.add_component("ground", 200, 60, "GND", "GND2")
    s.add_wire([(0, -30), (50, -30)])
    s.add_wire([(150, -30), (200, -30)])
    s.add_wire([(0, 30), (0, 60)])
    s.add_wire([(200, 30), (200, 60)])
    s.add_wire([(60, -10), (60, 40), (0, 40)])
    s.add_wire([(140, -10), (140, 40), (200, 40)])
    if stub:
        # 75 mm in air: a quarter of the wave of 1 GHz
        s.add_component("rf.tline", 300, -30, "TL", "TL2", {"length": "0.0749", "velocity": "1"})
        s.add_wire([(200, -30), (250, -30)])
        s.add_wire([(260, -10), (260, 40), (200, 40)])
        s.add_wire([(340, -10), (340, 0), (260, 0)])
    return s


@pytest.mark.skipif(engines.version() < 44, reason="S-parameters need ngspice 44 or newer")
def test_s_parameters_of_a_line_and_a_stub(catalog):
    params = {"start": "100 MHz", "stop": "2 GHz", "points": "200", "scale": "lin"}
    through = analysis.run_analysis(spice.emit(rf_circuit(False), catalog.spec, catalog.models, analysis="sp",
                                               analysis_params=params), "sp")
    assert np.all(np.abs(analysis.magnitude_db(through.traces["s_2_1"])) < 0.1)  # (matched: everything passes)
    notch = analysis.run_analysis(spice.emit(rf_circuit(True), catalog.spec, catalog.models, analysis="sp",
                                             analysis_params=params), "sp")
    s21 = np.abs(notch.traces["s_2_1"])
    assert notch.x[int(np.argmin(s21))] == pytest.approx(1e9, rel=0.03) and s21.min() < 0.1


def test_subcircuits_run_live(catalog):
    """(The live run drops the analysis lines of a netlist, not the .ends of its subcircuits.)"""
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "examples", "library", "14-circuits", "03-op-amp", "circuits",
                        "amplifier.circuit.yaml")
    circuit = spice.emit(yaml_io.load(path), catalog.spec, catalog.models)
    assert any(line.startswith(".ends") for line in circuit.lines)
    simulation = live.LiveSimulation(circuit, realtime=False)
    simulation.start()
    try:
        assert wait_until(lambda: simulation.clock() > 0.003)
        times, values = simulation.course("v(OUT)")
        assert values[times > 0.001].max() == pytest.approx(5.0, rel=0.1)  # (0.5 V ten times)
    finally:
        simulation.stop()


# ------------------------------------------------------------ review fixes
def test_a_dead_engine_process_is_an_error_of_the_run(catalog):
    simulation = live.LiveSimulation(spice.emit(low_pass(), catalog.spec, catalog.models), realtime=False)
    simulation.start()
    try:
        assert wait_until(lambda: simulation.clock() > 0.01)
        simulation._engine.process.kill()
        assert wait_until(lambda: simulation.state == "error", timeout=10)
        assert "ended" in simulation.error
    finally:
        simulation.stop()


def test_a_run_ngspice_gives_up_is_an_error():
    class Engine:
        errors = ["Warning: something", "doAnalyses: TRAN:  Timestep too small; time = 1e-3"]

    simulation = live.LocalSimulation(spice.Circuit(), realtime=False)
    simulation.state = "running"
    simulation._engine = Engine()
    simulation._running_changed(True)
    assert simulation.state == "running"
    simulation._halting = True
    simulation._running_changed(False)  # (our own halt: no error)
    assert simulation.state == "running"
    simulation._halting = False
    simulation._running_changed(False)
    assert simulation.state == "error" and "too small" in simulation.error


def test_a_probe_added_while_running_gets_its_course(catalog):
    s = low_pass()
    simulation = live.LiveSimulation(spice.emit(s, catalog.spec, catalog.models), realtime=False)
    simulation.start()
    try:
        assert wait_until(lambda: simulation.clock() > 0.05)
        s.add_component("probe.v", 0, -30, "P", "P2")  # (unnamed, on the source's net: no line changes)
        changed = spice.emit(s, catalog.spec, catalog.models)
        assert simulation.change(changed) == ""
        vector = next(probe.vector for probe in changed.probes if probe.component == "P2")
        mark = simulation.clock()
        assert wait_until(lambda: simulation.clock() > mark + 0.05)
        times, data = simulation.course(vector, mark)
        assert len(times) > 10 and np.nanmax(np.abs(data)) > 0.5  # (the sine of 1 V arrives)
    finally:
        simulation.stop()


def test_scope_channels_of_the_device_are_against_their_ground(catalog, tmp_path):
    from openscilab.circuits.nets import placed_pins
    from openscilab.circuits.source import open_circuit_instrument

    s = low_pass()
    s.add_component("probe.scope", 400, 0, "XSC", "XSC1")
    pins = {pin.name: point for pin, point in placed_pins(s.components["XSC1"], catalog.spec("probe.scope"))}
    s.add_wire([(120, -30), pins["CH1"]])
    cx, cy = pins["CH2"]
    s.add_wire([(0, -30), (0, -80), (cx - 50, -80), (cx - 50, cy), (cx, cy)])  # (CH2 on the source's plus)
    gx, gy = pins["GND"]
    s.add_wire([(gx, gy), (gx, gy + 20), (160, gy + 20), (160, -30)])  # (GND on OUT: CH1 reads 0, CH2 IN - OUT)
    path = tmp_path / "scope.circuit.yaml"
    yaml_io.save(s, str(path))
    instrument = open_circuit_instrument(str(path), fast=True)
    try:
        driver = instrument.simulated_driver
        names = list(driver.analog_channel_names())
        assert "XSC1_CH1" in names and "XSC1_CH2" in names and "XSC1_CH1_L" not in driver.profile["digital"]
        start = instrument.circuit_simulation.clock()
        ch1 = driver.sample_analog(names.index("XSC1_CH1"), start, 10_000, 200)
        assert np.max(np.abs(ch1)) < 5  # (its own GND: nothing, in raw counts)
        ch2 = driver.sample_analog(names.index("XSC1_CH2"), start, 10_000, 200)
        assert np.max(np.abs(ch2)) > 100  # (IN against OUT: not nothing)
    finally:
        instrument.close()


def test_the_voltage_range_follows_the_sources(catalog):
    from openscilab.circuits.source import _voltage_range

    assert _voltage_range(spice.emit(low_pass(), catalog.spec, catalog.models)) == 20.0  # (1 V sine at 1 kHz)
    mains = Schematic("Mains")
    mains.add_component("source.sine", 0, 0, "V", "V1", {"amplitude": "325 V", "frequency": "50 Hz"})
    mains.add_component("ground", 0, 60, "GND", "GND1")
    mains.add_wire([(0, 30), (0, 60)])
    assert _voltage_range(spice.emit(mains, catalog.spec, catalog.models)) == round(1.5 * 325)


def test_a_run_that_makes_no_progress_is_given_up():
    from openscilab.circuits.engines import process

    class Engine:
        errors = ["Warning: singular matrix:  check node n24", "Warning: source stepping failed"]

    class Simulation:
        state = "running"
        error = ""
        _engine = Engine()

        def clock(self):
            return 1.0

    child = process._Child(None, "")
    simulation = Simulation()
    child._watch_progress(simulation, points=True)
    child._progress_at = (1.0, time.monotonic() - process.STALL_SECONDS - 1)
    child._watch_progress(simulation, points=False)
    assert simulation.state == "error" and "source stepping failed" in simulation.error
