# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Microcontrollers and ngspice in lockstep (in the engine process, ``engines/process.py``).

* Before ngspice takes a step it asks how long the step may be (``GetSyncData``): the microcontrollers
  run ahead to its end - with the inputs they had - and the step is shortened to their first change
  of a pin, so an edge comes at its time.
* While ngspice solves the step it asks for the ``external`` sources of the pins (``GetVSRCData``):
  the level, whether the pin drives and its pull-up, at the time asked, from what the
  microcontrollers did.
* After each accepted point the inputs take the voltages of their nets: logic levels with the
  thresholds of the supply (and hysteresis), the analog inputs in volts.

What they sent on their UARTs, and their pins, go to the application with the points
(:meth:`CoSimulator.drain`); it sends what is typed into a serial monitor (:meth:`CoSimulator.request`).
"""

from __future__ import annotations

import bisect
import logging
import threading

from .. import CircuitError
from .avr import EVENT_DIRECTION, EVENT_PORT, EVENT_UART, AvrMcu
from .boards import McuInfo, McuPin

log = logging.getLogger(__name__)

#: how far the timeline of a pin reaches back (seconds): ngspice may step back after a rejected step
KEEP = 0.01


class _PinState:
    """What a port pin did over time: (time, level, drives, pull-up)."""

    def __init__(self, pin: McuPin) -> None:
        self.pin = pin
        self.times: list[float] = [0.0]
        self.states: list[tuple[int, bool, bool]] = [(0, False, False)]
        self.input_level: bool | None = None

    def add(self, time: float, level: int, drives: bool, pullup: bool) -> None:
        state = (level, drives, pullup)
        if state == self.states[-1]:
            return
        time = max(time, self.times[-1])
        self.times.append(time)
        self.states.append(state)
        if len(self.times) > 64 and self.times[-1] - self.times[0] > KEEP:
            cut = bisect.bisect_left(self.times, self.times[-1] - KEEP)
            del self.times[:max(cut - 1, 0)]
            del self.states[:max(cut - 1, 0)]

    def rebase(self, now: float = 0.0, state: tuple[int, bool, bool] | None = None) -> None:
        """Forget the past: from ``now`` on the pin is in ``state`` (its current one by default) - after
        a restart of ngspice (its time starts at 0 again) or a reset of the chip."""
        self.times = [now]
        self.states = [state if state is not None else self.states[-1]]

    def at(self, time: float) -> tuple[int, bool, bool]:
        index = bisect.bisect_right(self.times, time) - 1
        return self.states[max(index, 0)]

    def next_change(self, after: float, until: float) -> float | None:
        index = bisect.bisect_right(self.times, after)
        if index < len(self.times) and self.times[index] <= until:
            return self.times[index]
        return None


class _Running:
    def __init__(self, info: McuInfo, mcu: AvrMcu) -> None:
        self.info = info
        self.mcu = mcu
        self.port_out: dict[str, int] = {}
        self.port_ddr: dict[str, int] = {}
        self.pins = {(pin.port, pin.bit): _PinState(pin) for pin in info.pins if not pin.analog_only}
        self.analog = [pin for pin in info.pins if pin.adc is not None]
        #: what it sent, by UART ("0" is Serial, "1" Serial1 ...)
        self.serial: dict[str, bytearray] = {}
        #: added to the chip's time to get ngspice's (the chip may have run before this run)
        self.time_offset = 0.0
        self._resync = False

    def apply_events(self, lock: threading.Lock) -> None:
        if self._resync:  # (after a reset: the registers as they are now, whatever was heard meanwhile)
            self._resync = False
            now = self.mcu.time + self.time_offset
            for port in sorted({port for port, _bit in self.pins}):
                registers = self.mcu.port(port)
                if registers is None:
                    continue
                self.port_out[port], self.port_ddr[port] = registers[0], registers[1]
                for bit in range(8):
                    state = self.pins.get((port, bit))
                    if state is not None:
                        drives = bool(registers[1] & (1 << bit))
                        level = (registers[0] >> bit) & 1
                        state.add(now, level, drives, (not drives) and bool(level))
        for event in self.mcu.events():
            time = event.time + self.time_offset
            if event.kind == EVENT_UART:
                with lock:
                    self.serial.setdefault(event.port, bytearray()).append(event.value)
                continue
            if event.kind == EVENT_PORT:
                self.port_out[event.port] = event.value
            elif event.kind == EVENT_DIRECTION:
                self.port_ddr[event.port] = event.value
            else:
                continue  # (EVENT_PIN of an older shim: not used)
            out, ddr = self.port_out.get(event.port, 0), self.port_ddr.get(event.port, 0)
            for bit in range(8):
                state = self.pins.get((event.port, bit))
                if state is None:
                    continue
                drives = bool(ddr & (1 << bit))
                level = (out >> bit) & 1
                state.add(time, level, drives, (not drives) and bool(level))

    def rebase(self, now: float = 0.0) -> None:
        """ngspice's time starts at ``now`` again: the chip's time maps onto it from here on."""
        self.time_offset = now - self.mcu.time
        for state in self.pins.values():
            state.rebase(now)

    def reset(self, now: float) -> None:
        """Start the firmware again at ngspice's time ``now``: the chip is as switched on - no pin drives,
        no pull-up - until the firmware says otherwise."""
        self.mcu.reset()  # (its cycle count starts at 0)
        self.time_offset = now
        self.port_out.clear()
        self.port_ddr.clear()
        for state in self.pins.values():
            state.rebase(now, (0, False, False))
            state.input_level = None
        self._resync = True


class CoSimulator:
    """See the module. ``mcus``: the :class:`~.boards.McuInfo` of the circuit's microcontrollers."""

    def __init__(self, mcus: list[McuInfo], circuit=None) -> None:
        self.running: dict[str, _Running] = {}
        self.sources: dict[str, tuple[_Running, _PinState, str]] = {}
        self.problems: list[str] = []
        #: guards what the application's threads touch (serial output, requests) against ngspice's thread
        self._lock = threading.Lock()
        self._requests: list[tuple[str, str, object]] = []
        self._time = 0.0  # (the history's time of the last point)
        self._raw_time = 0.0  # (ngspice's own time: 0 at every restart)
        self._simulation = None
        self.max_ahead = 50e-6
        for info in mcus:
            self._add(info)
        self._bind_sources()

    @property
    def vcc(self) -> float:
        """The supply of the first microcontroller (each has its own: ``info.vcc``)."""
        return next((running.info.vcc for running in self.running.values()), 5.0)

    def _add(self, info: McuInfo) -> _Running | None:
        try:
            mcu = AvrMcu(info.core, info.frequency, info.vcc)
            if info.firmware:
                mcu.load_firmware(info.firmware)
            else:
                self.problems.append(f"{info.ref} has no firmware: it does nothing")
        except (CircuitError, OSError) as error:
            self.problems.append(f"{info.ref}: {error}")
            return None
        running = _Running(info, mcu)
        self.running[info.ref] = running
        return running

    def _bind_sources(self) -> None:
        self.sources = {}
        for running in self.running.values():
            for state in running.pins.values():
                pin = state.pin
                self.sources[pin.level] = (running, state, "level")
                self.sources[pin.drive] = (running, state, "drive")
                self.sources[pin.pullup] = (running, state, "pullup")

    def attach(self, simulation, restart: bool = False) -> None:
        """Let ``simulation`` (a :class:`~..engines.live.LocalSimulation`) run the microcontrollers."""
        from ..engines.live import MCU_STEP

        self._simulation = simulation
        self.max_ahead = simulation.max_step or MCU_STEP
        if restart:
            for running in self.running.values():
                running.rebase(0.0)
        simulation.prepare = self._prepare
        simulation.on_point = self._point
        simulation.on_restart = self._restart
        self._watch(simulation)

    def _watch(self, simulation) -> None:
        for running in self.running.values():  # (the voltages at its pins at every point: _point)
            simulation.watched.update(state.pin.net.lower() for state in running.pins.values())
            simulation.watched.update(pin.net.lower() for pin in running.analog)

    def _restart(self, circuit) -> None:
        """The simulation starts again with ``circuit`` (its time at 0): the microcontrollers take their
        new wiring - a changed one starts its firmware anew, an unchanged one goes on - and their
        timelines start at 0 too."""
        infos = {info.ref: info for info in getattr(circuit, "mcus", [])}
        with self._lock:
            for ref in [ref for ref in self.running if ref not in infos]:
                self.running.pop(ref).mcu.close()
            for ref, info in infos.items():
                running = self.running.get(ref)
                if running is not None and running.info == info:
                    running.rebase(0.0)
                    continue
                if running is not None:
                    running.mcu.close()
                    del self.running[ref]
                self._add(info)
            self._bind_sources()
            self._raw_time = 0.0
        if self._simulation is not None:
            self._watch(self._simulation)

    def _prepare(self, engine) -> None:
        engine.synchronize(steps=True)
        engine.on_source = self._source
        engine.on_sync = self._sync

    # --------------------------------------------------- ngspice's thread
    def _advance(self, until: float) -> None:
        for running in self.running.values():
            local = until - running.time_offset
            if local > running.mcu.time:
                running.mcu.run_until(local)
                running.apply_events(self._lock)

    def _apply_requests(self, now: float) -> None:
        """Serial input and resets, applied between two steps (simavr is not thread-safe)."""
        with self._lock:
            requests, self._requests = self._requests, []
        for ref, action, data in requests:
            running = self.running.get(ref)
            if running is None:
                continue
            if action == "serial":
                uart, payload = data if isinstance(data, tuple) else ("0", data)
                running.mcu.uart_write(bytes(payload or b""), uart)
            elif action == "reset":
                running.reset(now)

    def _sync(self, time: float, delta: float, olddelta: float, redostep: int, location: int) -> float | None:
        if location != 0 or not self.running:
            return None
        self._raw_time = time
        if self._requests:
            self._apply_requests(time)
        delta = min(delta, self.max_ahead)
        end = time + delta
        self._advance(end)
        first = None
        for running in self.running.values():
            for state in running.pins.values():
                change = state.next_change(time + 1e-12, end)
                if change is not None and (first is None or change < first):
                    first = change
        if first is not None and first - time > 1e-9:
            return first - time + 1e-10
        return delta

    def _source(self, name: str, time: float) -> float:
        found = self.sources.get(name)
        if found is None:
            return 0.0
        running, state, which = found
        if time > running.mcu.time + running.time_offset:
            self._advance(time)
        level, drives, pullup = state.at(time)
        if which == "level":
            return running.info.vcc if level else 0.0
        if which == "drive":
            return 1.0 if drives else 0.0
        return 1.0 if pullup else 0.0

    def _point(self, time: float, latest: dict) -> None:
        self._time = time
        for running in self.running.values():
            vcc = running.info.vcc
            high, low = 0.6 * vcc, 0.3 * vcc
            for state in running.pins.values():
                pin = state.pin
                if state.states[-1][1]:
                    continue  # (an output: what the net does is the pin's own doing; set_input would be filtered)
                value = latest.get(pin.net.lower())
                if value is None:
                    continue
                volts = float(value.real if isinstance(value, complex) else value)
                level = state.input_level
                if level is None or (level and volts < low) or (not level and volts > high):
                    level = volts > (high + low) / 2 if level is None else not level
                    state.input_level = level
                    running.mcu.set_input(pin.port, pin.bit, level)
            for pin in running.analog:
                value = latest.get(pin.net.lower())
                if value is not None:
                    running.mcu.set_analog(pin.adc, float(value.real if isinstance(value, complex) else value))

    # ---------------------------------------------------------- the application
    def drain(self) -> dict | None:
        """What happened since the last call: serial output (by UART) and pin states by microcontroller."""
        found = {}
        with self._lock:
            for ref, running in self.running.items():
                serial, running.serial = running.serial, {}
                item = {"serial": bytes(serial.get("0", b"")), "state": running.mcu.state,
                        "time": running.mcu.time,
                        "ports": {port: (running.port_out.get(port, 0), running.port_ddr.get(port, 0))
                                  for port in set(running.port_out) | set(running.port_ddr)}}
                for uart, data in serial.items():
                    if uart != "0" and data:
                        item[f"serial{uart}"] = bytes(data)
                found[ref] = item
            if self.problems:
                found["#problems"] = list(self.problems)
                self.problems = []
        return found or None

    def request(self, ref: str, action: str, data=None):
        """Serial input (``data``: bytes, or (uart, bytes)) or a reset; taken at the next step."""
        if ref not in self.running:
            raise CircuitError(f"no microcontroller {ref}")
        if action not in ("serial", "reset"):
            raise CircuitError(f"unknown request {action!r}")
        with self._lock:
            self._requests.append((ref, action, data))

    def close(self) -> None:
        with self._lock:
            for running in self.running.values():
                running.mcu.close()
            self.running.clear()
            self.sources = {}


def attach(simulation, schematic, resolve, base_folder: str) -> list[str]:
    """Called by the document: give ``simulation`` (a :class:`~..engines.live.LiveSimulation`) the
    microcontrollers of its circuit; returns problems for people."""
    infos = getattr(simulation.circuit, "mcus", [])
    problems = []
    for info in infos:
        if not info.firmware:
            problems.append(f"{info.ref} has no firmware (choose a .hex or .elf file in the inspector)")
    from . import avr

    if infos and not avr.available():
        problems.append("the AVR emulator is not installed: the microcontrollers do not run")
        return problems
    simulation.mcus = list(infos)
    return problems
