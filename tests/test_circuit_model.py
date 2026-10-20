"""Circuits without Qt: the model and its file, values, the library of openSciLab's own components,
the nets a schematic connects and the netlist it gives ngspice."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from openscilab.circuits import CircuitError, library, spice, values, yaml_io
from openscilab.circuits.catalog import Catalog
from openscilab.circuits.catalog.models import ModelLibrary, standard_library
from openscilab.circuits.model import Schematic, drag_wires, orthogonal, simplify
from openscilab.circuits.nets import extract
from openscilab.circuits.symbols import arc_angles, transform_angle, transform_point


@pytest.fixture(scope="module")
def catalog():
    return Catalog(kicad=[], models=[], user=[], use_cache=False)


def low_pass(r: str = "1k", c: str = "159n") -> Schematic:
    """V1 - R1 - OUT - C1 - ground, a probe on OUT."""
    s = Schematic("RC")
    s.add_component("source.sine", 0, 0, "V", "V1", {"amplitude": "1 V", "frequency": "1 kHz"})
    s.add_component("resistor", 60, -30, "R", "R1", {"value": r})
    s.add_component("capacitor", 120, 0, "C", "C1", {"value": c}, rotation=90)
    s.add_component("ground", 0, 60, "GND", "GND1")
    s.add_component("ground", 120, 60, "GND", "GND2")
    s.add_component("probe.v", 120, -30, "P", "P1", {"name": "OUT"})
    for points in ([(0, -30), (30, -30)], [(90, -30), (120, -30), (120, -20)], [(0, 30), (0, 60)],
                   [(120, 20), (120, 60)]):
        s.add_wire(points)
    return s


# ------------------------------------------------------------------ values
@pytest.mark.parametrize("text, number", [("10k", 1e4), ("4.7 uF", 4.7e-6), ("1meg", 1e6), ("4k7", 4700),
                                          ("2R2", 2.2), ("1u5", 1.5e-6), ("100 mV", 0.1), ("10 kΩ", 1e4),
                                          ("1Meg", 1e6), ("5", 5.0)])
def test_values_as_people_write_them(text, number):
    assert values.parse(text) == pytest.approx(number)


def test_netlists_get_plain_numbers():
    assert values.spice_number("10k") == "10000"
    assert values.spice_number("4.7 uF") == "4.7e-06"
    assert values.spice_number("2N3904") == "2N3904"  # (a model name stays)
    with pytest.raises(ValueError):
        values.parse("lots")


# ------------------------------------------------------------------ geometry
def test_rotation_and_mirroring_of_points():
    assert transform_point(10, 0, 90) == (0, -10)  # counter-clockwise on the screen
    assert transform_point(10, 0, 180) == (-10, 0)
    assert transform_point(10, 0, 0, mirror=True) == (-10, 0)
    assert transform_angle(0, 90) == 270 and transform_angle(0, 0, mirror=True) == 180


def test_an_arc_through_three_points():
    cx, cy, r, start, span = arc_angles((10, 0), (0, -10), (-10, 0))
    assert (cx, cy, r) == pytest.approx((0, 0, 10)) and start == pytest.approx(0) and span == pytest.approx(180)


def test_wires_are_simplified_and_routed():
    assert simplify([(0, 0), (0, 0), (10, 0), (20, 0), (20, 10)]) == [(0, 0), (20, 0), (20, 10)]
    assert orthogonal((0, 0), (20, 10)) == [(0, 0), (20, 0), (20, 10)]
    assert orthogonal((0, 0), (20, 10), horizontal_first=False) == [(0, 0), (0, 10), (20, 10)]


def test_wires_follow_moved_pins():
    s = Schematic()
    s.add_wire([(0, 0), (40, 0), (40, 40)])
    drag_wires(s, {(0, 0): (0, 20)})
    assert list(s.wires.values())[0].points == [(0, 20), (40, 20), (40, 40)]


# ---------------------------------------------------------------- the file
def test_the_file_reads_back_as_written(tmp_path):
    s = low_pass()
    s.analyses = {"ac": {"start": "10 Hz"}}
    s.settings = {"vcc": "3.3 V"}
    s.extra = {"layout": {"zoom": 2}}
    path = tmp_path / "rc.circuit.yaml"
    yaml_io.save(s, str(path))
    again = yaml_io.load(str(path))
    assert yaml_io.to_data(again) == yaml_io.to_data(s)
    assert yaml_io.roundtrip_problem(s) == ""
    text = path.read_text()
    assert "  R1: {type: resistor, at: [60, -30], params: {value: 1k}}" in text


def test_a_bad_file_says_why():
    with pytest.raises(CircuitError, match="needs a type"):
        yaml_io.loads("components: {R1: {at: [0, 0]}}")
    with pytest.raises(CircuitError, match="newer openSciLab"):
        yaml_io.loads("version: 99")


@settings(max_examples=40, deadline=None)
@given(st.text(min_size=1, max_size=20), st.dictionaries(st.sampled_from(["value", "name", "model"]),
                                                         st.text(max_size=12), max_size=3))
def test_any_text_reads_back(name, params):
    s = Schematic(name)
    s.add_component("resistor", 0, 0, "R", "R1", params)
    assert yaml_io.to_data(yaml_io.loads(yaml_io.dumps(s))) == yaml_io.to_data(s)


# ----------------------------------------------------------------- library
def test_the_own_library_is_complete():
    own = library.builtin()
    assert not library.builtin_problems()
    assert {"resistor", "capacitor", "inductor", "source.sine", "diode", "led", "npn", "nmos", "opamp", "ground",
            "probe.v", "gate.nand", "switch", "potentiometer", "timer555"} <= set(own)
    for spec in own.values():
        assert spec.title and spec.description, spec.type
        for param in spec.params:  # (docs/design.md: every parameter has a label and a description)
            assert param.label and param.description, f"{spec.type}.{param.name}"
        assert spec.symbol.pins or spec.role, spec.type
        numbers = [pin.number for pin in spec.symbol.pins]
        assert len(numbers) == len(set(numbers)), spec.type
        off_grid = [pin.number for pin in spec.symbol.pins if pin.x % 10 or pin.y % 10]
        assert not off_grid, f"{spec.type}: pins {off_grid} are not on the grid (wires could not reach them)"


def test_templates_render_with_nets_and_values():
    text = library.render("{ref:R}a {pin.1} {pin.2} {{max(value*position, 1e-3)}}", "RV1",
                          {"1": "a", "2": "w"}, {"value": "10k", "position": "0.25"})
    assert text == "RV1a a w 2500"
    with pytest.raises(CircuitError):
        library.evaluate("__import__('os')", {})


def test_model_libraries_resolve_what_they_need():
    models = ModelLibrary()
    models.add_text(".subckt AMP a b out\nX1 a b out INNER\n.ends\n.subckt INNER a b c\nD1 a b DX\n.ends\n"
                    ".model DX D(Is=1e-14)\n")
    found, missing = models.resolve(["amp", "nothing"])
    assert [d.name for d in found] == ["DX", "INNER", "AMP"] and missing == ["nothing"]
    standard = standard_library()
    for name in ("1N4148", "2N3904", "LM358", "NE555", "LED_RED", "IRLZ44N", "OPAMP"):
        assert name in standard, name


# -------------------------------------------------------------------- nets
def test_the_nets_of_a_schematic(catalog):
    netlist = extract(low_pass(), catalog.spec)
    assert netlist.net_of("R1", "2") == "OUT" == netlist.net_of("C1", "2")  # (named by the probe)
    assert netlist.net_of("V1", "2") == "0" == netlist.net_of("C1", "1")
    assert netlist.net_of("V1", "1") == netlist.net_of("R1", "1") == "N1"
    assert not netlist.problems


def test_t_junctions_connect_crossings_do_not(catalog):
    s = Schematic()
    s.add_wire([(0, 0), (100, 0)])
    s.add_wire([(50, 0), (50, 50)])  # a T
    s.add_wire([(20, -20), (20, 20)])  # crosses the first without a corner
    netlist = extract(s, catalog.spec)
    nets = netlist.wire_net
    assert nets["w1"] == nets["w2"] and nets["w3"] != nets["w1"]
    assert (50, 0) in netlist.junctions and (20, 0) not in netlist.junctions


def test_labels_name_and_join_nets(catalog):
    s = Schematic()
    s.add_component("resistor", 0, 0, "R", "R1")
    s.add_component("resistor", 200, 0, "R", "R2")
    s.add_label("SIG", 30, 0)
    s.add_label("SIG", 170, 0)
    netlist = extract(s, catalog.spec)
    assert netlist.net_of("R1", "2") == "SIG" == netlist.net_of("R2", "1")


def test_open_pins_are_problems(catalog):
    s = Schematic()
    s.add_component("resistor", 0, 0, "R", "R1")
    problems = extract(s, catalog.spec).problems
    assert {(p.item, p.pin) for p in problems} == {("R1", "1"), ("R1", "2")}
    assert all("not connected" in p.text for p in problems)


def test_unknown_components_are_errors(catalog):
    s = Schematic()
    s.add_component("kicad:Nowhere:Thing", 0, 0)
    assert any(p.severity == "error" and "not known" in p.text for p in extract(s, catalog.spec).problems)


# ----------------------------------------------------------------- netlist
def test_the_netlist_of_a_low_pass(catalog):
    circuit = spice.emit(low_pass(), catalog.spec, catalog.models, analysis="ac")
    assert not circuit.errors
    lines = circuit.lines
    assert "R1 N1 OUT 1000" in lines and "C1 0 OUT 1.59e-07" in lines
    assert any(line.startswith("V1 N1 0 DC 0 AC 1 SIN(") for line in lines)
    assert lines[-2].startswith(".ac dec 50") and lines[-1] == ".end"
    assert [(p.name, p.vector) for p in circuit.probes] == [("OUT", "v(OUT)")]


def test_models_go_into_the_netlist(catalog):
    s = Schematic()
    s.add_component("diode", 0, 0, "D", "D1", {"value": "1N4148"})
    s.add_component("ground", 30, 20, "GND", "GND1")
    s.add_component("source.dc", -60, 30, "V", "V1")
    s.add_wire([(30, 0), (30, 20)])
    s.add_wire([(-30, 0), (-60, 0)])
    s.add_component("ground", -60, 60, "GND", "GND2")
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert any(line.startswith(".model 1N4148 D(") for line in circuit.lines)
    s.components["D1"].params["value"] = "NOSUCH"
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert any("NOSUCH is in no model library" in p.text for p in circuit.errors)


def test_digital_parts_meet_analog_nets_through_bridges(catalog):
    s = Schematic()
    s.add_component("gate.not", 0, 0, "U", "U1")
    s.add_component("source.square", -40, 30, "V", "V1")
    s.add_component("ground", -40, 60, "GND", "GND1")
    s.add_component("probe.logic", 40, 0, "P", "P1", {"name": "Y"})
    s.add_wire([(-40, 0), (-40, 0)])
    circuit = spice.emit(s, catalog.spec, catalog.models)
    text = circuit.text
    assert "adc_bridge" in text and "dac_bridge" in text and "d_inverter" in text
    assert circuit.probes[0].kind == "logic"


def test_supplies_give_their_voltage(catalog):
    s = Schematic()
    s.add_component("power", 0, 0, "PWR", "PWR1", {"net": "VCC", "voltage": "3.3 V"})
    s.add_component("resistor", 0, 30, "R", "R1", rotation=90)
    s.add_component("ground", 0, 60, "GND", "GND1")
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert "V_VCC VCC 0 DC 3.3" in circuit.lines


def test_changes_while_running_become_alter_commands():
    assert spice.alterations(["R1 a b 1000"], ["R1 a b 2000"]) == ["alter R1 = 2000"]
    assert spice.alterations(["V1 a 0 DC 5"], ["V1 a 0 DC 3"]) == ["alter V1 dc = 3"]
    assert spice.alterations(["R1 a b 1000"], ["R1 a c 1000"]) is None  # (another net: start again)
    assert spice.alterations(["Q1 c b e 2N3904"], ["Q1 c b e BC547"]) is None
    # the gains of controlled sources (a variac's wiper)
    assert spice.alterations(["ET1 x 0 a 0 0.5"], ["ET1 x 0 a 0 0.8"]) == ["alter ET1 gain = 0.8"]
    assert spice.alterations(["FT1 a 0 VT1s 0.5"], ["FT1 a 0 VT1s 0.8"]) == ["alter FT1 gain = 0.8"]
    assert spice.alterations(["ET1 x 0 a 0 0.5"], ["ET1 x 0 b 0 0.5"]) is None


def test_parts_of_power_electronics(catalog):
    s = Schematic("Parts")
    s.add_component("switch.voltage", 0, 0, "S", "S1", {"threshold": "6 V", "ron": "0.5"})
    s.add_component("rectifier.bridge", 200, 0, "BR", "BR1")
    s.add_component("source.triangle", 400, 0, "V", "V1", {"low": "0.5 V", "high": "5 V", "frequency": "1 kHz"})
    s.add_component("transformer.variable", 600, 0, "T", "T1", {"output": "69 V", "input": "230 V"})
    s.add_component("source.sine_reference", 800, 0, "X", "X1", {"frequency": "60 Hz"})
    s.add_component("terminal", 900, 0, "J", "J1", {"name": "DC+"})
    lines = spice.emit(s, catalog.spec, catalog.models).lines
    assert any(line.startswith("S1 ") and line.endswith(" osl_sw_S1") for line in lines)
    assert any(line.startswith(".model osl_sw_S1 SW(Ron=0.5 ") and "Vt=6" in line for line in lines)
    assert sum(line.startswith("DBR1") and line.endswith(" 1N4007") for line in lines) == 4
    assert any(line.startswith("V1 ") and "PULSE(0.5 5 0 0.0004995 0.0004995 1e-06 0.001)" in line for line in lines)
    gains = [line for line in lines if line.startswith(("ET1 ", "FT1 "))]
    assert len(gains) == 2 and all(line.endswith(" 0.3") for line in gains)  # (output / rated input)
    assert any(line.startswith("BX1 ") and "sin(6.28318531*60*time)" in line for line in lines)


def test_knobs_turn_through_the_range_of_their_part(catalog):
    assert catalog.spec("transformer.variable").controls[0].bounds({"input": "230 V"}) == (0.0, 230.0)
    assert catalog.spec("potentiometer").controls[0].bounds({}) == (0.0, 1.0)


def test_a_terminal_names_what_is_taken_off_there(catalog):
    s = low_pass()
    s.remove(["P1"])
    s.add_component("terminal", 120, -30, "J", "J1", {"name": "DC+"})
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert [(probe.name, probe.kind) for probe in circuit.probes] == [("DC+", "voltage")]
    assert circuit.probes[0].vector == "v(DCp)"  # (a net name SPICE takes)


def test_settings_for_switching_circuits(catalog):
    s = low_pass()
    s.settings.update({"tolerance": "0.01", "start": "power on"})
    circuit = spice.emit(s, catalog.spec, catalog.models, analysis="tran")
    assert circuit.power_on and any(line.startswith(".tran") and line.endswith(" uic") for line in circuit.lines)
    assert any(line.startswith(".options") and "reltol=0.01" in line for line in circuit.lines)
    settled = spice.emit(low_pass(), catalog.spec, catalog.models, analysis="tran")
    assert not settled.power_on and not any(line.endswith(" uic") for line in settled.lines)


def test_a_part_with_all_its_pins_on_one_net_is_shorted(catalog):
    s = low_pass()
    s.add_wire([(30, -30), (90, -30)])  # (straight past R1: both its pins on one wire)
    problems = extract(s, catalog.spec).problems
    assert any(problem.item == "R1" and "shorted" in problem.text for problem in problems)
    assert not any("shorted" in problem.text for problem in extract(low_pass(), catalog.spec).problems)


KICAD_R = """(kicad_symbol_lib (version 20251024) (generator "test")
  (symbol "R" (pin_numbers (hide yes)) (pin_names (offset 0))
    (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
    (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
    (property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
    (symbol "R_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54) (stroke (width 0.254)) (fill (type none))))
    (symbol "R_1_1"
      (pin passive line (at 0 3.81 270) (length 1.27) (name "" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 0 -3.81 90) (length 1.27) (name "" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))))))
"""
KICAD_R10K = """(kicad_symbol_lib (version 20251024) (generator "test")
  (symbol "R_10k" (extends "R")
    (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 0 0 90) (effects (font (size 1.27 1.27))))))
"""


@pytest.mark.parametrize("as_folder", [False, True])
def test_kicad_libraries_as_files_and_folders(tmp_path, as_folder):
    if as_folder:  # (KiCad 9+ in its sources: a folder of one file per symbol)
        folder = tmp_path / "Mine.kicad_symdir"
        folder.mkdir()
        (folder / "R.kicad_sym").write_text(KICAD_R)
        (folder / "R_10k.kicad_sym").write_text(KICAD_R10K)
    else:
        derived_symbol = KICAD_R10K.split('(generator "test")', 1)[1].rstrip()[:-1]  # (its symbol, without the end)
        (tmp_path / "Mine.kicad_sym").write_text(KICAD_R.rstrip()[:-1] + derived_symbol + ")\n")
    catalog = Catalog(kicad=[str(tmp_path)], models=[], user=[], use_cache=False)
    assert list(catalog.kicad_libraries()) == ["Mine"]
    derived = catalog.spec("kicad:Mine:R_10k")
    assert derived is not None and derived.params[0].default == "10k" and derived.model is not None
    pins = {pin.number: (pin.x, pin.y, pin.angle) for pin in derived.symbol.pins}
    assert pins == {"1": (0, -30, 90), "2": (0, 30, 270)}  # (mm to scene units, y turned down)
    catalog.build_index()
    assert {e.type for e in catalog.entries() if e.source == "kicad"} == {"kicad:Mine:R", "kicad:Mine:R_10k"}
    assert catalog.search("R_10k")[0].type == "kicad:Mine:R_10k"


def test_an_oscilloscope_measures_its_wired_channels(catalog):
    s = low_pass()
    s.add_component("probe.scope", 400, 0, "XSC", "XSC1")
    from openscilab.circuits.nets import placed_pins

    pins = {pin.name: point for pin, point in placed_pins(s.components["XSC1"], catalog.spec("probe.scope"))}
    s.add_wire([(120, -30), pins["CH1"]])
    s.add_wire([pins["GND"], (pins["GND"][0], 60), (120, 60)])
    circuit = spice.emit(s, catalog.spec, catalog.models)
    scope = [p for p in circuit.probes if p.kind == "scope"]
    assert [(p.channel, p.vector, p.reference) for p in scope] == [("CH1", "v(OUT)", "")]
    assert not [p for p in circuit.problems if p.item == "XSC1"]  # (its other channels need no wire)


def test_the_trigger_and_the_picture_of_an_oscilloscope():
    import numpy as np

    from openscilab.ui.circuits.scope import ROLL_FROM, acquire, find_trigger, measure, settings_of

    times = np.arange(0, 0.01, 1e-6)
    square = np.where((times * 1000) % 1 < 0.5, 5.0, 0.0)  # 1 kHz, 0 to 5 V
    assert find_trigger(times, square, 2.5, "rising", 0.0095, 0.0) == pytest.approx(0.009, abs=2e-6)
    assert find_trigger(times, square, 2.5, "falling", 0.0092, 0.0) == pytest.approx(0.0085, abs=2e-6)

    class Simulation:
        def course(self, vector):
            return times, square

    from openscilab.circuits.spice import Probe

    probe = Probe("XSC1", "XSC1_CH1", "scope", "v(a)", "a", "CH1")
    picture = acquire(Simulation(), [probe], {"time_div": "200 us", "trigger": "CH1", "level": "2.5 V"})
    assert picture.triggered and picture.span == pytest.approx(2e-3)
    assert picture.trigger - picture.start == pytest.approx(1e-3)  # (the trigger in the middle)
    found = measure(picture.channels["CH1"], picture.span)
    assert found["vpp"] == pytest.approx(5.0) and found["frequency"] == pytest.approx(1000, rel=0.05)
    untriggered = acquire(Simulation(), [probe], {"time_div": "200 us", "level": "9 V"})
    assert not untriggered.triggered  # (auto: it runs on)
    rolling = acquire(Simulation(), [probe], {"time_div": "1 ms", "mode": "roll"})
    assert rolling.rolling and not rolling.triggered and rolling.start == pytest.approx(times[-1] - 0.01)
    slow = settings_of({"time_div": "200 ms"})
    assert slow["mode"] == "auto" and slow["time_div"] >= ROLL_FROM  # (auto rolls from 100 ms per division)


def test_comments_and_groups_read_back_as_written():
    s = low_pass()
    s.components["R1"].comment = "1 kΩ: the corner at 1 kHz"
    wire_id = list(s.wires)[1]
    s.wires[wire_id].comment = "to the output"
    s.add_label("OUT", 200, -30).comment = "measured here"
    group = s.add_group(["R1", "C1", wire_id], "Filter", "first order\nno load")
    group.color = "blue"
    text = yaml_io.dumps(s)
    assert "comment: to the output" in text and "members: [R1, C1, w2]" in text
    again = yaml_io.loads(text)
    assert again.components["R1"].comment == "1 kΩ: the corner at 1 kHz"
    assert again.wires["w2"].comment == "to the output" and again.labels["l1"].comment == "measured here"
    [read] = again.groups.values()
    assert (read.name, read.comment, read.members) == ("Filter", "first order\nno load", ["R1", "C1", "w2"])
    assert read.color == "blue"
    assert yaml_io.roundtrip_problem(again) == ""
    # a member leaves its group when it goes; a group without members goes
    again.remove(["R1", "C1", "w2"])
    assert not again.groups
    # renamed, it stays in its group; grouped again, it leaves the first group
    s.rename_component("R1", "R9")
    assert s.groups[group.id].members[0] == "R9"
    s.add_group(["R9"], "Alone")
    assert s.groups[group.id].members == ["C1", wire_id] and len(s.groups) == 2


# ------------------------------------------------------------ review fixes
def test_two_probes_of_one_name_do_not_join_their_nets(catalog):
    s = Schematic("Two outputs")
    s.add_component("source.dc", 0, 0, "V", "V1", {"value": "5 V"})
    s.add_component("source.dc", 200, 0, "V", "V2", {"value": "3 V"})
    s.add_component("ground", 0, 60, "GND", "GND1")
    s.add_component("ground", 200, 60, "GND", "GND2")
    s.add_component("terminal", 0, -60, "J", "J1", {"name": "OUT"})
    s.add_component("terminal", 200, -60, "J", "J2", {"name": "OUT"})
    for points in ([(0, -30), (0, -60)], [(200, -30), (200, -60)], [(0, 30), (0, 60)], [(200, 30), (200, 60)]):
        s.add_wire(points)
    netlist = extract(s, catalog.spec)
    assert netlist.net_of("V1", "1") == "OUT" and netlist.net_of("V2", "1") not in ("OUT", None)
    assert any("named OUT already" in problem.text and problem.item == "J2" for problem in netlist.problems)
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert any(line.startswith("V1 OUT 0") for line in circuit.lines)
    assert not any(line.startswith("V2 OUT 0") for line in circuit.lines)
    assert [probe.name for probe in circuit.probes] == ["OUT", "OUT"]  # (both channels keep their name)


def test_a_supply_without_parameters_is_on_its_default_net(catalog):
    s = Schematic("Rail")
    s.add_component("power", 0, 0, "PWR", "PWR1")  # (no params: the default net VCC)
    s.add_component("resistor", 0, 30, "R", "R1", rotation=90)
    s.add_label("VCC", 100, 0)
    s.add_wire([(0, 0), (100, 0)])
    netlist = extract(s, catalog.spec)
    assert netlist.net_of("R1", "2") == "VCC" and netlist.net_of("PWR1", "1") == "VCC"


def test_the_noise_output_is_a_net_of_the_circuit(catalog):
    s = low_pass()
    s.analyses["noise"] = {"output": "OUT", "source": "V1"}
    lines = spice.emit(s, catalog.spec, catalog.models, analysis="noise").lines
    assert any(line.startswith(".noise v(OUT) V1") for line in lines)
    s.analyses["noise"]["output"] = "nowhere"
    circuit = spice.emit(s, catalog.spec, catalog.models, analysis="noise")
    assert any("names no net" in problem.text for problem in circuit.problems)


def test_a_wire_corner_with_another_wire_is_a_junction(catalog):
    s = Schematic("T")
    s.add_wire([(0, 0), (50, 0), (50, 50)])
    s.add_wire([(50, 0), (100, 0)])
    assert extract(s, catalog.spec).junctions == [(50, 0)]


def test_model_libraries_with_continuations_and_nested_subcircuits():
    from openscilab.circuits.catalog.models import parse

    found = parse(".model QX NPN(IS=1e-14\n* a comment between\n+ BF=200)\n"
                  ".subckt OUTER a b\nX1 a b INNER\n.subckt INNER c d\nR1 c d 1k\n.ends\nDQ a b DM\n.model DM D(Is=1n)\n.ends\n")
    by_name = {definition.name: definition for definition in found}
    assert "BF=200)" in by_name["QX"].text and by_name["QX"].text.count("(") == by_name["QX"].text.count(")")
    assert by_name["OUTER"].lines[-1].strip() == ".ends" and "R1 c d 1k" in by_name["OUTER"].text
    assert "INNER" not in by_name["OUTER"].uses and "DM" not in by_name["OUTER"].uses


def test_inferred_regulators_read_the_voltage_from_the_suffix():
    from openscilab.circuits.catalog.infer import _REGULATOR

    def voltage(name):
        match = _REGULATOR.search(name)
        groups = match.groups() if match else ()
        if match and groups[0]:
            return float(f"{groups[0]}.{groups[1]}")
        if match and groups[2]:
            return float(groups[2])
        return float(groups[3]) if match and groups[3] else None

    assert voltage("LM2940-5.0") == 5.0 and voltage("LM2931-3.3") == 3.3 and voltage("LM7805") == 5.0
    assert voltage("L7812") == 12.0 and voltage("AMS1117-3.3") == 3.3


def test_a_part_with_its_own_model_names_it_directly():
    from openscilab.circuits.catalog.infer import _own_model

    template, params = _own_model("{ref:D} {pin.1} {pin.2} {value}", "{ref}_m", (".model {ref}_m D(Is=1n)",))
    assert template == "{ref:D} {pin.1} {pin.2} {ref}_m" and params == ()
    rendered = library.render(template, "D1", {"1": "a", "2": "k"}, {})
    assert rendered == "D1 a k D1_m"
    template, params = _own_model("{ref:D} {pin.1} {pin.2} {value}", "1N4148", ())
    assert "{value}" in template and params[0].default == "1N4148"


def test_pwl_sources_repeat_and_sources_alter_only_dc():
    pwl = spice.render if hasattr(spice, "render") else library.render
    assert pwl("{ref:V} a b PWL({points}){repeat=yes? r=0}", "V1", {}, {"points": "0 0 1m 5", "repeat": "yes"}) \
        .endswith(") r=0")
    assert spice.alterations(["V1 a 0 DC 5 AC 1"], ["V1 a 0 AC 5 AC 1"]) is None
    assert spice.alterations(["V1 a 0 DC 5 AC 1"], ["V1 a 0 DC 3 AC 1"]) == ["alter V1 dc = 3"]


def test_both_ends_of_a_wire_follow_and_groups_forget_gone_wires():
    s = Schematic("Wires")
    s.add_component("resistor", 0, 0, "R", "R1")
    s.add_component("resistor", 100, 0, "R", "R2")
    wire = s.add_wire([(0, 0), (100, 0)])
    drag_wires(s, {(0, 0): (0, 20), (100, 0): (120, 0)})
    assert wire.points[0] == (0, 20) and wire.points[-1] == (120, 0)  # (both ends moved, a corner between)
    # a wire that collapses leaves its group; a new wire does not inherit its id
    s = Schematic("Collapse")
    short = s.add_wire([(0, 0), (20, 0)])
    group = s.add_group([short.id], "G")
    drag_wires(s, {(20, 0): (0, 0)})
    assert short.id not in s.wires and not s.groups  # (the group had no other member)
    s2 = Schematic("Collapse 2")
    kept = s2.add_wire([(0, 50), (50, 50)])
    short = s2.add_wire([(0, 0), (20, 0)])
    group = s2.add_group([kept.id, short.id], "G")
    drag_wires(s2, {(20, 0): (0, 0)})
    assert s2.groups[group.id].members == [kept.id]
    assert s2.add_wire([(0, 100), (50, 100)]).id != short.id or short.id not in s2.groups[group.id].members


def test_references_cannot_look_like_wire_or_label_ids():
    s = Schematic("Ids")
    with pytest.raises(CircuitError):
        s.add_component("resistor", 0, 0, "R", "w2")
    s.add_component("resistor", 0, 0, "R", "R1")
    with pytest.raises(CircuitError):
        s.rename_component("R1", "l1")
    with pytest.raises(CircuitError):
        yaml_io.loads("circuit: X\ncomponents:\n  g1: {type: resistor, at: [0, 0]}\n")
    assert yaml_io.loads("circuit: X\ncomponents: {}\nwires: []\ngroups:\n"
                         "  - {name: A, color: orange, members: [R9]}\n").groups == {}


def test_a_shorted_component_is_left_out_of_the_netlist(catalog):
    s = low_pass()
    s.add_component("resistor", 300, 300, "R", "R9")
    s.add_wire([(270, 300), (330, 300)])  # (both pins on one net that leads nowhere)
    circuit = spice.emit(s, catalog.spec, catalog.models)
    assert not any(line.startswith("R9 ") for line in circuit.lines)
    assert any(problem.item == "R9" and "shorted" in problem.text for problem in circuit.problems)
    assert not circuit.errors


def test_a_plugin_brings_components_of_a_physics_of_its_own(tmp_path):
    from openscilab.circuits.library import PLUGIN_FOLDERS, builtin, register_folder

    (tmp_path / "pipes.yaml").write_text(
        "- type: pipes.valve\n  title: Valve\n  category: Pipes\n  prefix: VL\n  physics: hydraulic\n"
        "  symbol:\n    shapes: [{line: [-10, 0, 10, 0]}]\n"
        "    pins: [{number: a, at: [-20, 0], angle: 0, length: 10}, {number: b, at: [20, 0], angle: 180, length: 10}]\n"
        "  params: [{name: R, label: Resistance, default: '', kind: text}]\n")
    try:
        register_folder(str(tmp_path))
        valve = builtin()["pipes.valve"]
        assert valve.physics == "hydraulic" and valve.simulated  # (its plugin simulates it)
        assert builtin()["resistor"].physics == ""  # (an electrical part)
    finally:
        PLUGIN_FOLDERS.remove(str(tmp_path))
        register_folder.__globals__["_builtin"] = None
