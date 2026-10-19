# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The results of a circuit: the course of its probes while it runs, and every analysis computed -
an operating point as a table, sweeps and transients as charts (magnitude and phase of an AC sweep,
on a logarithmic frequency axis), S-parameters in dB."""

from __future__ import annotations

import csv
import time

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...circuits import values
from ...circuits.engines.analysis import AnalysisResult, magnitude_db, phase_degrees
from ..documents.chart import ChartWidget
from ..theme import set_role, size, smaller_font, space
from ..widgets.smith_chart import SmithChart
from .inspector import ANALYSIS_TITLES

#: points of a trace drawn at most (the chart reduces them further to the pixels)
MAX_POINTS = 20000


def _decimate(x: np.ndarray, y: np.ndarray) -> tuple[list[float], list[float]]:
    if len(x) > MAX_POINTS:
        step = int(np.ceil(len(x) / MAX_POINTS))
        x, y = x[::step], y[::step]
    return x.tolist(), y.tolist()


class ResultsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal, self)
        layout.addWidget(splitter)
        self.list = QListWidget(splitter)
        self.list.setMaximumWidth(size("results-list"))
        splitter.addWidget(self.list)
        self.stack = QStackedWidget(splitter)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        # the live course of the probes
        self.live_page = QWidget(self.stack)
        live_layout = QVBoxLayout(self.live_page)
        live_layout.setContentsMargins(space(2), space(2), space(2), space(2))
        self.live_hint = QLabel("Run the circuit: its probes are drawn here as they change.", self.live_page)
        set_role(self.live_hint, "hint")
        live_layout.addWidget(self.live_hint)
        self.live_chart = ChartWidget("strip_chart", self.live_page)
        live_layout.addWidget(self.live_chart, 1)
        self.stack.addWidget(self.live_page)
        # an analysis
        self.page = QWidget(self.stack)
        page_layout = QVBoxLayout(self.page)
        page_layout.setContentsMargins(space(2), space(2), space(2), space(2))
        self.title = QLabel("", self.page)
        set_role(self.title, "heading")
        page_layout.addWidget(self.title)
        self.note = QLabel("", self.page)
        self.note.setWordWrap(True)
        set_role(self.note, "hint")
        self.note.setFont(smaller_font(self.note.font()))
        page_layout.addWidget(self.note)
        self.chart = ChartWidget("strip_chart", self.page)
        self.chart2 = ChartWidget("strip_chart", self.page)
        self.table = QTableWidget(self.page)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Vector", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.smith = SmithChart(self.page)
        charts = QHBoxLayout()
        column = QVBoxLayout()
        column.addWidget(self.chart, 1)
        column.addWidget(self.chart2, 1)
        charts.addLayout(column, 3)
        charts.addWidget(self.smith, 2)
        page_layout.addLayout(charts, 1)
        page_layout.addWidget(self.table, 1)
        self.stack.addWidget(self.page)
        live = QListWidgetItem("Live", self.list)
        live.setData(Qt.UserRole, None)
        live.setToolTip("The probes while the circuit runs")
        self.results: list[tuple[AnalysisResult, list[str], float]] = []
        self.list.currentRowChanged.connect(self._show)
        self.list.setCurrentRow(0)
        #: the vectors of the probes (shown first) and their names: vector -> channel name
        self.probes: dict[str, str] = {}

    # ---------------------------------------------------------------- live
    def set_live(self, series: dict[str, tuple[np.ndarray, np.ndarray]], window: float = 0.02) -> None:
        """The course of the probes (name -> (times, values)), the last ``window`` seconds."""
        shown = {}
        for name, (times, data) in series.items():
            if not len(times):
                continue
            keep = times >= times[-1] - window
            shown[name] = _decimate(times[keep], data[keep])
        self.live_hint.setVisible(not shown)
        self.live_chart.set_series(shown, "s", "")

    # ------------------------------------------------------------ analyses
    def add(self, result: AnalysisResult, wanted: list[str]) -> None:
        """Show a computed analysis (``wanted``: the vectors to draw, the probes first)."""
        self.results.append((result, wanted, time.time()))
        stamp = time.strftime("%H:%M:%S")
        item = QListWidgetItem(f"{ANALYSIS_TITLES.get(result.kind, result.kind)}  {stamp}", self.list)
        item.setData(Qt.UserRole, len(self.results) - 1)
        self.list.setCurrentItem(item)

    def current(self) -> AnalysisResult | None:
        item = self.list.currentItem()
        index = item.data(Qt.UserRole) if item is not None else None
        return self.results[index][0] if index is not None and index < len(self.results) else None

    def _show(self, row: int) -> None:
        item = self.list.item(row)
        index = item.data(Qt.UserRole) if item is not None else None
        if index is None:
            self.stack.setCurrentWidget(self.live_page)
            return
        result, wanted, _when = self.results[index]
        self.stack.setCurrentWidget(self.page)
        self.title.setText(ANALYSIS_TITLES.get(result.kind, result.kind))
        notes = []
        if result.errors:
            notes.append("ngspice: " + " ".join(result.errors[:3]))
        notes.append(f"Computed in {result.duration:.2f} s.")
        self.note.setText(" ".join(notes))
        self.smith.setVisible(result.kind == "sp")
        if result.kind == "op":
            self.chart.hide()
            self.chart2.hide()
            self.table.show()
            names = [name for name in wanted if name in result.values] + \
                    sorted(name for name in result.values if name not in wanted)
            self.table.setRowCount(len(names))
            for row_index, name in enumerate(names):
                unit = "A" if "#branch" in name or name.startswith("@") or name.startswith("i(") else "V"
                self.table.setItem(row_index, 0, QTableWidgetItem(self.probes.get(name, name)))
                self.table.setItem(row_index, 1, QTableWidgetItem(values.format_value(result.values[name], unit, 5)))
            return
        self.table.hide()
        if result.kind == "sp":
            traces = sorted(name for name in result.traces if name.startswith("s_"))
            self.smith.set_series({name.upper().replace("_", ""): (result.x, result.traces[name])
                                   for name in traces if name.split("_")[1] == name.split("_")[2]})
        else:
            traces = [name for name in wanted if name in result.traces] or \
                [name for name in result.traces if "#branch" not in name and not name.startswith("@")][:8]
        x = result.x
        log_x = result.kind in ("ac", "sp", "noise")
        if result.complex:
            magnitude = {self.probes.get(name, name): _decimate(x, magnitude_db(result.traces[name])) for name in traces}
            phase = {self.probes.get(name, name): _decimate(x, phase_degrees(result.traces[name])) for name in traces}
            self.chart.set_series(magnitude, result.x_unit or result.x_label, "dB", log_x=log_x)
            self.chart2.set_series(phase, result.x_unit or result.x_label, "°", log_x=log_x)
            self.chart.show()
            self.chart2.show()
        else:
            series = {self.probes.get(name, name): _decimate(x, np.real(result.traces[name])) for name in traces}
            unit = "V/√Hz" if result.kind == "noise" else ""
            self.chart.set_series(series, result.x_unit or result.x_label, unit, log_x=log_x)
            self.chart.show()
            self.chart2.hide()
        for chart in (self.chart, self.chart2):
            chart.navigator.reset() if hasattr(chart.navigator, "reset") else None

    def export_touchstone(self, path: str, z0: float = 50.0) -> bool:
        """The S-parameters as a Touchstone file (``.s1p``, ``.s2p`` ...: real and imaginary parts)."""
        result = self.current()
        if result is None or result.kind != "sp":
            return False
        names = [name for name in result.traces if name.startswith("s_")]
        ports = max(int(name.split("_")[1]) for name in names) if names else 0
        if not ports:
            return False
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("! S-parameters computed by openSciLab (ngspice)\n")
            handle.write(f"# HZ S RI R {z0:g}\n")
            for index, frequency in enumerate(result.x):
                row = [f"{frequency:.9g}"]
                order = [(i, j) for i in range(1, ports + 1) for j in range(1, ports + 1)]
                if ports == 2:  # (Touchstone's order for two ports: S11 S21 S12 S22)
                    order = [(1, 1), (2, 1), (1, 2), (2, 2)]
                for i, j in order:
                    value = complex(result.traces.get(f"s_{i}_{j}", np.zeros(len(result.x)))[index])
                    row += [f"{value.real:.9g}", f"{value.imag:.9g}"]
                handle.write(" ".join(row) + "\n")
        return True

    def export_csv(self, path: str) -> bool:
        result = self.current()
        if result is None:
            return False
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if result.kind == "op":
                writer.writerow(["vector", "value"])
                for name, value in result.values.items():
                    writer.writerow([name, f"{value:.9g}"])
                return True
            names = list(result.traces)
            header = [result.x_label or "x"]
            for name in names:
                if np.iscomplexobj(result.traces[name]):
                    header += [f"{name} re", f"{name} im"]
                else:
                    header.append(name)
            writer.writerow(header)
            for index, x in enumerate(result.x):
                row = [f"{x:.9g}"]
                for name in names:
                    value = result.traces[name][index] if index < len(result.traces[name]) else np.nan
                    if np.iscomplexobj(result.traces[name]):
                        row += [f"{value.real:.9g}", f"{value.imag:.9g}"]
                    else:
                        row.append(f"{np.real(value):.9g}")
                writer.writerow(row)
        return True
