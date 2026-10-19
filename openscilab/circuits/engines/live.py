# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The live simulation: a transient that runs on while the circuit is shown.

ngspice runs a transient without end in its background thread (``bg_run``) and stores nothing
(``.save none``: an endless run would fill the memory); every accepted point goes into a
:class:`~.history.History`. In real time the run waits for the clock (in ngspice's thread, so the
simulation itself is paced); in virtual time it runs as fast as it can. A changed value goes in while
it runs - halted, ``alter``, resumed; a changed circuit starts it again, its time going on where it
was.

No Qt here: the document reads :attr:`LiveSimulation.history` with a timer.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout

from .. import values
from ..spice import Circuit, alterations, data_name
from . import acquire, ngspice, release
from .history import History

log = logging.getLogger(__name__)

#: the transient runs this long (simulated seconds) - "without end"
ENDLESS = 1e7
#: the longest step of a circuit with microcontrollers (their edges get points of their own anyway)
MCU_STEP = 1e-3


class LocalSimulation:
    """The live simulation in this process (the engine process runs it, see :class:`LiveSimulation`)."""

    def __init__(self, circuit: Circuit, step: float = 10e-6, realtime: bool = True, speed: float = 1.0,
                 owner: str = "a circuit", max_step: float | None = None) -> None:
        self.circuit = circuit
        self.step = step
        self.max_step = max_step
        self.realtime = realtime
        self.speed = speed if speed > 0 else 1.0
        self.owner = owner
        self.history = History(self.tracked(circuit))
        #: "idle", "running", "paused", "stopped", "error"
        self.state = "idle"
        self.error = ""
        self._engine: ngspice.NgSpice | None = None
        self._wall_start = 0.0
        self._sim_start = 0.0
        self._paused_at: float | None = None
        self._lock = threading.Lock()
        #: called in ngspice's thread with each point after the history took it (co-simulation)
        self.on_point: Callable[[float, dict], None] | None = None
        #: extra preparation of the engine before the circuit is loaded (co-simulation)
        self.prepare: Callable[[ngspice.NgSpice], None] | None = None
        #: vectors needed at every point besides those kept (the nets of microcontroller pins)
        self.watched: set[str] = set()
        #: called with the new circuit before a restart (the co-simulation takes its new wiring)
        self.on_restart: Callable[[Circuit], None] | None = None
        self._halting = False  # (a halt of ours: ngspice's thread ending is no error then)
        #: what connected devices read (``device.input``): external source (lower case) -> volts
        self.inputs: dict[str, float] = {}

    @staticmethod
    def tracked(circuit: Circuit) -> list[str]:
        """The vectors whose course is kept: those of the probes, of the nets connected devices follow
        (``device.output``), and the currents of the parts."""
        names = [data_name(probe.vector) for probe in circuit.probes if probe.vector != "0"]
        names += [data_name(probe.reference) for probe in circuit.probes if probe.reference]
        names += [data_name(pin.vector) for pin in getattr(circuit, "device_pins", ()) if pin.vector not in ("", "0")]
        return list(dict.fromkeys(names))

    def _source(self, fallback):
        """The values of ``external`` sources: what devices read, else the co-simulation's (``fallback``)."""
        inputs = self.inputs

        def source(name: str, time_: float) -> float:
            value = inputs.get(name)
            if value is not None:
                return value
            return fallback(name, time_) if fallback is not None else 0.0

        return source

    def set_inputs(self, values: dict[str, float]) -> None:
        """What connected devices read now (external source -> volts); taken at the next step."""
        self.inputs.update(values)

    def value(self, vector: str):
        """The latest value of a vector (``v(out)``, ``i(v1)``, ``@d1[id]``); ``None`` before it has one."""
        return self.history.latest(data_name(vector))

    def course(self, vector: str, since: float | None = None):
        """(times, values) of a probe's vector (``since``: from that time on)."""
        return self.history.snapshot(data_name(vector), since)

    # ----------------------------------------------------------------- run
    def _lines(self, circuit: Circuit) -> list[str]:
        def analysis_or_end(line: str) -> bool:
            words = line.strip().lower().split()
            # (".ends" closes a subcircuit: it stays)
            return bool(words) and words[0] in (".save", ".end", ".tran", ".op", ".ac", ".dc", ".sp", ".noise")

        lines = [line for line in circuit.lines if not analysis_or_end(line)]
        lights = getattr(circuit, "lights", None)
        saves = sorted({vector for ref, vector in circuit.currents.items()
                        if vector.startswith("@") and (lights is None or ref in lights)})
        if getattr(circuit, "show_currents", False):
            from .. import currents

            saves = sorted(set(saves) | set(currents.saved_vectors(lines)))
            lines = currents.with_ammeters(lines)
        lines.append(".save none" + "".join(f" {vector}" for vector in saves))
        longest = self.max_step or (MCU_STEP if circuit.mcus else None)
        tmax = f" {values.spice_number(longest)}" if longest else ""
        # (power on: from empty capacitors, as when it is switched on, not from its operating point)
        start = " uic" if getattr(circuit, "power_on", False) else ""
        lines.append(f".tran {values.spice_number(self.step)} {ENDLESS:g} 0{tmax}{start}")
        lines.append(".end")
        return lines

    def start(self) -> None:
        """Start (or continue after :meth:`pause`); raises EngineBusy or NgSpiceError."""
        if self.state == "paused":
            self.resume()
            return
        if self.state == "running":
            return
        acquire(self.owner)
        try:
            engine = ngspice.instance()
            engine.set_every_point(self._wanted(self.circuit))
            engine.on_data = self._point
            engine.on_running = self._running_changed
            self._halting = False
            engine.on_source = None
            engine.on_sync = None
            engine.command("set no_mem_check")
            if self.circuit.mcus or any(" external" in line for line in self.circuit.lines):
                engine.synchronize(steps=False)
            if self.prepare is not None:
                self.prepare(engine)
            engine.on_source = self._source(engine.on_source)
            engine.load(self._lines(self.circuit))
            self._engine = engine
            self._wall_start = time.monotonic()
            self._sim_start = self.history.offset
            engine.command("bg_run")
            self.state = "running"
        except Exception as error:
            self.state = "error"
            self.error = str(error)
            release()
            raise

    def _wanted(self, circuit: Circuit) -> set[str]:
        """The vectors read at every point: time, the probes' and the currents of parts that light."""
        lights = getattr(circuit, "lights", set())
        return ({"time"} | set(self.history.tracked) | self.watched
                | {data_name(vector) for ref, vector in circuit.currents.items() if ref in lights})

    def _retrack(self, circuit: Circuit) -> None:
        """Keep the courses of ``circuit``'s probes (new ones too) and read them at every point."""
        self.history.track(self.tracked(circuit))
        if self._engine is not None:
            self._engine.set_every_point(self._wanted(circuit))

    def _running_changed(self, running: bool) -> None:
        """ngspice's thread started or ended (its thread): an end we did not ask for is an error of the
        run (a time step too small, a singular matrix) - the state says so instead of staying "running"."""
        if running or self._halting or self.state != "running" or self._engine is None:
            return
        errors = [line.strip() for line in self._engine.errors if ngspice._is_error(line) or "too small" in line.lower()
                  or "singular" in line.lower() or "aborted" in line.lower()]
        self.error = "\n".join(dict.fromkeys(errors[-6:])) or "ngspice ended the run"
        self.state = "error"

    def _halt(self) -> None:
        self._halting = True
        try:
            if self._engine is not None:
                self._engine.halt()
        finally:
            self._halting = False

    def _point(self, names: list[str], numbers: list) -> None:
        self.history.append(names, numbers)
        if self.on_point is not None:
            self.on_point(self.history.last_time, self.history.latest())
        if self.realtime:
            ahead = (self.history.last_time - self._sim_start) / self.speed - (time.monotonic() - self._wall_start)
            if ahead > 0.002:
                time.sleep(min(ahead, 0.1))

    def pause(self) -> None:
        if self.state != "running" or self._engine is None:
            return
        self._halt()
        self._paused_at = time.monotonic()
        self.state = "paused"

    def resume(self) -> None:
        if self.state != "paused" or self._engine is None:
            return
        if self._paused_at is not None:
            self._wall_start += time.monotonic() - self._paused_at  # (the pause does not count)
            self._paused_at = None
        self._engine.command("bg_resume")
        self.state = "running"

    def set_speed(self, speed: float) -> None:
        """Run on at ``speed`` (simulated seconds per second, in real time) from the moment reached."""
        if speed <= 0:
            return
        # (the clock starts anew here: the time run so far does not count at the new speed; paused, the
        # pause is added when it resumes)
        self._sim_start = self.history.last_time
        self._wall_start = self._paused_at if self._paused_at is not None else time.monotonic()
        self.speed = speed

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._halt()
                self._engine.on_data = None
                self._engine.on_running = None
                self._engine.set_every_point(None)
            finally:
                self._engine = None
                release()
        if self.state in ("running", "paused"):
            self.state = "stopped"

    def clock(self) -> float:
        """The simulated time reached (seconds)."""
        return self.history.last_time

    # -------------------------------------------------------------- changes
    def change(self, circuit: Circuit) -> str:
        """Take the circuit as it is now: ``"altered"`` (values changed while running), ``"restarted"``
        (it had to start again) or ``""`` (nothing to do)."""
        old = self.circuit
        if old.lines == circuit.lines and getattr(old, "show_currents", False) == getattr(circuit, "show_currents", False):
            # (a probe added or taken away changes no line: its course is kept from now on)
            self.circuit = circuit
            self._retrack(circuit)
            return ""
        commands = self._alterations(old, circuit)
        if commands is not None and self._engine is not None and self.state in ("running", "paused"):
            running = self.state == "running"
            engine = self._engine
            if running:
                self._halt()
            for command in commands:
                engine.command(command)
            self.circuit = circuit
            self._retrack(circuit)
            if running:
                engine.command("bg_resume")
            return "altered"
        # start again where the time was
        was = self.state
        self.stop()
        self.history.offset = self.history.last_time
        self.history.track(self.tracked(circuit))
        self.circuit = circuit
        if self.on_restart is not None:
            self.on_restart(circuit)
        if was in ("running", "paused"):
            self.state = "idle"
            self.start()
            if was == "paused":
                self.pause()
        return "restarted"

    @staticmethod
    def _alterations(old: Circuit, new: Circuit) -> list[str] | None:
        if set(old.elements) != set(new.elements):
            return None
        if getattr(old, "show_currents", False) != getattr(new, "show_currents", False):
            return None  # (other saves, ammeters: a new start)
        rest_old = [line for line in old.lines if not any(line in lines for lines in old.elements.values())]
        rest_new = [line for line in new.lines if not any(line in lines for lines in new.elements.values())]
        if rest_old != rest_new:
            # supplies of power symbols are not elements of a component: their voltage as a source
            found = alterations(rest_old, rest_new)
            if found is None:
                return None
            commands = list(found)
        else:
            commands = []
        for ref, lines in new.elements.items():
            found = alterations(old.elements[ref], lines)
            if found is None:
                return None
            commands.extend(found)
        return commands


