# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""KiCad's symbol libraries (``*.kicad_sym``, KiCad 6 to 10) as components.

The graphics convert one to one (millimetres to scene units, y turned down); parts derived from
another (``extends``) take its graphics and pins. Which parts can be simulated and how follows
KiCad: the ``Sim.*`` fields where a part has them, else what the reference and the pins say
(:mod:`.infer`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .. import UNITS_PER_MM
from ..library import ComponentSpec, ParamSpec
from ..symbols import Field, Pin, Shape, Symbol
from . import sexpr

SUFFIX = ".kicad_sym"
#: a library as a folder of one file per symbol (KiCad 9 and later in its sources)
FOLDER_SUFFIX = ".kicad_symdir"
_UNIT = re.compile(r"^(.*)_(\d+)_(\d+)$")


@dataclass
class RawSymbol:
    """A symbol as the library has it (before :func:`to_symbol`)."""

    name: str
    node: list
    properties: dict[str, str] = field(default_factory=dict)
    extends: str = ""
    power: bool = False

    @property
    def description(self) -> str:
        return self.properties.get("Description", "") or self.properties.get("ki_description", "")

    @property
    def keywords(self) -> str:
        return self.properties.get("ki_keywords", "")


def read_library(path: str) -> dict[str, RawSymbol]:
    """The symbols of a library by name (derived ones with their parent's body, see :func:`body`): a
    ``.kicad_sym`` file or a ``.kicad_symdir`` folder of them."""
    if os.path.isdir(path):
        symbols: dict[str, RawSymbol] = {}
        for name in sorted(os.listdir(path)):
            if name.endswith(SUFFIX):
                symbols.update(read_library(os.path.join(path, name)))
        return symbols
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    expressions = sexpr.parse(text)
    root = next((node for node in expressions if sexpr.head(node) == "kicad_symbol_lib"), None)
    if root is None:
        raise ValueError(f"{os.path.basename(path)} is no KiCad symbol library")
    symbols = {}
    for node in sexpr.children(root, "symbol"):
        if len(node) < 2:
            continue
        name = str(node[1])
        properties = {}
        for prop in sexpr.children(node, "property"):
            if len(prop) >= 3:
                properties[str(prop[1])] = str(prop[2])
        symbols[name] = RawSymbol(name, node, properties, sexpr.value(node, "extends"),
                                  sexpr.find(node, "power") is not None)
    return symbols


def body(raw: RawSymbol, library: dict[str, RawSymbol]) -> RawSymbol:
    """The symbol whose graphics and pins ``raw`` uses (its parent when it is derived)."""
    seen = {raw.name}
    current = raw
    while current.extends and current.extends in library and current.extends not in seen:
        seen.add(current.extends)
        current = library[current.extends]
    return current


def properties_of(raw: RawSymbol, library: dict[str, RawSymbol]) -> dict[str, str]:
    """The properties of ``raw`` with those it inherits."""
    chain = []
    current: RawSymbol | None = raw
    seen: set[str] = set()
    while current is not None and current.name not in seen:
        seen.add(current.name)
        chain.append(current)
        current = library.get(current.extends) if current.extends else None
    merged: dict[str, str] = {}
    for item in reversed(chain):
        merged.update({key: value for key, value in item.properties.items() if value != "" or key not in merged})
    return merged


# --------------------------------------------------------------- graphics
def _mm(value: float) -> float:
    return round(value * UNITS_PER_MM, 3)


def _point(node: list | None) -> tuple[float, float]:
    x, y = sexpr.numbers(node, 2)
    return _mm(x), -_mm(y)


def _fill(node: list) -> str:
    fill = sexpr.find(node, "fill")
    kind = sexpr.value(fill, "type", "none") if fill is not None else "none"
    return kind if kind in ("none", "outline", "background") else ("background" if kind == "color" else "none")


def _width(node: list) -> float:
    stroke = sexpr.find(node, "stroke")
    width = float(sexpr.value(stroke, "width", "0") or 0) if stroke is not None else 0.0
    return _mm(width)


def _text_size(node: list, default: float = 10.0) -> float:
    effects = sexpr.find(node, "effects")
    font = sexpr.find(effects, "font") if effects is not None else None
    size = sexpr.find(font, "size") if font is not None else None
    if size is None:
        return default
    height = sexpr.numbers(size, 2, 1.27)[0]
    return _mm(height)


def _justify(node: list) -> str:
    effects = sexpr.find(node, "effects")
    justify = sexpr.find(effects, "justify") if effects is not None else None
    if justify is None:
        return "center"
    atoms = [atom for atom in justify[1:] if isinstance(atom, str)]
    return "left" if "left" in atoms else "right" if "right" in atoms else "center"


def _hidden(node: list) -> bool:
    if sexpr.flag(node, "hide"):
        return True
    effects = sexpr.find(node, "effects")
    return effects is not None and sexpr.flag(effects, "hide")


def _shape(node: list) -> Shape | None:
    kind = sexpr.head(node)
    if kind == "polyline":
        points = tuple(_point(xy) for xy in sexpr.children(sexpr.find(node, "pts") or [], "xy"))
        return Shape("line", points, fill=_fill(node), width=_width(node)) if len(points) >= 2 else None
    if kind == "rectangle":
        return Shape("rect", (_point(sexpr.find(node, "start")), _point(sexpr.find(node, "end"))),
                     fill=_fill(node), width=_width(node))
    if kind == "circle":
        radius = float(sexpr.value(node, "radius", "0") or 0)
        return Shape("circle", (_point(sexpr.find(node, "center")),), radius=_mm(radius), fill=_fill(node),
                     width=_width(node))
    if kind == "arc":
        start, end = sexpr.find(node, "start"), sexpr.find(node, "end")
        mid = sexpr.find(node, "mid")
        if mid is None:  # (the first format of KiCad 6: a radius with its angles)
            radius = sexpr.find(node, "radius")
            center = _point(sexpr.find(radius, "at")) if radius is not None else (0.0, 0.0)
            a, b = _point(start), _point(end)
            import math

            angle_a = math.atan2(a[1] - center[1], a[0] - center[0])
            angle_b = math.atan2(b[1] - center[1], b[0] - center[0])
            r = math.hypot(a[0] - center[0], a[1] - center[1])
            middle = (angle_a + angle_b) / 2
            return Shape("arc", (a, (center[0] + r * math.cos(middle), center[1] + r * math.sin(middle)), b),
                         fill=_fill(node), width=_width(node))
        return Shape("arc", (_point(start), _point(mid), _point(end)), fill=_fill(node), width=_width(node))
    if kind == "bezier":
        points = tuple(_point(xy) for xy in sexpr.children(sexpr.find(node, "pts") or [], "xy"))
        return Shape("bezier", points, fill=_fill(node), width=_width(node)) if len(points) == 4 else None
    if kind == "text" and len(node) > 1:
        at = sexpr.find(node, "at")
        x, y = _point(at)
        angle = sexpr.numbers(at, 3)[2] / 10 if at is not None and abs(sexpr.numbers(at, 3)[2]) > 360 \
            else (sexpr.numbers(at, 3)[2] if at is not None else 0.0)
        return Shape("text", ((x, y),), text=str(node[1]), size=_text_size(node, 8.0), angle=angle,
                     halign=_justify(node))
    return None


def _pin(node: list, unit: int) -> Pin | None:
    if len(node) < 3:
        return None
    kind, style = str(node[1]), str(node[2])
    at = sexpr.find(node, "at")
    if at is None:
        return None
    x, y, angle = sexpr.numbers(at, 3)
    length = float(sexpr.value(node, "length", "2.54") or 2.54)
    name_node = sexpr.find(node, "name")
    number_node = sexpr.find(node, "number")
    name = str(name_node[1]) if name_node is not None and len(name_node) > 1 else ""
    number = str(number_node[1]) if number_node is not None and len(number_node) > 1 else ""
    return Pin(number, name, _mm(x), -_mm(y), int(round(-angle)) % 360, _mm(length), kind, style,
               sexpr.flag(node, "hide"), unit)


def _field(raw: RawSymbol, name: str, default: Field) -> Field:
    prop = next((p for p in sexpr.children(raw.node, "property") if len(p) > 1 and p[1] == name), None)
    if prop is None:
        return default
    at = sexpr.find(prop, "at")
    x, y = _point(at)
    angle = sexpr.numbers(at, 3)[2] if at is not None else 0.0
    return Field(x, y, angle, _text_size(prop), _hidden(prop), _justify(prop))


def to_symbol(raw: RawSymbol, library: dict[str, RawSymbol]) -> Symbol:
    """The symbol of ``raw`` in scene units (every unit; De Morgan styles left out)."""
    source = body(raw, library)
    shapes: list[Shape] = []
    owners: list[int] = []
    pins: list[Pin] = []
    units = 1
    for child in sexpr.children(source.node, "symbol"):
        match = _UNIT.match(str(child[1])) if len(child) > 1 else None
        unit, style = (int(match.group(2)), int(match.group(3))) if match else (0, 0)
        if style > 1:
            continue
        units = max(units, unit)
        for item in child[1:]:
            if not isinstance(item, list):
                continue
            if sexpr.head(item) == "pin":
                pin = _pin(item, unit)
                if pin is not None:
                    pins.append(pin)
                continue
            shape = _shape(item)
            if shape is not None:
                shapes.append(shape)
                owners.append(unit)
    pin_names = sexpr.find(source.node, "pin_names")
    pin_numbers = sexpr.find(source.node, "pin_numbers")
    offset = float(sexpr.value(pin_names, "offset", "0.508") or 0.508) if pin_names is not None else 0.508
    show_names = not (pin_names is not None and sexpr.flag(pin_names, "hide"))
    show_numbers = not (pin_numbers is not None and sexpr.flag(pin_numbers, "hide"))
    # a pin of every unit (KiCad's "_0_" sections) appears once even when listed per style
    seen: set = set()
    unique = []
    for pin in pins:
        key = (pin.number, pin.unit, pin.x, pin.y)
        if key not in seen:
            seen.add(key)
            unique.append(pin)
    return Symbol(tuple(shapes), tuple(unique), _field(raw, "Reference", Field(0, -20)),
                  _field(raw, "Value", Field(0, 20)), _mm(offset), show_names, show_numbers, units,
                  raw.power or source.power, tuple(owners))


# ------------------------------------------------------------------- specs
def spec_type(library_name: str, name: str) -> str:
    return f"kicad:{library_name}:{name}"


def to_spec(library_name: str, raw: RawSymbol, library: dict[str, RawSymbol], models=None) -> ComponentSpec:
    """A component of the catalog from a KiCad symbol (its model inferred, :mod:`.infer`)."""
    from . import infer

    properties = properties_of(raw, library)
    symbol = to_symbol(raw, library)
    reference = properties.get("Reference", "U").lstrip("#") or "U"
    value = properties.get("Value", raw.name)
    found = infer.infer(library_name, raw.name, properties, symbol, models)
    params = found.params or (ParamSpec("value", "Value", value, kind="text", description="The value of the part."),)
    return ComponentSpec(
        type=spec_type(library_name, raw.name), title=raw.name, category=library_name.replace("_", " "),
        description=raw.description, keywords=raw.keywords or properties_of(raw, library).get("ki_keywords", ""),
        prefix=re.sub(r"[^A-Za-z]", "", reference) or "U", symbol_source=symbol, params=params, model=found.model,
        controls=found.controls, role=found.role, source="kicad", datasheet=properties.get("Datasheet", ""),
        library=library_name, board=found.board, show=found.show, current=found.current,
        extra={"lights": True} if found.lights else {})
