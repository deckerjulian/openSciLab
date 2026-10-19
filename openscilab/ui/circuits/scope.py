# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The oscilloscopes of a circuit (component ``probe.scope``): their screens below the schematic.

Ten divisions of time, eight of voltage; the channels against the scope's GND. An edge of the trigger
channel crossing the level holds the picture still, the trigger in the middle of the screen; without
an edge the picture runs on (*auto*) or stays as it was (*normal*). Its settings are parameters of
the component (saved with the circuit, undone like any other change); *Hold* keeps the picture.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...circuits import spice, values
from ..theme import qcolor, size, smaller_font, space, tabular_font, trace_color

DIVISIONS_X, DIVISIONS_Y = 10, 8
CHANNELS = ("CH1", "CH2", "CH3", "CH4")
#: the width of the column of settings and readouts beside the screen (px): theme.size("scope-side")
#: points drawn across the screen
POINTS = 800
#: from this time per division *auto* rolls (as oscilloscopes do: a triggered picture would wait seconds)
ROLL_FROM = 0.1
TIME_STEPS = [m * 10.0 ** e for e in range(-6, 1) for m in (1, 2, 5)]
VOLT_STEPS = [m * 10.0 ** e for e in range(-3, 2) for m in (1, 2, 5)]


@dataclass
class Picture:
    """What the screen shows: the time of its left edge, its span, and the values of each channel."""

    start: float = 0.0
    span: float = 1e-3
    channels: dict[str, np.ndarray] = field(default_factory=dict)
    triggered: bool = False
    #: the trigger's time (``None``: untriggered)
    trigger: float | None = None
    #: the newest values come in on the right
    rolling: bool = False


def settings_of(params: dict[str, str]) -> dict:
    def number(name: str, default: float) -> float:
        found = values.parse_optional(params.get(name, ""))
        return default if found is None else found

    return {"time_div": max(number("time_div", 1e-3), 1e-9), "volts_div": max(number("volts_div", 1.0), 1e-6),
            "offset": number("offset", 0.0), "trigger": params.get("trigger") or "CH1",
            "level": number("level", 2.5), "edge": params.get("edge") or "rising", "mode": params.get("mode") or "auto"}


def acquire(simulation, probes: list, params: dict[str, str], previous: Picture | None = None) -> Picture | None:
    """The picture of an oscilloscope from what ``simulation`` computed (its ``probes``: its channels)."""
    settings = settings_of(params)
    span = settings["time_div"] * DIVISIONS_X
    rolling = settings["mode"] == "roll" or (settings["mode"] == "auto" and settings["time_div"] >= ROLL_FROM)
    clock = simulation.clock() if hasattr(simulation, "clock") else None
    since = None if clock is None else clock - (1.05 if rolling else 3.05) * span
    courses = {}
    for probe in probes:
        times, data = simulation.course(probe.vector, since) if since is not None else simulation.course(probe.vector)
        if probe.reference:
            ref_times, ref_values = simulation.course(probe.reference, since) if since is not None else \
                simulation.course(probe.reference)
            if len(ref_times):
                data = data - np.interp(times, ref_times, ref_values)
        if len(times):
            courses[probe.channel] = (times, data)
    if not courses:
        return None
    last = max(times[-1] for times, _data in courses.values())
    trigger = None
    wanted = courses.get(settings["trigger"])
    if rolling:
        wanted = None
    if wanted is not None:
        trigger = find_trigger(wanted[0], wanted[1], settings["level"], settings["edge"], last - span / 2,
                               last - 3 * span)
    if trigger is None and settings["mode"] == "normal" and previous is not None:
        return previous
    start = trigger - span / 2 if trigger is not None else last - span
    sample_times = start + np.arange(POINTS) * (span / (POINTS - 1))
    channels = {}
    for channel, (times, data) in courses.items():
        good = ~np.isnan(data)
        if not good.any():
            continue
        shown = np.interp(sample_times, times[good], data[good], left=np.nan, right=np.nan)
        channels[channel] = shown
    return Picture(start, span, channels, trigger is not None, trigger, rolling)


