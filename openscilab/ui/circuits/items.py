# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a schematic is drawn of: components, wires, junctions, net labels.

A component is drawn from its symbol (``circuits/symbols.py``) with its rotation and mirroring applied
to every point, so texts stay upright; its reference and value (the parameters its kind shows) are
written at their fields. While the circuit runs, probes show what they measure, LEDs light with their
current, and switches, buttons and knobs can be operated on the schematic.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QStyle

from ...circuits import GRID, values
from ...circuits.library import ComponentSpec
from ...circuits.model import Component, NetLabel, Wire
from ...circuits.symbols import Shape, arc_angles, transform_angle, transform_point
from ..theme import qcolor

PEN = 1.4
PIN_DOT = 2.2
Z_GROUP, Z_WIRE, Z_COMPONENT, Z_LABEL, Z_JUNCTION, Z_FLOWS = 0, 1, 2, 3, 4, 5
#: the size of the text of comments
COMMENT_SIZE = 8.5
#: how strong a group's colour shows behind it (a light tint)
GROUP_TINT = 0.14
#: the current view: a stream along the wires, as wide as its current (scene units, from 1 µA to 1 A;
#: never thinner than FLOW_THINNEST pixels on the screen), with waves FLOW_WAVE apart (scene units: twice
#: the grid, so a wave goes on round a corner)
FLOW_WIDTH = (7.0, 12.0)
FLOW_THINNEST = 6.0
FLOW_WAVE = 2.0 * GRID
#: shades of a voltage colour (a component is drawn again only when its shade changes)
VOLTAGE_STEPS = 24


def voltage_color(volts: float, largest: float) -> QColor:
    """The colour of a voltage while the circuit runs: grey at 0 V, towards green the more positive,
    towards red the more negative it is (against the largest voltage of the circuit)."""
    share = max(-1.0, min(1.0, volts / largest)) if largest > 0 else 0.0
    share = round(share * VOLTAGE_STEPS) / VOLTAGE_STEPS
    zero = qcolor("circuit.voltage.zero")
    end = qcolor("circuit.voltage.positive" if share >= 0 else "circuit.voltage.negative")
    amount = abs(share) ** 0.7  # (small voltages are seen too)
    return QColor(round(zero.red() + (end.red() - zero.red()) * amount),
                  round(zero.green() + (end.green() - zero.green()) * amount),
                  round(zero.blue() + (end.blue() - zero.blue()) * amount))


def flow_speed(current: float) -> float:
    """How fast the waves of ``current`` (amperes) move (scene units per second): with the decades of the
    current, from 1 µA (slow) to 1 A (fast) - the eye sees which branch carries more."""
    decades = math.log10(max(abs(current), 1e-9)) + 6.0  # (0 at 1 µA, 6 at 1 A)
    return min(max(12.0 + 20.0 * decades, 8.0), 140.0)


def flow_width(current: float) -> float:
    """How wide the stream of ``current`` (amperes) is (scene units): wider the more flows."""
    decades = math.log10(max(abs(current), 1e-9)) + 6.0
    share = min(max(decades / 6.0, 0.0), 1.0)
    return FLOW_WIDTH[0] + (FLOW_WIDTH[1] - FLOW_WIDTH[0]) * share


def flow_stops(color: QColor) -> list:
    """The colours of one wave: clear, rising slowly to a bright crest that falls off steeply in front (a
    drop running along the wire)."""
    base, crest = QColor(color.lighter(130)), color.lighter(165)
    base.setAlpha(0)
    crest.setAlpha(235)
    stops = []
    for step in range(21):
        where = step / 20
        rise = where / 0.7 if where <= 0.7 else (1.0 - where) / 0.3
        level = rise * rise * (3.0 - 2.0 * rise)
        stops.append((where, QColor(round(base.red() + (crest.red() - base.red()) * level),
                                    round(base.green() + (crest.green() - base.green()) * level),
                                    round(base.blue() + (crest.blue() - base.blue()) * level),
                                    round(base.alpha() + (crest.alpha() - base.alpha()) * level))))
    return stops


class Flow:
    """A stretch the current flows along (from ``start`` to ``end``, amperes) and where its waves are."""

    __slots__ = ("start", "end", "current", "offset", "length")

    def __init__(self, start: QPointF, end: QPointF, current: float, offset: float = 0.0) -> None:
        self.start, self.end, self.current, self.offset = start, end, current, offset
        self.length = math.hypot(end.x() - start.x(), end.y() - start.y())

    def advance(self, seconds: float) -> None:
        self.offset = (self.offset + flow_speed(self.current) * seconds) % FLOW_WAVE

    def direction(self) -> tuple[float, float]:
        """Where the current goes (a unit vector; from ``end`` to ``start`` for a negative current)."""
        sign = 1.0 if self.current >= 0 else -1.0
        return (sign * (self.end.x() - self.start.x()) / self.length,
                sign * (self.end.y() - self.start.y()) / self.length)

    def brush(self, stops: list) -> QBrush:
        """The waves, placed by the scene coordinates: along a wire ``x`` or ``y`` (in the direction of
        the current) plus the other one - on the grid that is the same number on both sides of a corner
        (up to twice the grid), so a wave flows round it without a jump."""
        dx, dy = self.direction()
        x, y = self.start.x(), self.start.y()
        along = x * dx + y * dy
        if abs(dx) < 1e-9 or abs(dy) < 1e-9:
            along += abs(dy) * x + abs(dx) * y
        first = (self.offset - along) % FLOW_WAVE
        gradient = QLinearGradient(x + dx * first, y + dy * first,
                                   x + dx * (first + FLOW_WAVE), y + dy * (first + FLOW_WAVE))
        gradient.setSpread(QLinearGradient.RepeatSpread)
        gradient.setStops(stops)
        return QBrush(gradient)


