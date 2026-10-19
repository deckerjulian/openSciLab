# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the current flows in a running circuit: the current into every terminal of every element and
along every piece of wire on the schematic (the *current view*). No Qt.

What ngspice gives while it runs:

* the voltages of all nodes and the branch currents of voltage sources, inductors and E/H sources -
  for nothing (they are unknowns of its equations);
* the currents of the other devices when they are saved (``@c1[i]``, ``@q1[ic]`` ...): computed at every
  point, which costs - so they are saved only while the current view is on (:func:`saved_vectors`);
* none for the terminals of a subcircuit: a 0 V source in front of each of them is an ammeter
  (:func:`with_ammeters`).

Every current is the one *into* an element at a terminal (ngspice's convention: into the first node).
Resistors are computed from their voltages; what is still unknown (XSPICE bridges, transmission lines)
follows from Kirchhoff's current law at every node (least squares).

On the schematic a net is a graph of points (wire ends, corners, pins, T-junctions) joined by the pieces
of its wires; the components inject their pins' currents at the pins, and what comes from elements that
are no drawn pin (the supply of a rail symbol, the ground) enters through a hub joined to the net's
labels, rail and ground symbols. The pieces' currents solve that network with conductances of
1/length - wires are ideal, so where they form a loop the split is a choice, the sums are right.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import values

#: elements whose branch current ngspice computes anyway: ``<name>#branch``
BRANCH = "VLEH"
#: the saved currents of the other elements: per terminal, the vector of the current into it (None: 0 A,
#: "rest": minus the others)
SAVED = {
    "C": ("i", "rest"), "D": ("id", "rest"), "S": ("i", "rest", None, None), "B": ("i", "rest"),
    "I": ("c", "rest"), "W": ("i", "rest"), "F": ("i", "rest"), "G": ("i", "rest", None, None),
    "Q": ("ic", "ib", "rest"), "J": ("id", "ig", "rest"), "M": ("id", None, "rest"),
}
#: how many of an element's first words are its nodes (the rest: a value, a model, parameters)
NODES = {"R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "D": 2, "B": 2, "F": 2, "H": 2, "W": 2, "S": 4, "E": 4, "G": 4,
         "Q": 3, "J": 3, "M": 3, "T": 4, "O": 4, "U": 3, "Y": 4}
#: the prefix of the ammeters in front of a subcircuit's terminals
AMMETER = "V_am_"
_PARAMS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Element:
    """One element of the netlist: its name, its letter, its nodes and how its currents are known."""

    name: str
    letter: str
    nodes: list[str]
    #: per terminal: "branch" / "@..." vector / "rest" / None (0 A) / "unknown" / "resistor" / "ammeter"
    how: list = field(default_factory=list)
    resistance: float = 0.0


def _top_level(lines: list[str]):
    """The element lines of the circuit itself (not inside ``.subckt`` … ``.ends``, no dot lines)."""
    depth = 0
    for line in lines:
        words = line.split()
        if not words:
            continue
        keyword = words[0].lower()
        if keyword.startswith(".subckt"):
            depth += 1
            continue
        if keyword.startswith(".ends"):
            depth = max(0, depth - 1)
            continue
        if depth or keyword.startswith((".", "*", "+")):
            continue
        yield line, words


def _subckt_nodes(words: list[str]) -> list[str]:
    """The nodes of an ``X`` line (its words without the subcircuit's name and parameters)."""
    rest = [word for word in words[1:] if not _PARAMS.match(word) and word.lower() not in ("params:", "param:")]
    return rest[:-1]


def parse(lines: list[str]) -> list[Element]:
    """The elements of a netlist with how the current into each terminal is known."""
    found = []
    for _line, words in _top_level(lines):
        name = words[0]
        letter = name[0].upper()
        if letter == "K":  # (a coupling: no terminals)
            continue
        if letter == "X":
            nodes = _subckt_nodes(words)
            found.append(Element(name, letter, nodes, ["ammeter"] * len(nodes)))
            continue
        if letter == "A":  # (XSPICE: the analog nodes in its brackets; their currents follow from KCL)
            nodes = [word.strip("[]~%") for word in words[1:-1] if word.strip("[]~%")]
            nodes = [node for node in nodes if not node.lower().startswith("d_")]
            # (an analog-to-digital bridge only looks at its node: no current)
            how = None if words[-1].lower() == "osl_adc" else "unknown"
            found.append(Element(name, letter, nodes, [how] * len(nodes)))
            continue
        count = NODES.get(letter)
        if count is None or len(words) <= count:
            continue
        nodes = words[1:1 + count]
        element = Element(name, letter, nodes)
        if letter == "R":
            resistance = values.parse_optional(words[3]) if len(words) > 3 else None
            if resistance:
                element.how, element.resistance = ["resistor", "rest"], resistance
            else:
                element.how = ["unknown", "rest"]
        elif letter in BRANCH:
            element.how = ["branch", "rest"] + [None] * (count - 2)
        elif letter in SAVED:
            element.how = [f"@{name.lower()}[{part}]" if part not in ("rest", None) else part for part in SAVED[letter]]
        else:
            element.how = ["unknown"] * (count - 1) + ["rest"]
        found.append(element)
    return found


def saved_vectors(lines: list[str]) -> list[str]:
    """The device currents to save so that the current view knows them (``@q1[ic]`` ...)."""
    vectors = []
    for element in parse(lines):
        vectors += [how for how in element.how if isinstance(how, str) and how.startswith("@")]
    return list(dict.fromkeys(vectors))


def with_ammeters(lines: list[str]) -> list[str]:
    """``lines`` with a 0 V source in front of each terminal of every subcircuit instance."""
    result = []
    depth = 0
    for line in lines:
        words = line.split()
        keyword = words[0].lower() if words else ""
        if keyword.startswith(".subckt"):
            depth += 1
        elif keyword.startswith(".ends"):
            depth = max(0, depth - 1)
        if depth or not words or words[0][0].upper() != "X":
            result.append(line)
            continue
        nodes = _subckt_nodes(words)  # (the first words after the name: the subcircuit's name and parameters follow)
        inner = [f"am_{_safe(node)}__{words[0]}_{position + 1}" for position, node in enumerate(nodes)]
        result += [f"{AMMETER}{words[0]}_{position + 1} {node} {inner[position]} DC 0"
                   for position, node in enumerate(nodes)]
        result.append(" ".join([words[0], *inner, *words[1 + len(nodes):]]))
    return result


def _safe(node: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", node) or "n"


def _number(value) -> float:
    if value is None:
        return math.nan
    value = value.real if isinstance(value, complex) else value
    return float(value)


class CurrentModel:
    """The currents of a circuit's elements from what ngspice gave (see the module)."""

    def __init__(self, lines: list[str]) -> None:
        self.elements = parse(lines)
        nodes = sorted({node for element in self.elements for node in element.nodes if node != "0"})
        self.node_index = {node: index for index, node in enumerate(nodes)}
        # the unknowns: per element, its "unknown" terminals (a "rest" terminal takes minus the others)
        self.unknowns: list[tuple[int, int]] = []
        for e_index, element in enumerate(self.elements):
            for t_index, how in enumerate(element.how):
                if how == "unknown":
                    self.unknowns.append((e_index, t_index))
        self._solver = None
        if self.unknowns and nodes:
            matrix = np.zeros((len(nodes), len(self.unknowns)))
            for column, (e_index, t_index) in enumerate(self.unknowns):
                element = self.elements[e_index]
                node = element.nodes[t_index]
                if node in self.node_index:
                    matrix[self.node_index[node], column] += 1.0
                rest = element.how.index("rest") if "rest" in element.how else None
                if rest is not None and element.nodes[rest] in self.node_index:
                    matrix[self.node_index[element.nodes[rest]], column] -= 1.0
            self._solver = np.linalg.pinv(matrix)

    def terminal_currents(self, latest: dict) -> dict[str, list[float]]:
        """Element name -> the current into each of its terminals (amperes)."""
        def value(name: str) -> float:
            return _number(latest.get(name.lower()))

        def voltage(node: str) -> float:
            return 0.0 if node == "0" else _number(latest.get(node.lower()))

        currents: dict[str, list[float]] = {}
        for element in self.elements:
            terminal = [0.0] * len(element.nodes)
            for index, how in enumerate(element.how):
                if how == "resistor":
                    terminal[index] = (voltage(element.nodes[0]) - voltage(element.nodes[1])) / element.resistance
                elif how == "branch":
                    terminal[index] = value(f"{element.name}#branch")
                elif how == "ammeter":
                    terminal[index] = value(f"{AMMETER}{element.name}_{index + 1}#branch")
                elif isinstance(how, str) and how.startswith("@"):
                    terminal[index] = value(how)
            currents[element.name] = terminal
        for name, terminal in currents.items():
            for index, number in enumerate(terminal):
                if math.isnan(number):
                    terminal[index] = 0.0
        has_unknown = {element.name: "unknown" in element.how for element in self.elements}

        def fill_rest(element: Element) -> None:  # (what goes in elsewhere comes out at its "rest" terminal)
            if "rest" in element.how:
                terminal = currents[element.name]
                rest = element.how.index("rest")
                terminal[rest] = -sum(number for index, number in enumerate(terminal) if index != rest)

        for element in self.elements:
            if not has_unknown[element.name]:
                fill_rest(element)
        # what is not measured: from Kirchhoff's current law at every node
        if self._solver is not None:
            residual = np.zeros(len(self.node_index))
            for element in self.elements:
                terminal = currents[element.name]
                for index, node in enumerate(element.nodes):
                    how = element.how[index]
                    if how == "unknown" or (how == "rest" and has_unknown[element.name]):
                        continue
                    if node in self.node_index:
                        residual[self.node_index[node]] += terminal[index]
            solved = self._solver @ (-residual)
            for (e_index, t_index), number in zip(self.unknowns, solved):
                currents[self.elements[e_index].name][t_index] = float(number)
        for element in self.elements:
            if has_unknown[element.name]:
                fill_rest(element)
        return currents

    def node_currents(self, currents: dict[str, list[float]], names) -> dict[str, float]:
        """Node -> the current flowing from the node into the elements ``names`` (amperes)."""
        found: dict[str, float] = {}
        wanted = set(names)
        for element in self.elements:
            if element.name not in wanted:
                continue
            for node, number in zip(element.nodes, currents.get(element.name, ())):
                found[node] = found.get(node, 0.0) + number
        return found


# ------------------------------------------------------------- the wires
@dataclass
class _NetGraph:
    points: list[tuple[int, int]]
    index: dict
    edges: list[tuple[int, int, str]]  # (point index a, point index b, wire id)
    solver: np.ndarray  # (edge currents per injection vector)
    hub: int | None


@dataclass
class WireFlow:
    """A piece of a wire and the current along it (amperes, positive from ``start`` to ``end``)."""

    start: tuple[int, int]
    end: tuple[int, int]
    current: float


class WireCurrents:
    """The currents along the wires of a schematic (see the module)."""

    def __init__(self, schematic, netlist, circuit, resolve) -> None:
        from .nets import placed_pins

        self.netlist = netlist
        self.circuit = circuit
        self.model = CurrentModel(circuit.lines)
        element_names = {element.name for element in self.model.elements}
        #: component -> its element names; elements of no component inject at the net's hub
        self.component_elements: dict[str, list[str]] = {}
        owned = set()
        for ref, lines in circuit.elements.items():
            names = [line.split()[0] for line in lines if line.split() and line.split()[0] in element_names]
            self.component_elements[ref] = names
            owned.update(names)
        # per net of the schematic: the pins on it (ref, pin, point) and its ports to the hub
        pins_of_net: dict[str, list] = {}
        ports_of_net: dict[str, list] = {}
        for ref, component in schematic.components.items():
            spec = resolve(component.type)
            if spec is None:
                continue
            for pin, point in placed_pins(component, spec):
                net = netlist.net_of(ref, pin.number)
                if net is None:
                    continue
                pins_of_net.setdefault(net, []).append((ref, pin.number, point))
                if spec.role in ("ground", "power"):
                    ports_of_net.setdefault(net, []).append(point)
        for label_id, label in schematic.labels.items():
            net = netlist.label_net.get(label_id)
            if net is not None:
                ports_of_net.setdefault(net, []).append((label.x, label.y))
        self.pins_of_net = pins_of_net
        # two-pin components (not probes, rails ...): the current through them, from pin 1 to pin 2
        self.two_pins: dict[str, tuple[str, str]] = {}
        for ref, component in schematic.components.items():
            spec = resolve(component.type)
            if spec is None or spec.role or ref not in self.component_elements:
                continue
            pins = sorted(spec.symbol.pins_of(component.unit), key=lambda pin: pin.number)
            if len(pins) != 2:
                continue
            first, second = (netlist.net_of(ref, pin.number) for pin in pins)
            if first is not None and second is not None and first != second:
                self.two_pins[ref] = (first, second)
        # the other components (a potentiometer, a transistor, a microcontroller): the current into each pin
        # (a microcontroller's ground pin on the ground: what its elements give the ground, by Kirchhoff)
        self.pin_nets: dict[str, list[tuple[str, str]]] = {}
        for net, pins in pins_of_net.items():
            for ref, pin, _point in pins:
                spec = resolve(schematic.components[ref].type)
                if (spec is not None and spec.role in ("", "mcu") and ref in self.component_elements
                        and ref not in self.two_pins):
                    self.pin_nets.setdefault(ref, []).append((pin, net))
        self.graphs: dict[str, _NetGraph] = {}
        wires_of_net: dict[str, list] = {}
        for wire_id, wire in schematic.wires.items():
            net = netlist.wire_net.get(wire_id)
            if net is not None:
                wires_of_net.setdefault(net, []).append((wire_id, wire))
        for net, wires in wires_of_net.items():
            self.graphs[net] = self._graph(wires, [point for _r, _p, point in pins_of_net.get(net, [])],
                                           ports_of_net.get(net, []))

    @staticmethod
    def _graph(wires, pin_points, ports) -> _NetGraph:
        special = set(pin_points) | set(ports)
        for _wire_id, wire in wires:
            special.update((wire.points[0], wire.points[-1]))
            special.update(wire.points)
        points: list = []
        index: dict = {}

        def node(point) -> int:
            if point not in index:
                index[point] = len(points)
                points.append(point)
            return index[point]

        edges = []
        for wire_id, wire in wires:
            for a, b in zip(wire.points, wire.points[1:]):
                on = [p for p in special if _between(p, a, b)]
                on.sort(key=lambda p: abs(p[0] - a[0]) + abs(p[1] - a[1]))
                chain = [a] + [p for p in on if p not in (a, b)] + [b]
                for p, q in zip(chain, chain[1:]):
                    if p != q:
                        edges.append((node(p), node(q), wire_id))
        for point in pin_points:
            node(point)
        hub = None
        connected_ports = [point for point in ports if point in index]
        if connected_ports:
            hub = len(points)
            points.append(("hub",))
            for point in connected_ports:
                edges.append((index[point], hub, ""))
        size = len(points)
        incidence = np.zeros((len(edges), size))
        conductance = np.zeros(len(edges))
        for row, (a, b, _wire) in enumerate(edges):
            incidence[row, a], incidence[row, b] = 1.0, -1.0
            pa, pb = points[a], points[b]
            length = abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) if len(pa) == 2 and len(pb) == 2 else 10.0
            conductance[row] = 1.0 / max(length, 1.0)
        laplacian = incidence.T @ (conductance[:, None] * incidence)
        solver = (conductance[:, None] * incidence) @ np.linalg.pinv(laplacian) if size else np.zeros((0, 0))
        return _NetGraph(points, index, edges, solver, hub)

    def compute(self, latest: dict) -> tuple[dict[str, list[WireFlow]], dict[str, float], dict[str, dict]]:
        """The currents along the wires (wire id -> its pieces), through the two-pin components (into
        pin 1, out at pin 2) and into the pins of the others (component -> pin -> amperes; pins on one
        net share its current), for the values ``latest`` of a running circuit."""
        currents = self.model.terminal_currents(latest)
        nets = self.circuit.nets
        through = {}
        for ref, (first, _second) in self.two_pins.items():
            into = self.model.node_currents(currents, self.component_elements.get(ref, ()))
            through[ref] = into.get(nets.get(first, first), 0.0)
        pins: dict[str, dict] = {}
        for ref, pin_nets in self.pin_nets.items():
            into = self.model.node_currents(currents, self.component_elements.get(ref, ()))
            sharing: dict[str, int] = {}
            for _pin, net in pin_nets:
                sharing[net] = sharing.get(net, 0) + 1
            pins[ref] = {pin: into.get(nets.get(net, net), 0.0) / sharing[net] for pin, net in pin_nets}
        return self._flows(currents), through, pins

    def flows(self, latest: dict) -> dict[str, list[WireFlow]]:
        """Wire id -> its pieces with their currents, for the values ``latest`` of a running circuit."""
        return self._flows(self.model.terminal_currents(latest))

    def _flows(self, currents: dict[str, list[float]]) -> dict[str, list[WireFlow]]:
        nets = self.circuit.nets  # (net as shown -> its SPICE name)
        result: dict[str, list[WireFlow]] = {}
        for net, graph in self.graphs.items():
            spice_net = nets.get(net, net)
            injection = np.zeros(len(graph.points))
            total = 0.0
            pins = self.pins_of_net.get(net, [])
            by_component: dict[str, list] = {}
            for ref, pin, point in pins:
                by_component.setdefault(ref, []).append(point)
            for ref, points in by_component.items():
                into = self.model.node_currents(currents, self.component_elements.get(ref, ())).get(spice_net, 0.0)
                for point in points:  # (current into the component leaves the net there)
                    if point in graph.index:
                        injection[graph.index[point]] -= into / len(points)
                        total -= into / len(points)
            if graph.hub is not None:
                injection[graph.hub] = -total  # (the rest of Kirchhoff's sum: supplies, the ground)
            elif abs(total) > 0:
                injection -= total / max(len(graph.points), 1)
            edge_currents = graph.solver @ injection if len(injection) else []
            for (a, b, wire_id), number in zip(graph.edges, edge_currents):
                if wire_id:
                    result.setdefault(wire_id, []).append(WireFlow(graph.points[a], graph.points[b], float(number)))
        return result


def _between(point, a, b) -> bool:
    (x, y), (x1, y1), (x2, y2) = point, a, b
    if x1 == x2 == x:
        return min(y1, y2) <= y <= max(y1, y2)
    if y1 == y2 == y:
        return min(x1, x2) <= x <= max(x1, x2)
    return False
