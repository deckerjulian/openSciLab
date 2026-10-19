# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A circuit: components, wires and net labels on a grid, its settings and analyses.

Positions are scene units (:data:`~openscilab.circuits.GRID` apart). Ground, power symbols, probes and
microcontrollers are components like the others (their spec has a ``role``). The document edits a
copy and keeps the one before for undo (:meth:`Schematic.copy`).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from . import GRID, CircuitError

_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: the ids of wires (w1), labels (l1) and groups (g1): no component may be called so
_INTERNAL_ID = re.compile(r"^[wlg]\d+$")
Point = tuple[int, int]


def snap(value: float, step: int = GRID) -> int:
    return int(round(value / step) * step)


@dataclass
class Component:
    #: its reference: R1, C2, U3 (unique in the circuit, also its name in the netlist)
    id: str
    #: the catalog's name of its kind (``resistor``, ``kicad:Device:R``)
    type: str
    x: int = 0
    y: int = 0
    #: degrees counter-clockwise, a multiple of 90
    rotation: int = 0
    mirror: bool = False
    #: parameters as written (values with units, model names); missing ones have their default
    params: dict[str, str] = field(default_factory=dict)
    #: which unit of a part with several (the gates of a 7400)
    unit: int = 1
    #: a note of the author, shown beside it
    comment: str = ""


@dataclass
class Wire:
    id: str
    #: corners of an orthogonal polyline
    points: list[Point] = field(default_factory=list)
    #: a note of the author, shown beside it
    comment: str = ""

    def segments(self) -> list[tuple[Point, Point]]:
        return list(zip(self.points, self.points[1:]))


@dataclass
class NetLabel:
    id: str
    #: the name of the net; labels of the same name are one net
    name: str
    x: int = 0
    y: int = 0
    rotation: int = 0
    #: a note of the author, shown beside it
    comment: str = ""


@dataclass
class Group:
    """Components, wires and labels that belong together (a block of the circuit: "Positive rail"),
    drawn in a frame with their name and comment."""

    id: str
    name: str = ""
    comment: str = ""
    #: the ids of its members (components, wires, labels)
    members: list[str] = field(default_factory=list)
    #: a light background ("blue", "green", "yellow", "red", "purple", "gray"); "": none
    color: str = ""


#: the colours a group's background can have
GROUP_COLORS = ("blue", "green", "yellow", "red", "purple", "gray")


#: the settings of a circuit's simulation and their defaults
SETTINGS = {
    "step": "10 us",       # the time step of the live simulation and of transients
    "max_step": "",        # the longest step ngspice may take (empty: ngspice decides)
    "vcc": "5 V",          # the supply of digital parts, logic thresholds, microcontrollers
    "temperature": "27",   # °C
    "tolerance": "0.001",  # the relative error ngspice allows (reltol): larger is faster, less exact
    "speed": "1",          # live in real time: simulated seconds per second
    "start": "settled",    # settled: from its operating point; power on: every capacitor empty (as switched on)
}
#: settings with a choice of words instead of a value
SETTING_CHOICES = {"start": ("settled", "power on")}

#: the analyses and their parameters (defaults)
ANALYSES = {
    "op": {},
    "dc": {"source": "V1", "start": "0", "stop": "5", "step": "0.1"},
    "ac": {"start": "10 Hz", "stop": "1 MHz", "points": "50", "scale": "dec"},
    "tran": {"stop": "10 ms", "step": "10 us"},
    "sp": {"start": "1 MHz", "stop": "1 GHz", "points": "100", "scale": "dec"},
    "noise": {"output": "", "source": "V1", "start": "10 Hz", "stop": "1 MHz", "points": "20"},
}


