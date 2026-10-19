# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The file of a circuit (``*.circuit.yaml``)::

    circuit: Low pass
    version: 1
    settings: {step: 10 us, vcc: 5 V}
    components:
      V1: {type: source.sine, at: [40, 100], params: {amplitude: 1 V, frequency: 1 kHz}}
      R1: {type: resistor, at: [120, 60], rotation: 90, params: {value: 10k}}
      GND1: {type: ground, at: [40, 160]}
    wires:
      - [[40, 80], [40, 60], [100, 60]]
    labels:
      - {name: OUT, at: [200, 60]}
    groups:
      - {name: Filter, comment: the corner at 1 kHz, color: blue, members: [R1, C1, w2]}
    analyses:
      ac: {start: 10 Hz, stop: 1 MHz}

Written in a stable order (one line per component, wire and label) so that a change of the circuit is
a small change of the file. Keys this version does not know are kept. A component, wire or label can
carry a ``comment``; a wire with one is written ``{points: [...], comment: ...}``. A group names its
members: components by reference, wires and labels by their place in the file (``w2``: the second wire,
``l1``: the first label).
"""

from __future__ import annotations

import os
import re
from typing import Any

import yaml

from ..core import yaml_text
from ..core.files import atomic_open
from . import CircuitError
from .model import GROUP_COLORS, Component, Group, NetLabel, Schematic, Wire

VERSION = 1
_KNOWN = ("circuit", "description", "version", "settings", "components", "wires", "labels", "groups", "analyses",
          "libraries")


def _point(value, what: str) -> tuple[int, int]:
    try:
        x, y = value
        return int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        raise CircuitError(f"{what}: {value!r} is no point [x, y]") from None


def from_data(data: Any) -> Schematic:
    if not isinstance(data, dict):
        raise CircuitError("a circuit file holds a mapping (circuit: ..., components: ...)")
    version = data.get("version", VERSION)
    if not isinstance(version, int) or version > VERSION:
        raise CircuitError(f"the circuit was written by a newer openSciLab (version {version})")
    schematic = Schematic(name=str(data.get("circuit") or "Circuit"), description=str(data.get("description") or ""))
    schematic.settings = {str(key): str(value) for key, value in (data.get("settings") or {}).items()}
    components = data.get("components") or {}
    if not isinstance(components, dict):
        raise CircuitError("components: a mapping of references to components")
    for ref, item in components.items():
        if not isinstance(item, dict) or "type" not in item:
            raise CircuitError(f"{ref}: a component needs a type")
        if re.match(r"^[wlg]\d+$", str(ref)):
            raise CircuitError(f"{ref}: the name of a wire, label or group, no reference")
        x, y = _point(item.get("at", (0, 0)), str(ref))
        rotation = int(item.get("rotation", 0))
        if rotation % 90:
            raise CircuitError(f"{ref}: the rotation is a multiple of 90")
        params = {str(key): "" if value is None else str(value) for key, value in (item.get("params") or {}).items()}
        schematic.components[str(ref)] = Component(str(ref), str(item["type"]), x, y, rotation % 360,
                                                   bool(item.get("mirror", False)), params, int(item.get("unit", 1)),
                                                   str(item.get("comment") or ""))
    for index, item in enumerate(data.get("wires") or []):
        points, comment = (item.get("points"), str(item.get("comment") or "")) if isinstance(item, dict) else (item, "")
        # (named by its place in the file, as groups name it - also when one before it is empty)
        wire = Wire(f"w{index + 1}", [_point(point, "a wire") for point in points or []], comment)
        if len(wire.points) >= 2:
            schematic.wires[wire.id] = wire
    for index, item in enumerate(data.get("labels") or []):
        if not isinstance(item, dict) or not item.get("name"):
            raise CircuitError("a label needs a name")
        x, y = _point(item.get("at", (0, 0)), "a label")
        label = NetLabel(f"l{index + 1}", str(item["name"]), x, y, int(item.get("rotation", 0)) % 360,
                         str(item.get("comment") or ""))
        schematic.labels[label.id] = label
    for index, item in enumerate(data.get("groups") or []):
        if not isinstance(item, dict):
            raise CircuitError("a group is a mapping {name: ..., members: [...]}")
        members = [str(member) for member in item.get("members") or []]
        members = [member for member in members if member in schematic.components or member in schematic.wires
                   or member in schematic.labels]
        if members:
            color = str(item.get("color") or "")
            group = Group(f"g{index + 1}", str(item.get("name") or ""), str(item.get("comment") or ""), members,
                          color if color in GROUP_COLORS else "")
            schematic.groups[group.id] = group
    for name, params in (data.get("analyses") or {}).items():
        schematic.analyses[str(name)] = {str(key): str(value) for key, value in (params or {}).items()}
    schematic.libraries = [str(path) for path in data.get("libraries") or []]
    schematic.extra = {key: value for key, value in data.items() if key not in _KNOWN}
    return schematic


def loads(text: str) -> Schematic:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise CircuitError(f"no YAML: {error}") from None
    return from_data(data or {})


def load(path: str) -> Schematic:
    with open(path, encoding="utf-8") as handle:
        return loads(handle.read())


class _Dumper(yaml_text.SafeDumper):
    """Text with space at its ends (also Unicode's) in double quotes: YAML drops it from plain text."""


def _represent(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if data != data.strip() or (data and not data.strip()):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return yaml_text.represent_str(dumper, data)


_Dumper.add_representer(str, _represent)


def _inline(value: Any) -> str:
    text = yaml.dump(value, Dumper=_Dumper, default_flow_style=True, width=1 << 30, allow_unicode=True,
                     sort_keys=False)
    text = text.rstrip("\n")
    text = text.removesuffix("\n...")
    return text.strip("\n")


def to_data(schematic: Schematic) -> dict[str, Any]:
    data: dict[str, Any] = {"circuit": schematic.name}
    if schematic.description:
        data["description"] = schematic.description
    data["version"] = VERSION
    if schematic.settings:
        data["settings"] = dict(schematic.settings)
    if schematic.libraries:
        data["libraries"] = list(schematic.libraries)
    data["components"] = {ref: _component_data(component) for ref, component in schematic.components.items()}
    data["wires"] = [_wire_data(wire) for wire in schematic.wires.values()]
    data["labels"] = [_label_data(label) for label in schematic.labels.values()]
    if schematic.groups:
        # (wires and labels by their place in the file: their ids are not written)
        names = {ref: ref for ref in schematic.components}
        names.update({wire_id: f"w{index + 1}" for index, wire_id in enumerate(schematic.wires)})
        names.update({label_id: f"l{index + 1}" for index, label_id in enumerate(schematic.labels)})
        groups = [_group_data(group, names) for group in schematic.groups.values()]
        data["groups"] = [group for group in groups if group["members"]]
    if schematic.analyses:
        data["analyses"] = {name: dict(params) for name, params in schematic.analyses.items()}
    data.update(schematic.extra)
    return data


def _component_data(component: Component) -> dict[str, Any]:
    item: dict[str, Any] = {"type": component.type, "at": [component.x, component.y]}
    if component.rotation:
        item["rotation"] = component.rotation
    if component.mirror:
        item["mirror"] = True
    if component.unit != 1:
        item["unit"] = component.unit
    if component.params:
        item["params"] = dict(component.params)
    if component.comment:
        item["comment"] = component.comment
    return item


def _wire_data(wire: Wire):
    points = [list(point) for point in wire.points]
    return {"points": points, "comment": wire.comment} if wire.comment else points


def _group_data(group: Group, names: dict[str, str]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    if group.name:
        item["name"] = group.name
    if group.comment:
        item["comment"] = group.comment
    if group.color:
        item["color"] = group.color
    item["members"] = [names[member] for member in group.members if member in names]
    return item


def _label_data(label: NetLabel) -> dict[str, Any]:
    item: dict[str, Any] = {"name": label.name, "at": [label.x, label.y]}
    if label.rotation:
        item["rotation"] = label.rotation
    if label.comment:
        item["comment"] = label.comment
    return item


def dumps(schematic: Schematic) -> str:
    data = to_data(schematic)
    lines = []
    for key, value in data.items():
        if key == "components":
            lines.append("components:" if value else "components: {}")
            lines.extend(f"  {_inline(ref)}: {_inline(item)}" for ref, item in value.items())
        elif key in ("wires", "labels", "groups"):
            lines.append(f"{key}:" if value else f"{key}: []")
            lines.extend(f"  - {_inline(item)}" for item in value)
        elif key == "analyses":
            lines.append("analyses:")
            lines.extend(f"  {_inline(name)}: {_inline(params)}" for name, params in value.items())
        elif isinstance(value, (dict, list)) and key not in ("settings", "libraries"):
            text = yaml_text.dump({key: value}, default_flow_style=False, allow_unicode=True, sort_keys=False)
            lines.append(text.rstrip("\n"))
        else:
            lines.append(f"{_inline(key)}: {_inline(value)}")
    return "\n".join(lines) + "\n"


def save(schematic: Schematic, path: str) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with atomic_open(path, newline="\n") as handle:
        handle.write(dumps(schematic))


def roundtrip_problem(schematic: Schematic) -> str:
    """Why the file of ``schematic`` would not read back as it is ("" when it does)."""
    again = loads(dumps(schematic))
    return "" if to_data(again) == to_data(schematic) else "the file does not read back as written"
