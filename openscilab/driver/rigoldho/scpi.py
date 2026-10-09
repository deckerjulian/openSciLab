# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""SCPI over TCP for the Rigol DHO900: lines, IEEE 488.2 definite-length blocks, the error queue.

The commands the driver sends are collected in :data:`COMMANDS`, so the findings on the
instrument (``tools/dho_la_probe.py``) change one table, not the driver. They
follow the programming guide of the DHO800/900 series and are provisional until checked there.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional

#: SCPI server of the instrument and the bridge app (docs/protocols.md, *Bridge*)
SCPI_PORT = 5555
BRIDGE_PORT = 5560
BEACON_PORT = 5561

#: The commands of the driver; ``{n}``, ``{value}`` … are filled in. Provisional until step 3a.
COMMANDS = {
    "identify": "*IDN?",
    "clear": "*CLS",
    "error": ":SYSTem:ERRor?",
    "run": ":RUN",
    "stop": ":STOP",
    "single": ":SINGle",
    "force": ":TFORce",
    "status": ":TRIGger:STATus?",
    "la_state": ":LA:STATe {value}",
    "la_channel": ":LA:DIGital{n}:DISPlay {value}",
    "la_threshold": ":LA:POD{n}:THReshold {value}",
    "channel": ":CHANnel{n}:DISPlay {value}",
    "memory_depth": ":ACQuire:MDEPth {value}",
    "memory_depth?": ":ACQuire:MDEPth?",
    "sample_rate?": ":ACQuire:SRATe?",
    "timebase_scale": ":TIMebase:MAIN:SCALe {value}",
    "timebase_offset": ":TIMebase:MAIN:OFFSet {value}",
    "trigger_sweep": ":TRIGger:SWEep {value}",
    "trigger_mode": ":TRIGger:MODE {value}",
    "edge_source": ":TRIGger:EDGE:SOURce {value}",
    "edge_slope": ":TRIGger:EDGE:SLOPe {value}",
    "edge_level": ":TRIGger:EDGE:LEVel {value}",
    "pattern": ":TRIGger:PATTern:PATTern {value}",
    "wave_source": ":WAVeform:SOURce {value}",
    "wave_mode": ":WAVeform:MODE RAW",
    # BYTE: the top 8 of the 12 bits of the ADC. WORD would keep them all, but the driver, the
    # bridge app's codec and its overviews work with 8 bit raw values (docs/protocols.md, *Bridge*)
    "wave_format": ":WAVeform:FORMat BYTE",
    "wave_start": ":WAVeform:STARt {value}",
    "wave_stop": ":WAVeform:STOP {value}",
    "wave_data?": ":WAVeform:DATA?",
    "wave_preamble?": ":WAVeform:PREamble?",
    # the built-in generator (GI)
    "afg_function": ":SOURce:FUNCtion {value}",
    "afg_frequency": ":SOURce:FREQuency {value}",
    "afg_amplitude": ":SOURce:VOLTage:AMPLitude {value}",
    "afg_offset": ":SOURce:VOLTage:OFFSet {value}",
    "afg_duty": ":SOURce:PULSe:DCYCle {value}",
    "afg_square_duty": ":SOURce:FUNCtion:SQUare:DUTY {value}",
    "afg_phase": ":SOURce:PHASe {value}",
    "afg_output": ":SOURce:OUTPut:STATe {value}",
    "afg_output?": ":SOURce:OUTPut:STATe?",
    "afg_arb": ":SOURce:TRACe:DATA VOLATILE,{value}",
    "afg_sweep": ":SOURce:SWEep:STATe {value}",
    "afg_sweep_start": ":SOURce:SWEep:STARt {value}",
    "afg_sweep_stop": ":SOURce:SWEep:STOP {value}",
    "afg_sweep_time": ":SOURce:SWEep:TIME {value}",
    "afg_burst": ":SOURce:BURSt:STATe {value}",
    "afg_burst_cycles": ":SOURce:BURSt:CYCLes {value}",
    "afg_burst_period": ":SOURce:BURSt:PERiod {value}",
}

#: Memory depths the instrument offers (points); the driver takes the smallest that holds a capture
MEMORY_DEPTHS = (1_000, 10_000, 100_000, 1_000_000, 10_000_000, 25_000_000, 50_000_000)
#: The query that brings a connection back in step after a timeout (its answer is recognised)
SYNC_QUERY = "*IDN?"
#: Points of one ``:WAVeform:DATA?`` read in RAW mode
MAX_POINTS_PER_READ = 250_000
#: Divisions of the time base
DIVISIONS = 10