class LiveSimulation:
    """The live simulation of a circuit, run by an engine process (``engines/process.py``); its
    :attr:`history` mirrors what the process computed. The same interface as :class:`LocalSimulation`."""

    def __init__(self, circuit: Circuit, step: float = 10e-6, realtime: bool = True, speed: float = 1.0,
                 owner: str = "a circuit", max_step: float | None = None, mcus: list | None = None) -> None:
        self.circuit = circuit
        self.step = step
        self.max_step = max_step
        self.realtime = realtime
        self.speed = speed if speed > 0 else 1.0
        self.owner = owner
        #: microcontrollers running with it (descriptions for ``circuits/mcu/cosim.py``)
        self.mcus = list(mcus or [])
        self.history = History(LocalSimulation.tracked(circuit))
        self.state = "idle"
        self.error = ""
        #: what the microcontrollers did (pins, serial output), from the last batches
        self.mcu_events: list = []
        self._engine = None
        self._generation = -1  # (of the engine process when it was ours: a change sent late must not reach another run)
        self._lock = threading.Lock()

    tracked = staticmethod(LocalSimulation.tracked)

    def _ended(self) -> None:
        """The engine process ended (ngspice crashed, the computer killed it)."""
        if self.state in ("running", "paused"):
            self.state, self.error = "error", "the simulator's process ended (ngspice crashed on this circuit)"
        with self._lock:
            self._engine = None

    def _batch(self, times, columns, latest, state, error, extra) -> None:
        self.history.extend(times, columns, latest)
        if state and self.state in ("running", "paused") and state in ("error", "stopped"):
            self.state, self.error = state, error
        if extra:
            with self._lock:
                self.mcu_events.append(extra)
                del self.mcu_events[:-200]

    def take_mcu_events(self) -> list:
        with self._lock:
            events, self.mcu_events = self.mcu_events, []
        return events

    def start(self) -> None:
        if self.state == "paused":
            self.resume()
            return
        if self.state == "running":
            return
        from . import process

        engine = process.acquire()
        engine.on_batch = self._batch
        engine.on_ended = self._ended
        try:
            engine.call("live", timeout=60, circuit=self.circuit, step=self.step, realtime=self.realtime,
                        speed=self.speed, owner=self.owner, max_step=self.max_step, offset=self.history.last_time,
                        mcus=self.mcus)
        except Exception as error:
            self.state, self.error = "error", str(error)
            if isinstance(error, (TimeoutError, FutureTimeout)):
                engine.close()  # (it may still be starting the run: not fit for the pool)
            else:
                process.release(engine)
            raise
        with self._lock:
            self._engine = engine
            self._generation = engine.generation
        self.state = "running"
        if getattr(self, "_inputs", None):  # (what devices read before: a new run starts from it)
            engine.call_async("inputs", values=dict(self._inputs))

    def pause(self) -> None:
        if self.state == "running" and self._engine is not None:
            self._engine.call("pause")
            self.state = "paused"

    def resume(self) -> None:
        if self.state == "paused" and self._engine is not None:
            self._engine.call("resume")
            self.state = "running"

    def set_speed(self, speed: float) -> None:
        """Run on at ``speed`` (simulated seconds per second, in real time); sent without waiting."""
        if speed <= 0:
            return
        self.speed = speed
        with self._lock:
            engine = self._engine
        if engine is None:
            return
        try:
            engine.call_async("speed", speed=speed)
        except Exception:  # (the process ended: _ended says so)
            log.debug("speed not sent", exc_info=True)

    def stop(self) -> None:
        with self._lock:
            engine, self._engine = self._engine, None
            self._pending = None
        if engine is not None:
            from . import process

            engine.on_ended = None
            try:
                engine.call("stop", timeout=10)
            except Exception:  # noqa: BLE001 - the process is given back or closed either way
                engine.close()
            finally:
                process.release(engine)
        if self.state in ("running", "paused"):
            self.state = "stopped"

    def clock(self) -> float:
        return self.history.last_time

    def value(self, vector: str):
        return self.history.latest(data_name(vector))

    def course(self, vector: str, since: float | None = None):
        return self.history.snapshot(data_name(vector), since)

    def change(self, circuit: Circuit) -> str:
        if self._engine is None or self.state not in ("running", "paused"):
            self.circuit = circuit
            return ""
        result = self._engine.call("change", timeout=60, circuit=circuit)
        self.circuit = circuit
        self.history.track(LocalSimulation.tracked(circuit))  # (new probes: their course from now on)
        return result

    def submit(self, circuit: Circuit) -> None:
        """Take the circuit as it is now without waiting (a knob turned, a value typed): the newest of
        several quick changes goes to the engine when the one before is done."""
        self.circuit = circuit
        with self._lock:
            if getattr(self, "_in_flight", False):
                self._pending = circuit
                return
            self._in_flight = True
        self._send(circuit)

    def _send(self, circuit: Circuit) -> None:
        with self._lock:
            engine = self._engine
            # (the engine was given back meanwhile: another circuit may run on it now)
            if engine is not None and engine.generation != self._generation:
                engine = None
        if engine is None or self.state not in ("running", "paused"):
            with self._lock:
                self._in_flight = False
            return
        try:
            future = engine.call_async("change", circuit=circuit)
        except Exception:  # noqa: BLE001 - the engine ended: its state says so
            with self._lock:
                self._in_flight = False
            return
        future.add_done_callback(lambda done, circuit=circuit: self._changed(done, circuit))

    def _changed(self, done, circuit: Circuit) -> None:
        try:
            done.result()
            self.history.track(LocalSimulation.tracked(circuit))
        except Exception as error:  # noqa: BLE001 - shown by the document
            self.error = str(error)
        with self._lock:
            pending, self._pending = getattr(self, "_pending", None), None
            if pending is None:
                self._in_flight = False
        if pending is not None:
            self._send(pending)

    def set_inputs(self, values: dict[str, float]) -> None:
        """What connected devices read now (``device.input``: external source -> volts); sent to the
        engine process without waiting, the newest values win."""
        self._inputs = {**getattr(self, "_inputs", {}), **values}
        with self._lock:
            engine = self._engine
        if engine is None:
            return
        try:
            engine.call_async("inputs", values=dict(values))
        except Exception:  # (the process ended: _ended says so)
            log.debug("inputs not sent", exc_info=True)

    def mcu(self, ref: str, action: str, data=None):
        """A request to a microcontroller of the circuit (serial input, reset)."""
        if self._engine is None:
            raise RuntimeError("the circuit does not run")
        return self._engine.call("mcu", ref=ref, action=action, data=data)
