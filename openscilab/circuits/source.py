# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A running circuit as a simulated device: ``circuit:<file>``.

Its probes are its channels - voltage probes analog inputs (and logic channels), logic probes logic
channels, current probes analog inputs in milliamperes - so the data view captures and streams them,
flows (``device.capture``) record them and panels show them, as with every simulator. The device's time
is the circuit's simulated time; a capture that reaches beyond what was simulated waits for it.

When the circuit's document runs it, the device attaches to that run (``circuits/running.py``);
otherwise it starts the circuit itself, without a window (in virtual time when the flow is fast).
"""

from __future__ import annotations

import logging
import os
import re
import time

import numpy as np

from ..core.instrument import Facet
from ..driver.simulated.circuit import Circuit as NetCircuit
from ..driver.simulated.circuit import Source
from . import CircuitError, running, spice, values, yaml_io
from .engines.history import History

#: a capture waits at most this long for the simulation to reach its end (seconds of the clock)
log = logging.getLogger(__name__)

WAIT_LIMIT = 30.0


class HistorySource(Source):
    """A probe's course as a source of the simulated device (``driver/simulated/circuit.py``)."""

    kind = "circuit"

    def __init__(self, simulation, vector: str, threshold: float, scale: float = 1.0, logic: bool = False,
                 reference: str = "") -> None:
        self.simulation = simulation
        self.name = spice.data_name(vector)
        self.vector = vector
        #: a vector subtracted from it (an oscilloscope's GND that is not on ground)
        self.reference = spice.data_name(reference) if reference else ""
        self.high = threshold * 2
        self.threshold = threshold
        self.scale = scale
        self.logic = logic
        self._waited_too_long = False

    @property
    def history(self) -> History:
        return self.simulation.history

    def _wait_for(self, end: float) -> None:
        history = self.history
        if history.last_time >= end or getattr(self.simulation, "state", "running") not in ("running",):
            return
        limit = time.monotonic() + WAIT_LIMIT
        while history.last_time < end and time.monotonic() < limit and self.simulation.state == "running":
            time.sleep(0.005)
        if history.last_time < end and self.simulation.state == "running" and not self._waited_too_long:
            self._waited_too_long = True
            log.warning("the circuit did not reach %.3f s within %.0f s: the capture holds its last value from %.3f s",
                        end, WAIT_LIMIT, history.last_time)

    def _course(self, start: float, rate: float, count: int) -> np.ndarray:
        data = self.history.sample(self.name, start, rate, count)
        if self.reference:
            data = data - self.history.sample(self.reference, start, rate, count)
        return data

    def analog(self, start: float, rate: float, count: int) -> np.ndarray:
        self._wait_for(start + count / rate)
        data = self._course(start, rate, count)
        if self.logic:
            return (data > self.threshold).astype(np.float64) * self.high
        return data * self.scale

    def digital(self, start: float, rate: float, count: int) -> np.ndarray:
        self._wait_for(start + count / rate)
        return (self._course(start, rate, count) > self.threshold).astype(np.uint8)

    def value_range(self, analog: bool) -> tuple[float, float]:
        if not analog or self.logic:
            return (0.0, 1.0) if not analog else (0.0, self.high)
        low, high = self.history.range_of(self.name)
        return low * self.scale, high * self.scale

    def describe(self) -> str:
        return f"probe {self.vector} of the circuit"


def _voltage_range(circuit) -> float:
    """How far the probes may go (V): half again the largest voltage of a source of the circuit (a
    supply from the mains reaches 325 V), at least 20 V and four times the logic supply."""
    largest = 0.0

    def take(*texts: str) -> None:
        nonlocal largest
        for text in texts:
            number = values.parse_optional(text)
            if number is not None:
                largest = max(largest, abs(number))

    for line in circuit.lines:
        words = line.split()
        if not words or words[0][0].upper() != "V" or line.lstrip().startswith(("*", ".")):
            continue
        rest = " ".join(words[3:])
        dc = re.search(r"\bDC\s+(\S+)", rest, re.IGNORECASE)
        if dc:
            take(dc.group(1))
        sine = re.search(r"\bSIN\s*\(\s*(\S+)\s+(\S+)", rest, re.IGNORECASE)  # (offset, amplitude)
        if sine:
            offset, amplitude = values.parse_optional(sine.group(1)) or 0.0, values.parse_optional(sine.group(2)) or 0.0
            take(str(abs(offset) + abs(amplitude)))
        pulse = re.search(r"\bPULSE\s*\(\s*(\S+)\s+(\S+)", rest, re.IGNORECASE)  # (the two levels)
        if pulse:
            take(pulse.group(1), pulse.group(2))
        pwl = re.search(r"\bPWL\s*\((.*?)\)", rest, re.IGNORECASE)
        if pwl:
            take(*pwl.group(1).split()[1::2])  # (every second number is a level)
        if not (dc or sine or pulse or pwl) and len(words) >= 4:
            take(words[3])  # ("V1 a 0 5")
    return max(20.0, 4 * circuit.vcc, round(1.5 * largest))