#: across a stream (from edge to edge): how much of its colour shows - a wide soft glow, the core in the middle
FLOW_ACROSS = ((0.0, 0), (0.12, 14), (0.26, 48), (0.34, 105), (0.355, 185), (0.645, 185), (0.66, 105), (0.74, 48),
               (0.88, 14), (1.0, 0))
#: how wide the core is (the share of the stream the waves run in)
FLOW_CORE = 0.29


def draw_flows(painter: QPainter, flows: list) -> None:
    """The streams of ``flows``: a soft glow as wide as the current, a translucent core in its middle and
    the waves running along the core (the corners of both filled)."""
    flows = [flow for flow in flows if flow.length > 0]
    if not flows:
        return
    color = qcolor("circuit.flow")
    across = [(where, QColor(color.red(), color.green(), color.blue(), alpha)) for where, alpha in FLOW_ACROSS]
    stops = flow_stops(color)
    thinnest = FLOW_THINNEST / (abs(painter.worldTransform().m11()) or 1.0)
    shapes = []  # (flow, its unit vector, its width)
    ends: dict[tuple, list] = {}
    for index, flow in enumerate(flows):
        unit = ((flow.end.x() - flow.start.x()) / flow.length, (flow.end.y() - flow.start.y()) / flow.length)
        shapes.append((flow, unit, max(flow_width(flow.current), thinnest)))
        for point, away in ((flow.start, unit), (flow.end, (-unit[0], -unit[1]))):
            ends.setdefault((round(point.x(), 3), round(point.y(), 3)), []).append((away, index))
    # (flat ends meet without overlapping - two of them would shine brighter - which leaves a square out at
    # the outside of a corner: filled with the colours of one of them)
    corners = []
    for (x, y), meeting in ends.items():
        if len(meeting) == 2:
            ((ax, ay), first), ((bx, by), second) = meeting
            if abs(ax * bx + ay * by) < 1e-6:
                corners.append((x, y, ax, ay, bx, by, first, min(shapes[first][2], shapes[second][2])))
    glows = []
    for flow, (ux, uy), width in shapes:
        half = width / 2
        gradient = QLinearGradient(flow.start.x() - uy * half, flow.start.y() + ux * half,
                                   flow.start.x() + uy * half, flow.start.y() - ux * half)
        gradient.setStops(across)
        glows.append(QBrush(gradient))
    # (round the outside of a corner: the glow from the corner outwards, as from the middle to an edge)
    rounds = []
    outwards = [(1.0 - 2.0 * where, color) for where, color in reversed(across) if where <= 0.5]
    for x, y, _ax, _ay, _bx, _by, _index, width in corners:
        gradient = QRadialGradient(QPointF(x, y), width / 2)
        gradient.setStops(outwards)
        rounds.append(QBrush(gradient))
    _draw_streams(painter, shapes, corners, glows, 1.0, rounds)
    _draw_streams(painter, shapes, corners, [flow.brush(stops) for flow, _unit, _width in shapes], FLOW_CORE)


def _draw_streams(painter: QPainter, shapes: list, corners: list, brushes: list, share: float,
                  rounds: list | None = None) -> None:
    for (flow, _unit, width), brush in zip(shapes, brushes):
        pen = QPen(brush, width * share)
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.drawLine(QLineF(flow.start, flow.end))
    painter.setPen(Qt.NoPen)
    for number, (x, y, ax, ay, bx, by, index, width) in enumerate(corners):
        half = width * share / 2
        painter.setBrush(rounds[number] if rounds else brushes[index])
        painter.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x - ax * half, y - ay * half),
                                       QPointF(x - (ax + bx) * half, y - (ay + by) * half),
                                       QPointF(x - bx * half, y - by * half)]))


