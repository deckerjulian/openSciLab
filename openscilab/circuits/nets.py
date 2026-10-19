# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which pins a circuit connects: its nets.

1. Wires connect where their corners or ends meet, and where the end of one lies on another (a T);
   wires that only cross do not connect.
2. A pin connects to the wires through its connection point, and to the pins on the same point.
3. Labels connect to what lies under them; labels of the same name are one net, and so are power
   symbols of the same value (+5V) and hidden power pins of the same name (VCC of a 7400).
4. Ground (and nets named GND, GNDA, GNDD, 0) is the net ``0``.

A net is named after its label, else after a probe on it, else ``N1``, ``N2`` ... in the order of
its leftmost, topmost point - the same circuit always gets the same names.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from .library import ComponentSpec
from .model import Component, Point, Schematic
from .symbols import Pin, transform_point

#: names of nets that are the ground of SPICE
GROUND_NAMES = ("0", "GND", "GNDA", "GNDD", "GNDREF", "GNDPWR", "EARTH", "VSS0")


@dataclass(frozen=True)
class PinRef:
    component: str
    pin: str

    def __str__(self) -> str:
        return f"{self.component}.{self.pin}"


@dataclass
class Problem:
    severity: str  # "error" or "warning"
    text: str
    #: the component, wire or label it is about
    item: str = ""
    pin: str = ""

    def __str__(self) -> str:
        return f"{self.item}: {self.text}" if self.item else self.text


@dataclass
class Netlist:
    #: net name -> the pins on it (in the order of the components)
    nets: dict[str, list[PinRef]] = field(default_factory=dict)
    pin_net: dict[PinRef, str] = field(default_factory=dict)
    wire_net: dict[str, str] = field(default_factory=dict)
    label_net: dict[str, str] = field(default_factory=dict)
    #: where three or more connections meet on a wire (drawn as dots)
    junctions: list[Point] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    #: pins with their absolute position: (component, pin number) -> point
    positions: dict[PinRef, Point] = field(default_factory=dict)

    def net_of(self, component: str, pin: str) -> str | None:
        return self.pin_net.get(PinRef(component, pin))

    def open_pins(self) -> list[PinRef]:
        return [Problem_pin for problem in self.problems if problem.pin
                for Problem_pin in (PinRef(problem.item, problem.pin),)]


class _Union:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, key):
        parent = self.parent.setdefault(key, key)
        if parent != key:
            root = self.find(parent)
            self.parent[key] = root
            return root
        return key

    def union(self, a, b) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def pin_position(component: Component, pin: Pin) -> Point:
    x, y = transform_point(pin.x, pin.y, component.rotation, component.mirror)
    return int(round(component.x + x)), int(round(component.y + y))


def placed_pins(component: Component, spec: ComponentSpec | None) -> list[tuple[Pin, Point]]:
    if spec is None:
        return []
    return [(pin, pin_position(component, pin)) for pin in spec.symbol.pins_of(component.unit)]


def _on_segment(point: Point, a: Point, b: Point) -> bool:
    (x, y), (x1, y1), (x2, y2) = point, a, b
    if x1 == x2 == x:
        return min(y1, y2) <= y <= max(y1, y2)
    if y1 == y2 == y:
        return min(x1, x2) <= x <= max(x1, x2)
    return False


def effective_params(component, spec: ComponentSpec) -> dict[str, str]:
    """The parameters of ``component`` with the defaults of its kind for those not written."""
    params = spec.defaults()
    params.update({key: value for key, value in component.params.items() if value != "" or key not in params})
    return params