class CircuitRun(Facet):
    """Stops the simulation a ``circuit:`` device started itself when the device is closed (the end of a
    flow, ``with api.open(...)``); a simulation the circuit's document runs goes on."""

    title = "Circuit"

    def __init__(self, simulation, owned: bool) -> None:
        super().__init__()
        self.simulation = simulation
        self.owned = owned

    def close(self) -> None:
        if self.owned and running.release(self.simulation):
            try:
                self.simulation.stop()
            finally:
                running.unregister(self.simulation)


def _channel_name(name: str, taken: set[str]) -> str:
    text = re.sub(r"\s+", "_", name.strip()) or "CH"
    candidate, number = text, 2
    while candidate in taken:
        candidate = f"{text}_{number}"
        number += 1
    taken.add(candidate)
    return candidate


def resolve_path(rest: str, project: str | None = None) -> str:
    """The circuit file of an address: absolute, relative to the flow's project (and its ``circuits/``
    folder), or to the working folder."""
    text = os.path.expanduser(rest.strip())
    if not text:
        raise CircuitError("circuit: needs the file of a circuit (circuit:circuits/filter.circuit.yaml)")
    candidates = []
    if not os.path.isabs(text):
        from ..lab.project import Project

        roots = [project] if project else []
        found = Project.find(os.getcwd())
        if found:
            roots.append(found)
        for root in roots:
            candidates += [os.path.join(root, text), os.path.join(root, "circuits", text)]
    candidates.append(text)
    for candidate in candidates:
        for path in (candidate, candidate + ".circuit.yaml"):
            if os.path.isfile(path):
                return os.path.abspath(path)
    return text


def _headless(path: str, fast: bool):
    """Start the circuit of ``path`` without a window."""
    from .catalog import default_catalog
    from .engines.live import LiveSimulation

    schematic = yaml_io.load(path)
    catalog = default_catalog()
    folder = os.path.dirname(os.path.abspath(path))
    libraries = [os.path.join(folder, item) if not os.path.isabs(item) else item for item in schematic.libraries]
    models = catalog.models_for(libraries) if libraries else catalog.models
    circuit = spice.emit(schematic, catalog.spec, models, title=schematic.name, base_folder=folder)
    if circuit.errors:
        raise CircuitError("; ".join(str(problem) for problem in circuit.errors[:4]))
    step = values.parse(schematic.setting("step"))
    simulation = LiveSimulation(circuit, step, realtime=not fast,
                                speed=values.parse_optional(schematic.setting("speed")) or 1.0,
                                owner=os.path.basename(path), max_step=values.parse_optional(schematic.setting("max_step")),
                                mcus=circuit.mcus)
    simulation.start()
    simulation.headless = True
    running.register(path, simulation)
    return simulation


def open_circuit_instrument(rest: str, clock=None, fast: bool = False, seed: int = 1, wiring=None, signals=None,
                            name: str | None = None, project: str | None = None):
    """The instrument of ``circuit:<rest>`` (see the module)."""

    path = resolve_path(rest, project)
    simulation = running.find(path) or running.find(rest)
    if simulation is None:
        if not os.path.isfile(path):
            raise CircuitError(f"there is no circuit {rest!r} (neither running nor a file)")
        simulation = _headless(path, fast)
    # (a run started for devices is shared by them and stops with the last one; a document's run goes on)
    owned = bool(getattr(simulation, "headless", False))
    if owned:
        running.retain(simulation)
    try:
        return _instrument(rest, simulation, owned, clock, fast, seed, name)
    except Exception:
        if owned and running.release(simulation):
            try:
                simulation.stop()
            finally:
                running.unregister(simulation)
        raise


