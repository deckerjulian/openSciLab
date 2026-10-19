# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The schematic as a scene: a view of the circuit's model.

The scene mirrors the model (:meth:`SchematicScene.sync`); every edit goes to the *controller* (the
circuit document), which changes the model with undo and syncs the scene again - as the flow editor
does. Tools: *select* (click, drag, rubber band; pressing on a pin starts a wire), *wire* (click the
corners, double-click or click a pin to end, Esc to stop), *place* (a component follows the pointer
until it is clicked down; R rotates, X mirrors) and *label*.
"""

from __future__ import annotations

import time
from typing import Protocol

from PySide6.QtCore import QPointF, QTimer, Qt, Signal
from PySide6.QtWidgets import QGraphicsScene

from ...circuits import GRID
from ...circuits.library import ComponentSpec
from ...circuits.model import Component, Schematic, orthogonal, simplify
from ...circuits.nets import Netlist, PinRef
from .items import (
    ComponentItem,
    Flow,
    FlowsItem,
    GroupItem,
    JunctionsItem,
    LabelItem,
    PreviewItem,
    StubsItem,
    WireItem,
)

PIN_SNAP = 6.0


#: seconds between two changes of the numbers on a running schematic (probes, net labels)
TEXT_EVERY = 0.25
#: the current view: the waves move this often (ms); currents below FLOW_LEAST amperes, or below FLOW_SHARE
#: of the largest, show none (a leak through a megohm is no flow to watch)
FLOW_INTERVAL_MS = 33
FLOW_LEAST = 1e-7
FLOW_SHARE = 1e-4

class Controller(Protocol):  # pragma: no cover - the interface the scene calls
    running: bool

    def spec(self, type_name: str) -> ComponentSpec | None: ...
    def move_items(self, moves: dict[str, tuple[int, int]]) -> None: ...
    def add_wire(self, points: list[tuple[int, int]]) -> None: ...
    def add_component(self, type_name: str, x: int, y: int, rotation: int = 0, mirror: bool = False,
                      unit: int = 1) -> str | None: ...
    def add_label(self, x: int, y: int) -> None: ...
    def delete_items(self, ids: list[str]) -> None: ...
    def operate(self, ref: str, param: str, value: str, final: bool) -> None: ...
    def item_activated(self, ref: str) -> None: ...


def snap(point: QPointF) -> tuple[int, int]:
    return int(round(point.x() / GRID) * GRID), int(round(point.y() / GRID) * GRID)


class SchematicScene(QGraphicsScene):
    selection_changed_ids = Signal(list)
    #: the tool changed ("select", "wire", "place", "label")
    tool_changed = Signal(str)

    def __init__(self, controller: Controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.components: dict[str, ComponentItem] = {}
        self.wires: dict[str, WireItem] = {}
        self.labels: dict[str, LabelItem] = {}
        self.groups: dict[str, GroupItem] = {}
        self.junctions = JunctionsItem()
        self.addItem(self.junctions)
        self.preview = PreviewItem()
        self.addItem(self.preview)
        #: new pieces of wire while something is dragged
        self.stubs = StubsItem()
        self.addItem(self.stubs)
        self.tool = "select"
        self._wire: list[tuple[int, int]] = []
        self._horizontal_first = True
        self._ghost: ComponentItem | None = None
        self._ghost_type = ""
        self._ghost_unit = 1
        self._pressed_positions: dict[str, tuple[float, float]] = {}
        self._texts_due = 0.0
        self.schematic: Schematic | None = None
        self.netlist: Netlist | None = None
        self.selectionChanged.connect(self._selection_changed)
        # the current view: the waves move with a clock of their own, drawn by one item over the schematic
        self.flows_item = FlowsItem()
        self.addItem(self.flows_item)
        self._flow_items: list = []
        self._flow_clock = time.monotonic()
        self._flow_timer = QTimer(self)
        self._flow_timer.setInterval(FLOW_INTERVAL_MS)
        self._flow_timer.timeout.connect(self._move_flows)

    # ------------------------------------------------------------------- sync
    def sync(self, schematic: Schematic, netlist: Netlist | None = None) -> None:
        """Make the items match ``schematic`` (and show what ``netlist`` found)."""
        self.schematic = schematic
        self.netlist = netlist
        selected_before = self.selected_ids()
        blocked = self.blockSignals(True)
        try:
            for ref in list(self.components):
                if ref not in schematic.components:
                    self.removeItem(self.components.pop(ref))
            for ref, component in schematic.components.items():
                spec = self.controller.spec(component.type)
                item = self.components.get(ref)
                if item is None:
                    item = ComponentItem(component, spec, self.controller)
                    self.components[ref] = item
                    self.addItem(item)
                else:
                    item.update_from(component, spec)
            for wire_id in list(self.wires):
                if wire_id not in schematic.wires:
                    self.removeItem(self.wires.pop(wire_id))
            for wire_id, wire in schematic.wires.items():
                item = self.wires.get(wire_id)
                if item is None:
                    item = WireItem(wire)
                    self.wires[wire_id] = item
                    self.addItem(item)
                else:
                    item.update_from(wire)
            for label_id in list(self.labels):
                if label_id not in schematic.labels:
                    self.removeItem(self.labels.pop(label_id))
            for label_id, label in schematic.labels.items():
                item = self.labels.get(label_id)
                if item is None:
                    item = LabelItem(label)
                    self.labels[label_id] = item
                    self.addItem(item)
                else:
                    item.update_from(label)
            for group_id in list(self.groups):
                if group_id not in schematic.groups:
                    self.removeItem(self.groups.pop(group_id))
            for group_id, group in schematic.groups.items():
                item = self.groups.get(group_id)
                if item is None:
                    item = GroupItem(group, self.item_of)
                    self.groups[group_id] = item
                    self.addItem(item)
                else:
                    item.update_from(group)
            self.set_netlist(netlist)
        finally:
            self.blockSignals(blocked)
        if self.selected_ids() != selected_before:
            self._selection_changed()  # (a selected item went: the inspector must know)

    def set_netlist(self, netlist: Netlist | None) -> None:
        self.stubs.set_paths([])
        self.netlist = netlist
        open_pins: dict[str, set[str]] = {}
        problems: dict[str, str] = {}
        if netlist is not None:
            self.junctions.set_points(netlist.junctions)
            for problem in netlist.problems:
                if problem.pin:
                    open_pins.setdefault(problem.item, set()).add(problem.pin)
                elif problem.item in self.components and problem.severity == "error":
                    problems[problem.item] = problem.text
            for wire_id, item in self.wires.items():
                item.set_net(netlist.wire_net.get(wire_id, ""))
        else:
            self.junctions.set_points([])
        for ref, item in self.components.items():
            pins = open_pins.get(ref, set())
            if pins != item.open_pins or problems.get(ref, "") != item.problem:
                item.open_pins = pins
                item.problem = problems.get(ref, "")
                item.update_from(item.component, item.spec)

    def set_problems(self, problems: dict[str, str]) -> None:
        """Errors of the simulation by component (a dashed frame and the text in its tooltip)."""
        for ref, item in self.components.items():
            text = problems.get(ref, "")
            if text != item.problem:
                item.problem = text
                item.update_from(item.component, item.spec)

    def set_flows(self, wires: dict[str, list], components: dict[str, float],
                  pins: dict[str, dict] | None = None) -> None:
        """The current view: ``wires`` - wire id -> [(start, end, amperes)] -, ``components`` - component
        -> the current through it (two pins) - and ``pins`` - component -> pin -> the current into it (the
        others); currents far below the largest are left out. Empty: none."""
        pins = pins or {}
        largest = max([abs(current) for pieces in wires.values() for _a, _b, current in pieces]
                      + [abs(current) for current in components.values()]
                      + [abs(current) for into in pins.values() for current in into.values()] + [0.0])
        least = max(FLOW_LEAST, largest * FLOW_SHARE)
        items = []
        for wire_id, item in self.wires.items():
            pieces = [piece for piece in wires.get(wire_id, ()) if abs(piece[2]) >= least]
            if pieces or item.flows:
                item.set_flows(pieces)
            if pieces:
                items.append(item)
        for ref, item in self.components.items():
            current = components.get(ref)
            item.set_flow(current if current is not None and abs(current) >= least else None,
                          {pin: into for pin, into in pins.get(ref, {}).items() if abs(into) >= least})
            if item.flows:
                items.append(item)
        self._flow_items = items
        self._show_flows()
        if items and not self._flow_timer.isActive():
            self._flow_clock = time.monotonic()
            self._flow_timer.start()
        elif not items:
            self._flow_timer.stop()

    def set_voltages(self, volts: dict[str, float]) -> None:
        """While the circuit runs: wires and pins in the colours of their nets' voltages (grey at 0 V,
        green positive, red negative - against the largest voltage seen); empty: as drawn."""
        from .items import voltage_color

        if not volts:
            self._largest_voltage = 0.0
            for item in self.wires.values():
                item.set_voltage(None)
            for item in self.components.values():
                item.set_pin_colors({})
            return
        # (the scale grows at once and shrinks slowly: an AC circuit does not flicker)
        largest = max(abs(value) for value in volts.values())
        self._largest_voltage = max(largest, getattr(self, "_largest_voltage", 0.0) * 0.995, 0.1)
        scale = self._largest_voltage
        colors: dict[str, object] = {}

        def color_of(net):
            if net not in colors:
                value = volts.get(net)
                colors[net] = voltage_color(value, scale) if value is not None else None
            return colors[net]

        for item in self.wires.values():
            item.set_voltage(color_of(item.net) if item.net else None)
        netlist = self.netlist
        if netlist is None:
            return
        for ref, item in self.components.items():
            spec = item.spec
            if spec is None:
                continue
            pins = {}
            for pin in spec.symbol.pins_of(item.component.unit):
                net = netlist.pin_net.get(PinRef(ref, pin.number))
                color = color_of(net) if net is not None else None
                if color is not None:
                    pins[pin.number] = color
            item.set_pin_colors(pins)

    def freeze_flows(self, frozen: bool) -> None:
        """The waves stand still while the circuit pauses (and move on when it runs again)."""
        if frozen:
            self._flow_timer.stop()
        elif self._flow_items:
            self._flow_clock = time.monotonic()
            self._flow_timer.start()

    def _move_flows(self) -> None:
        now = time.monotonic()
        seconds, self._flow_clock = min(now - self._flow_clock, 0.2), now
        for item in self._flow_items:
            for flow in item.flows:
                flow.advance(seconds)
        self._show_flows()

    def _show_flows(self) -> None:
        """All streams in scene coordinates, drawn by the one item over the schematic."""
        flows = []
        for item in self._flow_items:
            if isinstance(item, ComponentItem):
                flows += [Flow(item.mapToScene(flow.start), item.mapToScene(flow.end), flow.current, flow.offset)
                          for flow in item.flows]
            else:
                flows.extend(item.flows)
        self.flows_item.set_flows(flows)

    def set_live(self, texts: dict[str, str], glows: dict[str, float], nets: dict[str, str]) -> None:
        """What the running circuit shows: probe readings, LED brightness, the voltages of nets. Numbers
        change :data:`TEXT_EVERY` seconds apart (faster nobody reads them, and each repaints its part of
        the schematic); LEDs follow at once. Empty ``texts`` and ``nets`` (stopped) clear at once."""
        now = time.monotonic()
        update_texts = now >= self._texts_due or not (texts or nets)
        if update_texts:
            self._texts_due = now + TEXT_EVERY
        for ref, item in self.components.items():
            text = texts.get(ref, "") if update_texts else item.live_text
            glow = glows.get(ref, 0.0)
            if text != item.live_text or abs(glow - item.glow) > 0.02:
                item.set_live(text, glow)
        if not update_texts:
            return
        for item in self.wires.values():
            item.set_net(item.net, nets.get(item.net, ""))
        for label_id, item in self.labels.items():
            net = self.netlist.label_net.get(label_id, "") if self.netlist is not None else ""
            live = nets.get(net, "")
            if live != item.live:
                item.prepareGeometryChange()
                item.live = live
                item.update()

    # ---------------------------------------------------------- selection
    def selected_ids(self) -> list[str]:
        found = []
        for item in self.selectedItems():
            ref = getattr(item, "ref", None)
            if ref:
                found.append(ref)
        return found

    def select_ids(self, ids) -> None:
        wanted = set(ids)
        blocked = self.blockSignals(True)
        try:
            self.clearSelection()
            for items in (self.components, self.wires, self.labels, self.groups):
                for ref, item in items.items():
                    if ref in wanted:
                        item.setSelected(True)
        finally:
            self.blockSignals(blocked)
        self._selection_changed()

    def _selection_changed(self) -> None:
        self.selection_changed_ids.emit(self.selected_ids())

    # --------------------------------------------------------------- tools
    def set_tool(self, tool: str) -> None:
        if tool == self.tool:
            return
        self.cancel()
        self.tool = tool
        self.tool_changed.emit(tool)

    def cancel(self) -> None:
        """Stop what is being drawn or placed."""
        self._wire = []
        self.preview.set_points([])
        if self._ghost is not None:
            self.removeItem(self._ghost)
            self._ghost = None
        if self.tool in ("wire", "place", "label"):
            self.tool = "select"
            self.tool_changed.emit("select")

    def place(self, type_name: str, unit: int = 1, at: QPointF | None = None) -> bool:
        """Let the component ``type_name`` follow the pointer until it is clicked down."""
        spec = self.controller.spec(type_name)
        if spec is None:
            return False
        self.cancel()
        self.tool = "place"
        self.tool_changed.emit("place")
        ghost = ComponentItem(Component("?", type_name, unit=unit), spec, None)
        ghost.setOpacity(0.6)
        ghost.setFlag(ghost.GraphicsItemFlag.ItemIsSelectable, False)
        ghost.setFlag(ghost.GraphicsItemFlag.ItemIsMovable, False)
        ghost.setZValue(20)
        self.addItem(ghost)
        self._ghost = ghost
        self._ghost_type = type_name
        self._ghost_unit = unit
        if at is not None:
            x, y = snap(at)
            ghost.setPos(x, y)
        return True

    def rotate_ghost(self) -> bool:
        if self._ghost is None:
            return False
        component = self._ghost.component
        component.rotation = (component.rotation + 90) % 360
        self._ghost.update_from(component, self._ghost.spec)
        return True

    def mirror_ghost(self) -> bool:
        if self._ghost is None:
            return False
        component = self._ghost.component
        component.mirror = not component.mirror
        self._ghost.update_from(component, self._ghost.spec)
        return True

    def toggle_route(self) -> None:
        self._horizontal_first = not self._horizontal_first

    def pin_at(self, point: QPointF) -> tuple[str, str, tuple[int, int]] | None:
        """(component, pin, position) of a pin near ``point``."""
        best = None
        for ref, item in self.components.items():
            if not item.boundingRect().translated(item.pos()).adjusted(-PIN_SNAP, -PIN_SNAP, PIN_SNAP, PIN_SNAP).contains(point):
                continue
            for number, position in item.pin_positions().items():
                distance = abs(position.x() - point.x()) + abs(position.y() - point.y())
                if distance <= PIN_SNAP and (best is None or distance < best[0]):
                    best = (distance, ref, number, (int(round(position.x())), int(round(position.y()))))
        return best[1:] if best else None

    def _connects(self, point: tuple[int, int]) -> bool:
        """``point`` is a pin, the end of a wire or on a wire (a wire drawn to it ends there)."""
        if self.pin_at(QPointF(*point)) is not None:
            return True
        for item in self.wires.values():
            points = item.wire.points
            for a, b in zip(points, points[1:]):
                if (a[0] == b[0] == point[0] and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])) or \
                        (a[1] == b[1] == point[1] and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])):
                    return True
        return any((label.label.x, label.label.y) == point for label in self.labels.values())

    def _route(self, end: tuple[int, int]) -> list[tuple[int, int]]:
        if not self._wire:
            return []
        return self._wire[:-1] + orthogonal(self._wire[-1], end, self._horizontal_first)

    def _finish_wire(self, points: list[tuple[int, int]]) -> None:
        cleaned = simplify(points)
        self._wire = []
        self.preview.set_points([])
        if len(cleaned) >= 2:
            self.controller.add_wire(cleaned)

    # --------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:
        point = event.scenePos()
        if event.button() == Qt.RightButton and self.tool in ("wire", "place", "label"):
            if self.tool == "wire" and len(self._wire) >= 1:
                self._finish_wire(self._route(snap(point)))
            self.cancel()
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        if self.tool == "place" and self._ghost is not None:
            x, y = snap(point)
            component = self._ghost.component
            self.controller.add_component(self._ghost_type, x, y, component.rotation, component.mirror,
                                          self._ghost_unit)
            if not event.modifiers() & Qt.ShiftModifier:  # (Shift: place another)
                self.cancel()
            event.accept()
            return
        if self.tool == "label":
            x, y = snap(point)
            self.controller.add_label(x, y)
            self.cancel()
            event.accept()
            return
        if self.tool == "wire":
            here = snap(point)
            pin = self.pin_at(point)
            if pin is not None:
                here = pin[2]
            if not self._wire:
                self._wire = [here]
                self.preview.set_points([here])
            else:
                route = self._route(here)
                if here == self._wire[-1] or (len(route) >= 2 and self._connects(here)):
                    self._finish_wire(route)
                else:
                    self._wire = route
            event.accept()
            return
        # select: a press on a pin draws a wire from it
        pin = self.pin_at(point)
        item = self.itemAt(point, self.views()[0].transform()) if self.views() else None
        if pin is not None and not (isinstance(item, ComponentItem) and item.control() is not None
                                    and self.controller.running):
            self.tool = "wire"
            self.tool_changed.emit("wire")
            self._wire = [pin[2]]
            self.preview.set_points([pin[2]])
            event.accept()
            return
        super().mousePressEvent(event)
        self._pressed_positions = {item.ref: (item.pos().x(), item.pos().y()) for item in self.selectedItems()
                                   if isinstance(item, (ComponentItem, LabelItem))}

    def mouseMoveEvent(self, event) -> None:
        point = event.scenePos()
        if self._ghost is not None:
            x, y = snap(point)
            self._ghost.setPos(x, y)
        if self.tool == "wire" and self._wire:
            here = snap(point)
            pin = self.pin_at(point)
            if pin is not None:
                here = pin[2]
            self.preview.set_points(self._route(here))
        super().mouseMoveEvent(event)
        if self.tool == "select" and self._pressed_positions and event.buttons() & Qt.LeftButton:
            moves = self._current_moves()
            if moves and hasattr(self.controller, "preview_moves"):
                self.controller.preview_moves(moves)
            elif not moves:
                self.restore_wires()
            self.refresh_groups()

    def _current_moves(self) -> dict[str, tuple[int, int]]:
        moves = {}
        for item in self.selectedItems():
            if isinstance(item, (ComponentItem, LabelItem)):
                before = self._pressed_positions.get(item.ref)
                now = (int(item.pos().x()), int(item.pos().y()))
                if before is not None and (int(before[0]), int(before[1])) != now:
                    moves[item.ref] = now
        return moves

    def item_of(self, item_id: str):
        """The item of a component, wire, label or group."""
        for items in (self.components, self.wires, self.labels, self.groups):
            if item_id in items:
                return items[item_id]
        return None

    def refresh_groups(self) -> None:
        """The frames around their members where these are now (while they are dragged)."""
        for item in self.groups.values():
            item.refresh()

    def preview_group_move(self, group_item, moves: dict, wires: dict | None = None) -> None:
        if hasattr(self.controller, "preview_moves"):
            self.controller.preview_moves(moves, wires)

    def finish_group_move(self, group_item, moves: dict, wires: dict | None = None) -> None:
        self.restore_wires()
        self.controller.move_items(moves, wires)

    def preview_wires(self, schematic: Schematic) -> None:
        """The wires of ``schematic`` drawn in place of the circuit's (while something is dragged)."""
        extra = []
        for wire_id, wire in schematic.wires.items():
            item = self.wires.get(wire_id)
            if item is None or wire_id not in self.schematic.wires:
                extra.append(wire.points)
            else:
                item.show_points(wire.points)
        for wire_id, item in self.wires.items():
            if wire_id not in schematic.wires:
                item.show_points([])
        self.stubs.set_paths(extra)

    def restore_wires(self) -> None:
        """The wires as the circuit has them again (a drag ended or went back to where it began)."""
        for item in self.wires.values():
            item.show_points(item.wire.points)
        self.stubs.set_paths([])

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self.tool != "select" or not self._pressed_positions:
            self._pressed_positions = {}
            return
        moves = {}
        for item in self.selectedItems():
            if isinstance(item, (ComponentItem, LabelItem)):
                before = self._pressed_positions.get(item.ref)
                now = (item.pos().x(), item.pos().y())
                if before is not None and before != now:
                    moves[item.ref] = (int(now[0]), int(now[1]))
        self._pressed_positions = {}
        self.restore_wires()
        if moves:
            self.controller.move_items(moves)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.tool == "wire" and self._wire:
            here = snap(event.scenePos())
            self._finish_wire(self._route(here))
            self.cancel()
            event.accept()
            return
        item = self.itemAt(event.scenePos(), self.views()[0].transform()) if self.views() else None
        ref = getattr(item, "ref", None)
        if ref:
            self.controller.item_activated(ref)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
