# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""An AVR microcontroller emulated by simavr (``libopenscilab_avr``, built from ``circuits/mcu/shim``).

:class:`AvrMcu` runs a firmware cycle by cycle; what it does to its pins and UARTs comes back as
events with their time, what the circuit does to its inputs goes in with :meth:`AvrMcu.set_input`
and :meth:`AvrMcu.set_analog`.
"""

from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass

from .. import CircuitError
from .firmware import read_firmware

EVENT_PIN, EVENT_DIRECTION, EVENT_UART, EVENT_PORT = 0, 1, 2, 3
#: the version of the shim this module speaks (``OSL_AVR_VERSION`` in osl_avr.h)
SHIM_VERSION = 2


class OslEvent(ctypes.Structure):
    _fields_ = [("cycle", ctypes.c_uint64), ("kind", ctypes.c_uint8), ("port", ctypes.c_uint8),
                ("bit", ctypes.c_uint8), ("value", ctypes.c_uint8)]


@dataclass(frozen=True)
class Event:
    #: seconds since the start of the microcontroller
    time: float
    kind: int
    port: str
    bit: int
    value: int


_library: ctypes.CDLL | None = None
_lock = threading.Lock()


def find_library() -> str | None:
    from ...core import vendor

    return vendor.find("simavr")


def library() -> ctypes.CDLL:
    """The emulator's library (raises CircuitError without it)."""
    global _library
    with _lock:
        if _library is None:
            path = find_library()
            if not path:
                raise CircuitError("the AVR emulator (libopenscilab_avr) is not installed")
            lib = ctypes.CDLL(path)
            lib.osl_avr_version.restype = ctypes.c_int
            version = int(lib.osl_avr_version())
            if version != SHIM_VERSION:
                raise CircuitError(f"the AVR emulator at {path} is of version {version}, this openSciLab needs "
                                   f"{SHIM_VERSION}: install the tools again (Settings → Circuits)")
            lib.osl_avr_new.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32]
            lib.osl_avr_new.restype = ctypes.c_void_p
            lib.osl_avr_free.argtypes = [ctypes.c_void_p]
            lib.osl_avr_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32]
            lib.osl_avr_load.restype = ctypes.c_int
            lib.osl_avr_reset.argtypes = [ctypes.c_void_p]
            lib.osl_avr_cycle.argtypes = [ctypes.c_void_p]
            lib.osl_avr_cycle.restype = ctypes.c_uint64
            lib.osl_avr_frequency.argtypes = [ctypes.c_void_p]
            lib.osl_avr_frequency.restype = ctypes.c_uint32
            lib.osl_avr_run_until.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
            lib.osl_avr_run_until.restype = ctypes.c_int
            lib.osl_avr_poll.argtypes = [ctypes.c_void_p, ctypes.POINTER(OslEvent), ctypes.c_int]
            lib.osl_avr_poll.restype = ctypes.c_int
            lib.osl_avr_set_pin.argtypes = [ctypes.c_void_p, ctypes.c_char, ctypes.c_int, ctypes.c_int]
            lib.osl_avr_set_adc.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
            lib.osl_avr_uart_write.argtypes = [ctypes.c_void_p, ctypes.c_char, ctypes.c_char_p, ctypes.c_int]
            lib.osl_avr_port.argtypes = [ctypes.c_void_p, ctypes.c_char, ctypes.POINTER(ctypes.c_uint8),
                                         ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint8)]
            lib.osl_avr_port.restype = ctypes.c_int
            _library = lib
        return _library


def available() -> bool:
    try:
        library()
        return True
    except (CircuitError, OSError):
        return False


#: simavr's states
STATES = {0: "limbo", 1: "stopped", 2: "running", 3: "sleeping", 4: "step", 5: "step done", 6: "done", 7: "crashed"}


class AvrMcu:
    """One emulated AVR (see the module)."""

    def __init__(self, core: str, frequency: float = 16e6, vcc: float = 5.0) -> None:
        self.lib = library()
        self.handle = self.lib.osl_avr_new(core.encode("ascii"), int(frequency), int(round(vcc * 1000)))
        if not self.handle:
            raise CircuitError(f"the AVR emulator does not know the microcontroller {core!r}")
        self.core = core
        self.frequency = float(self.lib.osl_avr_frequency(self.handle))
        self.vcc = vcc
        self.state = "stopped"
        self._buffer = (OslEvent * 4096)()
        self.firmware = ""

    def load_firmware(self, path: str) -> None:
        image, start = read_firmware(path)
        if not image:
            raise CircuitError("the firmware is empty")
        if self.lib.osl_avr_load(self.handle, image, len(image), start) != 0:
            raise CircuitError("the firmware does not fit into the flash of the microcontroller")
        self.firmware = path
        self.reset()

    def reset(self) -> None:
        self.lib.osl_avr_reset(self.handle)

    @property
    def cycle(self) -> int:
        return int(self.lib.osl_avr_cycle(self.handle))

    @property
    def time(self) -> float:
        return self.cycle / self.frequency

    def run_until(self, seconds: float) -> str:
        """Run until ``seconds`` since the start (nothing when it is there already)."""
        target = int(seconds * self.frequency)
        if target > self.cycle:
            self.state = STATES.get(self.lib.osl_avr_run_until(self.handle, target), "?")
        return self.state

    def events(self) -> list[Event]:
        found: list[Event] = []
        while True:
            count = self.lib.osl_avr_poll(self.handle, self._buffer, len(self._buffer))
            for item in self._buffer[:count]:
                found.append(Event(item.cycle / self.frequency, item.kind, chr(item.port), item.bit, item.value))
            if count < len(self._buffer):
                return found

    def set_input(self, port: str, bit: int, level: bool) -> None:
        self.lib.osl_avr_set_pin(self.handle, port.encode("ascii"), bit, 1 if level else 0)

    def set_analog(self, channel: int, volts: float) -> None:
        self.lib.osl_avr_set_adc(self.handle, channel, max(0, int(round(volts * 1000))))

    def uart_write(self, data: bytes, uart: str = "0") -> None:
        if data:
            self.lib.osl_avr_uart_write(self.handle, uart.encode("ascii"), data, len(data))

    def port(self, name: str) -> tuple[int, int, int] | None:
        """(PORT, DDR, PIN) of a port."""
        out, ddr, pin = ctypes.c_uint8(), ctypes.c_uint8(), ctypes.c_uint8()
        if self.lib.osl_avr_port(self.handle, name.encode("ascii"), ctypes.byref(out), ctypes.byref(ddr),
                                 ctypes.byref(pin)) != 0:
            return None
        return out.value, ddr.value, pin.value

    def close(self) -> None:
        if self.handle:
            self.lib.osl_avr_free(self.handle)
            self.handle = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110 - at garbage collection nothing can be said
            pass