@dataclass
class Schematic:
    name: str = "Circuit"
    description: str = ""
    components: dict[str, Component] = field(default_factory=dict)
    wires: dict[str, Wire] = field(default_factory=dict)
    labels: dict[str, NetLabel] = field(default_factory=dict)
    #: groups of components, wires and labels, each in a frame with a name and a comment
    groups: dict[str, Group] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)
    #: analyses with their parameters, in their order (``ac: {start: 10 Hz, ...}``)
    analyses: dict[str, dict[str, str]] = field(default_factory=dict)
    #: SPICE model libraries of the circuit (paths relative to its file)
    libraries: list[str] = field(default_factory=list)
    #: other keys of the file, kept as they are
    extra: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> Schematic:
        return copy.deepcopy(self)

    def setting(self, name: str) -> str:
        value = self.settings.get(name)
        return str(value) if value not in (None, "") else SETTINGS.get(name, "")

    # ------------------------------------------------------------ names
    def free_id(self, prefix: str, taken: set[str] | None = None) -> str:
        prefix = re.sub(r"[^A-Za-z0-9_]", "", prefix) or "U"
        if prefix[0].isdigit():
            prefix = f"U{prefix}"
        used = set(self.components) | set(taken or ())
        number = 1
        while f"{prefix}{number}" in used:
            number += 1
        return f"{prefix}{number}"

    def _group_member_ids(self) -> set[str]:
        return {member for group in self.groups.values() for member in group.members}

    def free_wire_id(self) -> str:
        number = len(self.wires) + 1
        taken = self._group_member_ids()
        while f"w{number}" in self.wires or f"w{number}" in taken:
            number += 1
        return f"w{number}"

    def free_group_id(self) -> str:
        number = len(self.groups) + 1
        while f"g{number}" in self.groups:
            number += 1
        return f"g{number}"

    def add_group(self, members, name: str = "", comment: str = "") -> Group:
        """A group of ``members`` (ids of components, wires, labels; a member leaves its other group)."""
        wanted = [item for item in dict.fromkeys(members) if item in self.components or item in self.wires
                  or item in self.labels]
        if not wanted:
            raise CircuitError("a group needs components, wires or labels")
        for group in self.groups.values():
            group.members = [item for item in group.members if item not in wanted]
        self.groups = {key: group for key, group in self.groups.items() if group.members}
        group = Group(self.free_group_id(), name, comment, wanted)
        self.groups[group.id] = group
        return group

    def group_of(self, item_id: str) -> Group | None:
        return next((group for group in self.groups.values() if item_id in group.members), None)

    def free_label_id(self) -> str:
        number = len(self.labels) + 1
        taken = self._group_member_ids()
        while f"l{number}" in self.labels or f"l{number}" in taken:
            number += 1
        return f"l{number}"

    def prune_groups(self) -> None:
        """Groups forget members that are gone; a group without members goes."""
        for group in self.groups.values():
            group.members = [item for item in group.members
                             if item in self.components or item in self.wires or item in self.labels]
        self.groups = {key: group for key, group in self.groups.items() if group.members}

    # ---------------------------------------------------------- editing
    def add_component(self, type_name: str, x: float, y: float, prefix: str = "U", component_id: str = "",
                      params: dict[str, str] | None = None, rotation: int = 0, mirror: bool = False,
                      unit: int = 1, comment: str = "") -> Component:
        component_id = component_id or self.free_id(prefix)
        if not _REF.match(component_id):
            raise CircuitError(f"{component_id!r} is no reference (letters, digits, _)")
        if _INTERNAL_ID.match(component_id):
            raise CircuitError(f"{component_id} is the name of a wire, label or group: not a reference")
        if component_id in self.components:
            raise CircuitError(f"there is a component {component_id} already")
        component = Component(component_id, type_name, snap(x), snap(y), rotation % 360, mirror,
                              dict(params or {}), unit, comment)
        self.components[component_id] = component
        return component

    def rename_component(self, old: str, new: str) -> None:
        if old == new:
            return
        if not _REF.match(new) or new in self.components:
            raise CircuitError(f"{new!r} is no free reference")
        if _INTERNAL_ID.match(new):
            raise CircuitError(f"{new} is the name of a wire, label or group: not a reference")
        self.components = {(new if key == old else key): value for key, value in self.components.items()}
        self.components[new].id = new
        for group in self.groups.values():
            group.members = [new if item == old else item for item in group.members]

    def add_wire(self, points: list[tuple[float, float]], comment: str = "") -> Wire | None:
        """A wire through ``points`` (snapped; corners on one line merged); ``None`` when nothing is left."""
        cleaned = simplify([(snap(x), snap(y)) for x, y in points])
        if len(cleaned) < 2:
            return None
        wire = Wire(self.free_wire_id(), cleaned, comment)
        self.wires[wire.id] = wire
        return wire

    def add_label(self, name: str, x: float, y: float, rotation: int = 0, comment: str = "") -> NetLabel:
        label = NetLabel(self.free_label_id(), name, snap(x), snap(y), rotation % 360, comment)
        self.labels[label.id] = label
        return label

    def remove(self, ids) -> None:
        """Take out components, wires, labels and groups (a group's members stay; a group without
        members goes)."""
        ids = set(ids)
        for item_id in ids:
            self.components.pop(item_id, None)
            self.wires.pop(item_id, None)
            self.labels.pop(item_id, None)
            self.groups.pop(item_id, None)
        for group in self.groups.values():
            group.members = [item for item in group.members if item not in ids]
        self.groups = {key: group for key, group in self.groups.items() if group.members}

    def items(self) -> list[str]:
        return [*self.components, *self.wires, *self.labels]


def simplify(points: list[Point]) -> list[Point]:
    """Drop repeated points and corners that lie on a straight line."""
    result: list[Point] = []
    for point in points:
        if result and result[-1] == point:
            continue
        if len(result) >= 2:
            (x0, y0), (x1, y1) = result[-2], result[-1]
            if (x0 == x1 == point[0]) or (y0 == y1 == point[1]):
                result[-1] = point
                continue
        result.append(point)
    return result


def orthogonal(start: Point, end: Point, horizontal_first: bool = True) -> list[Point]:
    """An L-shaped route from ``start`` to ``end``."""
    if start[0] == end[0] or start[1] == end[1]:
        return [start, end]
    corner = (end[0], start[1]) if horizontal_first else (start[0], end[1])
    return [start, corner, end]


