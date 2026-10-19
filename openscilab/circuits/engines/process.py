# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""ngspice in a process of its own.

ngspice calls back for every point of a run - up to a hundred thousand times a second - and each
call needs Python's interpreter lock; in the application's process any other busy thread (a flow, a
decoder, the catalog reading KiCad's libraries) would starve it. In a process of its own it runs at
full speed, several circuits run at once (one ngspice each), and a crash of ngspice ends only that
process.

The engine process (:func:`main`) runs :class:`~.live.LocalSimulation` and the analyses and sends
what a run produced every :data:`BATCH_INTERVAL` seconds; :class:`EngineProcess` is its other end.
Idle processes are kept for the next run (:func:`acquire`, :func:`release`).
"""

from __future__ import annotations

import itertools
import logging
import multiprocessing
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

log = logging.getLogger(__name__)

#: how often the engine process sends the points of a run (seconds)
BATCH_INTERVAL = 0.015
#: an engine process that does not start within this long is given up (seconds)
START_TIMEOUT = 30.0
#: a live run without a new point for this long is given up (ngspice stuck in a failing operating point)
STALL_SECONDS = 5.0
#: idle engine processes kept for the next run
POOL_SIZE = 2


class EngineEnded(RuntimeError):
    """The engine process ended (ngspice crashed, or it was closed)."""


# ======================================================== the engine process
class _Child:  # pragma: no cover - runs in the engine process (tested through EngineProcess)
    def __init__(self, connection, ngspice_path: str) -> None:
        self.connection = connection
        self.ngspice_path = ngspice_path
        self.simulation = None
        self.send_lock = threading.Lock()
        self.flush_lock = threading.Lock()  # (the flusher and pause/stop flush: one at a time, in order)
        self.sent_until = -1.0
        self.cosimulation = None
        self._sent_state = ""
        self._progress_at = (0.0, 0.0)  # (the clock last seen, and when: a run that makes no progress is an error)
        self.stopped = threading.Event()

    def send(self, message) -> None:
        with self.send_lock:
            try:
                self.connection.send(message)
            except (OSError, EOFError, BrokenPipeError):
                self.stopped.set()

    def flusher(self) -> None:
        while not self.stopped.is_set():
            time.sleep(BATCH_INTERVAL)
            simulation = self.simulation
            if simulation is None:
                continue
            try:
                self.flush(simulation)
            except Exception:
                log.exception("flush")

    def flush(self, simulation) -> None:
        with self.flush_lock:
            times, columns = simulation.history.since(self.sent_until)
            extra = self.cosimulation.drain() if self.cosimulation is not None else None
            self._watch_progress(simulation, bool(len(times)))
            if not len(times) and extra is None and simulation.state == self._sent_state:
                return
            if len(times):
                self.sent_until = float(times[-1])
            self._sent_state = simulation.state
            # (the newest of each vector, overlaid with the last point that brought all: one moment for the
            # currents of the current view, its voltages with them)
            history = simulation.history
            latest = {key: (value.real if isinstance(value, complex) else value)
                      for key, value in {**history.latest(), **history.complete()}.items()}
            self.send(("batch", times, columns, latest, simulation.state, simulation.error, extra))

    def _watch_progress(self, simulation, points: bool) -> None:
        """ngspice stuck in a failing operating point does not end its thread and does not answer a halt:
        a running simulation without a point for :data:`STALL_SECONDS` is marked an error (the
        application then closes this process and starts another)."""
        now = time.monotonic()
        clock, since = self._progress_at
        if points or simulation.state != "running" or simulation.clock() != clock:
            self._progress_at = (simulation.clock(), now)
            return
        if now - since > STALL_SECONDS:
            errors = [line.strip() for line in getattr(simulation._engine, "errors", [])
                      if "singular" in line.lower() or "failed" in line.lower() or "too small" in line.lower()]
            simulation.error = "ngspice does not get on (it fails to converge" + (
                f": {errors[-1]}" if errors else "") + ")"
            simulation.state = "error"

    def serve(self) -> None:
        threading.Thread(target=self.flusher, name="engine-flush", daemon=True).start()
        while not self.stopped.is_set():
            try:
                message = self.connection.recv()
            except (EOFError, OSError):
                break
            number, method, arguments = message
            try:
                result = getattr(self, f"do_{method}")(**arguments)
                self.send(("reply", number, True, result))
            except Exception as error:  # noqa: BLE001 - answered to the application
                self.send(("reply", number, False, f"{type(error).__name__}: {error}"))
            if method == "quit":
                break
        self.stopped.set()
        if self.simulation is not None:
            try:
                self.simulation.stop()
            except Exception:  # noqa: BLE001 - the process ends anyway
                log.debug("stop at the end", exc_info=True)

    # ------------------------------------------------------------- requests
    def do_ping(self) -> dict:
        from . import ngspice

        engine = ngspice.instance(self.ngspice_path)
        return {"version": engine.version, "code_models": engine.code_models}

    def do_live(self, circuit, step: float, realtime: bool, speed: float, owner: str, max_step, offset: float = 0.0,
                mcus=None) -> dict:
        from . import ngspice
        from .live import LocalSimulation

        ngspice.instance(self.ngspice_path)
        if self.simulation is not None:
            self.simulation.stop()
        if self.cosimulation is not None:
            self.cosimulation.close()
            self.cosimulation = None
        simulation = LocalSimulation(circuit, step, realtime, speed, owner, max_step)
        simulation.history.offset = offset
        simulation.history.last_time = offset
        self.sent_until = offset - 1.0
        self._sent_state = ""
        cosimulation = None
        if mcus:
            from ..mcu import cosim

            cosimulation = cosim.CoSimulator(mcus, circuit)
            cosimulation.attach(simulation)
        try:
            simulation.start()
        except Exception:
            if cosimulation is not None:
                cosimulation.close()
            raise
        self.cosimulation = cosimulation
        # (its progress counts from now: before its first point a run of a busy computer is not stuck)
        self._progress_at = (simulation.clock(), time.monotonic())
        self.simulation = simulation
        return {"state": simulation.state}

    def do_pause(self) -> str:
        if self.simulation is not None:
            self.simulation.pause()
            self.flush(self.simulation)
            return self.simulation.state
        return "idle"

    def do_resume(self) -> str:
        if self.simulation is not None:
            self.simulation.resume()
            return self.simulation.state
        return "idle"

    def do_change(self, circuit) -> str:
        if self.simulation is None:
            return ""
        return self.simulation.change(circuit)  # (a restart rebinds the microcontrollers: on_restart)

    def do_stop(self) -> str:
        if self.simulation is not None:
            self.simulation.stop()
            self.flush(self.simulation)
            self.simulation = None
        if self.cosimulation is not None:
            self.cosimulation.close()
            self.cosimulation = None
        return "stopped"

    def do_speed(self, speed: float) -> None:
        """Simulated seconds per second from now on (in real time)."""
        if self.simulation is not None:
            self.simulation.set_speed(speed)

    def do_inputs(self, values: dict) -> None:
        """What connected devices read (``device.input``): the values of their external sources."""
        if self.simulation is not None:
            self.simulation.set_inputs(values)

    def do_mcu(self, ref: str, action: str, data=None):
        if self.cosimulation is None:
            raise RuntimeError("no microcontroller runs")
        return self.cosimulation.request(ref, action, data)

    def do_analysis(self, circuit, kind: str, limit: float):
        from . import ngspice
        from .analysis import local_analysis

        ngspice.instance(self.ngspice_path)
        if self.simulation is not None:
            raise RuntimeError("this engine runs a live simulation")
        return local_analysis(circuit, kind, limit)

    def do_quit(self) -> None:
        return None


def main(connection, ngspice_path: str, levels: int) -> None:  # pragma: no cover - a process
    logging.getLogger().setLevel(levels)
    try:
        from ...core import crashes

        crashes.enable_faulthandler()
    except Exception:  # noqa: BLE001 - a convenience of crash reports
        log.debug("faulthandler", exc_info=True)
    child = _Child(connection, ngspice_path)
    try:
        child.do_ping()
        connection.send(("ready", True, ""))
    except Exception as error:  # noqa: BLE001
        connection.send(("ready", False, f"{type(error).__name__}: {error}"))
        return
    child.serve()


# ===================================================== the application's end
class EngineProcess:
    """One engine process (see the module)."""

    def __init__(self, ngspice_path: str | None = None) -> None:
        if ngspice_path is None:
            from ...core import vendor

            ngspice_path = vendor.find("ngspice")
        if not ngspice_path:
            from .ngspice import NgSpiceError

            raise NgSpiceError("ngspice is not installed")
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=main, args=(child, ngspice_path, logging.getLogger().getEffectiveLevel()),
                                       name="openscilab-ngspice", daemon=True)
        self.process.start()
        child.close()
        if not self.connection.poll(START_TIMEOUT):
            self.process.kill()
            raise EngineEnded("the simulator's process did not start")
        try:
            _kind, ok, error = self.connection.recv()
        except (EOFError, OSError):
            self.process.join(2)
            raise EngineEnded(f"the simulator's process ended while starting (exit code {self.process.exitcode})") from None
        if not ok:
            self.process.join(2)
            from .ngspice import NgSpiceError

            raise NgSpiceError(error)
        self._numbers = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # (requests come from the Qt thread and the reader thread)
        self.ended = threading.Event()
        #: grows every time the process is given back to the pool: a run knows whether it still owns it
        self.generation = 0
        #: called with each batch: (times, columns, latest, state, error, extra), in the reading thread
        self.on_batch: Callable[..., None] | None = None
        #: called once when the process ended (in the reading thread)
        self.on_ended: Callable[[], None] | None = None
        threading.Thread(target=self._read, name="openscilab-ngspice-reader", daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                message = self.connection.recv()
            except (EOFError, OSError):
                break
            except Exception:
                log.exception("the engine process sent what cannot be read")
                break
            if message[0] == "batch":
                callback = self.on_batch
                if callback is not None:
                    try:
                        callback(*message[1:])
                    except Exception:
                        log.exception("batch")
            elif message[0] == "reply":
                _kind, number, ok, value = message
                with self._lock:
                    future = self._pending.pop(number, None)
                if future is not None:
                    if ok:
                        future.set_result(value)
                    else:
                        future.set_exception(_error(value))
        self.ended.set()
        with self._lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            future.set_exception(EngineEnded("the simulator's process ended"))
        callback = self.on_ended
        if callback is not None:
            try:
                callback()
            except Exception:
                log.exception("ended")

    def call(self, method: str, timeout: float | None = 30.0, **arguments) -> Any:
        return self.call_async(method, **arguments).result(timeout)

    def call_async(self, method: str, **arguments) -> Future:
        """Send a request; its answer comes as the future's result (in the reading thread)."""
        if self.ended.is_set() or not self.process.is_alive():
            raise EngineEnded("the simulator's process ended")
        future: Future = Future()
        with self._lock:
            number = next(self._numbers)
            self._pending[number] = future
        try:
            with self._send_lock:
                self.connection.send((number, method, arguments))
        except (OSError, BrokenPipeError) as error:
            with self._lock:
                self._pending.pop(number, None)
            raise EngineEnded(f"the simulator's process ended: {error}") from None
        return future

    @property
    def alive(self) -> bool:
        return self.process.is_alive() and not self.ended.is_set()

    def close(self) -> None:
        if self.alive:
            try:
                self.call("quit", timeout=3)
            except Exception:  # noqa: BLE001 - killed below then
                log.debug("quit", exc_info=True)
        self.process.join(2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(2)
        try:
            self.connection.close()
        except OSError:
            pass


def _error(text: str) -> Exception:
    from . import EngineBusy
    from .ngspice import NgSpiceError

    if text.startswith("EngineBusy"):
        return EngineBusy(text.split(": ", 1)[-1])
    return NgSpiceError(text.split(": ", 1)[-1] if text.startswith("NgSpiceError") else text)


_pool: list[EngineProcess] = []
_pool_lock = threading.Lock()


def acquire() -> EngineProcess:
    """An idle engine process (started when none is kept)."""
    with _pool_lock:
        while _pool:
            engine = _pool.pop()
            if engine.alive:
                engine.on_batch = None
                engine.on_ended = None
                return engine
            engine.close()  # (a dead one: its process joined, its pipe closed)
    return EngineProcess()


def release(engine: EngineProcess) -> None:
    """Give an engine process back (kept for the next run, or closed)."""
    engine.on_batch = None
    engine.on_ended = None
    engine.generation += 1
    if not engine.alive:
        engine.close()
        return
    with _pool_lock:
        if len(_pool) < POOL_SIZE:
            _pool.append(engine)
            return
    engine.close()


def close_all() -> None:
    with _pool_lock:
        engines, _pool[:] = list(_pool), []
    for engine in engines:
        engine.close()


def warm_up() -> None:
    """Start an engine process in the background, so the first run starts at once."""
    def run() -> None:
        try:
            release(EngineProcess())
        except Exception:  # (only a head start)
            log.debug("warm-up", exc_info=True)

    threading.Thread(target=run, name="openscilab-ngspice-warm-up", daemon=True).start()


_analysis_engine: EngineProcess | None = None
_analysis_lock = threading.Lock()


def analysis_engine() -> EngineProcess:
    """The engine process of analyses (one, kept; analyses run one after the other)."""
    global _analysis_engine
    if _analysis_engine is None or not _analysis_engine.alive:
        _analysis_engine = EngineProcess()
    return _analysis_engine
