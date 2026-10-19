# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a component looks like: shapes and pins in scene units (y down, see :data:`~.GRID`).

The shapes are those of KiCad's symbols (lines, rectangles, circles, arcs through three points,
Bézier curves, texts), so its libraries convert without loss; openSciLab's own components are written
the same way in YAML (``library/*.yaml``)::

    symbol:
      - {line: [-20, 0, -12, 0]}
      - {rect: [-12, -5, 12, 5]}
      - {circle: [0, 0, 4], fill: background}
      - {arc: [-6, 0, 0, -6, 6, 0]}           # start, a point on the arc, end
      - {text: "+", at: [-8, -8], size: 8}
    pins:
      - {number: "1", name: "", at: [-20, 0], angle: 0, length: 8}

A pin's ``at`` is where wires connect; ``angle`` points from there into the body (0: to the right, 90:
down), ``length`` is the drawn part. Rotation and mirroring of a placed component are applied by
:func:`transform_point`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

Point = tuple[float, float]

#: the electrical kinds of pins (KiCad's names)
PIN_KINDS = ("input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
             "power_in", "power_out", "open_collector", "open_emitter", "no_connect")
FILLS = ("none", "outline", "background")


@dataclass(frozen=True)
class Shape:
    """One drawn element: ``kind`` is ``line`` (a polyline through ``points``), ``rect`` (two
    corners), ``circle`` (centre, ``radius``), ``arc`` (start, middle, end), ``bezier`` (four points)
    or ``text`` (at ``points[0]``)."""

    kind: str
    points: tuple[Point, ...]
    radius: float = 0.0
    fill: str = "none"
    width: float = 0.0
    text: str = ""
    size: float = 10.0
    #: of a text: rotation in degrees (counter-clockwise on the screen) and alignment
    angle: float = 0.0
    halign: str = "center"
    valign: str = "center"
    #: drawn only while a parameter has a value: ``"state=1"`` (a switch that is closed)
    when: str = ""

    def shown(self, params: dict) -> bool:
        if not self.when:
            return True
        name, _sep, wanted = self.when.partition("=")
        return str(params.get(name.strip(), "")).strip() == wanted.strip()

    def bounds(self) -> tuple[float, float, float, float]:
        if self.kind == "circle":
            (x, y), r = self.points[0], self.radius
            return x - r, y - r, x + r, y + r
        if self.kind == "arc":
            return arc_bounds(*self.points)
        if self.kind == "text":
            (x, y) = self.points[0]
            half = self.size * max(len(self.text), 1) * 0.3
            return x - half, y - self.size / 2, x + half, y + self.size / 2
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class Pin:
    number: str
    name: str
    x: float
    y: float
    #: from the connection point into the body, degrees (0 right, 90 down, 180 left, 270 up)
    angle: int = 0
    length: float = 10.0
    kind: str = "passive"
    #: KiCad's graphic style (line, inverted, clock, ...)
    shape: str = "line"
    hidden: bool = False
    #: which unit of a component with several (0: every unit)
    unit: int = 0

    @property
    def end(self) -> Point:
        """Where the drawn pin meets the body."""
        radians = math.radians(self.angle)
        return self.x + self.length * math.cos(radians), self.y + self.length * math.sin(radians)

    @property
    def label(self) -> str:
        """The name to show (KiCad's ``~`` is no name; ``~{X}`` is an overlined X)."""
        name = self.name or ""
        return "" if name in ("~", "") else name.replace("~{", "").replace("}", "") if "~{" in name else name


@dataclass(frozen=True)
class Field:
    """Where the reference (``R1``) or the value (``10k``) of a component is written."""

    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0
    size: float = 10.0
    hidden: bool = False
    halign: str = "center"


@dataclass(frozen=True)
class Symbol:
    shapes: tuple[Shape, ...] = ()
    pins: tuple[Pin, ...] = ()
    reference: Field = field(default_factory=lambda: Field(0, -20))
    value: Field = field(default_factory=lambda: Field(0, 20))
    #: names of pins inside the body, this far from its edge (KiCad's ``pin_names offset``); 0: beside the pin
    name_offset: float = 4.0
    show_pin_names: bool = True
    show_pin_numbers: bool = True
    #: the number of units (gates of a 7400 ...)
    units: int = 1
    #: a power symbol (GND, +5V): its value names the net it connects to
    power: bool = False

    def pins_of(self, unit: int = 1) -> tuple[Pin, ...]:
        return tuple(pin for pin in self.pins if pin.unit in (0, unit))

    def shapes_of(self, unit: int = 1) -> tuple[Shape, ...]:
        return tuple(shape for shape, owner in zip(self.shapes, self.shape_units) if owner in (0, unit)) \
            if self.shape_units else self.shapes

    #: the unit of each shape (empty: all belong to every unit)
    shape_units: tuple[int, ...] = ()

    def bounds(self, unit: int = 1) -> tuple[float, float, float, float]:
        boxes = [shape.bounds() for shape in self.shapes_of(unit) if shape.kind != "text"]
        boxes += [(min(p.x, p.end[0]), min(p.y, p.end[1]), max(p.x, p.end[0]), max(p.y, p.end[1]))
                  for p in self.pins_of(unit)]
        if not boxes:
            return -10.0, -10.0, 10.0, 10.0
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes))


# ------------------------------------------------------------- geometry
def transform_point(x: float, y: float, rotation: int = 0, mirror: bool = False) -> Point:
    """A point of a symbol placed with ``rotation`` (degrees, counter-clockwise on the screen, a
    multiple of 90) and ``mirror`` (left-right, before the rotation)."""
    if mirror:
        x = -x
    rotation %= 360
    if rotation == 90:
        return y, -x
    if rotation == 180:
        return -x, -y
    if rotation == 270:
        return -y, x
    return x, y


def transform_angle(angle: float, rotation: int = 0, mirror: bool = False) -> int:
    """A direction (degrees, 0 right, 90 down) of a symbol placed so."""
    if mirror:
        angle = 180 - angle
    return int(round(angle - rotation)) % 360


def arc_center(start: Point, mid: Point, end: Point) -> tuple[float, float, float] | None:
    """``(cx, cy, r)`` of the circle through three points; ``None`` when they are on a line."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None
    s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
    cx = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
    cy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
    return cx, cy, math.hypot(x1 - cx, y1 - cy)


def arc_angles(start: Point, mid: Point, end: Point) -> tuple[float, float, float, float, float] | None:
    """``(cx, cy, r, start angle, span)`` of an arc, angles in degrees as Qt counts them (counter-
    clockwise on the screen, i.e. with y pointing down: ``-atan2``)."""
    circle = arc_center(start, mid, end)
    if circle is None:
        return None
    cx, cy, r = circle

    def angle(point: Point) -> float:
        return math.degrees(math.atan2(-(point[1] - cy), point[0] - cx)) % 360

    a0, am, a1 = angle(start), angle(mid), angle(end)
    span = (a1 - a0) % 360
    if (am - a0) % 360 > span:  # the middle is on the other way round
        span -= 360
    return cx, cy, r, a0, span


def arc_bounds(start: Point, mid: Point, end: Point) -> tuple[float, float, float, float]:
    points = [start, mid, end]
    found = arc_angles(start, mid, end)
    if found is not None:
        cx, cy, r, a0, span = found
        for k in range(0, 360, 90):  # the extreme points the arc passes
            if (span >= 0 and (k - a0) % 360 <= span) or (span < 0 and (a0 - k) % 360 <= -span):
                points.append((cx + r * math.cos(math.radians(k)), cy - r * math.sin(math.radians(k))))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------- YAML
def _floats(values: Iterable) -> list[float]:
    return [float(value) for value in values]


def _pairs(values: list[float]) -> tuple[Point, ...]:
    if len(values) % 2:
        raise ValueError("points need pairs of numbers")
    return tuple((values[i], values[i + 1]) for i in range(0, len(values), 2))


def shape_from_data(data: dict) -> Shape:
    """A shape of a YAML symbol (see the module)."""
    shape = _shape_from_data(data)
    when = str(data.get("when", ""))
    if when:
        from dataclasses import replace

        shape = replace(shape, when=when)
    return shape


def _shape_from_data(data: dict) -> Shape:
    fill = str(data.get("fill", "none"))
    width = float(data.get("width", 0.0))
    if "line" in data:
        return Shape("line", _pairs(_floats(data["line"])), fill=fill, width=width)
    if "rect" in data:
        x1, y1, x2, y2 = _floats(data["rect"])
        return Shape("rect", ((x1, y1), (x2, y2)), fill=fill, width=width)
    if "circle" in data:
        x, y, r = _floats(data["circle"])
        return Shape("circle", ((x, y),), radius=r, fill=fill, width=width)
    if "arc" in data:
        values = _pairs(_floats(data["arc"]))
        if len(values) != 3:
            raise ValueError("an arc needs its start, a point on it and its end")
        return Shape("arc", values, fill=fill, width=width)
    if "bezier" in data:
        return Shape("bezier", _pairs(_floats(data["bezier"])), fill=fill, width=width)
    if "text" in data:
        x, y = _floats(data.get("at", (0, 0)))
        return Shape("text", ((x, y),), text=str(data["text"]), size=float(data.get("size", 8)),
                     angle=float(data.get("angle", 0)), halign=str(data.get("align", "center")))
    raise ValueError(f"unknown shape {sorted(data)}")


def pin_from_data(data: dict) -> Pin:
    x, y = _floats(data["at"])
    kind = str(data.get("kind", "passive"))
    if kind not in PIN_KINDS:
        raise ValueError(f"unknown kind of pin {kind!r}")
    return Pin(str(data["number"]), str(data.get("name", "")), x, y, int(data.get("angle", 0)) % 360,
               float(data.get("length", 10)), kind, str(data.get("shape", "line")), bool(data.get("hidden", False)),
               int(data.get("unit", 0)))


def field_from_data(data, default: Field) -> Field:
    if data is None:
        return default
    if data is False:
        return Field(default.x, default.y, default.angle, default.size, True)
    x, y = _floats(data.get("at", (default.x, default.y)))
    return Field(x, y, float(data.get("angle", 0)), float(data.get("size", default.size)), bool(data.get("hidden", False)),
                 str(data.get("align", "center")))


def symbol_from_data(data: dict) -> Symbol:
    shapes = tuple(shape_from_data(item) for item in data.get("shapes", ()))
    pins = tuple(pin_from_data(item) for item in data.get("pins", ()))
    return Symbol(shapes, pins, field_from_data(data.get("reference"), Field(0, -20)),
                  field_from_data(data.get("value"), Field(0, 20)), float(data.get("name_offset", 4)),
                  bool(data.get("pin_names", True)), bool(data.get("pin_numbers", False)),
                  int(data.get("units", 1)), bool(data.get("power", False)))
