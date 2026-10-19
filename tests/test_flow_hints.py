"""Suggested values and checks of parameters (lab/hints.py) and the inspector editors that use them."""

from __future__ import annotations

import glob
import os

import pytest

from openscilab.lab import hints
from openscilab.lab.flow_files import load_flow
from openscilab.lab.model import Flow
from openscilab.lab.nodes.registry import Param, RegistryError, default_registry
from openscilab.lab.project import Project

DECODERS = os.path.isdir(os.path.join(os.path.dirname(__file__), "..", "decoders", "uart"))


def uno_flow() -> Flow:
    flow = Flow("t")
    flow.add_node("device.device", "uno", address="sim:uno")
    for node_id, type_name in (("pwm", "gpio.pwm"), ("write", "gpio.output"), ("monitor", "device.monitor"),
                               ("capture", "device.capture")):
        flow.add_node(type_name, node_id)
        flow.connect("uno.device", f"{node_id}.device")
    return flow


def warnings(flow: Flow) -> list[str]:
    return [str(problem) for problem in hints.check(flow, default_registry, hints.flow_devices(flow))]


# -------------------------------------------------------------------- devices
def test_what_a_simulated_board_has():
    uno = hints.device_hints("sim:uno")
    assert uno.title == "Simulated Arduino Uno"
    assert "D9" in uno.pins_that("PWM") and "D4" not in uno.pins_that("PWM")
    assert uno.analog == ["A0", "A1", "A2", "A3", "A4", "A5"] and "A0" in uno.channels
    assert uno.reserved["D0"].startswith("USB")
    assert hints.device_hints("sim:free").channels[:2] == ["D0", "D1"]
    assert hints.device_hints("rigol-sim:bridge").analog == ["CH1", "CH2", "CH3", "CH4"]
    assert hints.device_hints("sim:pico*2").channels[0].startswith("B1.")
    assert hints.device_hints("pico:/dev/none") is None  # not open: nothing known
    assert hints.device_hints("sim:nothing") is None


def test_an_unknown_suggestion_source_is_refused():
    with pytest.raises(RegistryError, match="unknown suggestion"):
        Param("pin", "str", suggest="wishes")


# ---------------------------------------------------------------- suggestions
def test_suggestions_follow_what_the_node_needs_of_its_device():
    flow = uno_flow()
    devices = hints.flow_devices(flow)

    def offered(node_id: str) -> dict:
        return hints.node_suggestions(flow, node_id, flow.spec(flow.nodes[node_id], default_registry), devices)

    assert offered("pwm")["pin"] == ["D3", "D5", "D6", "D9", "D10", "D11"]
    assert "D0" not in offered("write")["pin"]  # reserved for USB
    assert offered("monitor")["analog"] == ["A0", "A1", "A2", "A3", "A4", "A5"]
    assert offered("capture")["channels"][:2] == ["D2", "D3"] and "A0" in offered("capture")["channels"]
    flow.add_node("gpio.pwm", "loose")  # not wired yet: the only device of the flow
    assert offered("loose")["pin"] == offered("pwm")["pin"]
    flow.add_node("device.device", "other", address="sim:free")
    assert offered("loose") == {}  # two devices: which one is not known


def test_channels_and_columns_come_from_upstream():
    flow = Flow("t")
    flow.add_node("device.capture", "capture", channels=["D9", "D15"])
    flow.add_node("data.buffer", "buffer")
    flow.add_node("convert.channel", "channel")
    flow.connect("capture.capture", "buffer.in")
    flow.connect("buffer.out", "channel.in")
    assert hints.upstream_channels(flow, "channel") == ["D9", "D15"]  # through the buffer
    flow.add_node("data.table", "points", columns=["duty", "volts"])
    flow.add_node("report.chart", "diagram")
    flow.connect("points.table", "diagram.in")
    assert hints.upstream_columns(flow, "diagram") == ["duty", "volts"]


# --------------------------------------------------------------------- checks
def test_values_of_the_wrong_kind_are_found():
    flow = Flow("t")
    flow.add_node("device.capture", "capture", rate="5 ms", samples="many", pre=1.5)
    flow.add_node("control.compare", "compare", operator="about")
    flow.add_node("device.monitor", "monitor", pins="D2")
    found = warnings(flow)
    assert any("rate" in text and "Hz" in text for text in found)
    assert any("samples: 'many' is not a whole number" in text for text in found)
    assert any("pre: 1.5 is not a whole number" in text for text in found)
    assert any("operator: 'about' is not one of" in text for text in found)
    assert any("pins: 'D2' is not a list" in text for text in found)
    assert all(problem.severity == "warning" for problem in hints.check(flow, default_registry))