def command(name: str, **values) -> str:
    return COMMANDS[name].format(**values)


def depth_text(points: int) -> str:
    """A memory depth as ``:ACQuire:MDEPth`` takes it: ``1k``, ``10k``, ``100k``, ``1M``, ``10M`` …"""
    if points >= 1_000_000 and points % 1_000_000 == 0:
        return f"{points // 1_000_000}M"
    if points >= 1_000 and points % 1_000 == 0:
        return f"{points // 1_000}k"
    return str(points)


def parse_depth(text: str) -> Optional[int]:
    """The answer of ``:ACQuire:MDEPth?`` in points (``1M``, ``1.0000E+06``, ``1000000``);
    ``None`` for ``AUTO`` or anything else that is no depth."""
    value = text.strip().upper()
    factor = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(value[-1:], 1)
    if factor != 1:
        value = value[:-1]
    try:
        return round(float(value) * factor)
    except (ValueError, OverflowError):
        return None


class ScpiError(IOError):
    """The instrument did not answer, or answered something unreadable."""


class ScpiTimeout(ScpiError):
    """No answer in time. It may still come: the connection resynchronises before its next query."""


class TcpTransport:
    """The bytes of a connection over the network (the instrument's port 5555, the bridge's 5560)."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.name = host
        try:
            self._socket = socket.create_connection((host, port), timeout=timeout)
        except OSError as error:
            raise ScpiError(f"{host}:{port} cannot be reached: {error}") from error
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, data: bytes, timeout: float) -> None:
        try:
            self._socket.sendall(data)
        except OSError as error:
            raise ScpiError(f"the connection to {self.name} failed: {error}") from error

    def receive(self, timeout: float) -> bytes:
        self._socket.settimeout(timeout)
        try:
            data = self._socket.recv(1 << 20)
        except socket.timeout as error:
            raise ScpiTimeout(f"{self.name} did not answer in time") from error
        except OSError as error:
            raise ScpiError(f"the connection to {self.name} failed: {error}") from error
        if not data:
            raise ScpiError(f"{self.name} closed the connection")
        return data

    def close(self) -> None:
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()


class UsbTransport:
    """The bytes of a connection over USB (USBTMC, ``driver/usbtmc.py``): every command a message,
    every answer asked for; a timeout empties the instrument's buffers."""

    def __init__(self, link) -> None:
        self.link = link
        self.name = link.name

    def send(self, data: bytes, timeout: float) -> None:
        from .. import usbtmc

        try:
            self.link.write(data, timeout)
        except usbtmc.UsbTmcError as error:
            raise ScpiError(f"the connection to {self.name} failed: {error}") from error

    def receive(self, timeout: float) -> bytes:
        from .. import usbtmc

        try:
            data = self.link.read(timeout)
        except usbtmc.UsbTmcTimeout as error:
            raise ScpiTimeout(f"{self.name} did not answer in time") from error
        except usbtmc.UsbTmcError as error:
            raise ScpiError(f"the connection to {self.name} failed: {error}") from error
        if not data:
            raise ScpiTimeout(f"{self.name} sent an empty answer")
        return data

    def close(self) -> None:
        self.link.close()


