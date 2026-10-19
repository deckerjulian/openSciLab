"""A node's parameters in a window of their own (a double-click), big text fields for mappings and
long lists, and YAML read as version 1.2 (``on``/``off`` stay words: the state machine)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox

from openscilab.lab import yaml_io
from openscilab.ui.flow.inspector import YamlEdit

MACHINE = {"red": {"enter": {"red": 1, "green": 0}, "on": [{"after": "1 s", "to": "green"}]},
           "green": {"enter": {"red": 0, "green": 1}, "on": [{"after": "1 s", "to": "off"}]},
           "off": {"enter": {"red": 0, "green": 0}}}


def settle(times: int = 10) -> None:
    for _ in range(times):
        QApplication.processEvents()


def test_yaml_keeps_on_and_off_as_words():
    assert yaml_io.safe_load("{on: 1, off: 2, yes: 3, a: true, b: False}") == {
        "on": 1, "off": 2, "yes": 3, "a": True, "b": False}
    flow = yaml_io.loads("flow: t\nnodes:\n  m: {type: control.state_machine, initial: red, states: "
                         "{red: {on: [{after: 1 s, to: 'off'}]}, off: {}}}\nedges: []\n")
    assert flow.nodes["m"].params["states"] == {"red": {"on": [{"after": "1 s", "to": "off"}]}, "off": {}}
    again = yaml_io.loads(yaml_io.dumps(flow))  # (written so that YAML 1.1 reads it the same way too)
    assert again.nodes["m"].params == flow.nodes["m"].params
    import yaml

    assert yaml.safe_load(yaml_io.dumps(flow))["nodes"]["m"]["states"]["red"]["on"]


def test_the_state_machine_runs_through_its_states_and_says_what_is_wrong():
    from openscilab.lab.engine import Engine
    from openscilab.lab.model import Flow

    flow = Flow("t")
    flow.add_node("control.state_machine", "light", states=MACHINE, initial="red")
    states = []
    engine = Engine(flow, mode="virtual")
    engine.subscribe(lambda event: event.kind == "value" and event.port == "state" and states.append(event.value))
    assert engine.run(timeout=10).ok and states == ["red", "green", "off"]
    broken = Flow("t")
    broken.add_node("control.state_machine", "light", states={"red": {True: [{"after": "1 s", "to": "red"}]}},
                    initial="red")
    result = Engine(broken, mode="virtual").run(timeout=10)
    assert not result.ok and "'enter' and 'on'" in result.error


def test_the_states_of_a_machine_are_a_table_and_yaml(shell):
    from openscilab.ui.flow.editors import StatesEditor

    document = shell.new_flow()
    document.add_node("control.state_machine", (0, 0), "light", states=MACHINE, initial="red")
    document.scene.select_nodes(["light"])
    settle()
    inspector = document.inspector_widget()
    states = inspector.editors["states"]
    # the initial state is one of the states: a choice of them
    initial = inspector.editors["initial"]
    assert isinstance(states, StatesEditor) and isinstance(initial, QComboBox) and not initial.isEditable()
    assert [initial.itemText(i) for i in range(initial.count())][1:] == list(MACHINE)
    # a state a row: its name, what it sets, its ways out
    assert states.table.rowCount() == len(MACHINE) and states.table.item(0, 0).text() == "red"
    assert "after 1 s →" in states.table.item(0, 2).text()
    states.table.item(0, 2).setText(states.table.item(0, 2).text().replace("1 s", "2 s"))
    assert document.flow.nodes["light"].params["states"]["red"]["on"][0]["after"] == "2 s"
    # the YAML view: what the node stores, edited as text
    yaml_edit = states.yaml
    assert isinstance(yaml_edit, YamlEdit) and "after: 2 s" in yaml_edit.toPlainText()
    yaml_edit.setPlainText(yaml_edit.toPlainText().replace("2 s", "3 s"))
    yaml_edit.commit()
    assert document.flow.nodes["light"].params["states"]["red"]["on"][0]["after"] == "3 s"
    states.table.item(0, 2).setText("nonsense")
    assert "write 'after 1 s" in states.message.text()


def test_a_double_click_opens_the_editor_window(shell):
    document = shell.new_flow()
    document.add_node("control.state_machine", (0, 0), "light", states=MACHINE, initial="red")
    document.scene.node_double_clicked("light")
    settle()
    editor = document._node_editors["light"]
    assert editor.isVisible() and "State machine" in editor.windowTitle()
    states = editor.inspector.editors["states"]
    assert states.table.rowCount() == len(MACHINE)
    initial = editor.inspector.editors["initial"]
    initial.setCurrentIndex(initial.findData("green"))
    assert document.flow.nodes["light"].params["initial"] == "green"
    # it follows a rename and closes with its node
    document.rename_node("light", "lamp")
    settle()
    assert document._node_editors["lamp"] is editor and "lamp" in editor.windowTitle()
    document.delete_items(["lamp"], [])
    settle()
    assert "lamp" not in document._node_editors and not editor.isVisible()


def test_a_parameter_that_does_not_count_is_locked_and_says_why(shell):
    document = shell.new_flow()
    document.add_node("report.check", (0, 0), "check", low=4.5)
    document.scene.select_nodes(["check"])
    settle()
    inspector = document.inspector_widget()
    tolerance = inspector.editors["tolerance"]
    assert not tolerance.isEnabled() and "Locked: only with 'expected' set" in tolerance.toolTip()
    assert inspector.editors["low"].isEnabled()
    document.set_param("check", "expected", 5.0)
    settle()
    assert document.inspector_widget().editors["tolerance"].isEnabled()


def test_the_pattern_editor_builds_tracks_from_blocks(shell, monkeypatch):
    from openscilab.core import pattern_blocks
    from openscilab.ui.flow import pattern_editor

    document = shell.new_flow()
    document.add_node("device.device", (0, 0), "uno", address="sim:uno")
    document.add_node("generator.pattern", (300, 0), "pattern", rate="1 kHz")
    assert document.connect_ports("uno.device", "pattern.device")[0]
    document.scene.select_nodes(["pattern"])
    settle()
    holder = document.inspector_widget().editors["tracks"]
    assert holder.summary.text() == "no pins yet"

    def fill(dialog) -> int:
        assert dialog.pins[:2] == ["D2", "D3"]  # the outputs of the board
        dialog.rows[0].pin.setCurrentText("D7")
        dialog.add_row("D3", pattern_blocks.Block("square", {"high": 2, "low": 3}))
        dialog.length.setValue(10)
        assert dialog.error.text() == ""
        dialog.accept()
        return pattern_editor.PatternEditor.Accepted

    monkeypatch.setattr(pattern_editor.PatternEditor, "exec", fill)
    holder.button.click()
    settle()
    tracks = document.flow.nodes["pattern"].params["tracks"]
    assert tracks == {"D7": "// clock period=2 start=low\n{l1,h1,}5;",
                      "D3": "// square high=2 low=3 start=high\n{h2,l3,}2;"}
    assert document.inspector_widget().editors["tracks"].summary.text() == "2 pins: D7, D3"


def test_inputs_are_chosen_from_the_outputs_of_the_flow_and_wired(shell):
    document = shell.new_flow()
    document.add_node("control.timer", (0, 0), "timer")
    document.add_node("measure.count", (300, 0), "count")
    document.add_node("view.chart", (600, 0), "chart")
    document.scene.select_nodes(["chart"])
    settle()
    menu = document.inspector_widget().editors["inputs"].menu
    actions = {action.text(): action for action in menu.actions()}
    assert {"timer.tick", "count.out"} <= set(actions) and not actions["count.out"].isChecked()
    actions["count.out"].setChecked(True)  # a trace for it: an input named after it, wired
    settle()
    assert document.flow.nodes["chart"].params["inputs"] == ["out"]
    assert [str(edge) for edge in document.flow.edges_into("chart", "out")] == ["count.out -> chart.out"]
    menu = document.inspector_widget().editors["inputs"].menu
    action = next(action for action in menu.actions() if action.text() == "count.out")
    assert action.isChecked()
    action.setChecked(False)  # its only wire: the input goes too
    settle()
    assert not document.flow.nodes["chart"].params.get("inputs") and not document.flow.edges_into("chart", "out")


def test_the_trigger_of_a_capture_is_chosen(shell):
    document = shell.new_flow()
    document.add_node("device.device", (0, 0), "la", address="sim:free")
    document.add_node("device.capture", (300, 0), "capture", channels=["D0", "D1"])
    document.scene.select_nodes(["capture"])
    settle()
    trigger = document.inspector_widget().editors["trigger"]
    assert trigger.kind.currentData() == "none" and not trigger.edge.isEnabled()
    trigger.kind.setCurrentIndex(trigger.kind.findData("edge"))
    assert document.flow.nodes["capture"].params["trigger"] == {"edge": "rising", "source": "D0"}
    settle()
    trigger = document.inspector_widget().editors["trigger"]
    trigger.source.setCurrentText("D1")
    trigger.kind.setCurrentIndex(trigger.kind.findData("pattern"))
    trigger.pattern.setText("1x0")
    trigger.pattern.editingFinished.emit()
    assert document.flow.nodes["capture"].params["trigger"] == {"pattern": "1x0", "source": "D1"}
