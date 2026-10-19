# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The analyses of a circuit: operating point, DC sweep, AC sweep, transient, noise, S-parameters.

The points are taken as ngspice delivers them (not from its stored vectors: after a live run ngspice
keeps storing nothing, a flaw of its shared library).
"""

from __future__ import annotations

import time
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

import numpy as np

from ..spice import Circuit
from . import acquire, ngspice, release

#: the scale of each analysis: its vector, label and unit
SCALES = {"op": ("", "", ""), "dc": ("v-sweep", "Sweep", "V"), "ac": ("frequency", "Frequency", "Hz"),
          "tran": ("time", "Time", "s"), "sp": ("frequency", "Frequency", "Hz"),
          "noise": ("frequency", "Frequency", "Hz")}


@dataclass
class AnalysisResult:
    kind: str
    #: the values along the scale (empty for an operating point)
    x: np.ndarray = field(default_factory=lambda: np.zeros(0))
    x_label: str = ""
    x_unit: str = ""
    #: vector name -> values (complex for AC and S-parameters)
    traces: dict[str, np.ndarray] = field(default_factory=dict)
    #: of an operating point: vector name -> value
    values: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def complex(self) -> bool:
        return any(np.iscomplexobj(trace) for trace in self.traces.values())


def run_analysis(circuit: Circuit, kind: str, timeout: float = 120.0, owner: str = "an analysis") -> AnalysisResult:
    """Run the analysis whose line ``circuit`` ends with, in the engine process of analyses (one at a
    time); raises EngineBusy or NgSpiceError."""
    from . import EngineBusy, process

    if not process._analysis_lock.acquire(timeout=timeout):
        raise EngineBusy("another analysis is being computed")
    try:
        engine = process.analysis_engine()
        try:
            return engine.call("analysis", timeout=timeout + 10, circuit=circuit, kind=kind, limit=timeout)
        except process.EngineEnded:
            # ngspice crashed on this circuit: a fresh process for the next one
            process._analysis_engine = None
            raise ngspice.NgSpiceError("ngspice ended while computing (the circuit made it crash)") from None
        except (TimeoutError, FutureTimeout):
            # (the process hangs in this circuit: closed, so the next analysis does not wait behind it)
            process._analysis_engine = None
            engine.close()
            raise ngspice.NgSpiceError(f"the analysis took longer than {timeout:g} s and was stopped") from None
    finally:
        process._analysis_lock.release()


def local_analysis(circuit: Circuit, kind: str, timeout: float = 120.0, owner: str = "an analysis") -> AnalysisResult:
    """Run the analysis in this process (the engine process does)."""
    acquire(owner)
    started = time.monotonic()
    try:
        engine = ngspice.instance()
        rows: list[tuple[list[str], list]] = []
        engine.every_point = None  # (every vector at every point)
        engine.on_data = lambda names, numbers: rows.append((names, numbers))
        engine.command("set no_mem_check")
        engine.load(circuit.lines)
        engine.command("bg_run")
        engine._running.wait(2.0)  # (until ngspice's thread says it runs: wait() would return at once before)
        if not engine.wait(timeout):
            engine.halt()
            raise ngspice.NgSpiceError(f"the analysis took longer than {timeout:g} s and was stopped")
        engine.on_data = None
        errors = [line for line in engine.errors if ngspice._is_error(line) or "aborted" in line.lower()
                  or "too small" in line.lower() or "singular" in line.lower()]
        result = AnalysisResult(kind, errors=errors)
        scale, result.x_label, result.x_unit = SCALES.get(kind, ("", "", ""))
        if not rows:
            if errors:
                raise ngspice.NgSpiceError("\n".join(errors))
            return result
        names = rows[0][0]
        columns = {name: [] for name in names}
        for row_names, numbers in rows:
            for name, number in zip(row_names, numbers):
                if name in columns:
                    columns[name].append(number)
        arrays = {name: np.asarray(items) for name, items in columns.items()}
        if kind == "op":
            result.values = {name: float(np.real(array[-1])) for name, array in arrays.items() if len(array)}
        else:
            if scale and scale in arrays:
                result.x = np.real(arrays.pop(scale))
            elif kind == "dc":
                sweep = next((name for name in arrays if name.endswith("-sweep") or name == "sweep"), None)
                if sweep is not None:
                    result.x = np.real(arrays.pop(sweep))
            result.traces = arrays
        result.duration = time.monotonic() - started
        return result
    finally:
        try:
            ngspice.instance().on_data = None
        except ngspice.NgSpiceError:
            pass
        release()


def magnitude_db(values: np.ndarray) -> np.ndarray:
    """|values| in dB, a trace that is exactly 0 at -200 dB (not minus infinity: the chart would squash)."""
    return 20 * np.log10(np.maximum(np.abs(values), 1e-30))


def phase_degrees(values: np.ndarray) -> np.ndarray:
    return np.degrees(np.unwrap(np.angle(values)))


def corner_frequency(x: np.ndarray, values: np.ndarray, below: float = 3.0) -> float | None:
    """Where the magnitude first falls ``below`` dB under its value at the start."""
    db = magnitude_db(values)
    if not len(db):
        return None
    target = db[0] - below
    index = np.nonzero(db <= target)[0]
    if not len(index):
        return None
    i = index[0]
    if i == 0:
        return float(x[0])
    # between the two points, in the logarithm of the frequency
    x0, x1 = np.log10(x[i - 1]), np.log10(x[i])
    y0, y1 = db[i - 1], db[i]
    return float(10 ** (x0 + (target - y0) * (x1 - x0) / (y1 - y0))) if y1 != y0 else float(x[i])
