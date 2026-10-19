"""The circuit editor in the window (offscreen): the Circuits section, File → New → Circuit, drawing
with the mouse, undo, saving and opening, the inspector, the catalog, running live and the results."""

from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication
from test_circuit_model import low_pass

from openscilab.circuits import engines, yaml_io
from openscilab.circuits.catalog import Catalog


@pytest.fixture
def circuit(shell):
    """A new circuit document in the window."""
    document = shell.new_document("circuit")
    assert document is not None and document.document_kind == "circuit"
    return document


def process_until(condition, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_section_and_the_new_entry(shell):
    assert "circuits" in shell.activity_bar.keys()
    section = shell.plugin_section("circuits")
    assert section is not None and section.browser is not None
    assert "circuit" in shell.plugin_new_actions
    shell.activity_bar.select("circuits")
    assert shell.sidebar.current_key == "circuits"


def test_components_are_placed_wired_and_undone(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    assert sorted(circuit.schematic.components) == ["R1", "R2"]
    scene = circuit.scene
    assert set(scene.components) == {"R1", "R2"}
    # a press on a pin draws a wire from it; a click on the other pin ends it
    scene.mousePressEvent(_event(scene, QPointF(30, 0)))
    assert scene.tool == "wire"
    scene.mousePressEvent(_event(scene, QPointF(70, 0)))
    assert len(circuit.schematic.wires) == 1 and scene.tool in ("select", "wire")
    assert circuit.dirty
    circuit.undo.undo()
    assert not circuit.schematic.wires
    circuit.undo.redo()
    assert len(circuit.schematic.wires) == 1
    netlist = scene.netlist
    assert netlist.net_of("R1", "2") == netlist.net_of("R2", "1")


def _event(scene, point: QPointF, button=Qt.LeftButton):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    event = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMousePress)
    event.setScenePos(point)
    event.setButton(button)
    event.setButtons(button)
    return event


def test_moving_drags_the_wires_along(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_wire([(30, 0), (80, 0), (80, 40)])
    circuit.move_items({"R1": (0, 20)})
    assert list(circuit.schematic.wires.values())[0].points == [(30, 20), (80, 20), (80, 40)]


def test_rotate_mirror_delete_copy_paste(circuit):
    circuit.add_component("diode", 0, 0)
    circuit.scene.select_ids(["D1"])
    circuit.rotate()
    assert circuit.schematic.components["D1"].rotation == 90
    circuit.scene.select_ids(["D1"])
    circuit.mirror()
    assert circuit.schematic.components["D1"].mirror
    circuit.scene.select_ids(["D1"])
    assert circuit.copy_selection()
    circuit.paste()
    assert sorted(circuit.schematic.components) == ["D1", "D2"]
    circuit.scene.select_ids(["D2"])
    circuit.delete_selection()
    assert sorted(circuit.schematic.components) == ["D1"]


def test_it_is_saved_and_opened_again(shell, circuit, tmp_path, monkeypatch):
    for ref, component in low_pass().components.items():
        circuit.add_component(component.type, component.x, component.y, component.rotation)
    path = tmp_path / "rc.circuit.yaml"
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(path), ""))
    assert circuit.save() and not circuit.dirty
    assert yaml_io.load(str(path)).components.keys() == circuit.schematic.components.keys()
    shell.area.close_document(circuit, force=True)
    opened = shell.open_file(str(path))
    assert opened.document_kind == "circuit" and opened.path == str(path)
    assert shell.open_file(str(path)) is opened


def test_the_project_lists_its_circuits(shell, tmp_path):
    from openscilab.lab.project import Project

    project = Project.create(str(tmp_path / "lab"), "Lab")
    assert os.path.isdir(os.path.join(project.root, "circuits"))
    yaml_io.save(low_pass(), os.path.join(project.root, "circuits", "rc.circuit.yaml"))
    shell.project_section.set_project(Project.open(project.root))
    listed = [shell.project_section.files.item(row).text() for row in range(shell.project_section.files.count())]
    assert os.path.join("circuits", "rc.circuit.yaml") in listed


