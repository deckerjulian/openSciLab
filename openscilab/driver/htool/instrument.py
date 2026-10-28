# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The HTOOL HT008 as an instrument: a generator output ``CH1`` (sine, 23.5 MHz to 6 GHz, four
power levels) and its settings, over its serial port (``protocol.py``).

Opening it sends the frame of its power on state (3 GHz, −4 dBm): an HT008 that was just switched
on takes it without an answer and listens to frames from then on, one that already does answers it.
An HT008 that got anything else first (SCPI) answers no frame until it is switched off and on.

What the HT008 cannot do: switch its output off (a stopped waveform leaves the carrier on), set
CH2 (its attenuated output, set on the device only), be read back (the settings show what was sent),
and be switched off with its switch while it listens to frames (unplug it). Qt free.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from . import protocol
from ...core import units
from ...core import waveform as waves
from ...core.instrument import GeneratorFacet, Instrument, InstrumentError, OutputInfo, SettingsFacet
from ...driver.base import DeviceConnectionError

log = logging.getLogger("openscilab.driver")

KIND = "ht008"
TITLE = "HTOOL HT008"
OUTPUT = "CH1"
#: USB identifiers of its serial chip (a CH340, as on many Arduino clones)
USB_IDS = ((0x1A86, 0x7523),)
#: seconds the HT008 may take to answer a frame (measured: up to 0.3 s)
ANSWER_TIMEOUT_S = 1.0
#: dBm a waveform's amplitude may be off the nearest power level
LEVEL_TOLERANCE_DB = 1.5
INFO = OutputInfo(OUTPUT, "analog",
                  voltage_range=(-protocol.dbm_to_volts(max(protocol.LEVELS_DBM)),
                                 protocol.dbm_to_volts(max(protocol.LEVELS_DBM))),
                  max_rate=0, max_points=0, max_frequency=protocol.MAX_FREQUENCY)


class Ht008Link:
    """The serial port of an HT008 and what was last sent to it."""

    def __init__(self, port: str, opener: Optional[Callable[..., Any]] = None) -> None:
        self.port = port
        self.lock = threading.Lock()
        self.frequency = protocol.POWER_ON_FREQUENCY
        self.level = protocol.POWER_ON_LEVEL
        if opener is None:
            import serial

            opener = serial.Serial
        try:
            self._serial = opener(port, protocol.BAUD, timeout=0.05, write_timeout=2.0, exclusive=True)
        except (OSError, ValueError) as error:  # (SerialException is an OSError)
            raise DeviceConnectionError(f"{port} cannot be opened: {error}") from None

    def send(self, frequency: float, level: int, timeout: float = ANSWER_TIMEOUT_S) -> bool:
        """Send a frame; whether the HT008 acknowledged it."""
        data = protocol.frame(frequency, level)
        with self.lock:
            self._serial.reset_input_buffer()
            self._serial.write(data)
            answer = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not answer.endswith(protocol.ACKNOWLEDGE):
                answer += self._serial.read(max(1, self._serial.in_waiting))
        if not answer.endswith(protocol.ACKNOWLEDGE):
            return False
        self.frequency, self.level = protocol.round_frequency(frequency), level
        return True

    def ask(self, line: str, timeout: float = 0.5) -> bytes:
        """An SCPI query and its answer line (only for an HT008 that listens to SCPI)."""
        with self.lock:
            self._serial.reset_input_buffer()
            self._serial.write(line.encode("ascii") + b"\n")
            answer = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not answer.endswith(b"\n"):
                answer += self._serial.read(max(1, self._serial.in_waiting))
        return answer.strip()

    def apply(self, frequency: float, level: int) -> None:
        """Set the output; raises :class:`InstrumentError` when the HT008 does not take it."""
        try:
            taken = self.send(frequency, level)
        except ValueError as error:
            raise InstrumentError(str(error)) from None
        except OSError as error:
            raise InstrumentError(f"the HT008 on {self.port} does not answer ({error})") from error
        if not taken:
            raise InstrumentError(f"the HT008 on {self.port} did not acknowledge "
                                  f"{units.format_quantity(frequency, 'Hz', 10)}; was it switched off?")

    def close(self) -> None:
        try:
            self._serial.close()
        except OSError:
            log.debug("Closing the port of the HT008 failed", exc_info=True)