class ScpiConnection:
    """One connection - over the network or USB (``transport``) -: commands, queries, block queries."""

    def __init__(self, host: str = "", port: int = 0, timeout: float = 5.0, transport=None) -> None:
        self._transport = transport if transport is not None else TcpTransport(host, port, timeout)
        self.host, self.port = (host or self._transport.name), port
        self.timeout = timeout
        self._buffer = bytearray()
        #: a data block was read: a line feed may follow it (the instrument sends one)
        self._after_block = False
        #: a query timed out: its answer may still come and would be taken for the next one's
        self._stale = False
        #: answers to :data:`SYNC_QUERY` still to come while resynchronising
        self._markers = 0
        #: the answer to ``*IDN?``: it marks where the connection is in step again
        self.identity: Optional[str] = None
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- bytes
    def _receive(self, timeout: Optional[float]) -> None:
        self._buffer += self._transport.receive(self.timeout if timeout is None else timeout)

    def _line(self, timeout: Optional[float]) -> str:
        while b"\n" not in self._buffer:
            self._receive(timeout)
        if self._after_block and self._buffer[:1] == b"\n":
            del self._buffer[:1]  # the line feed that ended the data block before
            self._after_block = False
            return self._line(timeout)
        self._after_block = False
        return self._raw_line(timeout)

    def _raw_line(self, timeout: Optional[float]) -> str:
        while b"\n" not in self._buffer:
            self._receive(timeout)
        index = self._buffer.index(b"\n")
        line = bytes(self._buffer[:index]).decode("ascii", "replace").strip()
        del self._buffer[:index + 1]
        return line

    def _exactly(self, count: int, timeout: Optional[float]) -> bytes:
        while len(self._buffer) < count:
            self._receive(timeout)
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    # ------------------------------------------------------------- commands
    def write(self, text: str) -> None:
        with self.lock:
            self._transport.send(text.encode("ascii") + b"\n", self.timeout)

    def query(self, text: str, timeout: Optional[float] = None) -> str:
        with self.lock:
            self._resynchronize(timeout)
            self.write(text)
            try:
                line = self._line(timeout)
            except ScpiTimeout:
                self._stale = True
                raise
            if self.identity is None and text.strip().upper() == SYNC_QUERY:
                self.identity = line
            return line

    def query_block(self, text: str, timeout: Optional[float] = None) -> bytes:
        """The data of an IEEE 488.2 definite-length block (``#<n><length><data>``)."""
        with self.lock:
            self._resynchronize(timeout)
            self.write(text)
            try:
                return self._block(timeout)
            except ScpiTimeout:
                self._stale = True  # (also in the middle of the block: the rest is dropped)
                raise

    def _block(self, timeout: Optional[float]) -> bytes:
        self._after_block = False
        while True:
            head = self._exactly(1, timeout)
            if head == b"#":
                break
            if head not in b" \r\n":
                rest = self._line(timeout)
                raise ScpiError(f"no data block but {(head + rest.encode()).decode('ascii', 'replace')!r}")
        digits = int(self._exactly(1, timeout))
        if digits == 0:
            raise ScpiError("blocks of unknown length are not supported")
        length = int(self._exactly(digits, timeout))
        data = self._exactly(length, timeout)
        if self._buffer[:1] == b"\n":
            del self._buffer[:1]
        else:
            self._after_block = True  # it may still come: the next answer skips it
        return data

    def _resynchronize(self, timeout: Optional[float]) -> None:
        """After a timeout the late answer (a line or a block) would be taken for the answer of
        the next query, and every answer after it would be one behind. So :data:`SYNC_QUERY` is
        sent and everything up to its answer dropped: the instrument answers in order, so the late
        answer comes before it (or never, to a query it does not know). A timeout here leaves the
        connection out of step; the next query goes on waiting for the same answer."""
        if not self._stale:
            return
        if not self._markers:
            self.write(SYNC_QUERY)
            self._markers = 1
        while self._markers:
            line = self._raw_line(timeout)
            # (at the end of the line: a data block without its line feed may stand before it;
            # before the first *IDN? any identity: maker, model, serial number, firmware)
            if line.endswith(self.identity) if self.identity else _looks_like_identity(line):
                self._markers -= 1
        self._after_block = False
        self._stale = False

    def errors(self) -> list[str]:
        """Empties the error queue of the instrument (``0,"No error"`` ends it)."""
        found = []
        for _ in range(32):
            answer = self.query(command("error"))
            if answer.startswith(("0,", "+0,")):
                break
            found.append(answer)
        return found

    def close(self) -> None:
        self._transport.close()


def _looks_like_identity(line: str) -> bool:
    parts = line.split(",")
    return len(parts) == 4 and any(character.isalpha() for character in parts[0])


def parse_block(data: bytes) -> bytes:
    """The data of an IEEE block given as bytes (for the bridge answers kept as a whole)."""
    if not data.startswith(b"#"):
        raise ScpiError("no data block")
    digits = int(data[1:2])
    length = int(data[2:2 + digits])
    return data[2 + digits:2 + digits + length]


def block(data: bytes) -> bytes:
    """``data`` as an IEEE 488.2 definite-length block."""
    length = str(len(data)).encode()
    return b"#" + str(len(length)).encode() + length + data


def parse_preamble(text: str) -> dict[str, float]:
    """``:WAVeform:PREamble?``: format, type, points, count, xinc, xorig, xref, yinc, yorig, yref."""
    keys = ("format", "type", "points", "count", "xinc", "xorig", "xref", "yinc", "yorig", "yref")
    values = [float(item) for item in text.strip().split(",")]
    return dict(zip(keys, values))


def parse_meta(text: str) -> dict[str, str]:
    result = {}
    for item in text.split(";"):
        key, sep, value = item.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    return result
