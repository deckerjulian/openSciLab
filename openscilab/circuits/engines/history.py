# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a running simulation produced.

ngspice delivers every accepted point (:meth:`History.append`, in its thread). The history keeps the
latest value of every vector (the schematic shows them) and the course of the vectors it tracks - the
probes, which are channels of the circuit's device - for :data:`KEEP` seconds or :data:`CAPACITY`
points, whichever is shorter. Readers in other threads take snapshots: the arrays are replaced, never
changed where a reader may look (as ``driver/simulated/circuit.py`` does).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

import numpy as np

#: seconds of the past kept (captures look back, triggers search it)
KEEP = 60.0
#: points kept per vector at most
CAPACITY = 1_000_000


class History:
    def __init__(self, tracked: Iterable[str] = (), keep: float = KEEP, capacity: int = CAPACITY) -> None:
        self.keep = keep
        self.capacity = capacity
        self._lock = threading.Lock()
        self.tracked = [name.lower() for name in tracked]
        self._latest: dict[str, complex | float] = {}
        self._complete: dict = {}
        self._complete_size = 0
        self._size = 0
        self._chunk = 4096
        self._times = np.zeros(self._chunk)
        self._values = {name: np.zeros(self._chunk) for name in self.tracked}
        #: added to the times of ngspice (a run started again goes on where the last one ended)
        self.offset = 0.0
        self.last_time = 0.0
        self.points = 0

    def track(self, names: Iterable[str]) -> None:
        with self._lock:
            for name in names:
                name = name.lower()
                if name not in self._values:
                    self.tracked.append(name)
                    self._values[name] = np.full(len(self._times), np.nan)

    def append(self, names: list[str], values: list) -> None:
        """One point of ngspice (``time`` among the names)."""
        latest = dict(zip(names, values))
        if len(names) >= self._complete_size:
            # (a point with every vector - most points bring only some: ngspice.every_point)
            self._complete_size = len(names)
            self._complete = latest
        time = latest.get("time")
        if time is None:
            with self._lock:
                self._latest.update(latest)
            return
        t = self.offset + float(time.real if isinstance(time, complex) else time)
        with self._lock:
            self._latest.update(latest)  # (in place: readers copy it under the lock)
            if self._size and t <= self._times[self._size - 1]:
                # a point ngspice computed again after a step it rejected: replace the later ones
                keep = int(np.searchsorted(self._times[: self._size], t, side="left"))
                self._size = keep
            if self._size >= len(self._times):
                self._grow()
            self._times[self._size] = t
            for name, array in self._values.items():
                value = latest.get(name)
                array[self._size] = np.nan if value is None else float(value.real if isinstance(value, complex) else value)
            self._size += 1
            self.last_time = t
            self.points += 1

    def extend(self, times: np.ndarray, columns: dict, latest: dict) -> None:
        """Many points at once (from the engine process): ``times`` and the tracked ``columns``; points
        not later than what is kept replace the later ones (steps ngspice took again)."""
        with self._lock:
            self._latest = dict(latest)
            if not len(times):
                return
            if self._size and times[0] <= self._times[self._size - 1]:
                self._size = int(np.searchsorted(self._times[: self._size], times[0], side="left"))
            count = len(times)
            while self._size + count > len(self._times):
                self._grow(count)
            self._times[self._size:self._size + count] = times
            for name, array in self._values.items():
                column = columns.get(name)
                array[self._size:self._size + count] = np.nan if column is None else column
            self._size += count
            self.last_time = float(times[-1])
            self.points += count

    def since(self, time: float) -> tuple[np.ndarray, dict]:
        """The points after ``time``: (times, tracked name -> values)."""
        with self._lock:
            size = self._size
            start = int(np.searchsorted(self._times[:size], time, side="right"))
            return self._times[start:size].copy(), {name: array[start:size].copy() for name, array in self._values.items()}

    def _grow(self, needed: int = 0) -> None:
        """Room for more points: older ones than :attr:`keep` (or beyond the capacity) are dropped."""
        size = self._size
        times = self._times[:size]
        start = int(np.searchsorted(times, times[-1] - self.keep, side="left")) if size else 0
        start = max(start, size - self.capacity + self._chunk)
        start = max(start, 0)
        kept = size - start
        length = max(min(int(kept * 1.5) + self._chunk + needed, self.capacity + self._chunk + needed), self._chunk + needed)
        new_times = np.zeros(length)
        new_times[:kept] = times[start:]
        values = {}
        for name, array in self._values.items():
            fresh = np.full(length, np.nan)
            fresh[:kept] = array[start:size]
            values[name] = fresh
        self._times, self._values, self._size = new_times, values, kept

    # -------------------------------------------------------------- reading
    def complete(self) -> dict:
        """The last point that brought every vector: voltages and currents of one moment (``latest`` mixes
        the newest of each, some from the last point, the others up to 30 ms older)."""
        return dict(self._complete)

    def latest(self, name: str | None = None):
        """The latest value of ``name`` (``None``: all, a dict)."""
        if name is None:
            with self._lock:
                return dict(self._latest)
        return self._latest.get(name.lower())

    def snapshot(self, name: str, since: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(times, values) of a tracked vector; ``since``: only from that time on (with the point before)."""
        with self._lock:
            size = self._size
            array = self._values.get(name.lower())
            if array is None:
                return np.zeros(0), np.zeros(0)
            start = 0
            if since is not None and size:
                start = max(int(np.searchsorted(self._times[:size], since, side="left")) - 1, 0)
            return self._times[start:size].copy(), array[start:size].copy()

    def span(self) -> tuple[float, float]:
        with self._lock:
            if not self._size:
                return 0.0, 0.0
            return float(self._times[0]), float(self._times[self._size - 1])

    def sample(self, name: str, start: float, rate: float, count: int, hold: bool = False) -> np.ndarray:
        """``count`` values of ``name`` from ``start`` at ``rate`` per second (linear between the points,
        or holding each value with ``hold``); before the first point its value, after the last point
        the last value."""
        times, values = self.snapshot(name)
        wanted = start + np.arange(count) / float(rate)
        if not len(times):
            return np.zeros(count)
        good = ~np.isnan(values)
        if not good.all():
            times, values = times[good], values[good]
            if not len(times):
                return np.zeros(count)
        if hold:
            index = np.clip(np.searchsorted(times, wanted, side="right") - 1, 0, len(times) - 1)
            return values[index]
        return np.interp(wanted, times, values)

    def range_of(self, name: str) -> tuple[float, float]:
        _times, values = self.snapshot(name)
        values = values[~np.isnan(values)]
        if not len(values):
            return 0.0, 0.0
        return float(values.min()), float(values.max())