def extract(schematic: Schematic, resolve: Callable[[str], ComponentSpec | None]) -> Netlist:
    """The nets of ``schematic``; ``resolve`` gives the spec of a component type."""
    union = _Union()
    result = Netlist()
    # ------------------------------------------------------------ points
    terminals: dict[Point, list] = defaultdict(list)  # point -> what is there
    specs: dict[str, ComponentSpec | None] = {}
    for ref, component in schematic.components.items():
        spec = specs[ref] = resolve(component.type)
        if spec is None:
            result.problems.append(Problem("error", f"the kind of component {component.type!r} is not known", ref))
            continue
        for pin, point in placed_pins(component, spec):
            pin_ref = PinRef(ref, pin.number)
            result.positions[pin_ref] = point
            key = ("pin", pin_ref)
            union.find(key)
            if pin.hidden and pin.kind in ("power_in", "power_out") and pin.label:
                union.union(key, ("name", pin.label.upper() if pin.label.upper() in GROUND_NAMES else pin.label))
                continue
            terminals[point].append(key)
        if spec.role == "ground":
            for pin, _point in placed_pins(component, spec):
                union.union(("pin", PinRef(ref, pin.number)), ("name", "0"))
        elif spec.role == "power" or (spec.symbol.power and spec.role == ""):
            params = effective_params(component, spec)
            name = params.get("net") or params.get("value") or spec.title
            for pin, _point in placed_pins(component, spec):
                union.union(("pin", PinRef(ref, pin.number)), ("name", _global(name)))
    for label_id, label in schematic.labels.items():
        key = ("label", label_id)
        terminals[(label.x, label.y)].append(key)
        union.union(key, ("name", _global(label.name)))
    # ------------------------------------------------------------- wires
    segments: list[tuple[str, Point, Point]] = []
    for wire_id, wire in schematic.wires.items():
        key = ("wire", wire_id)
        union.find(key)
        for point in wire.points:
            terminals[point].append(key)
        segments.extend((wire_id, a, b) for a, b in wire.segments())
    by_x: dict[int, list[Point]] = defaultdict(list)
    by_y: dict[int, list[Point]] = defaultdict(list)
    for point in terminals:
        by_x[point[0]].append(point)
        by_y[point[1]].append(point)
    passes: dict[Point, int] = defaultdict(int)  # wires passing through a point (not ending there)
    wire_ends = {(wire_id, wire.points[0]) for wire_id, wire in schematic.wires.items()}
    wire_ends |= {(wire_id, wire.points[-1]) for wire_id, wire in schematic.wires.items()}
    for wire_id, a, b in segments:
        candidates = by_x[a[0]] if a[0] == b[0] else by_y[a[1]] if a[1] == b[1] else []
        for point in candidates:
            if _on_segment(point, a, b):
                for key in terminals[point]:
                    union.union(("wire", wire_id), key)
                if point not in (a, b) and not any(k == ("wire", wire_id) for k in terminals[point]):
                    passes[point] += 1
    # every terminal on one point is connected
    for point, keys in terminals.items():
        for key in keys[1:]:
            union.union(keys[0], key)
    # ---------------------------------------------------------- junctions
    for point, keys in terminals.items():
        # (a wire's corner continues on both sides: it counts as two ends)
        ends = sum(1 if (key[1], point) in wire_ends else 2 for key in keys if key[0] == "wire") \
            + 2 * passes.get(point, 0)
        others = sum(1 for key in keys if key[0] == "pin")
        if ends and ends + others >= 3:
            result.junctions.append(point)
    result.junctions.sort()
    # -------------------------------------------------------------- names
    classes: dict = defaultdict(list)
    for key in list(union.parent):
        classes[union.find(key)].append(key)
    named: dict = {}
    probe_names: dict = {}  # root -> (name, probe)
    for ref, component in schematic.components.items():
        spec = specs.get(ref)
        if spec is not None and spec.role in ("probe.v", "probe.logic"):
            name = effective_params(component, spec).get("name")
            if name:
                for pin, _point in placed_pins(component, spec):
                    probe_names.setdefault(union.find(("pin", PinRef(ref, pin.number))), (name, ref))
    order: list = []
    for root, keys in classes.items():
        names = sorted({key[1] for key in keys if key[0] == "name"})
        grounds = [name for name in names if name.upper() in GROUND_NAMES]
        if grounds:
            named[root] = "0"
            others = [name for name in names if name.upper() not in GROUND_NAMES]
        else:
            others = names
            if names:
                named[root] = names[0]
        if len(others) > 1 or (grounds and others):
            item = next((key[1] for key in keys if key[0] == "label"), "")
            result.problems.append(Problem("warning", f"one net has several names: {', '.join(names)}", item))
        if root not in named and any(key[0] in ("pin", "wire") for key in keys):
            points = [point for point, there in terminals.items() if any(union.find(k) == root for k in there)]
            pins = [key[1] for key in keys if key[0] == "pin"]
            order.append((min(points) if points else (1 << 30, 1 << 30), str(pins[0]) if pins else "", root))
    # a probe names its net - unless the name is taken (a label, another probe on another net): two nets
    # of one name would be one node in the netlist
    taken = set(named.values())
    for root, (name, ref) in sorted(probe_names.items(), key=lambda item: item[1][1]):
        if root in named:
            continue
        name = _global(name)
        if name in taken:
            result.problems.append(Problem("warning", f"another net is named {name} already: the probe keeps its "
                                                      "name, its net gets one of its own", ref))
            continue
        named[root] = name
        taken.add(name)
    number = 1
    for _point, _pin, root in sorted(order):
        if root in named:
            continue
        while f"N{number}" in taken:
            number += 1
        named[root] = f"N{number}"
        taken.add(named[root])
    # ------------------------------------------------------------ results
    for ref in schematic.components:
        spec = specs.get(ref)
        if spec is None:
            continue
        for pin in spec.symbol.pins_of(schematic.components[ref].unit):
            pin_ref = PinRef(ref, pin.number)
            name = named.get(union.find(("pin", pin_ref)))
            if name is None:
                continue
            result.pin_net[pin_ref] = name
            result.nets.setdefault(name, []).append(pin_ref)
    for wire_id in schematic.wires:
        name = named.get(union.find(("wire", wire_id)))
        if name is not None:
            result.wire_net[wire_id] = name
    for label_id in schematic.labels:
        name = named.get(union.find(("label", label_id)))
        if name is not None:
            result.label_net[label_id] = name
    # ----------------------------------------------------------- problems
    for root, keys in classes.items():
        pins = [key[1] for key in keys if key[0] == "pin"]
        wires = [key[1] for key in keys if key[0] == "wire"]
        has_name = any(key[0] == "name" for key in keys)
        if len(pins) == 1 and not has_name:
            pin_ref = pins[0]
            spec = specs.get(pin_ref.component)
            pin = next((p for p in spec.symbol.pins if p.number == pin_ref.pin), None) if spec else None
            if pin is not None and pin.kind == "no_connect":
                continue
            if spec is not None and spec.role in ("mcu", "probe.scope"):
                continue  # (most pins of a microcontroller, channels of an oscilloscope are not used)
            label = f"{pin.label} ({pin.number})" if pin is not None and pin.label else pin_ref.pin
            text = "is not connected" if not wires else "is the only pin on its net"
            result.problems.append(Problem("warning", f"pin {label} {text}", pin_ref.component, pin_ref.pin))
        elif not pins and wires and not has_name:
            result.problems.append(Problem("warning", "the wire connects nothing", wires[0]))
    # a part whose pins are all on one net does nothing (a wire runs through it, a pin on another's wire)
    by_component: dict[str, set[str]] = {}
    for pin_ref, name in result.pin_net.items():
        by_component.setdefault(pin_ref.component, set()).add(name)
    for ref, names in by_component.items():
        spec = specs.get(ref)
        if spec is None or spec.role or len(names) != 1:
            continue
        connected = [pin_ref for pin_ref in result.pin_net if pin_ref.component == ref]
        if len(connected) >= 2:
            result.problems.append(Problem("warning", "all its pins are on one net: it is shorted", ref))
    return result


def _global(name: str) -> str:
    text = str(name).strip()
    return "0" if text.upper() in GROUND_NAMES else text