def test_pins_and_channels_the_device_does_not_have_are_found():
    flow = uno_flow()
    flow.nodes["pwm"].params["pin"] = "D4"
    flow.nodes["write"].params["pin"] = "D20"
    flow.nodes["monitor"].params["analog"] = ["A7"]
    flow.nodes["capture"].params["channels"] = ["D2", "CH9"]
    found = warnings(flow)
    assert any("pwm: pin: D4 of Simulated Arduino Uno cannot do PWM (pins that can: D3" in text for text in found)
    assert any("write: pin: Simulated Arduino Uno has no pin D20" in text for text in found)
    assert any("monitor: analog: Simulated Arduino Uno has no analog input A7" in text for text in found)
    assert any("capture: channels: Simulated Arduino Uno has no channel CH9" in text for text in found)
    flow.nodes["write"].params["pin"] = "D0"
    assert any("D0 of Simulated Arduino Uno is reserved (USB" in text for text in warnings(flow))


def test_an_upstream_column_that_is_not_there_is_found():
    flow = Flow("t")
    flow.add_node("data.table", "points", columns=["duty", "volts"])
    flow.add_node("report.chart", "diagram", x="duty", y="volts, amps")
    flow.connect("points.table", "diagram.in")
    assert warnings(flow) == ["diagram: y: the input has no column volts, amps (duty, volts)"]
    flow.nodes["diagram"].params["y"] = "volts"
    assert warnings(flow) == []


@pytest.mark.skipif(not DECODERS, reason="the sigrok decoders are not installed in ./decoders")
def test_decoder_channels_are_keys_of_the_decoder_and_channels_of_the_capture():
    flow = Flow("t")
    flow.add_node("device.capture", "capture", channels=["D9"])
    flow.add_node("decoder.uart", "uart", channels={"rx": "D3", "data": "D9"})
    flow.connect("capture.capture", "uart.in")
    spec = flow.spec(flow.nodes["uart"], default_registry)
    assert set(spec.param("channels").keys) >= {"rx", "tx"}
    assert hints.node_suggestions(flow, "uart", spec, {})["channels"] == ["D9"]
    assert any("channels: no data (keys:" in text for text in warnings(flow))
    flow.nodes["uart"].params["channels"] = {"rx": "D3"}
    assert warnings(flow) == ["uart: channels: the input has no channel D3 (D9)"]


def test_the_examples_and_templates_have_no_warnings():
    for path in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "examples", "**", "*.flow.yaml"),
                                 recursive=True)):
        root = Project.find(path)
        project = Project.open(root) if root else None
        flow = load_flow(path)
        devices = hints.flow_devices(flow, project.devices if project else {})
        assert hints.check(flow, default_registry, devices) == [], path


# ------------------------------------------------------------------ inspector
def test_the_inspector_offers_what_the_device_has(shell):
    from PySide6.QtWidgets import QComboBox

    document = shell.new_flow()
    document.add_node("device.device", (0, 0), "uno", address="sim:uno")
    document.add_node("gpio.pwm", (300, 0), "pwm")
    document.add_node("device.capture", (300, 200), "capture")
    document.scene.select_nodes(["pwm"])
    pin = document.inspector_widget().editors["pin"]
    # the pins that can do PWM are all there are: a choice, no free text
    assert isinstance(pin, QComboBox) and not pin.isEditable()
    assert [pin.itemText(i) for i in range(pin.count())][:3] == ["", "D3", "D5"]
    pin.setCurrentIndex(pin.findData("D9"))
    assert document.flow.nodes["pwm"].params["pin"] == "D9"

    document.scene.select_nodes(["capture"])
    channels = document.inspector_widget().editors["channels"]
    actions = {action.text(): action for action in channels.menu.actions()}
    actions["D2"].setChecked(True)
    document.inspector_widget().editors["channels"].menu.actions()[2].setChecked(True)  # D4 (a new inspector)
    assert document.flow.nodes["capture"].params["channels"] == ["D2", "D4"]
    node = document.flow.nodes["capture"]
    _inputs, outputs = document.flow.spec(node, document.registry).resolve_ports(node.params)
    assert {"D2", "D4"} <= {port.name for port in outputs}  # a port for each chosen channel