class Ht008Generator(GeneratorFacet):
    """Output CH1: a sine of 23.5 MHz to 6 GHz, its amplitude one of the power levels into 50 Ω."""

    def __init__(self, link: Ht008Link) -> None:
        super().__init__()
        self.link = link
        self._running = False

    def outputs(self) -> list[OutputInfo]:
        return [INFO]

    def frame_for(self, waveform: Any) -> tuple[float, int]:
        """Frequency and level byte of ``waveform``; raises :class:`InstrumentError` when it does
        not fit."""
        found = waves.problems(waveform, INFO)
        if waveform.kind != waves.SINE:
            found.append(f"the HT008 makes sine waves only, not {waveform.kind}")
        if waveform.frequency < protocol.MIN_FREQUENCY:
            found.append(f"{units.format_quantity(waveform.frequency, 'Hz')} is below the "
                         f"{units.format_quantity(protocol.MIN_FREQUENCY, 'Hz')} of the HT008")
        if waveform.offset:
            found.append("the output of the HT008 has no offset")
        if waveform.repeat > 0:
            found.append("the HT008 sends until stopped, not a number of periods")
        dbm = protocol.volts_to_dbm(abs(waveform.amplitude))
        level = protocol.nearest_level(dbm)
        if abs(protocol.LEVELS_DBM[level] - dbm) > LEVEL_TOLERANCE_DB:
            levels = ", ".join(f"{value:+g} dBm ({units.format_quantity(protocol.dbm_to_volts(value), 'V', 2)})"
                               for value in protocol.LEVELS_DBM)
            found.append(f"{units.format_quantity(abs(waveform.amplitude), 'V')} peak is {dbm:+.1f} dBm into "
                         f"{protocol.LOAD_OHMS:g} Ω; the HT008 has {levels}")
        if found:
            raise InstrumentError("; ".join(dict.fromkeys(found)))
        return waveform.frequency, level

    def start(self, output: str, waveform: Any) -> None:
        self.output(output)
        self.link.apply(*self.frame_for(waveform))
        self._running = True

    def stop(self, output: str) -> None:
        # the HT008 has no command that switches its output off: the carrier stays
        self.output(output)
        self._running = False

    def running(self, output: str) -> bool:
        return self._running

    def close(self) -> None:
        self.link.close()


def _power_text(dbm: float) -> str:
    return f"{dbm:+g} dBm".replace("-", "−")


class Ht008Settings(SettingsFacet):
    """Frequency and power of CH1, as last sent (the HT008 cannot be read)."""

    def __init__(self, link: Ht008Link) -> None:
        super().__init__()
        self.link = link

    def settings(self) -> list[dict]:
        group = "Output CH1"
        return [
            dict(name="frequency", label="Frequency", group=group, kind="number", unit="Hz",
                 minimum=protocol.MIN_FREQUENCY, maximum=protocol.MAX_FREQUENCY, digits=10),
            dict(name="power", label="Power (50 Ω)", group=group, kind="choice",
                 choices=[[f"{dbm:g}", _power_text(dbm)] for dbm in protocol.LEVELS_DBM]),
            dict(name="output", label="Output", group=group, kind="text"),
        ]

    def values(self) -> dict[str, Any]:
        return {"frequency": float(self.link.frequency), "power": f"{protocol.LEVELS_DBM[self.link.level]:g}",
                "output": "on (the HT008 cannot switch it off)"}

    def set(self, name: str, value: Any) -> Any:
        if name == "frequency":
            frequency = float(value)
            if not protocol.MIN_FREQUENCY <= frequency <= protocol.MAX_FREQUENCY:
                raise InstrumentError(f"Frequency: the HT008 makes {units.format_quantity(protocol.MIN_FREQUENCY, 'Hz')} "
                                      f"to {units.format_quantity(protocol.MAX_FREQUENCY, 'Hz')}")
            self.link.apply(frequency, self.link.level)
            return float(self.link.frequency)
        if name == "power":
            choices = [f"{dbm:g}" for dbm in protocol.LEVELS_DBM]
            if str(value) not in choices:
                raise InstrumentError(f"Power: {value!r} is not one of {', '.join(choices)} (dBm)")
            self.link.apply(self.link.frequency, choices.index(str(value)))
            return str(value)
        raise InstrumentError(f"the HT008 has no setting {name!r}")

    def action(self, name: str) -> None:
        raise InstrumentError(f"the HT008 has no action {name!r}")

    def close(self) -> None:
        self.link.close()


def open_ht008(port: str, opener: Optional[Callable[..., Any]] = None) -> Instrument:
    """The HT008 on serial port ``port`` as an instrument (see the module)."""
    port = port.strip()
    if not port:
        raise DeviceConnectionError("Give the serial port of the HT008: ht008:<port>.")
    link = Ht008Link(port, opener)
    try:
        # just switched on: the first frame is taken without an answer, the second answered
        listening = any(link.send(protocol.POWER_ON_FREQUENCY, protocol.POWER_ON_LEVEL) for _ in range(2))
        identity = b"" if listening else link.ask("*IDN?")
    except OSError as error:
        link.close()
        raise DeviceConnectionError(f"{port}: {error}") from None
    if not listening:
        link.close()
        if identity.startswith(b"HTOOL,"):
            raise DeviceConnectionError(
                f"The HT008 on {port} ({identity.decode('ascii', 'replace')}) was first spoken to with SCPI "
                "since it was switched on, and takes no frequency from a computer then. Switch it off and on, "
                "then connect again.")
        raise DeviceConnectionError(f"No HTOOL HT008 answers on {port}.")
    instrument = Instrument(TITLE, kind=TITLE, uri=f"{KIND}:{port}")
    instrument.add_facet(Ht008Generator(link))
    instrument.add_facet(Ht008Settings(link))
    return instrument


def detect() -> list[tuple[str, str]]:
    """Serial ports with the USB identifiers of the HT008's CH340, one per device (macOS names it
    twice); nothing is opened: the chip is also on Arduino clones."""
    from .. import ports

    found: dict[str, str] = {}
    for port in sorted(ports.comports(), key=lambda item: item.device):
        if (port.vid, port.pid) in USB_IDS:
            found.setdefault(getattr(port, "location", None) or port.device, port.device)
    return [(device, f"{TITLE} on {device} (CH340)") for device in sorted(found.values())]