def _font(size: float, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(max(4, int(round(size))))
    font.setBold(bold)
    return font


def comment_lines(text: str) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def comment_rect(text: str, x: float, y: float, halign: str = "center") -> QRectF:
    """Where a comment's lines go, its first line's middle at ``y`` (``halign`` at ``x``)."""
    lines = comment_lines(text)
    if not lines:
        return QRectF()
    font = _font(COMMENT_SIZE)
    font.setItalic(True)
    metrics = QFontMetricsF(font)
    width = max(metrics.horizontalAdvance(line) for line in lines)
    height = metrics.height() * len(lines)
    left = x - width / 2 if halign == "center" else (x if halign == "left" else x - width)
    return QRectF(left, y - metrics.height() / 2, width, height)


def draw_comment(painter: QPainter, text: str, x: float, y: float, halign: str = "center") -> QRectF:
    """The author's note, italic, line by line (the first line's middle at ``y``)."""
    rect = comment_rect(text, x, y, halign)
    if rect.isEmpty():
        return rect
    font = _font(COMMENT_SIZE)
    font.setItalic(True)
    metrics = QFontMetricsF(font)
    painter.save()
    painter.setFont(font)
    painter.setPen(qcolor("circuit.comment"))
    flags = {"center": Qt.AlignHCenter, "left": Qt.AlignLeft, "right": Qt.AlignRight}[halign] | Qt.AlignTop
    for index, line in enumerate(comment_lines(text)):
        painter.drawText(QRectF(rect.left(), rect.top() + index * metrics.height(), rect.width(), metrics.height()),
                         flags, line)
    painter.restore()
    return rect


def format_param(spec: ComponentSpec, name: str, text: str) -> str:
    """A parameter as written on the schematic: ``10k`` → ``10 kΩ``."""
    param = spec.param(name)
    if param is None or not text:
        return text
    if param.kind == "quantity" and param.unit:
        number = values.parse_optional(text)
        if number is not None:
            return values.format_value(number, param.unit, 3)
    if param.kind == "file":
        import os

        return os.path.basename(text)
    if name == "position":
        number = values.parse_optional(text)
        return f"{number * 100:.0f} %" if number is not None else text
    return text


def draw_text(painter: QPainter, text: str, x: float, y: float, size: float, halign: str = "center",
              valign: str = "center", color: QColor | None = None, angle: float = 0.0, bold: bool = False) -> QRectF:
    if not text:
        return QRectF()
    font = _font(size, bold)
    metrics = QFontMetricsF(font)
    width = metrics.horizontalAdvance(text)
    height = metrics.height()
    left = x - width / 2 if halign == "center" else (x if halign == "left" else x - width)
    top = y - height / 2 if valign == "center" else (y if valign == "top" else y - height)
    painter.save()
    painter.setFont(font)
    if color is not None:
        painter.setPen(color)
    if angle and abs(angle) % 180 == 90:
        painter.translate(x, y)
        painter.rotate(-angle)
        painter.translate(-x, -y)
    painter.drawText(QRectF(left, top, width + 1, height), Qt.AlignLeft | Qt.AlignVCenter, text)
    painter.restore()
    return QRectF(left, top, width, height)


class ComponentItem(QGraphicsObject):
    """A placed component (see the module)."""

    def __init__(self, component: Component, spec: ComponentSpec | None, controller=None) -> None:
        super().__init__()
        self.component = component
        self.spec = spec
        self.controller = controller
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(Z_COMPONENT)
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)  # (drawn again only when it changes)
        self.open_pins: set[str] = set()
        self.problem = ""
        #: what it shows while the circuit runs: text instead of its value, LED brightness (0..1)
        self.live_text = ""
        self.glow = 0.0
        self.live_values: dict[str, str] = {}
        #: an oscilloscope's picture on its symbol: (channel index, [(x, y) as shares of its screen])
        self.scope_traces: list = []
        #: the current view: the current through it, pin 1 to pin 2 (two-pin components)
        self.flows: list[Flow] = []
        #: while the circuit runs: the colours of the voltages at its pins (pin number -> colour)
        self.pin_colors: dict[str, QColor] = {}
        self._press_value = None
        self._bounds = QRectF()
        self._outline: QRectF | None = None
        self.update_from(component, spec)

    @property
    def ref(self) -> str:
        return self.component.id

    def update_from(self, component: Component, spec: ComponentSpec | None) -> None:
        self.prepareGeometryChange()
        self.component = component
        self.spec = spec
        self.setPos(component.x, component.y)
        self._bounds = self._compute_bounds()
        self._outline = None
        tip = f"{component.id}: {spec.title if spec else component.type}"
        if spec is not None and spec.description:
            tip += f"\n{spec.description}"
        if spec is not None and not spec.simulated:
            tip += "\n(no simulation model: it is drawn, not simulated)"
        if self.problem:
            tip += f"\n{self.problem}"
        self.setToolTip(tip)
        self.update()

    # -------------------------------------------------------------- geometry
    def _t(self, x: float, y: float) -> QPointF:
        px, py = transform_point(x, y, self.component.rotation, self.component.mirror)
        return QPointF(px, py)

    def _compute_bounds(self) -> QRectF:
        if self.spec is None:
            return QRectF(-20, -20, 40, 40)
        x1, y1, x2, y2 = self.spec.symbol.bounds(self.component.unit)
        corners = [self._t(x1, y1), self._t(x2, y2), self._t(x1, y2), self._t(x2, y1)]
        rect = QRectF(QPointF(min(p.x() for p in corners), min(p.y() for p in corners)),
                      QPointF(max(p.x() for p in corners), max(p.y() for p in corners)))
        return rect

    def body_rect(self) -> QRectF:
        return QRectF(self._bounds)

    def boundingRect(self) -> QRectF:
        # (asked for very often while the schematic is drawn: kept, computed again when it changes)
        if self._outline is None:
            self._outline = self._compute_outline()
        return self._outline

    def set_live(self, text: str, glow: float) -> None:
        """What it shows while the circuit runs: a reading instead of its value, an LED's brightness."""
        if text != self.live_text:
            self.prepareGeometryChange()
            self.live_text = text
            self._outline = None
        self.glow = glow
        self.update()

    def _compute_outline(self) -> QRectF:
        # texts beside the body (fields, pin names, live values) need room: the margins, and the value
        # text's own width (a potentiometer's "10 kΩ  50 %", a probe's reading) when that is wider
        rect = self._bounds.adjusted(-60, -36, 60, 36)
        text = self.value_text()
        if text and self.spec is not None:
            width = QFontMetricsF(_font(self.spec.symbol.value.size * 0.95, bool(self.live_text))).horizontalAdvance(text)
            rect = rect.united(QRectF(self._bounds.center().x() - width / 2 - 8, rect.top(), width + 16, rect.height()))
            rect = rect.united(QRectF(self._bounds.right(), rect.top(), width + 16, rect.height()))
        comment = self._comment_rect()
        return rect.united(comment.adjusted(-2, -2, 2, 2)) if not comment.isEmpty() else rect

    def _comment_rect(self) -> QRectF:
        """Under the body and its fields."""
        if not self.component.comment:
            return QRectF()
        return comment_rect(self.component.comment, self._bounds.center().x(), self._bounds.bottom() + 30)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self._bounds.adjusted(-3, -3, 3, 3))
        return path

    def set_pin_colors(self, colors: dict[str, QColor]) -> None:
        """The colours of the voltages at its pins while the circuit runs ({}: as drawn)."""
        if {key: value.rgb() for key, value in colors.items()} != {key: value.rgb() for key, value in
                                                                    self.pin_colors.items()}:
            self.pin_colors = colors
            self.update()

    def _voltage_stroke(self, stroke):
        """The pen of the symbol: from the colour of pin 1 to that of pin 2 across it (two pins), else the
        colour of all its pins when they are on one voltage."""
        pins = sorted(self.spec.symbol.pins_of(self.component.unit), key=lambda pin: pin.number)
        if len(pins) == 2 and all(pin.number in self.pin_colors for pin in pins):
            gradient = QLinearGradient(self._t(pins[0].x, pins[0].y), self._t(pins[1].x, pins[1].y))
            gradient.setColorAt(0.0, self.pin_colors[pins[0].number])
            gradient.setColorAt(1.0, self.pin_colors[pins[1].number])
            return QBrush(gradient)
        if len(pins) == 1 and pins[0].number in self.pin_colors:
            return self.pin_colors[pins[0].number]
        return stroke

    def set_flow(self, current: float | None, pins: dict[str, float] | None = None) -> None:
        """The current through a two-pin component (into pin 1, out at pin 2), or ``pins`` - the current
        into each pin of another one, along the drawn pin (from its end into the body); None: none shown."""
        symbol_pins = self.spec.symbol.pins_of(self.component.unit) if self.spec is not None else []
        offsets = [flow.offset for flow in self.flows]
        flows = []
        if current is not None and len(symbol_pins) == 2:
            first, second = sorted(symbol_pins, key=lambda pin: pin.number)
            flows.append(Flow(self._t(first.x, first.y), self._t(second.x, second.y), current))
        elif pins:
            for pin in symbol_pins:
                into = pins.get(pin.number)
                if into is not None and not pin.hidden and pin.length > 0:
                    flows.append(Flow(self._t(pin.x, pin.y), self._t(*pin.end), into))
        for index, flow in enumerate(flows):
            flow.offset = offsets[index] if index < len(offsets) else 0.0
        self.flows = flows

    def pin_positions(self) -> dict[str, QPointF]:
        if self.spec is None:
            return {}
        return {pin.number: self.pos() + self._t(pin.x, pin.y) for pin in self.spec.symbol.pins_of(self.component.unit)}

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene() is not None:
            point = value
            return QPointF(round(point.x() / GRID) * GRID, round(point.y() / GRID) * GRID)
        return super().itemChange(change, value)

    # --------------------------------------------------------------- texts
    def value_text(self) -> str:
        if self.live_text:
            # (a probe says what it measures: "C 105 mV", the voltage of its net C against ground)
            role = self.spec.role if self.spec is not None else ""
            name = str(self.component.params.get("pin" if role.startswith("device.") else "name", "")).strip()
            return f"{name} {self.live_text}" if name and role in (
                "probe.v", "probe.logic", "probe.i", "device.in", "device.out") else self.live_text
        spec = self.spec
        if spec is None:
            return self.component.type
        params = spec.defaults()
        params.update(self.component.params)
        parts = [format_param(spec, name, str(params.get(name, ""))) for name in spec.show]
        return "  ".join(part for part in parts if part)

    # --------------------------------------------------------------- paint
    def paint(self, painter: QPainter, option, widget=None) -> None:
        spec = self.spec
        selected = bool(option.state & QStyle.State_Selected)
        stroke = qcolor("circuit.selection") if selected else qcolor("circuit.symbol")
        if spec is None:
            painter.setPen(QPen(qcolor("circuit.pin.open"), PEN, Qt.DashLine))
            painter.drawRect(QRectF(-20, -20, 40, 40))
            draw_text(painter, "?", 0, 0, 14, color=qcolor("circuit.pin.open"))
            draw_text(painter, self.component.id, 0, -28, 9, color=qcolor("circuit.text"))
            return
        painter.setRenderHint(QPainter.Antialiasing)
        symbol = spec.symbol
        params = spec.defaults()
        params.update(self.component.params)
        body = qcolor("circuit.body")
        if self.pin_colors and not selected and spec.role in ("", "device.in", "device.out"):
            stroke = self._voltage_stroke(stroke)
        for shape in symbol.shapes_of(self.component.unit):
            if not shape.shown(params):
                continue
            self._paint_shape(painter, shape, stroke, body)
        # an oscilloscope shows its picture on its own screen
        screen = spec.extra.get("screen")
        if self.scope_traces and screen:
            corners = [self._t(screen[0], screen[1]), self._t(screen[2], screen[3])]
            box = QRectF(corners[0], corners[1]).normalized()
            painter.fillRect(box, qcolor("display.background"))
            painter.save()
            painter.setClipRect(box)
            from ..theme import trace_color

            for index, points in self.scope_traces:
                if len(points) < 2:
                    continue
                pen = QPen(QColor(trace_color(index)), 1.2)
                pen.setCosmetic(True)  # (a pen in pixels is drawn many times faster than one scaled with the zoom)
                painter.setPen(pen)
                corners = [QPointF(box.left() + x * box.width(), box.top() + y * box.height()) for x, y in points]
                # (separate lines: a polyline jumping up and down is many times slower, see scope.trace_segments)
                painter.drawLines([QLineF(a, b) for a, b in zip(corners, corners[1:])])
            painter.restore()
        # LEDs light with their current
        if self.glow > 0.01:
            glow = QColor(qcolor("circuit.current"))
            glow.setAlphaF(min(1.0, 0.25 + 0.6 * self.glow))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            center = self._bounds.center()
            radius = 6 + 6 * self.glow
            painter.drawEllipse(center, radius, radius)
        # pins
        pin_pen = QPen(stroke, PEN)
        for pin in symbol.pins_of(self.component.unit):
            if pin.hidden:
                continue
            start = self._t(pin.x, pin.y)
            end = self._t(*pin.end)
            if pin.length > 0:
                color = self.pin_colors.get(pin.number) if not selected else None
                painter.setPen(QPen(color, PEN) if color is not None else pin_pen)
                painter.drawLine(start, end)
                if pin.shape in ("inverted", "inverted_clock"):
                    painter.setBrush(body)
                    painter.drawEllipse(end, 2.5, 2.5)
            if pin.number in self.open_pins:
                painter.setPen(QPen(qcolor("circuit.pin.open"), 1.2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(start, PIN_DOT + 1, PIN_DOT + 1)
            angle = transform_angle(pin.angle, self.component.rotation, self.component.mirror)
            if symbol.show_pin_names and pin.label and pin.length > 0:
                self._pin_name(painter, pin, end, angle, symbol.name_offset)
            if symbol.show_pin_numbers and pin.length > 0 and spec.source == "kicad":
                mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
                vertical = angle in (90, 270)
                draw_text(painter, pin.number, mid.x() - (4 if vertical else 0), mid.y() - (0 if vertical else 4),
                          6, "right" if vertical else "center", "center" if vertical else "bottom",
                          qcolor("circuit.pin"))
        # fields
        text_color = qcolor("circuit.text")
        reference = symbol.reference
        value = symbol.value
        text = self.value_text()
        stacked = self._stacked_fields(reference, value, text)
        if not reference.hidden:
            point, align = stacked[0] if stacked else (self._t(reference.x, reference.y), None)
            draw_text(painter, self.component.id, point.x(), point.y(), reference.size * 0.95,
                      align or self._field_align(reference, point), color=text_color)
        if not value.hidden and text:
            point, align = stacked[1] if stacked else (self._t(value.x, value.y), None)
            color = qcolor("circuit.probe") if self.live_text else qcolor("circuit.value")
            draw_text(painter, text, point.x(), point.y(), value.size * 0.95, align or self._field_align(value, point),
                      color=color, bold=bool(self.live_text))
        if self.component.comment:
            draw_comment(painter, self.component.comment, self._bounds.center().x(), self._bounds.bottom() + 30)
        if self.problem:
            painter.setPen(QPen(qcolor("circuit.pin.open"), 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self._bounds.adjusted(-4, -4, 4, 4), 3, 3)
        elif selected:
            painter.setPen(QPen(qcolor("circuit.selection"), 0.8, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._bounds.adjusted(-3, -3, 3, 3))

    def _stacked_fields(self, reference, value, text: str):
        """Turned 90°, the reference and the value of a symbol whose fields are above and below it stand
        one over the other beside it (as KiCad places them): ``[(point, align), (point, align)]``."""
        if self.component.rotation not in (90, 270) or reference.hidden or value.hidden or not text:
            return None
        if abs(reference.x) > 4 or abs(value.x) > 4:
            return None  # (fields beside the body already)
        body = self._bounds
        x = body.right() + 6
        middle = body.center().y()
        return [(QPointF(x, middle - 7), "left"), (QPointF(x, middle + 7), "left")]

    def _field_align(self, field, point: QPointF) -> str:
        """The alignment of a field: as the symbol says, but turned 90° a field beside the body grows
        away from it (its text stays upright)."""
        if self.component.rotation in (90, 270):
            center = self._bounds.center()
            dx, dy = point.x() - center.x(), point.y() - center.y()
            if abs(dx) >= abs(dy) * 0.5:
                return "left" if dx > 0 else "right"
            return "center"
        return self._align(field.halign)

    def _align(self, halign: str) -> str:
        if self.component.mirror and halign in ("left", "right"):
            return "right" if halign == "left" else "left"
        if self.component.rotation == 180 and halign in ("left", "right"):
            return "right" if halign == "left" else "left"
        return halign

    def _pin_name(self, painter: QPainter, pin, end: QPointF, angle: int, offset: float) -> None:
        name = pin.label
        if offset > 0:  # inside the body, past the end of the pin
            dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            point = QPointF(end.x() + dx * (offset + 1), end.y() + dy * (offset + 1))
            halign = "left" if angle == 0 else "right" if angle == 180 else "center"
            valign = "top" if angle == 90 else "bottom" if angle == 270 else "center"
            draw_text(painter, name, point.x(), point.y(), 7, halign, valign, qcolor("circuit.pin"))
        else:  # beside the pin
            start = self._t(pin.x, pin.y)
            mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
            vertical = angle in (90, 270)
            draw_text(painter, name, mid.x() + (4 if vertical else 0), mid.y() - (0 if vertical else 4), 7,
                      "left" if vertical else "center", "center" if vertical else "bottom", qcolor("circuit.pin"))

    def _paint_shape(self, painter: QPainter, shape: Shape, stroke: QColor, body: QColor) -> None:
        width = max(PEN, shape.width)
        painter.setPen(QPen(stroke, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if shape.fill == "outline":
            painter.setBrush(stroke)
        elif shape.fill == "background":
            painter.setBrush(body)
        else:
            painter.setBrush(Qt.NoBrush)
        points = [self._t(x, y) for x, y in shape.points]
        if shape.kind == "line":
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            if shape.fill != "none":
                path.closeSubpath()
            painter.drawPath(path)
        elif shape.kind == "rect":
            painter.drawRect(QRectF(points[0], points[1]).normalized())
        elif shape.kind == "circle":
            painter.drawEllipse(points[0], shape.radius, shape.radius)
        elif shape.kind == "arc":
            found = arc_angles((points[0].x(), points[0].y()), (points[1].x(), points[1].y()),
                               (points[2].x(), points[2].y()))
            if found is None:
                painter.drawLine(points[0], points[2])
                return
            cx, cy, r, start, span = found
            path = QPainterPath(points[0])
            path.arcTo(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, span)
            painter.drawPath(path)
        elif shape.kind == "bezier" and len(points) == 4:
            path = QPainterPath(points[0])
            path.cubicTo(points[1], points[2], points[3])
            painter.drawPath(path)
        elif shape.kind == "text":
            draw_text(painter, shape.text, points[0].x(), points[0].y(), shape.size, shape.halign,
                      color=qcolor("circuit.text"))

    # ----------------------------------------------------------- operating
    def control(self):
        """The control of this component (knob, switch, button) or ``None``."""
        return self.spec.controls[0] if self.spec is not None and self.spec.controls else None

    def mousePressEvent(self, event) -> None:
        control = self.control()
        if control is not None and self.controller is not None and event.button() == Qt.LeftButton \
                and (self._operating() or event.modifiers() & Qt.AltModifier):
            self._press_value = (event.scenePos(), self._param(control.param))
            if control.kind == "button":
                self.controller.operate(self.ref, control.param, "1", final=False)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        control = self.control()
        if self._press_value is not None and control is not None and control.kind == "knob":
            start, value = self._press_value
            low, high = self._knob_bounds(control)
            number = values.parse_optional(value) or 0.0
            moved = self._knob_travel(start, event.scenePos()) / 150.0 * (high - low)  # (150 px: the whole range)
            self.controller.operate(self.ref, control.param, self._knob_value(number + moved, low, high), final=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        control = self.control()
        if self._press_value is not None and control is not None:
            _start, before = self._press_value
            self._press_value = None
            if control.kind == "switch":
                self.controller.operate(self.ref, control.param, "0" if before == "1" else "1", final=True)
            elif control.kind == "button":
                self.controller.operate(self.ref, control.param, "0", final=True)
            else:
                self.controller.operate(self.ref, control.param, self._param(control.param), final=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        control = self.control()
        if control is not None and control.kind == "knob" and self.controller is not None \
                and (self._operating() or event.modifiers() & Qt.AltModifier):
            delta = event.delta()
            if delta == 0:  # (a sideways turn of a trackpad)
                event.accept()
                return
            low, high = self._knob_bounds(control)
            number = values.parse_optional(self._param(control.param)) or 0.0
            step = (high - low) * 0.05 * max(-1.0, min(1.0, delta / 120.0))  # (a notch: 5 %; a trackpad: less)
            self.controller.operate(self.ref, control.param, self._knob_value(number + step, low, high), final=True)
            event.accept()
            return
        super().wheelEvent(event)

    def _knob_travel(self, start: QPointF, now: QPointF) -> float:
        """How far a knob was turned by a drag from ``start`` to ``now``: a potentiometer's wiper follows the
        pointer along it (towards pin 3 is up), a lying one also turns up and down; other knobs up and down."""
        up = start.y() - now.y()
        pins = self.pin_positions()
        if "1" not in pins or "3" not in pins or self.control() is None or self.control().param != "position":
            return up
        axis = pins["3"] - pins["1"]
        length = (axis.x() ** 2 + axis.y() ** 2) ** 0.5
        if length == 0:
            return up
        along = ((now.x() - start.x()) * axis.x() + (now.y() - start.y()) * axis.y()) / length
        lying = abs(axis.x()) > abs(axis.y())
        return along + up if lying else along

    def _knob_bounds(self, control) -> tuple[float, float]:
        params = self.spec.defaults() if self.spec is not None else {}
        params.update(self.component.params)
        return control.bounds(params)

    def _knob_value(self, number: float, low: float, high: float) -> str:
        """``number`` within the knob's range, in steps of a thousandth of it, with the parameter's unit."""
        step = (high - low) / 1000.0
        number = min(high, max(low, round(number / step) * step))
        if (low, high) == (0.0, 1.0):
            return f"{number:.3f}"
        unit = next((param.unit for param in self.spec.params if param.name == self.control().param), "")
        digits = max(4, len(f"{int(abs(high))}") + 1)  # (a thousandth of the range stays a step of its own)
        return f"{number:.{digits}g} {unit}".strip()

    def _operating(self) -> bool:
        return self.controller is not None and getattr(self.controller, "running", False)

    def _param(self, name: str) -> str:
        if self.spec is None:
            return ""
        return str(self.component.params.get(name, self.spec.defaults().get(name, "")))

    def hoverMoveEvent(self, event) -> None:
        control = self.control()
        if control is not None and self._operating():
            self.setCursor(Qt.SizeVerCursor if control.kind == "knob" else Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)


class WireItem(QGraphicsPathItem):
    def __init__(self, wire: Wire) -> None:
        super().__init__()
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(Z_WIRE)
        self.setAcceptHoverEvents(True)
        self.net = ""
        self.live = ""
        #: the current view: the pieces of the wire with their currents
        self.flows: list[Flow] = []
        #: while the circuit runs: the colour of its voltage (None: the colour of wires)
        self.voltage: QColor | None = None
        self.current = ""
        self.update_from(wire)

    @property
    def ref(self) -> str:
        return self.wire.id

    def set_voltage(self, color: QColor | None) -> None:
        if (color.rgb() if color is not None else None) != (self.voltage.rgb() if self.voltage is not None else None):
            self.voltage = color
            self.update()

    def set_flows(self, pieces: list) -> None:
        """The currents along the wire: ``[(start, end, amperes)]`` (the waves keep their places)."""
        offsets = [flow.offset for flow in self.flows]
        self.flows = [Flow(QPointF(*start), QPointF(*end), current, offsets[index] if index < len(offsets) else 0.0)
                      for index, (start, end, current) in enumerate(pieces)]
        largest = max((abs(flow.current) for flow in self.flows), default=0.0)
        current = values.display(largest, "A") if self.flows else ""
        if current != self.current:
            self.current = current
            self._tip()

    def update_from(self, wire: Wire) -> None:
        self.prepareGeometryChange()
        self.wire = wire
        self._shown = None
        self.show_points(wire.points)
        self._tip()

    def show_points(self, points) -> None:
        """Draw the wire through ``points`` (its own, or where a drag will take it)."""
        points = list(points)
        if points == self._shown:
            return
        self._shown = points
        path = QPainterPath()
        if points:
            path.moveTo(QPointF(*points[0]))
            for point in points[1:]:
                path.lineTo(QPointF(*point))
        self.setPath(path)

    def set_net(self, net: str, live: str = "") -> None:
        if (net, live) != (self.net, self.live):
            self.net, self.live = net, live
            self._tip()

    def _tip(self) -> None:
        text = f"Net {self.net}" if self.net else "Wire"
        if self.live:
            text += f": {self.live}"
        if getattr(self, "current", ""):
            text += f", {self.current} along it"
        if self.wire.comment:
            text += f"\n{self.wire.comment}"
        self.setToolTip(text)

    def _comment_anchor(self) -> tuple[float, float, str]:
        """Over the middle of its longest horizontal piece, else beside its longest piece."""
        pieces = list(zip(self._shown or [], (self._shown or [])[1:]))
        if not pieces:
            return 0.0, 0.0, "center"
        (x1, y1), (x2, y2) = max(pieces, key=lambda piece: abs(piece[1][0] - piece[0][0]) * 2
                                 + abs(piece[1][1] - piece[0][1]))
        if y1 == y2:
            return (x1 + x2) / 2, y1 - 9, "center"
        return x1 + 6, (y1 + y2) / 2, "left"

    def boundingRect(self) -> QRectF:
        rect = super().boundingRect()
        if self.wire.comment:
            x, y, align = self._comment_anchor()
            rect = rect.united(comment_rect(self.wire.comment, x, y, align).adjusted(-2, -2, 2, 2))
        return rect

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(8)
        return stroker.createStroke(self.path())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        selected = bool(option.state & QStyle.State_Selected)
        painter.setRenderHint(QPainter.Antialiasing)
        color = qcolor("circuit.wire.selected") if selected else (self.voltage or qcolor("circuit.wire"))
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self.path())
        if self.wire.comment:
            x, y, align = self._comment_anchor()
            draw_comment(painter, self.wire.comment, x, y, align)


class JunctionsItem(QGraphicsItem):
    """The dots where three or more connections meet."""

    def __init__(self) -> None:
        super().__init__()
        self.points: list[tuple[int, int]] = []
        self.setZValue(Z_JUNCTION)
        self._rect = QRectF()

    def set_points(self, points) -> None:
        self.prepareGeometryChange()
        self.points = list(points)
        if self.points:
            xs = [p[0] for p in self.points]
            ys = [p[1] for p in self.points]
            self._rect = QRectF(min(xs) - 4, min(ys) - 4, max(xs) - min(xs) + 8, max(ys) - min(ys) + 8)
        else:
            self._rect = QRectF()
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor("circuit.junction"))
        for x, y in self.points:
            painter.drawEllipse(QPointF(x, y), 3.0, 3.0)


class LabelItem(QGraphicsObject):
    """A net label: its name on a flag, the point of the flag on the net."""

    def __init__(self, label: NetLabel) -> None:
        super().__init__()
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setZValue(Z_LABEL)
        self.live = ""
        self.update_from(label)

    @property
    def ref(self) -> str:
        return self.label.id

    def update_from(self, label: NetLabel) -> None:
        self.prepareGeometryChange()
        self.label = label
        self.setPos(label.x, label.y)
        tip = f"Net label {label.name}" + (f": {self.live}" if self.live else "")
        self.setToolTip(tip + (f"\n{label.comment}" if label.comment else ""))
        self.update()

    def _rect(self) -> QRectF:
        metrics = QFontMetricsF(_font(9, True))
        width = metrics.horizontalAdvance(self.label.name + (f"  {self.live}" if self.live else "")) + 10
        flip = self.label.rotation in (180,)
        return QRectF(-width - 4 if flip else 4, -15, width, 13)

    def boundingRect(self) -> QRectF:
        rect = self._rect().adjusted(-6, -2, 2, 2).united(QRectF(-3, -3, 6, 6))
        if self.label.comment:
            rect = rect.united(comment_rect(self.label.comment, self._rect().left(), 10, "left").adjusted(-2, -2, 2, 2))
        return rect

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene() is not None:
            return QPointF(round(value.x() / GRID) * GRID, round(value.y() / GRID) * GRID)
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        color = qcolor("circuit.selection") if selected else qcolor("circuit.label")
        rect = self._rect()
        painter.setPen(QPen(color, 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(0, 0), QPointF(rect.left() if self.label.rotation != 180 else rect.right(), rect.bottom()))
        painter.drawRoundedRect(rect, 2, 2)
        text = self.label.name
        draw_text(painter, text, rect.left() + 5, rect.center().y(), 9, "left", color=color, bold=True)
        if self.live:
            metrics = QFontMetricsF(_font(9, True))
            draw_text(painter, self.live, rect.left() + 9 + metrics.horizontalAdvance(text), rect.center().y(), 8,
                      "left", color=qcolor("circuit.probe"))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(0, 0), 1.6, 1.6)
        if self.label.comment:
            draw_comment(painter, self.label.comment, rect.left(), 10, "left")


class PreviewItem(QGraphicsPathItem):
    """The wire being drawn."""

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(10)
        pen = QPen(qcolor("circuit.wire.selected"), 1.4, Qt.DashLine)
        self.setPen(pen)

    def set_points(self, points) -> None:
        path = QPainterPath()
        if points:
            path.moveTo(QPointF(*points[0]))
            for point in points[1:]:
                path.lineTo(QPointF(*point))
        self.setPath(path)


class StubsItem(QGraphicsPathItem):
    """Pieces of wire that a move will add, while it is dragged."""

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(Z_WIRE)
        self.setPen(QPen(qcolor("circuit.wire"), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def set_paths(self, routes) -> None:
        path = QPainterPath()
        for points in routes:
            if len(points) < 2:
                continue
            path.moveTo(QPointF(*points[0]))
            for point in points[1:]:
                path.lineTo(QPointF(*point))
        self.setPath(path)


class GroupItem(QGraphicsObject):
    """A group: a frame around its members with its name and comment above. A click on the frame or its
    title selects it; dragging them moves the members."""

    MARGIN = 14
    TITLE = 9.5

    def __init__(self, group, scene_items) -> None:
        super().__init__()
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(Z_GROUP)
        self.setAcceptHoverEvents(True)
        self._items = scene_items
        self._frame = QRectF()
        self._drag = None
        self.group = group
        self.update_from(group)

    @property
    def ref(self) -> str:
        return self.group.id

    def update_from(self, group) -> None:
        self.group = group
        self.refresh()
        tip = group.name or "Group"
        if group.comment:
            tip += f"\n{group.comment}"
        self.setToolTip(tip + f"\n{len(group.members)} members - drag the frame to move them")

    def member_items(self) -> list:
        return [item for item in (self._items(member) for member in self.group.members) if item is not None]

    def refresh(self) -> None:
        """Around its members where they are now."""
        rect = QRectF()
        for item in self.member_items():
            if isinstance(item, ComponentItem):
                box = item.body_rect().adjusted(-18, -16, 18, 16).translated(item.pos())
                comment = item._comment_rect()
                if not comment.isEmpty():
                    box = box.united(comment.translated(item.pos()))
            elif isinstance(item, WireItem):
                box = item.path().boundingRect()
            else:
                box = item.boundingRect().translated(item.pos())
            rect = rect.united(box)
        frame = rect.adjusted(-self.MARGIN, -self.MARGIN - self._header_height(), self.MARGIN, self.MARGIN) \
            if not rect.isEmpty() else QRectF()
        if not frame.isEmpty():  # (room for a long name or comment: nothing drawn outside the frame)
            frame.setWidth(max(frame.width(), self._header_width() + 20))
        if frame != self._frame:
            self.prepareGeometryChange()
            self._frame = frame
        self.update()

    def _header_width(self) -> float:
        width = 0.0
        if self.group.name:
            width = QFontMetricsF(_font(self.TITLE, True)).horizontalAdvance(self.group.name)
        if self.group.comment:
            width = max(width, comment_rect(self.group.comment, 0, 0, "left").width())
        return width

    def _header_height(self) -> float:
        height = 0.0
        if self.group.name:
            height += QFontMetricsF(_font(self.TITLE, True)).height()
        if self.group.comment:
            height += comment_rect(self.group.comment, 0, 0).height()
        return height + (4 if height else 0)

    def frame(self) -> QRectF:
        return QRectF(self._frame)

    def boundingRect(self) -> QRectF:
        return self._frame.adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        # only the frame and its title take clicks: inside, the members do
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        outline = QPainterPath()
        outline.addRoundedRect(self._frame, 8, 8)
        path = stroker.createStroke(outline)
        header = self._header_height()
        if header:
            path.addRect(QRectF(self._frame.left(), self._frame.top(), self._frame.width(), header + 6))
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._frame.isEmpty():
            return
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        painter.setBrush(Qt.NoBrush)
        if self.group.color:
            try:
                fill = QColor(qcolor(f"circuit.group.{self.group.color}"))
            except KeyError:  # (a colour this version does not know: no tint)
                fill = None
            if fill is not None:
                fill.setAlphaF(GROUP_TINT)
                painter.setBrush(fill)
        painter.setPen(QPen(qcolor("circuit.selection") if selected else qcolor("circuit.group"), 1.2, Qt.DashLine))
        painter.drawRoundedRect(self._frame, 8, 8)
        x = self._frame.left() + 10
        y = self._frame.top() + 6
        if self.group.name:
            height = QFontMetricsF(_font(self.TITLE, True)).height()
            draw_text(painter, self.group.name, x, y + height / 2, self.TITLE, "left", color=qcolor("circuit.text"),
                      bold=True)
            y += height
        if self.group.comment:
            draw_comment(painter, self.group.comment, x, y + comment_rect(self.group.comment, 0, 0).height()
                         / max(len(comment_lines(self.group.comment)), 1) / 2, "left")

    # ------------------------------------------------------- moving the members
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            members = self.member_items()
            self._drag = (event.scenePos(), {item.ref: item.pos() for item in members
                                             if isinstance(item, (ComponentItem, LabelItem))},
                          [item.ref for item in members if isinstance(item, WireItem)])
        super().mousePressEvent(event)

    def _dragged_by(self, event) -> tuple[int, int]:
        start = self._drag[0]
        return (round((event.scenePos().x() - start.x()) / GRID) * GRID,
                round((event.scenePos().y() - start.y()) / GRID) * GRID)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return super().mouseMoveEvent(event)
        _start, positions, wires = self._drag
        dx, dy = self._dragged_by(event)
        for item in self.member_items():
            if item.ref in positions:
                item.setPos(positions[item.ref] + QPointF(dx, dy))
        scene = self.scene()
        if scene is not None and hasattr(scene, "preview_group_move"):
            scene.preview_group_move(self, {ref: (int(pos.x() + dx), int(pos.y() + dy)) for ref, pos in positions.items()},
                                     {wire_id: (dx, dy) for wire_id in wires})
        self.refresh()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return super().mouseReleaseEvent(event)
        _start, positions, wires = self._drag
        dx, dy = self._dragged_by(event)
        self._drag = None
        scene = self.scene()
        if (dx or dy) and scene is not None and hasattr(scene, "finish_group_move"):
            scene.finish_group_move(self, {ref: (int(pos.x() + dx), int(pos.y() + dy)) for ref, pos in positions.items()},
                                    {wire_id: (dx, dy) for wire_id in wires})
        event.accept()


class FlowsItem(QGraphicsItem):
    """The streams of the current view, over the whole schematic: one item, so the wires and components below
    are not drawn again for every step of the waves (they come from their caches)."""

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(Z_FLOWS)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(False)
        self.flows: list[Flow] = []
        self._rect = QRectF()

    def set_flows(self, flows: list[Flow]) -> None:
        rect = QRectF()
        for flow in flows:
            rect = rect.united(QRectF(flow.start, flow.end).normalized())
        rect = rect.adjusted(-12, -12, 12, 12) if flows else QRectF()  # (streams, glow, also zoomed out)
        if rect != self._rect:
            self.prepareGeometryChange()
            self._rect = rect
        self.flows = flows
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect

    def shape(self) -> QPainterPath:
        return QPainterPath()  # (takes no clicks)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        draw_flows(painter, self.flows)