def _instrument(rest: str, simulation, owned: bool, clock, fast: bool, seed: int, name: str | None):
    from ..core.instrument import Instrument, InstrumentStatus
    from ..driver.simulated.device import SimulatedDriver
    from ..driver.simulated.facets import SimulationControl

    circuit = simulation.circuit
    vcc = circuit.vcc
    taken: set[str] = set()
    digital, analog, sources = [], [], {}
    for probe in circuit.probes:
        if probe.vector == "0":
            continue
        if probe.kind == "logic":
            channel = _channel_name(probe.name, taken)
            digital.append(channel)
            sources[channel] = HistorySource(simulation, probe.vector, vcc / 2, logic=True)
        elif probe.kind == "current":
            channel = _channel_name(f"{probe.name}_mA", taken)
            analog.append(channel)
            sources[channel] = HistorySource(simulation, probe.vector, vcc / 2, scale=1000.0)
        elif probe.kind == "scope":
            # (an oscilloscope's channel: against its GND, as its screen shows it; no logic twin)
            channel = _channel_name(probe.name, taken)
            analog.append(channel)
            sources[channel] = HistorySource(simulation, probe.vector, vcc / 2, reference=probe.reference)
        else:
            channel = _channel_name(probe.name, taken)
            analog.append(channel)
            sources[channel] = HistorySource(simulation, probe.vector, vcc / 2)
            logic = _channel_name(f"{probe.name}_L", taken)
            digital.append(logic)
            sources[logic] = HistorySource(simulation, probe.vector, vcc / 2)
    if not digital:
        digital = ["NONE"]
        sources["NONE"] = HistorySource(simulation, "v(0)", vcc / 2)
    title = os.path.basename(rest).replace(".circuit.yaml", "") or "circuit"
    profile = {
        "name": f"circuit-{re.sub(r'[^A-Za-z0-9_-]', '_', title)}", "title": f"Circuit {title}",
        "description": "The probes of a circuit (Circuits) as channels.",
        "digital": digital, "analog": analog,
        "pins": [{"name": item, "caps": ["DIN"]} for item in digital]
                + [{"name": item, "caps": ["ADC"], "analog_channel": index} for index, item in enumerate(analog)],
        # (the probes are interpolated between the points of the simulation: any rate up to 10 MHz)
        "max_rate": 10_000_000, "memory_depth": 1_000_000, "stream_bandwidth": 8_000_000,
        "adc_bits": 16, "analog_range": [-_voltage_range(circuit), _voltage_range(circuit)], "adc_noise": 0.0,
        "logic_level": vcc, "latency": 0.0, "gpio_latency": 0.0, "watchdog": 0, "monitor_rate": 1000,
        "capabilities": ["IMMEDIATE_TRIGGER", "CONTINUOUS_STREAM", "MONITOR"], "clock_pins": [], "circuit": {},
    }
    nets = NetCircuit()
    for channel, source in sources.items():
        nets.drive(channel, source)
    # (the time of a capture: the circuit's, but not before the flow's - in virtual time a flow's timer of
    # a second passes at once while the circuit is still computing its first tenth: a capture armed by
    # it then waits for the circuit to get there, as it would in real time)
    circuit_clock = simulation.clock
    speed = float(getattr(simulation, "speed", 1.0) or 1.0)  # (a circuit at half speed: half a second per second)
    device_clock = circuit_clock if clock is None else (lambda: max(circuit_clock(), clock() * speed))
    driver = SimulatedDriver(profile, nets, clock=device_clock, fast=fast)
    driver.seed = seed
    instrument = Instrument.from_driver(driver, name=name or profile["title"], uri=f"circuit:{rest}")
    instrument.kind = "Circuit"
    instrument.status = InstrumentStatus.SIMULATED
    instrument.simulated_driver = driver
    instrument.circuit_simulation = simulation
    try:
        from ..driver.simulated.facets import SimulatedMonitor

        instrument.add_facet(SimulatedMonitor(driver))
    except Exception:  # (a monitor is a convenience)
        log.debug("no monitor facet", exc_info=True)
    instrument.add_facet(SimulationControl(driver))
    instrument.add_facet(CircuitRun(simulation, owned))
    return instrument


def open_circuit_driver(rest: str):
    return open_circuit_instrument(rest).simulated_driver
