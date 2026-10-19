# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report nodes: sections, tables, images and checks collected into an HTML (or PDF) report with
passed or failed. The parts go into the engine's view sink, so ``openscilab run --report`` and
``report.write`` produce the same report."""

from __future__ import annotations

import html
import math
import os
from typing import Any, Optional

import numpy as np

from ...core import signals, units
from ..engine.runtime import NodeError, NodeRuntime, summarize
from ..report import report_html, svg_plot, table_html, write_html
from .registry import UNITS, In, Out, Param, node


def _show(ctx, node_id: str, render) -> None:
    """The part of ``node_id`` in the report: ``render()`` gives its HTML when the report is
    written (a node that gets a thousand values renders once, not a thousand times)."""
    ctx.views.show("report", node_id, None, html=render)


@node("report.section", title="Section",
      description="A heading and text in the report; values at 'in' are added below it.",
      inputs=[In("in", signals.ANY, "Values to list below the text")],
      params=[Param("title", "str", "Results", label="heading", description="The heading of the section"),
              Param("text", "str", "", label="text", description="The text below the heading")],
      icon="book", summary=("title",), keywords=("heading", "text", "chapter"))
class SectionNode(NodeRuntime):
    async def run(self) -> None:
        if not self.ctx.wired("in"):
            _show(self.ctx, self.node.id, self.section)

    async def setup(self) -> None:
        self.values: list[str] = []

    async def on_input(self, port: str, value: Any) -> None:
        self.values.append(summarize(value))
        _show(self.ctx, self.node.id, self.section)

    def section(self) -> str:
        text = str(self.p("text", ""))
        body = f"<p>{html.escape(text)}</p>" if text else ""
        if self.values:
            body += "<ul>" + "".join(f"<li>{html.escape(value)}</li>" for value in self.values) + "</ul>"
        return f"<h2>{html.escape(str(self.p('title', 'Results')))}</h2>{body}"


@node("report.table", title="Table",
      description="The last table (or the values) arriving at 'in' as a table in the report.",
      inputs=[In("in", signals.ANY, "A table, or values that become rows")],
      params=[Param("title", "str", "Table", label="title", description="The caption of the table")],
      icon="list", summary=("title",), keywords=("rows", "results"))
class TableNode(NodeRuntime):
    async def setup(self) -> None:
        self.table = None
        self.rows: list[Any] = []

    async def on_input(self, port: str, value: Any) -> None:
        if isinstance(value, signals.Table):
            self.table = value
        else:
            self.rows.append(value)
        _show(self.ctx, self.node.id, self.render)

    def render(self) -> str:
        if self.table is not None:
            columns = self.table.columns
        else:
            columns = {"#": list(range(1, len(self.rows) + 1)), "Value": [summarize(value) for value in self.rows]}
        return f"<h2>{html.escape(str(self.p('title', 'Table')))}</h2>" + table_html(columns)


@node("report.chart", title="Chart",
      description="A diagram in the report: an XY chart of two table columns ('x', 'y') or of (x, y) "
                  "values, or the traces of a capture or signal.",
      inputs=[In("in", signals.ANY, "A table, (x, y) values, a capture or a signal")],
      params=[Param("title", "str", "Diagram", label="title", description="The caption of the diagram"),
              Param("x", "str", description="The table column for x", suggest="upstream_columns", label="x column"),
              Param("y", "str", description="The table column(s) for y, comma separated",
                    suggest="upstream_columns", label="y column"),
              Param("x_label", "str", "", label="x axis", description="The name of the x axis", advanced=True),
              Param("y_label", "str", "", label="y axis", description="The name of the y axis", advanced=True),
              Param("points", "bool", True, label="points", description="Mark every point", advanced=True)],
      icon="chart", summary=("title", "x", "y"), keywords=("diagram", "image", "plot", "figure"))
class ImageNode(NodeRuntime):
    async def setup(self) -> None:
        self.last: Any = None
        self.pairs: list[tuple[float, float]] = []

    async def on_input(self, port: str, value: Any) -> None:
        if isinstance(value, (list, tuple)) and len(value) == 2 and not isinstance(value[0], (list, tuple)):
            self.pairs.append((float(_number(value[0])), float(_number(value[1]))))
        elif isinstance(value, dict) and {"x", "y"} <= set(value):
            self.pairs.append((float(_number(value["x"])), float(_number(value["y"]))))
        else:
            self.last = value
        if isinstance(self.last, signals.Table) and not self.pairs:
            self.series()  # a table without the columns asked for is an error now, not in the report
        _show(self.ctx, self.node.id, self.render)

    def series(self) -> list[tuple[str, list, list]]:
        if self.pairs:
            return [("", [x for x, _y in self.pairs], [y for _x, y in self.pairs])]
        value = self.last
        if isinstance(value, signals.Table):
            columns = value.columns
            x_name = self.p("x") or next(iter(columns), None)
            y_names = [name.strip() for name in str(self.p("y") or "").split(",") if name.strip()] or [
                name for name in columns if name != x_name]
            if x_name not in columns or any(name not in columns for name in y_names):
                raise NodeError(f"{self.node.id}: the table has the columns {', '.join(columns)}")
            series = []
            for name in y_names:
                # rows without a number in x or y (an empty cell, a text) are left out
                pairs = [(_maybe_number(x), _maybe_number(y)) for x, y in zip(columns[x_name], columns[name])]
                pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
                series.append((name, [x for x, _y in pairs], [y for _x, y in pairs]))
            return series
        if isinstance(value, signals.Capture):
            times = np.arange(value.sample_count) / value.rate
            series = [(name, *envelope(times, np.asarray(levels, dtype=float) + 1.5 * index))
                      for index, (name, levels) in enumerate(value.digital.items())]
            series += [(name, *envelope(times, np.asarray(values, dtype=float)))
                       for name, values in value.analog.items()]
            return series
        if isinstance(value, (signals.Analog, signals.Digital)):
            return [(value.name, *envelope(np.asarray(value.times()), np.asarray(value.values, dtype=float)))]
        return []

    def render(self) -> str:
        series = self.series()
        if not series:
            return ""
        figure = svg_plot(series, x_label=str(self.p("x_label") or self.p("x") or ""),
                          y_label=str(self.p("y_label") or self.p("y") or ""), points=bool(self.p("points", True)))
        return f"<h2>{html.escape(str(self.p('title', 'Diagram')))}</h2>{figure}"


#: points of a trace in a diagram of the report (more are drawn as their envelope)
DIAGRAM_POINTS = 2000


def envelope(times: np.ndarray, values: np.ndarray, limit: int = DIAGRAM_POINTS) -> tuple[list, list]:
    """``times`` and ``values`` as lists for a diagram: at most about ``limit`` points, the
    lowest and the highest of every stretch (a pulse is never lost, as it would be by taking
    every n-th sample)."""
    count = min(len(times), len(values))
    if count <= limit:
        return list(times[:count]), list(values[:count])
    step = -(-count // (limit // 2))
    whole = count // step * step
    blocks = values[:whole].reshape(-1, step)
    low, high = blocks.min(axis=1), blocks.max(axis=1)
    starts = times[:whole:step]
    xs = np.repeat(starts, 2)
    ys = np.empty(len(xs), dtype=float)
    # rising within the stretch: low first, else high first
    rising = blocks.argmin(axis=1) <= blocks.argmax(axis=1)
    ys[0::2] = np.where(rising, low, high)
    ys[1::2] = np.where(rising, high, low)
    if whole < count:
        xs = np.concatenate((xs, times[whole:count]))
        ys = np.concatenate((ys, values[whole:count]))
    return xs.tolist(), ys.tolist()


def _number(value: Any) -> float:
    from .control import plain

    value = plain(value)  # (a scalar, a truth value, the data of a single event)
    if isinstance(value, list):
        raise TypeError("several values")
    return float(value)


def _maybe_number(value: Any) -> Optional[float]:
    """``value`` as a finite number, else ``None`` (an empty cell, a text, NaN)."""
    try:
        number = _number(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@node("report.check", title="Check",
      description="Checks the values at 'in': at least the minimum, at most the maximum, or the "
                  "'expected' value ± tolerance; 'result' tells whether it passed, 'passed' and 'failed' "
                  "come as events, the report lists it under its title. With 'last' only the last value "
                  "counts, checked when the flow ends: a measurement that settles.",
      inputs=[In("in", signals.ANY, "The values to check")],
      outputs=[Out("result", signals.BOOL, "Whether the check passed"),
               Out("passed", signals.EVENT, "The check passed"), Out("failed", signals.EVENT, "The check failed")],
      params=[Param("title", "str", "", label="title", description="What the report calls the check"),
              Param("low", "float", label="minimum (≥)", description="The value must be at least this"),
              Param("high", "float", label="maximum (≤)", description="The value must be at most this"),
              Param("expected", "float", description="The value it should have (with the tolerance)",
                    label="expected"),
              Param("tolerance", "float", 0.0, label="± tolerance", only_if={"expected": True},
                    description="How far the value may be from 'expected'"),
              Param("unit", "str", "", label="display unit", choices=UNITS, advanced=True,
                    description="The unit written beside the values (it does not convert them)"),
              Param("last", "bool", False, label="last value only", advanced=True,
                    description="Check only the last value, when the flow ends (the accuracy of an alignment "
                                "settles as edges come; its first value says little)")],
      icon="check", summary=("title", "low", "high"), keywords=("test", "pass", "fail", "limit", "verify"))
class CheckNode(NodeRuntime):
    _last: Any = None

    async def on_input(self, port: str, value: Any) -> None:
        if self.p("last", False):
            self._last = value
            return
        self.judge(value)

    async def finish(self) -> None:
        if self.p("last", False) and self._last is not None:
            self.judge(self._last)

    def judge(self, value: Any) -> None:
        try:
            number = _number(value)
        except (TypeError, ValueError):
            raise NodeError(f"{self.node.id}: not a number: {value!r}") from None
        unit = str(self.p("unit", "") or getattr(value, "unit", "") or "")
        low, high, expected = self.p("low"), self.p("high"), self.p("expected")
        passed = True
        limits = []
        if expected is not None:
            tolerance = float(self.p("tolerance", 0.0))
            passed = abs(number - float(expected)) <= tolerance
            limits.append(f"= {_quantity(float(expected), unit)} ± {_quantity(tolerance, unit)}")
        if low is not None:
            passed = passed and number >= float(low)
            limits.append(f"≥ {_quantity(float(low), unit)}")
        if high is not None:
            passed = passed and number <= float(high)
            limits.append(f"≤ {_quantity(float(high), unit)}")
        name = str(self.p("title") or self.node.id)
        text = f"{name}: {_quantity(number, unit)} " + (", ".join(limits) if limits else "(no limits)")
        self.ctx.views.show("check", self.node.id, passed, text=text, measured=number)
        self.ctx.emit("result", signals.Bool(name=name, value=passed, at=self.ctx.now()))
        self.ctx.emit("passed" if passed else "failed",
                      signals.Event(name=name, times=[self.ctx.now()], data=[passed]))


def _quantity(value: float, unit: str) -> str:
    return units.format_quantity(value, unit, 5) if unit else f"{value:.6g}"


@node("report.write", title="Write",
      description="Writes the report (sections, tables, charts, checks with passed or failed) to 'path' "
                  "when the flow ends, or on every value at 'trigger'. HTML, or PDF for a .pdf path.",
      inputs=[In("trigger", signals.EVENT, "Write the report now (with a wire: only then)")],
      outputs=[Out("written", signals.EVENT, "The report was written (its path)"),
               Out("result", signals.BOOL, "Whether every check passed")],
      params=[Param("path", "path", "report.html", label="file", description="An .html or a .pdf file"),
              Param("title", "str", "", label="title", description="The title of the report (empty: the flow's)")],
      icon="export", summary=("path",), keywords=("html", "pdf", "document", "export"))
class WriteNode(NodeRuntime):
    finish_last = True

    async def on_input(self, port: str, value: Any) -> None:
        self.write()

    async def finish(self) -> None:
        if not self.ctx.wired("trigger"):
            self.write()

    def write(self) -> None:
        path = self.ctx.path(str(self.p("path", "report.html")))
        title = str(self.p("title") or self.ctx.engine.flow.name)
        error = self.ctx.engine.error
        failed = [f"<p class='fail'>The flow ended with an error: {html.escape(error)}</p>"] if error else None
        text = report_html(title, self.ctx.views, sections=failed, passed=False if error else None)
        checks = [bool(item[2]) for item in self.ctx.views.items if item[0] == "check"]
        if error:
            checks.append(False)
        try:
            if path.lower().endswith(".pdf"):
                from ...ui.report_pdf import write_pdf  # Qt renders the PDF

                write_pdf(text, path)
            else:
                write_html(path, text)
        except OSError as error:
            raise NodeError(f"{self.node.id}: {error}") from None
        self.ctx.log(f"report: {os.path.basename(path)}")
        self.ctx.emit("written", signals.Event(times=[self.ctx.now()], data=[path]))
        if checks:
            self.ctx.emit("result", signals.Bool(name="report", value=all(checks), at=self.ctx.now()))


NODES = [cls.node_spec for cls in (SectionNode, TableNode, ImageNode, CheckNode, WriteNode)]