def find_trigger(times: np.ndarray, data: np.ndarray, level: float, edge: str, latest: float,
                 earliest: float) -> float | None:
    """The time of the last edge through ``level`` between ``earliest`` and ``latest``."""
    if len(times) < 2:
        return None
    lo = int(np.searchsorted(times, earliest, side="left"))
    hi = int(np.searchsorted(times, latest, side="right"))
    if hi - lo < 2:
        return None
    t, v = times[lo:hi], data[lo:hi]
    above = v > level
    if edge == "falling":
        crossings = np.nonzero(above[:-1] & ~above[1:])[0]
    else:
        crossings = np.nonzero(~above[:-1] & above[1:])[0]
    if not len(crossings):
        return None
    i = crossings[-1]
    v0, v1 = v[i], v[i + 1]
    share = (level - v0) / (v1 - v0) if v1 != v0 else 0.0
    return float(t[i] + share * (t[i + 1] - t[i]))


def measure(data: np.ndarray, span: float) -> dict[str, float | None]:
    """Peak to peak, mean and frequency of what a channel shows (the frequency from the first to the last
    rising edge through the middle of its range)."""
    good = ~np.isnan(data)
    values_ = data[good]
    if len(values_) < 2:
        return {"vpp": None, "mean": None, "frequency": None}
    low, high = float(values_.min()), float(values_.max())
    result = {"vpp": high - low, "mean": float(values_.mean()), "frequency": None}
    if high - low > 1e-6:
        middle = (low + high) / 2
        above = data > middle
        rises = np.nonzero(~above[:-1] & above[1:] & good[:-1] & good[1:])[0]
        if len(rises) >= 2:
            step = span / max(len(data) - 1, 1)
            result["frequency"] = (len(rises) - 1) / ((rises[-1] - rises[0]) * step)
    return result


def trace_segments(xs, ys) -> list[QLineF]:
    """The line pieces through the points (``xs``, ``ys``), broken where a value is missing (NaN).
    Drawn as separate lines: antialiased, a polyline or path that jumps up and down at every point (a
    fast square wave) takes about a hundred times longer."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if len(xs) < 2:
        return []
    good = ~np.isnan(ys)
    starts = np.nonzero(good[:-1] & good[1:])[0]
    x0, y0 = xs[starts].tolist(), ys[starts].tolist()
    x1, y1 = xs[starts + 1].tolist(), ys[starts + 1].tolist()
    return [QLineF(a, b, c, d) for a, b, c, d in zip(x0, y0, x1, y1)]


def shown(value: float, unit: str) -> str:
    return values.display(value, unit)


class ScopeScreen(QWidget):
    """The screen: grid, traces, trigger marks."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.picture: Picture | None = None
        self.settings = settings_of({})
        self.channel_colors = {channel: trace_color(index) for index, channel in enumerate(CHANNELS)}
        self.setMinimumSize(size("scope-screen-width"), size("scope-screen-height"))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_picture(self, picture: Picture | None, settings: dict) -> None:
        self.picture = picture
        self.settings = settings
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), qcolor("display.background"))
        area = QRectF(self.rect()).adjusted(8, 8, -8, -22)
        dx, dy = area.width() / DIVISIONS_X, area.height() / DIVISIONS_Y
        painter.setPen(QPen(qcolor("display.grid.minor"), 1))
        for i in range(DIVISIONS_X * 5 + 1):  # (ticks on the middle lines)
            x = area.left() + i * dx / 5
            painter.drawLine(QPointF(x, area.center().y() - 3), QPointF(x, area.center().y() + 3))
        for i in range(DIVISIONS_Y * 5 + 1):
            y = area.top() + i * dy / 5
            painter.drawLine(QPointF(area.center().x() - 3, y), QPointF(area.center().x() + 3, y))
        painter.setPen(QPen(qcolor("display.grid.major"), 1))
        for i in range(DIVISIONS_X + 1):
            x = area.left() + i * dx
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
        for i in range(DIVISIONS_Y + 1):
            y = area.top() + i * dy
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        settings = self.settings
        volts = settings["volts_div"]
        offset = settings["offset"]

        def y_of(value: float) -> float:
            return area.center().y() - (value - offset) / volts * dy

        # the trigger level and the trigger time
        trigger_color = qcolor("display.line.trigger")
        painter.setPen(QPen(trigger_color, 1, Qt.DashLine))
        level_y = y_of(settings["level"])
        if area.top() <= level_y <= area.bottom():
            painter.drawLine(QPointF(area.left(), level_y), QPointF(area.left() + 12, level_y))
        painter.setFont(smaller_font(painter.font()))
        picture = self.picture
        if picture is not None and picture.triggered:
            painter.drawLine(QPointF(area.center().x(), area.top()), QPointF(area.center().x(), area.top() + 10))
        painter.save()
        painter.setClipRect(area)
        if picture is not None:
            for channel, data in picture.channels.items():
                color = QColor(self.channel_colors.get(channel, trace_color(0)))
                pen = QPen(color, 1.4)
                pen.setCosmetic(True)
                painter.setPen(pen)
                xs = area.left() + np.arange(len(data)) * (area.width() / max(len(data) - 1, 1))
                ys = area.center().y() - (np.asarray(data, dtype=float) - offset) / volts * dy  # (NaN stays NaN)
                painter.drawLines(trace_segments(xs, ys))
        painter.restore()
        painter.setPen(qcolor("display.text.dim"))
        if picture is not None and picture.rolling:
            status = "Roll"
        elif picture is not None and picture.triggered:
            status = "Trig'd"
        else:
            status = "Auto" if settings["mode"] == "auto" else "Ready"
        text = (f"{values.format_value(settings['time_div'], 's', 3)}/div   {values.format_value(volts, 'V', 3)}/div   "
                f"{settings['trigger']} {'↑' if settings['edge'] == 'rising' else '↓'} "
                f"{values.format_value(settings['level'], 'V', 3)}   {status}")
        painter.drawText(QRectF(area.left(), area.bottom() + 3, area.width(), 16), Qt.AlignLeft | Qt.AlignVCenter, text)
        if picture is None:
            painter.setPen(qcolor("display.text.dim"))
            painter.drawText(area, Qt.AlignCenter, "Run the circuit: its channels are drawn here")