def test_a_wrong_pin_shows_at_the_node_and_in_the_inspector(shell):
    from PySide6.QtWidgets import QLabel

    document = shell.new_flow()
    document.add_node("device.device", (0, 0), "uno", address="sim:uno")
    document.add_node("gpio.pwm", (300, 0), "pwm", pin="D4")
    assert any("cannot do PWM" in str(problem) for problem in document.problems())
    assert document.scene.nodes["pwm"].problems
    document.scene.select_nodes(["pwm"])
    texts = [label.text() for label in document.inspector_widget().findChildren(QLabel)]
    assert any("cannot do PWM" in text for text in texts)
    assert not [problem for problem in document.problems() if problem.severity == "error"]  # it can still run


@pytest.mark.skipif(not DECODERS, reason="the sigrok decoders are not installed in ./decoders")
def test_the_decoder_channels_are_chosen_per_decoder_channel(shell):
    document = shell.new_flow()
    document.add_node("device.device", (0, 0), "sim", address="sim:free")
    document.add_node("device.capture", (300, 0), "capture", channels=["D9", "D15"])
    document.add_node("decoder.uart", (600, 0), "uart")
    document.connect_ports("capture.capture", "uart.in")
    document.scene.select_nodes(["uart"])
    editor = document.inspector_widget().editors["channels"]
    rx = editor.boxes["rx"]
    assert [rx.itemText(i) for i in range(rx.count())] == ["", "D9", "D15"]
    rx.setCurrentIndex(1)
    rx.activated.emit(1)
    assert document.flow.nodes["uart"].params["channels"] == {"rx": "D9"}


# ----------------------------------------------------------------- what counts
def test_a_parameter_is_locked_while_another_one_or_a_wire_sets_what_it_would():
    from openscilab.lab.nodes.registry import locked_reason

    def reason(type_name: str, name: str, params: dict, wired=()) -> str:
        spec = default_registry.get(type_name)
        defaults = {param.name: param.default for param in spec.params}
        return locked_reason(spec.param(name), params, defaults, list(wired))

    assert reason("report.check", "tolerance", {}) == "only with 'expected' set"
    assert reason("report.check", "tolerance", {"expected": 5}) == ""
    assert reason("control.compare", "value", {}, ["a", "b"]) == "set by the wire at 'b'"
    assert reason("control.compare", "tolerance", {"operator": "=="}) == "only with operator = within"
    assert reason("device.capture", "duration", {"samples": 64}) == "not with 'samples' set"
    assert reason("device.capture", "clock_edge", {}) == "only with 'clock' set"
    assert reason("signal.filter", "cutoff", {"kind": "moving_average"}).startswith("only with kind = lowpass")
    assert reason("data.logger", "flush", {"path": "run.lac"}) == "only when 'path' ends with .csv"
    assert reason("data.logger", "flush", {}) == ""  # (the default path is a .csv)
    assert reason("gpio.pulse", "period", {"count": 1}) == "only with count > 1"
    assert reason("gpio.pulse", "period", {"count": 3}) == ""
    assert reason("generator.pattern", "tracks", {}, ["capture"]) == "set by the wire at 'capture'"


def test_choices_that_come_from_the_flow_itself():
    flow = load_flow(os.path.join(os.path.dirname(__file__), "..", "examples", "library", "02-digital",
                                  "06-mega-loopback", "flows", "loopback.flow.yaml"))
    devices = hints.flow_devices(flow, {"mega": "sim:mega"})

    def offered(node_id: str) -> dict:
        return hints.node_suggestions(flow, node_id, flow.spec(flow.nodes[node_id], default_registry), devices)

    # the channels of the states a state capture sends; the clocks of its device; pattern outputs only
    assert offered("bit")["bit"] == ["D35"]
    assert offered("states")["clock"][:4] == ["D53", "D52", "D51", "D50"] and "D35" not in offered("states")["clock"]
    assert offered("pattern")["output"] == ["PATTERN"]
    assert warnings_of(flow, devices) == []
    flow.nodes["states"].params["clock"] = "D35"
    assert any("cannot clock the state mode" in text for text in warnings_of(flow, devices))
    # the keys of a state machine's states, the inputs of a Python node
    flow.add_node("control.state_machine", "light", states={"red": {}, "green": {}}, initial="red")
    flow.add_node("control.python", "py", inputs=["a", "b"], pulled=["c"])
    assert offered("light")["initial"] == ["red", "green"] and offered("py")["pulled"] == ["a", "b"]
    assert any("c is not in 'inputs'" in text for text in warnings_of(flow, devices))


def warnings_of(flow: Flow, devices: dict) -> list[str]:
    return [str(problem) for problem in hints.check(flow, default_registry, devices)]