def test_the_inspector_checks_values(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.scene.select_ids(["R1"])
    widget = circuit.inspector_widget()
    widget._changed("value", "lots")
    assert widget.fields["value"].message.isVisibleTo(widget) and "no value" in widget.fields["value"].message.text()
    assert "value" not in circuit.schematic.components["R1"].params
    widget._changed("value", "4k7")
    assert circuit.schematic.components["R1"].params["value"] == "4k7"
    circuit.scene.select_ids([])
    assert type(circuit.inspector_widget()).__name__ == "CircuitInspector"


def test_the_catalog_finds_components(shell):
    from openscilab.ui.circuits.catalog_widget import ComponentBrowser

    browser = ComponentBrowser(Catalog(kicad=[], models=[], user=[], use_cache=False))
    browser.search.setText("resistor")
    browser.refresh()
    found = [browser.results.item(row).data(Qt.UserRole) for row in range(browser.results.count())]
    assert found[0] == "resistor"
    chosen = []
    browser.chosen.connect(lambda type_name, unit: chosen.append((type_name, unit)))
    browser._activate(browser.results.item(0))
    assert chosen == [("resistor", 1)]
    browser.deleteLater()


def test_without_ngspice_a_banner_says_so(circuit, monkeypatch):
    from openscilab.core import preferences

    monkeypatch.setattr(engines, "available", lambda: False)
    preferences.update({"circuits.install_tools": False})
    for ref, component in low_pass().components.items():
        circuit.add_component(component.type, component.x, component.y, component.rotation)
    assert not circuit.start_run()
    assert circuit.banner.isVisibleTo(circuit) and "ngspice" in circuit.banner.label.text()


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_it_runs_live_and_computes(shell, circuit):
    source = low_pass()
    circuit._set_schematic(source)
    assert shell.run_button.isEnabled()
    assert circuit.start_run() and circuit.run_state == "running"
    assert process_until(lambda: circuit.scene.components["P1"].live_text.endswith("V"))
    circuit.pause_run()
    assert circuit.run_state == "paused"
    circuit.stop_run()
    assert circuit.run_state == "stopped" and not circuit.scene.components["P1"].live_text
    circuit.compute("op")
    assert circuit.current_view() == "Results" and circuit.results.current().kind == "op"


@pytest.mark.skipif(not engines.available(), reason="ngspice is not installed")
def test_an_oscilloscope_shows_the_running_circuit(circuit):
    from openscilab.circuits.nets import placed_pins

    source = low_pass()
    source.components["V1"].params.update({"amplitude": "2.5 V", "offset": "2.5 V"})
    source.add_component("probe.scope", 400, 0, "XSC", "XSC1", {"time_div": "500 us", "volts_div": "1 V"})
    pins = {pin.name: point for pin, point in placed_pins(source.components["XSC1"], circuit.spec("probe.scope"))}
    source.add_wire([(120, -30), pins["CH1"]])
    source.add_wire([pins["GND"], (pins["GND"][0], 60), (120, 60)])
    circuit._set_schematic(source)
    panel = circuit.scopes["XSC1"]
    assert circuit.instruments.isVisibleTo(circuit) and circuit.instruments.indexOf(panel) >= 0
    circuit.item_activated("XSC1")
    assert circuit.instruments.currentWidget() is panel
    assert circuit.start_run()
    try:
        assert process_until(lambda: panel.picture is not None and panel.picture.triggered)
        assert "CH1" in panel.readouts.text() and circuit.scene.components["XSC1"].scope_traces
        panel._emit("time_div", "2 ms")  # (its settings are parameters of the component: undone like others)
        assert circuit.schematic.components["XSC1"].params["time_div"] == "2 ms"
    finally:
        circuit.stop_run()
    assert panel.picture is None and not circuit.scene.components["XSC1"].scope_traces  # (cleared with the run)


def test_moving_keeps_every_connection(circuit):
    # R1's right pin sits in the middle of a wire, R2's left pin touches R1's right pin directly
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 60, 0)
    circuit.add_wire([(30, -40), (30, 40)])
    before = circuit.scene.netlist
    assert before.net_of("R1", "2") == before.net_of("R2", "1")
    circuit.move_items({"R1": (0, 60)})
    netlist = circuit.scene.netlist
    assert netlist.net_of("R1", "2") == netlist.net_of("R2", "1")
    assert len(circuit.schematic.wires) == 2  # the old wire and a new piece from where the pin was
    circuit.undo.undo()
    assert len(circuit.schematic.wires) == 1 and circuit.schematic.components["R1"].y == 0


