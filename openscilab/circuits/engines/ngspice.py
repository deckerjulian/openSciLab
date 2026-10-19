# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""ngspice as a shared library (``sharedspice.h``) through ctypes.

ngspice keeps its state in the library: there is one simulator per process (:func:`instance`). Its
background thread (``bg_run``) calls :attr:`NgSpice.on_data` with every accepted point of a run;
those callbacks run in ngspice's thread with the GIL held - they must be short and must not touch
Qt. Co-simulation (microcontrollers, signals of devices) uses ``ngSpice_Init_Sync``: ngspice asks for
the values of ``external`` sources (:attr:`NgSpice.on_source`) and lets the caller shorten its next
step (:attr:`NgSpice.on_sync`).
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

NG_BOOL = ctypes.c_bool


class VecValues(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("creal", ctypes.c_double), ("cimag", ctypes.c_double),
                ("is_scale", NG_BOOL), ("is_complex", NG_BOOL)]


class VecValuesAll(ctypes.Structure):
    _fields_ = [("veccount", ctypes.c_int), ("vecindex", ctypes.c_int),
                ("vecsa", ctypes.POINTER(ctypes.POINTER(VecValues)))]


class VecInfo(ctypes.Structure):
    _fields_ = [("number", ctypes.c_int), ("vecname", ctypes.c_char_p), ("is_real", NG_BOOL),
                ("pdvec", ctypes.c_void_p), ("pdvecscale", ctypes.c_void_p)]


class VecInfoAll(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("title", ctypes.c_char_p), ("date", ctypes.c_char_p),
                ("type", ctypes.c_char_p), ("veccount", ctypes.c_int), ("vecs", ctypes.POINTER(ctypes.POINTER(VecInfo)))]


class NgComplex(ctypes.Structure):
    _fields_ = [("cx_real", ctypes.c_double), ("cx_imag", ctypes.c_double)]


class VectorInfo(ctypes.Structure):
    _fields_ = [("v_name", ctypes.c_char_p), ("v_type", ctypes.c_int), ("v_flags", ctypes.c_short),
                ("v_realdata", ctypes.POINTER(ctypes.c_double)), ("v_compdata", ctypes.POINTER(NgComplex)),
                ("v_length", ctypes.c_int)]


SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
SendStat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
ControlledExit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, NG_BOOL, NG_BOOL, ctypes.c_int, ctypes.c_void_p)
SendData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(VecValuesAll), ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(VecInfoAll), ctypes.c_int, ctypes.c_void_p)
BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, NG_BOOL, ctypes.c_int, ctypes.c_void_p)
GetVSRCData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.c_double, ctypes.c_char_p,
                               ctypes.c_int, ctypes.c_void_p)
GetISRCData = GetVSRCData
GetSyncData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_double, ctypes.POINTER(ctypes.c_double), ctypes.c_double,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)


class NgSpiceError(RuntimeError):
    pass


def code_model_folders(library: str) -> list[str]:
    """Where the XSPICE code models (``*.cm``) of the library ``library`` are."""
    folder = os.path.dirname(os.path.abspath(library))
    candidates = [os.path.join(folder, "ngspice"), os.path.join(folder, "..", "lib", "ngspice"),
                  os.path.join(folder, "..", "PlugIns", "sim", "ngspice"), os.path.join(folder, "..", "lib"),
                  os.path.join(folder, "codemodels"), folder]
    return [os.path.normpath(path) for path in candidates if glob.glob(os.path.join(path, "*.cm"))]