class ScopePanel(QWidget):
    """An oscilloscope: its screen, its settings and what it measures."""

    def __init__(self, ref: str, changed: Callable[[str, str, str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ref = ref
        self._changed = changed
        self.params: dict[str, str] = {}
        self.picture: Picture | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(space(1), space(1), space(1), space(1))
        self.screen = ScopeScreen(self)
        layout.addWidget(self.screen, 1)
        # (a column of a fixed width: the readouts change every frame, the screen must not jump)
        column = QWidget(self)
        column.setFixedWidth(size("scope-side"))
        side = QVBoxLayout(column)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(space(1))
        grid = QGridLayout()
        self.time_div = self._steps(TIME_STEPS, "s")
        self.volts_div = self._steps(VOLT_STEPS, "V")
        self.offset = QLineEdit(self)
        self.trigger = QComboBox(self)
        self.trigger.addItems(CHANNELS)
        self.level = QLineEdit(self)
        self.edge = QComboBox(self)
        self.edge.addItems(["rising", "falling"])
        self.mode = QComboBox(self)
        self.mode.addItems(["auto", "normal", "roll"])
        self.hold = QCheckBox("Hold the picture", self)
        rows = (("Time/div", self.time_div), ("Volts/div", self.volts_div), ("Offset", self.offset),
                ("Trigger", self.trigger), ("Level", self.level), ("Edge", self.edge), ("Mode", self.mode))
        for row, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label, self), row, 0)
            grid.addWidget(widget, row, 1)
        side.addLayout(grid)
        side.addWidget(self.hold)
        self.readouts = Readouts(column)
        self._readouts_due = 0.0
        side.addWidget(self.readouts)
        side.addStretch(1)
        layout.addWidget(column)
        self.time_div.activated.connect(lambda _i: self._emit("time_div", self.time_div.currentData()))
        self.volts_div.activated.connect(lambda _i: self._emit("volts_div", self.volts_div.currentData()))
        self.offset.editingFinished.connect(lambda: self._emit("offset", self.offset.text()))
        self.trigger.activated.connect(lambda _i: self._emit("trigger", self.trigger.currentText()))
        self.level.editingFinished.connect(lambda: self._emit("level", self.level.text()))
        self.edge.activated.connect(lambda _i: self._emit("edge", self.edge.currentText()))
        self.mode.activated.connect(lambda _i: self._emit("mode", self.mode.currentText()))

    def _steps(self, steps: list[float], unit: str) -> QComboBox:
        box = QComboBox(self)
        for step in steps:
            box.addItem(values.format_value(step, unit, 3), values.format_value(step, unit, 3))
        return box

    def _emit(self, name: str, text: str) -> None:
        text = (text or "").strip()
        if text and name in ("offset", "level") and values.parse_optional(text) is None:
            return
        if text != self.params.get(name, ""):
            self._changed(self.ref, name, text)

    def set_params(self, params: dict[str, str]) -> None:
        """The settings of the component (defaults included)."""
        self.params = dict(params)
        settings = settings_of(params)
        for box, value, unit in ((self.time_div, settings["time_div"], "s"), (self.volts_div, settings["volts_div"], "V")):
            text = values.format_value(value, unit, 3)
            index = box.findData(text)
            if index < 0:
                box.addItem(text, text)
                index = box.count() - 1
            if index != box.currentIndex():
                box.setCurrentIndex(index)
        for edit, name in ((self.offset, "offset"), (self.level, "level")):
            if not edit.hasFocus() and edit.text() != params.get(name, ""):
                edit.setText(params.get(name, ""))
        for box, name in ((self.trigger, "trigger"), (self.edge, "edge"), (self.mode, "mode")):
            index = box.findText(params.get(name, ""))
            if index >= 0 and index != box.currentIndex():
                box.setCurrentIndex(index)
        self.screen.set_picture(self.picture, settings)

    def show_live(self, simulation, probes: list) -> Picture | None:
        if self.hold.isChecked():
            return self.picture
        picture = acquire(simulation, probes, self.params, self.picture)
        self.picture = picture
        self.screen.set_picture(picture, settings_of(self.params))
        now = time.monotonic()
        if now < self._readouts_due:
            return picture  # (the numbers change four times a second: readable, and cheap)
        self._readouts_due = now + READOUTS_EVERY
        rows = []
        if picture is not None:
            for index, channel in enumerate(CHANNELS):
                data = picture.channels.get(channel)
                if data is None:
                    continue
                found = measure(data, picture.span)
                vpp = f"{shown(found['vpp'], 'V')} pp" if found["vpp"] is not None else "-"
                mean = shown(found["mean"], "V") if found["mean"] is not None else "-"
                frequency = shown(found["frequency"], "Hz") if found["frequency"] else "-"
                rows.append((trace_color(index), [channel, vpp, mean, frequency]))
        self.readouts.set_rows(rows, "No channel is wired")
        return picture

    def clear(self) -> None:
        self.picture = None
        self.screen.set_picture(None, settings_of(self.params))
        self._readouts_due = 0.0
        self.readouts.set_rows([], "")


#: seconds between two changes of the readouts
READOUTS_EVERY = 0.25


class Readouts(QWidget):
    """The measurements of the channels in columns, painted: its size never changes with the numbers (a
    label asked the window for a new layout at every change, and the schematic above was drawn again)."""

    COLUMNS = (0.0, 0.42, 0.7, 1.0)  # (where the columns end, as parts of the width; the first is the start)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(tabular_font(smaller_font(self.font())))
        self.setFixedHeight(round(QFontMetricsF(self.font()).lineSpacing() * 4 + 4))
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rows: list[tuple[str, list[str]]] = []
        self._empty = ""

    def set_rows(self, rows: list[tuple[str, list[str]]], empty: str = "") -> None:
        """``rows``: (colour, [channel, peak to peak, mean, frequency]); ``empty``: shown without rows."""
        if rows != self._rows or empty != self._empty:
            self._rows, self._empty = rows, empty
            self.update()

    def text(self) -> str:
        return "\n".join(" ".join(cells) for _color, cells in self._rows) or self._empty

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        metrics = QFontMetricsF(self.font())
        height = metrics.lineSpacing()
        width = self.width()
        if not self._rows:
            painter.setPen(qcolor("TEXT_MUTED"))
            painter.drawText(QRectF(0, 0, width, height), Qt.AlignLeft | Qt.AlignVCenter, self._empty)
            return
        for row, (color, cells) in enumerate(self._rows):
            painter.setPen(QColor(color))
            top = row * height
            for column, cell in enumerate(cells):
                if column == 0:
                    painter.drawText(QRectF(0, top, width, height), Qt.AlignLeft | Qt.AlignVCenter, cell)
                else:
                    right = width * self.COLUMNS[column]
                    painter.drawText(QRectF(0, top, right, height), Qt.AlignRight | Qt.AlignVCenter, cell)


def probes_of(circuit: spice.Circuit, ref: str) -> list:
    return [probe for probe in circuit.probes if probe.kind == "scope" and probe.component == ref]