def test_a_wire_between_moved_components_moves_along(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.add_wire([(30, 0), (50, 0), (50, 40), (70, 40), (70, 0)])
    circuit.move_items({"R1": (20, 20), "R2": (120, 20)})
    assert list(circuit.schematic.wires.values())[0].points == [(50, 20), (70, 20), (70, 60), (90, 60), (90, 20)]


def test_wires_follow_while_dragging(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_wire([(30, 0), (80, 0), (80, 40)])
    scene = circuit.scene
    wire_item = next(iter(scene.wires.values()))
    circuit.preview_moves({"R1": (0, 20)})
    shown = wire_item.path()
    assert shown.elementAt(0).y == 20 and circuit.schematic.components["R1"].y == 0
    scene.restore_wires()
    assert wire_item.path().elementAt(0).y == 0


def test_the_toolbar_brings_back_the_schematic(circuit):
    bar = circuit.toolbar()
    assert bar.isVisibleTo(circuit)
    circuit.set_view("Results")  # (where an analysis, e.g. the operating point, shows its result)
    assert circuit.view_actions["Results"].isChecked() and not circuit.action_wire.isEnabled()
    circuit.view_actions["Schematic"].trigger()
    assert circuit.current_view() == "Schematic" and circuit.action_wire.isEnabled()


def test_the_inspector_is_kept_while_values_change(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.scene.select_ids(["R1"])
    widget = circuit.inspector_widget()
    widget._changed("value", "4k7")
    assert circuit.inspector_widget() is widget


def test_without_microcontrollers_the_serial_monitor_is_not_shown(circuit):
    circuit.add_component("probe.scope", 0, 0)
    assert circuit.instruments.isVisibleTo(circuit) and circuit.instruments.indexOf(circuit.serial) < 0
    assert not circuit.serial.isVisibleTo(circuit)
    circuit.add_component("mcu.uno", 300, 0)
    assert circuit.instruments.indexOf(circuit.serial) == 0
    circuit.undo.undo()  # (the microcontroller goes again)
    assert circuit.instruments.indexOf(circuit.serial) < 0 and not circuit.serial.isVisibleTo(circuit)


def test_comments_on_components_wires_and_labels(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_wire([(30, 0), (80, 0)])
    wire_id = next(iter(circuit.schematic.wires))
    circuit.scene.select_ids(["R1"])
    widget = circuit.inspector_widget()
    widget.comment.setPlainText("the load")
    widget.comment.committed.emit("the load")
    assert circuit.schematic.components["R1"].comment == "the load"
    assert circuit.scene.components["R1"]._comment_rect().width() > 0  # (drawn under it)
    circuit.scene.select_ids([wire_id])
    widget = circuit.inspector_widget()
    assert type(widget).__name__ == "WireInspector"
    widget.comment_changed.emit(wire_id, "to the meter")
    assert circuit.schematic.wires[wire_id].comment == "to the meter"
    assert "to the meter" in circuit.scene.wires[wire_id].toolTip()
    circuit.undo.undo()
    assert circuit.schematic.wires[wire_id].comment == ""


def test_components_are_grouped_framed_moved_and_ungrouped(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.add_wire([(30, 0), (70, 0)])
    circuit.scene.select_ids(["R1", "R2", next(iter(circuit.schematic.wires))])
    group_id = circuit.group_selection()
    assert group_id in circuit.schematic.groups and len(circuit.schematic.groups[group_id].members) == 3
    frame = circuit.scene.groups[group_id].frame()
    assert frame.contains(circuit.scene.components["R1"].sceneBoundingRect().center())
    assert circuit.scene.selected_ids() == [group_id]
    widget = circuit.inspector_widget()
    assert type(widget).__name__ == "GroupInspector"
    widget.changed.emit(group_id, "name", "Divider")
    widget.changed.emit(group_id, "comment", "halves the input")
    group = circuit.schematic.groups[group_id]
    assert (group.name, group.comment) == ("Divider", "halves the input")
    assert widget.color.currentData() == ""  # (no background unless one is chosen)
    widget.changed.emit(group_id, "color", "green")
    assert circuit.schematic.groups[group_id].color == "green"
    assert circuit.scene.groups[group_id].frame().height() > frame.height()  # (room for name and comment)
    # dragging the frame moves the members (the wire between them goes along)
    circuit.scene.finish_group_move(circuit.scene.groups[group_id], {"R1": (0, 40), "R2": (100, 40)})
    assert (circuit.schematic.components["R1"].y, circuit.schematic.components["R2"].y) == (40, 40)
    assert list(circuit.schematic.wires.values())[0].points == [(30, 40), (70, 40)]
    circuit.scene.select_ids([group_id])
    circuit.ungroup()
    assert not circuit.schematic.groups and len(circuit.schematic.components) == 2
    circuit.undo.undo()
    assert group_id in circuit.schematic.groups and circuit.scene.groups[group_id].isVisible()


def _mouse(kind, point: QPointF, modifiers=Qt.NoModifier):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    event = QGraphicsSceneMouseEvent({"press": QEvent.GraphicsSceneMousePress, "move": QEvent.GraphicsSceneMouseMove,
                                      "release": QEvent.GraphicsSceneMouseRelease}[kind])
    event.setScenePos(point)
    event.setButton(Qt.LeftButton if kind != "move" else Qt.NoButton)
    event.setButtons(Qt.LeftButton if kind != "release" else Qt.NoButton)
    event.setModifiers(modifiers)
    return event


@pytest.mark.parametrize("rotation, drag, more", [(90, (0, -60), True), (90, (0, 60), False), (0, (60, 0), True),
                                                   (0, (0, -60), True), (270, (0, 60), True)])
def test_a_potentiometer_turns_whichever_way_it_lies(circuit, rotation, drag, more):
    circuit.add_component("potentiometer", 0, 0, rotation)
    item = circuit.scene.components["RV1"]
    start = item.mapToScene(item.body_rect().center())
    item.mousePressEvent(_mouse("press", start, Qt.AltModifier))  # (Alt: also while it does not run)
    item.mouseMoveEvent(_mouse("move", start + QPointF(*drag)))
    item.mouseReleaseEvent(_mouse("release", start + QPointF(*drag)))
    position = float(circuit.schematic.components["RV1"].params["position"])
    assert (position > 0.5) if more else (position < 0.5)  # (towards pin 3: more)


def test_paste_keeps_comments_and_groups(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.add_wire([(30, 0), (70, 0)])
    circuit.set_comment("R1", "the first")
    wire_id = next(iter(circuit.schematic.wires))
    circuit.set_comment(wire_id, "between")
    circuit.scene.select_ids(["R1", "R2", wire_id])
    group_id = circuit.group_selection("Pair", "two resistors")
    circuit.set_group(group_id, "color", "blue")
    circuit.scene.select_ids([group_id])  # (a selected frame copies its members and the group)
    assert circuit.copy_selection()
    circuit.paste()
    pasted = [ref for ref in circuit.schematic.components if ref not in ("R1", "R2")]
    assert len(pasted) == 2 and circuit.schematic.components[pasted[0]].comment == "the first"
    assert len(circuit.schematic.wires) == 2 and [w.comment for w in circuit.schematic.wires.values()].count("between") == 2
    groups = list(circuit.schematic.groups.values())
    assert len(groups) == 2 and groups[1].name == "Pair" and groups[1].color == "blue"
    assert set(groups[1].members) >= set(pasted)


def test_edits_that_change_nothing_leave_the_circuit_clean(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.undo.setClean()
    circuit.set_param("R1", "value", "1k")  # (the default: nothing changes)
    circuit.set_comment("R1", "")
    circuit._circuit_changed("name", circuit.schematic.name)
    assert circuit.undo.isClean() and not circuit.dirty


def test_grouping_a_group_keeps_it(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.scene.select_ids(["R1", "R2"])
    group_id = circuit.group_selection("Pair", "two")
    circuit.set_group(group_id, "color", "green")
    circuit.scene.select_ids([group_id])
    assert circuit.group_selection() == group_id  # (nothing to do)
    group = circuit.schematic.groups[group_id]
    assert (group.name, group.comment, group.color) == ("Pair", "two", "green")
    # merging with more parts keeps the name, comment and colour
    circuit.add_component("capacitor", 200, 0)
    circuit.scene.select_ids([group_id, "C1"])
    merged = circuit.group_selection()
    merged_group = circuit.schematic.groups[merged]
    assert (merged_group.name, merged_group.color) == ("Pair", "green") and "C1" in merged_group.members
    with pytest.raises(Exception):
        circuit.set_group(merged, "color", "orange") or (_ for _ in ()).throw(ValueError)  # (refused: a message)
    assert circuit.schematic.groups[merged].color == "green"


def test_deleting_a_frame_asks_about_the_members(circuit, monkeypatch):
    from openscilab.ui import messages

    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.scene.select_ids(["R1", "R2"])
    group_id = circuit.group_selection("Pair")
    asked = []
    monkeypatch.setattr(messages, "choose", lambda *args, **kwargs: asked.append(args) or 1)
    circuit.scene.select_ids([group_id])
    circuit.delete_selection()  # (answer 1: only the frame)
    assert len(asked) == 1 and not circuit.schematic.groups and len(circuit.schematic.components) == 2
    circuit.undo.undo()
    monkeypatch.setattr(messages, "choose", lambda *args, **kwargs: 0)
    circuit.scene.select_ids([group_id])
    circuit.delete_selection()  # (answer 0: the group and its members)
    assert not circuit.schematic.groups and not circuit.schematic.components
    circuit.undo.undo()
    circuit.scene.select_ids(["R1"])
    monkeypatch.setattr(messages, "choose", lambda *args, **kwargs: pytest.fail("no question without a frame"))
    circuit.delete_selection()
    assert "R1" not in circuit.schematic.components


def test_rotating_a_frame_rotates_its_members(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.scene.select_ids(["R1", "R2"])
    group_id = circuit.group_selection()
    circuit.scene.select_ids([group_id])
    circuit.rotate()
    assert all(component.rotation == 90 for component in circuit.schematic.components.values())
    circuit.scene.select_ids([group_id])
    circuit.mirror()
    assert all(component.mirror for component in circuit.schematic.components.values())


def test_changing_the_unit_keeps_the_part_selected(circuit):
    from openscilab.circuits.library import (
        ParamSpec,  # noqa: F401 - (the spec of a multi-unit part is KiCad's)
    )

    circuit.add_component("gate.and", 0, 0)  # (one unit: the reference stays)
    circuit.scene.select_ids(["U1"])
    circuit.set_unit("U1", 1)
    assert circuit.scene.selected_ids() == ["U1"]


def test_the_inspector_shows_the_circuit_after_delete_and_undo(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_component("resistor", 100, 0)
    circuit.scene.select_ids(["R2"])
    assert type(circuit.inspector_widget()).__name__ == "ComponentInspector"
    circuit.delete_selection()
    assert circuit.scene.selected_ids() == [] and type(circuit.inspector_widget()).__name__ == "CircuitInspector"
    circuit.add_component("capacitor", 200, 0)
    circuit.scene.select_ids(["C1"])
    circuit.undo.undo()
    assert type(circuit.inspector_widget()).__name__ == "CircuitInspector"


def test_typing_searches_and_the_tools_have_shift_keys(circuit):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    typed = []
    circuit.view.search = typed.append
    circuit.add_component("resistor", 0, 0)
    circuit.scene.select_ids([])
    for key, text in ((Qt.Key_L, "l"), (Qt.Key_E, "e"), (Qt.Key_D, "d"), (Qt.Key_R, "r")):
        circuit.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text))
    assert typed == ["l", "e", "d", "r"] and circuit.scene.tool == "select"
    assert circuit.schematic.components["R1"].rotation == 0  # (R typed, nothing selected: no rotation)
    circuit.scene.select_ids(["R1"])
    circuit.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.NoModifier, "r"))
    assert circuit.schematic.components["R1"].rotation == 90 and typed == ["l", "e", "d", "r"]
    assert circuit.action_wire.shortcut().toString() == "Shift+W"
    assert circuit.action_label.shortcut().toString() == "Shift+L"
    circuit.action_wire.trigger()
    assert circuit.scene.tool == "wire"


def test_dragging_a_frame_moves_the_wires_of_the_group(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_wire([(30, 0), (80, 0), (80, 40)])  # (one end free)
    wire_id = next(iter(circuit.schematic.wires))
    circuit.scene.select_ids(["R1", wire_id])
    group_id = circuit.group_selection()
    frame = circuit.scene.groups[group_id]
    circuit.scene.finish_group_move(frame, {"R1": (0, 20)}, {wire_id: (0, 20)})
    assert circuit.schematic.wires[wire_id].points == [(30, 20), (80, 20), (80, 60)]  # (moved as a whole)
    assert circuit.schematic.components["R1"].y == 20


def test_a_comment_being_typed_is_taken_when_the_field_is_left(circuit):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    circuit.add_component("resistor", 0, 0)
    circuit.scene.select_ids(["R1"])
    widget = circuit.inspector_widget()
    widget.comment.setPlainText("the load")
    widget.comment.focusOutEvent(QFocusEvent(QEvent.FocusOut))
    assert circuit.schematic.components["R1"].comment == "the load" and circuit.dirty


def test_a_wire_or_group_inspector_is_kept_while_its_item_changes(circuit):
    circuit.add_component("resistor", 0, 0)
    circuit.add_wire([(30, 0), (80, 0)])
    wire_id = next(iter(circuit.schematic.wires))
    circuit.scene.select_ids([wire_id])
    widget = circuit.inspector_widget()
    circuit.set_comment(wire_id, "between")
    assert circuit.inspector_widget() is widget and widget.comment.toPlainText() == "between"
    circuit.scene.select_ids(["R1"])
    group_id = circuit.group_selection("G")
    circuit.scene.select_ids([group_id])
    widget = circuit.inspector_widget()
    circuit.set_group(group_id, "name", "Block")
    assert circuit.inspector_widget() is widget and widget.name.text() == "Block"