def drag_wires(schematic: Schematic, moves: dict[Point, Point]) -> None:
    """Wires follow pins that moved: an end on a point of ``moves`` (old -> new position) goes along;
    the corner next to it moves too, so the wire stays orthogonal."""
    if not moves:
        return
    for wire in schematic.wires.values():
        points = list(wire.points)
        changed = False
        for end in (0, 1):
            index = 0 if end == 0 else len(points) - 1
            old = points[index]
            if old not in moves:
                continue
            new = moves[old]
            neighbour = 1 if index == 0 else len(points) - 2
            if len(points) == 2:
                # a straight wire: add a corner when it would become slanted
                other = points[neighbour]
                if new[0] != other[0] and new[1] != other[1]:
                    corner = (other[0], new[1]) if old[1] == other[1] else (new[0], other[1])
                    points = [new, corner, other] if index == 0 else [other, corner, new]
                    changed = True
                    continue  # (the other end, now at another index, is looked at below)
                points[index] = new
            else:
                other = points[neighbour]
                if old[1] == other[1]:  # the segment was horizontal: its corner takes the new height
                    points[neighbour] = (other[0], new[1])
                elif old[0] == other[0]:
                    points[neighbour] = (new[0], other[1])
                points[index] = new
            changed = True
        if changed:
            wire.points = simplify(points)
    gone = [wire_id for wire_id, wire in schematic.wires.items() if len(wire.points) < 2]
    if gone:
        schematic.remove(gone)  # (groups forget them too)


def _on_wire(point: Point, wire: Wire) -> bool:
    for (x1, y1), (x2, y2) in wire.segments():
        if (x1 == x2 == point[0] and min(y1, y2) <= point[1] <= max(y1, y2)) or \
                (y1 == y2 == point[1] and min(x1, x2) <= point[0] <= max(x1, x2)):
            return True
    return False


def apply_moves(schematic: Schematic, moves: dict, pins_of, wires: dict | None = None) -> None:
    """Move components and labels (``moves``: id -> new position) so that they keep their connections:
    wires ending at a moved pin follow it (staying orthogonal), wires between moved things move along, and
    a pin that touched a wire elsewhere than at its end - or a pin or label that stays - gets a new piece
    of wire from where it was. ``wires`` (id -> (dx, dy)) move as a whole (the members of a dragged group);
    their ends count as moved points. ``pins_of(component)`` gives the positions of a component's pins."""
    point_moves: dict[Point, Point] = {}
    for wire_id, (dx, dy) in (wires or {}).items():
        wire = schematic.wires.get(wire_id)
        if wire is None or not (dx or dy):
            continue
        for point in (wire.points[0], wire.points[-1]):
            point_moves.setdefault(point, (point[0] + dx, point[1] + dy))
    staying_points: set[Point] = set()
    for ref, component in schematic.components.items():
        if ref not in moves:
            staying_points.update(pins_of(component))
    for label_id, label in schematic.labels.items():
        if label_id not in moves:
            staying_points.add((label.x, label.y))
    for item_id, (x, y) in moves.items():
        if item_id in schematic.components:
            component = schematic.components[item_id]
            old = pins_of(component)
            component.x, component.y = snap(x), snap(y)
            for before, after in zip(old, pins_of(component)):
                point_moves[before] = after
        elif item_id in schematic.labels:
            label = schematic.labels[item_id]
            before = (label.x, label.y)
            label.x, label.y = snap(x), snap(y)
            point_moves[before] = (label.x, label.y)
    if not point_moves:
        return
    # wires between moved things (both ends by the same step) move along as a whole
    whole: dict[str, tuple[int, int]] = {wire_id: (dx, dy) for wire_id, (dx, dy) in (wires or {}).items()
                                         if wire_id in schematic.wires and (dx or dy)}
    for wire_id, wire in schematic.wires.items():
        if wire_id in whole:
            continue
        first, last = wire.points[0], wire.points[-1]
        if first in point_moves and last in point_moves:
            step = (point_moves[first][0] - first[0], point_moves[first][1] - first[1])
            if step == (point_moves[last][0] - last[0], point_moves[last][1] - last[1]):
                whole[wire_id] = step
    # a new piece of wire where a moved point was connected by something that does not follow it:
    # a pin or label that stays, or a wire passing through (not ending at) the point
    stubs = []
    for before, after in point_moves.items():
        if before == after:
            continue
        touching = before in staying_points or any(
            wire_id not in whole and before not in (wire.points[0], wire.points[-1]) and _on_wire(before, wire)
            for wire_id, wire in schematic.wires.items())
        if touching:
            stubs.append(orthogonal(before, after))
    for wire_id, (dx, dy) in whole.items():
        wire = schematic.wires[wire_id]
        wire.points = [(px + dx, py + dy) for px, py in wire.points]
    # the other wires: ends at moved points follow
    saved = schematic.wires
    schematic.wires = {wire_id: wire for wire_id, wire in saved.items() if wire_id not in whole}
    drag_wires(schematic, point_moves)
    kept = schematic.wires
    schematic.wires = {wire_id: wire for wire_id, wire in saved.items() if wire_id in whole or wire_id in kept}
    for points in stubs:
        schematic.add_wire(points)