class NgSpice:
    """The simulator (see the module). Use :func:`instance`."""

    def __init__(self, path: str) -> None:
        self.path = path
        if sys.platform.startswith("win"):
            os.add_dll_directory(os.path.dirname(os.path.abspath(path))) if os.path.isabs(path) else None
        try:
            self.lib = ctypes.CDLL(path)
        except OSError as error:
            raise NgSpiceError(f"ngspice could not be loaded from {path}: {error}") from None
        lib = self.lib
        lib.ngSpice_Init.argtypes = [SendChar, SendStat, ControlledExit, SendData, SendInitData, BGThreadRunning,
                                     ctypes.c_void_p]
        lib.ngSpice_Init.restype = ctypes.c_int
        lib.ngSpice_Init_Sync.argtypes = [GetVSRCData, GetISRCData, GetSyncData, ctypes.POINTER(ctypes.c_int),
                                          ctypes.c_void_p]
        lib.ngSpice_Init_Sync.restype = ctypes.c_int
        lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
        lib.ngSpice_Command.restype = ctypes.c_int
        lib.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
        lib.ngSpice_Circ.restype = ctypes.c_int
        lib.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
        lib.ngGet_Vec_Info.restype = ctypes.POINTER(VectorInfo)
        lib.ngSpice_CurPlot.restype = ctypes.c_char_p
        lib.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
        lib.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)
        lib.ngSpice_running.restype = NG_BOOL
        lib.ngSpice_SetBkpt.argtypes = [ctypes.c_double]
        lib.ngSpice_SetBkpt.restype = NG_BOOL
        #: what ngspice wrote (stdout and stderr lines)
        self.output: list[str] = []
        self.errors: list[str] = []
        self.version = 0
        #: called with (names, values) for every accepted point of a background run (ngspice's thread)
        self.on_data: Callable[[list[str], list[complex]], None] | None = None
        #: called with the names of the vectors when a run starts
        self.on_init: Callable[[list[str]], None] | None = None
        #: called with True/False when the background thread starts/ends
        self.on_running: Callable[[bool], None] | None = None
        #: ``external`` sources: (name, time) -> value
        self.on_source: Callable[[str, float], float] | None = None
        #: (time, delta, olddelta, redostep, location) -> a shorter delta or None
        self.on_sync: Callable[[float, float, float, int, int], float | None] | None = None
        #: the vectors :attr:`on_data` gets at every point (lower case; ``None``: all of them); all of
        #: them come every :attr:`all_every` seconds still (for showing every net). Reading each vector
        #: through ctypes costs about a microsecond: a hundred of them at every point slowed live runs
        #: down several times.
        self.every_point: set[str] | None = None
        self.all_every = 0.03
        self._names: list[str] = []
        self._data_count = -1
        self._data_names: list[str] = []
        self._data_wanted: list[int] = []
        self._next_all = 0.0
        self._has_circuit = False
        self._running = threading.Event()
        self._exited = False
        # (kept: ctypes frees a callback ngspice still calls otherwise)
        self._callbacks = (SendChar(self._send_char), SendStat(self._send_stat), ControlledExit(self._exit),
                           SendData(self._send_data), SendInitData(self._send_init), BGThreadRunning(self._bg))
        self._sync_callbacks = (GetVSRCData(self._vsrc), GetISRCData(self._isrc), GetSyncData(self._sync))
        if lib.ngSpice_Init(*self._callbacks, None) != 0:
            raise NgSpiceError("ngspice could not be started")
        self._ident = ctypes.c_int(0)
        self.synchronized = ""
        self._load_code_models()
        for line in self.output:
            match = re.search(r"ngspice-(\d+)", line)
            if match:
                self.version = int(match.group(1))
                break
        if not self.version:
            self.command("version -s")
            for line in self.output[-5:]:
                match = re.search(r"ngspice-(\d+)", line)
                if match:
                    self.version = int(match.group(1))

    def _load_code_models(self) -> None:
        loaded = set()
        for folder in code_model_folders(self.path):
            for path in sorted(glob.glob(os.path.join(folder, "*.cm"))):
                name = os.path.basename(path)
                if name in loaded or name == "table.cm":
                    continue
                loaded.add(name)
                self.command(f"codemodel {path}")
        self.code_models = sorted(loaded)

    # ------------------------------------------------------------ callbacks
    def _send_char(self, text, _ident, _user) -> int:
        line = text.decode("utf-8", "replace") if text else ""
        if line.startswith("stdout "):
            line = line[7:]
        elif line.startswith("stderr "):
            line = line[7:]
            if line.strip() and not line.startswith(("Note:", "Warning: can't find the initialization file")):
                self.errors.append(line)
                if len(self.errors) > 400:  # (a run of hours must not fill the memory with warnings)
                    del self.errors[:200]
        self.output.append(line)
        if len(self.output) > 2000:
            del self.output[:1000]
        return 0

    def _send_stat(self, _text, _ident, _user) -> int:
        return 0

    def _exit(self, status, unload, quit_, _ident, _user) -> int:
        self._exited = True
        log.warning("ngspice exited (%s)", status)
        return 0

    def _send_init(self, info, _ident, _user) -> int:
        try:
            data = info.contents
            self._names = [data.vecs[i].contents.vecname.decode("utf-8", "replace").lower() for i in range(data.veccount)]
            if self.on_init is not None:
                self.on_init(list(self._names))
        except Exception:
            log.exception("SendInitData")
        return 0

    def _send_data(self, values, count, _ident, _user) -> int:
        callback = self.on_data
        if callback is None:
            return 0
        try:
            data = values.contents
            count = data.veccount
            vectors = data.vecsa
            if count != self._data_count:  # (the names stay the same through a run: read once)
                self._data_names = [vectors[i].contents.name.decode("utf-8", "replace").lower() for i in range(count)]
                self._data_count = count
                wanted = self.every_point
                self._data_wanted = [i for i, name in enumerate(self._data_names)
                                     if wanted is None or name in wanted or name == "time"]
            indices = self._data_wanted
            if self.every_point is not None:
                now = time.monotonic()
                if now >= self._next_all:
                    self._next_all = now + self.all_every
                    indices = range(count)
            names = self._data_names
            picked = []
            numbers = []
            for i in indices:
                item = vectors[i].contents
                picked.append(names[i])
                numbers.append(complex(item.creal, item.cimag) if item.is_complex else item.creal)
            callback(picked, numbers)
        except Exception:
            log.exception("SendData")
        return 0

    def _bg(self, running, _ident, _user) -> int:
        # (ngspice says True when its thread does *not* run: "NG_BOOL true if background thread is running"
        # is inverted in every version since 26)
        is_running = not bool(running)
        if is_running:
            self._running.set()
        else:
            self._running.clear()
        if self.on_running is not None:
            try:
                self.on_running(is_running)
            except Exception:
                log.exception("BGThreadRunning")
        return 0

    def _vsrc(self, value, time_, name, _ident, _user) -> int:
        callback = self.on_source
        if callback is not None:
            try:
                value[0] = float(callback(name.decode("utf-8", "replace").lower(), time_))
            except Exception:
                log.exception("GetVSRCData")
        return 0

    def _isrc(self, value, time_, name, _ident, _user) -> int:
        return self._vsrc(value, time_, name, _ident, _user)

    def _sync(self, time_, delta, olddelta, redostep, _ident, location, _user) -> int:
        callback = self.on_sync
        if callback is None:
            return 0
        try:
            shorter = callback(time_, delta[0], olddelta, redostep, location)
            if shorter is not None and 0 < shorter < delta[0]:
                delta[0] = shorter
        except Exception:
            log.exception("GetSyncData")
        return 0

    def synchronize(self, steps: bool = False) -> None:
        """Ask for the values of ``external`` sources and - with ``steps`` - let :attr:`on_sync` shorten
        the next step (co-simulation); before a circuit with them is loaded."""
        wanted = "steps" if steps else "sources"
        if self.synchronized == "steps" and not steps:
            # ngspice keeps calling the step callback once it had one, also after it is set to none (a crash in
            # sharedsync): it stays registered - without on_sync it leaves every step as it is
            return
        if self.synchronized != wanted:
            vsrc, isrc, sync = self._sync_callbacks
            self.lib.ngSpice_Init_Sync(vsrc, isrc, sync if steps else ctypes.cast(None, GetSyncData),
                                       ctypes.byref(self._ident), None)
            self.synchronized = wanted

    # ------------------------------------------------------------- commands
    def command(self, text: str) -> int:
        return self.lib.ngSpice_Command(text.encode("utf-8"))

    def load(self, lines: list[str]) -> None:
        """Load a circuit (its netlist lines); raises :class:`NgSpiceError` with ngspice's errors."""
        self.errors = []
        self._data_count = -1
        if self.current_plot() not in ("", "const"):
            self.command("destroy all")
        if self._has_circuit:
            self.command("remcirc")
        array = (ctypes.c_char_p * (len(lines) + 1))()
        for index, line in enumerate(lines):
            array[index] = line.encode("utf-8")
        array[len(lines)] = None
        result = self.lib.ngSpice_Circ(array)
        self._has_circuit = True
        errors = [line for line in self.errors if _is_error(line)]
        if result != 0 or errors:
            raise NgSpiceError("\n".join(errors) or "ngspice could not read the circuit")

    def running(self) -> bool:
        return bool(self.lib.ngSpice_running())

    def wait(self, timeout: float = 10.0) -> bool:
        """Wait until the background thread ends; False when it still runs after ``timeout``."""
        end = time.monotonic() + timeout
        while self.running():
            if time.monotonic() > end:
                return False
            time.sleep(0.005)
        return True

    def halt(self, timeout: float = 5.0) -> None:
        """Stop the background run and wait for its thread; raises :class:`NgSpiceError` when it does
        not end (the circuit must not be unloaded under a running thread)."""
        for _attempt in range(3):
            if not self.running():
                return
            self.command("bg_halt")
            if self.wait(timeout):
                return
        raise NgSpiceError("ngspice does not stop its run")

    def set_every_point(self, names: set | None) -> None:
        """The vectors :attr:`on_data` gets at every point from now on (see :attr:`every_point`)."""
        self.every_point = None if names is None else {name.lower() for name in names}
        self._data_count = -1  # (the choice of vectors is made again at the next point)

    def current_plot(self) -> str:
        result = self.lib.ngSpice_CurPlot()
        return result.decode("utf-8", "replace") if result else ""

    def vector_names(self, plot: str = "") -> list[str]:
        plot = plot or self.current_plot()
        names = self.lib.ngSpice_AllVecs(plot.encode("utf-8"))
        found = []
        index = 0
        while names and names[index]:
            found.append(names[index].decode("utf-8", "replace"))
            index += 1
        return found

    def vector(self, name: str) -> np.ndarray | None:
        """The vector ``name`` of the current plot (complex when it is), ``None`` when there is none."""
        info = self.lib.ngGet_Vec_Info(name.encode("utf-8"))
        if not info:
            return None
        data = info.contents
        length = data.v_length
        if data.v_realdata:
            return np.ctypeslib.as_array(data.v_realdata, shape=(length,)).copy() if length else np.zeros(0)
        if data.v_compdata:
            raw = np.ctypeslib.as_array(ctypes.cast(data.v_compdata, ctypes.POINTER(ctypes.c_double)),
                                        shape=(2 * length,)).copy() if length else np.zeros(0)
            return raw[0::2] + 1j * raw[1::2]
        return None


def _is_error(line: str) -> bool:
    lowered = line.lower()
    return any(word in lowered for word in ("error", "unknown", "could not find", "not found", "fatal", "unable"))\
        and "warning" not in lowered


_instance: NgSpice | None = None
_instance_lock = threading.Lock()


def instance(path: str | None = None) -> NgSpice:
    """The simulator of this process (loaded once); raises :class:`NgSpiceError` without ngspice."""
    global _instance
    with _instance_lock:
        if _instance is None:
            if path is None:
                from ...core import vendor

                path = vendor.find("ngspice")
            if not path:
                raise NgSpiceError("ngspice is not installed")
            _instance = NgSpice(path)
        return _instance


def loaded() -> bool:
    return _instance is not None
