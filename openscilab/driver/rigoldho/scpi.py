# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""SCPI over TCP for the Rigol DHO900: lines, IEEE 488.2 definite-length blocks, the error queue.

The commands the driver sends are collected in :data:`COMMANDS`, so the findings on the
instrument (``tools/dho_la_probe.py``) change one table, not the driver. They are checked against
the programming guide of the DHO800/DHO900, the command table of a DHO924S with the modified
firmware NK v0.5.2 and on that instrument.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional

#: SCPI server of the instrument and the bridge app (docs/protocols.md, *Bridge*)
SCPI_PORT = 5555
BRIDGE_PORT = 5560
BEACON_PORT = 5561

#: The commands of the driver; ``{n}``, ``{value}`` … are filled in.
COMMANDS = {
    "identify": "*IDN?",
    "clear": "*CLS",
    "error": ":SYSTem:ERRor?",
    "run": ":RUN",
    "stop": ":STOP",
    "single": ":SINGle",
    "force": ":TFORce",
    "status": ":TRIGger:STATus?",
    "lan_address?": ":LAN:IPADdress?",
    "screen?": ":DISPlay:DATA?",
    "la_state": ":LA:ENABle {value}",
    "la_channel": ":LA:DIGital:ENABle D{n},{value}",
    "la_threshold": ":LA:POD{n}:THReshold {value}",
    "channel": ":CHANnel{n}:DISPlay {value}",
    "la_state?": ":LA:ENABle?",
    "la_channel?": ":LA:DIGital:ENABle? D{n}",
    "channel?": ":CHANnel{n}:DISPlay?",
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
    "edge_level?": ":TRIGger:EDGE:LEVel?",
    "wave_source": ":WAVeform:SOURce {value}",
    "wave_source?": ":WAVeform:SOURce?",
    "wave_mode": ":WAVeform:MODE RAW",
    "wave_mode_normal": ":WAVeform:MODE NORMal",
    # BYTE: the top 8 of the 12 bits of the ADC. WORD would keep them all, but the driver, the
    # bridge app's codec and its overviews work with 8 bit raw values (docs/protocols.md, *Bridge*)
    "wave_format": ":WAVeform:FORMat BYTE",
    # a logic channel comes as one 16 bit word per sample (0 or 1): BYTE would give its upper byte,
    # always 0 (the logic channels can be read at all only with a patch of the library of the
    # modified firmware NK v0.5.2: without it :WAVeform:SOURce D0 is refused)
    "wave_format_word": ":WAVeform:FORMat WORD",
    "wave_start": ":WAVeform:STARt {value}",
    "wave_stop": ":WAVeform:STOP {value}",
    "wave_data?": ":WAVeform:DATA?",
    "wave_preamble?": ":WAVeform:PREamble?",
    # the built-in generator (GI); its command table has no sweep, burst, pulse or arbitrary data
    "afg_function": ":SOURce:FUNCtion {value}",
    "afg_frequency": ":SOURce:FREQuency {value}",
    "afg_amplitude": ":SOURce:VOLTage:AMPLitude {value}",
    "afg_offset": ":SOURce:VOLTage:OFFSet {value}",
    "afg_square_duty": ":SOURce:FUNCtion:SQUare:DUTY {value}",
    "afg_ramp_symmetry": ":SOURce:FUNCtion:RAMP:SYMMetry {value}",
    "afg_phase": ":SOURce:PHASe {value}",
    "afg_modulation": ":SOURce:MOD:STATe {value}",
    "afg_output": ":SOURce:OUTPut:STATe {value}",
    "afg_output?": ":SOURce:OUTPut:STATe?",
}

#: Memory depths the driver asks for, from small to large: those of the programming guide (up to
#: 50M), then larger names for the modified firmware NK v0.5.2 (``*IDN?``: NK TECHNOLOGIES,
#: 00.05.02), which takes a tenth of a depth up to 10M and a twentieth above (``50M`` gives
#: 2.5 Mpts, ``500M`` the 25 Mpts of one channel). The driver reads back what it got.
MEMORY_DEPTHS = (1_000, 10_000, 100_000, 1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000,
                 100_000_000, 125_000_000, 250_000_000, 500_000_000)
#: The query that brings a connection back in step after a timeout (its answer is recognised)
SYNC_QUERY = "*IDN?"
#: Points of one ``:WAVeform:DATA?`` read in RAW mode
MAX_POINTS_PER_READ = 250_000
#: Divisions of the time base
DIVISIONS = 10
#: How often the time base is shortened until the sample rate is not below the one asked for, and by
#: how much more than the ratio of the two rates (the rates are steps: a little more gets to the next)
RATE_TRIES = 8
RATE_SHRINK = 0.9
#: ``:ACQuire:SRATe?`` after a new time base: asked every RATE_SETTLE_S until it gave the same rate
#: RATE_STABLE_READS times in a row, for at most RATE_SETTLE_TIMEOUT_S (measured: the instrument answers
#: with the old rate for a while, after a large change well over 0.25 s)
RATE_SETTLE_S = 0.15
RATE_STABLE_READS = 3
RATE_SETTLE_TIMEOUT_S = 2.0
#: From this time base (seconds per division) the DHO rolls: its trigger status stays STOP after
#: ``:SINGle`` (measured on a DHO924S: 20 ms/div takes a capture, 50 ms/div does not).
ROLL_SCALE = 0.05
#: The logic channels' real sample period is this many times the ``xincrement`` their own
#: ``:WAVeform:PREamble?`` reports (measured on a DHO924S, alone and with CH1, at 10 rates from
#: 125 kSa/s to 100 MSa/s of the analog side, with a signal of known frequency: real period 2.00
#: times the reported one): the instrument computes their preamble as if they were sampled at the
#: analog rate. For the same reason only the first ``points / LA_PERIOD_FACTOR`` words it gives out
#: are the capture's; the rest is old memory (stretches of one level that are not in the signal,
#: which read as holes: a 1 kHz wave showed 553 of its 800 edges and runs of 9299 samples). The
#: driver reads only those. Captured without the analog channels, it corrects ``xincrement`` by the
#: factor (this also halves the trigger sample the same preamble gives, measured correct too) and
#: sets the instrument up for twice the rate and samples, so that the capture has the rate and
#: samples asked for. Captured with them, their preamble already matches the analog one's (same
#: ``xincrement``, same trigger sample: measured through a known edge), and the driver instead
#: repeats every logic sample this many times, which lines it up with the analog channels' finer
#: time axis without changing (or needing to understand) the preamble.
LA_PERIOD_FACTOR = 2
#: Read out together with an analog channel, the logic channels come this many analog samples late
#: (positive) or early, by the rate of the analog channels (Sa/s). Measured on a DHO924S (NK v0.5.2):
#: CH1 and D0 on one square wave, three captures per rate at signal frequencies of rate/37.3, /41.7
#: and /53.1, 200 edges each, the typical value rounded. The instrument's own screen shows them in
#: line: it aligns them in the FPGA's display path, which the export of the memory does not go
#: through (also not in Rigol's 00.01.05.00.02). From capture to capture it varies by one sample
#: (some rates two), which no correction removes. Rates not measured are not corrected.
LA_SKEW = {1_250_000: 3, 3_125_000: 4, 6_250_000: 3, 15_625_000: 1, 31_250_000: -2, 62_500_000: 0,
           156_250_000: 0, 312_500_000: -1, 625_000_000: -1}


def la_skew(rate: float) -> int:
    """Analog samples the logic channels come late at this analog rate (:data:`LA_SKEW`; 0 if not measured)."""
    for known, skew in LA_SKEW.items():
        if abs(rate - known) <= 0.01 * known:
            return skew
    return 0


def shift_samples(samples, skew: int):
    """``samples`` moved ``skew`` samples earlier (later if negative), the ends held at their level."""
    import numpy as np

    if skew == 0 or len(samples) == 0:
        return samples
    skew = max(-len(samples), min(len(samples), skew))
    if skew > 0:
        return np.concatenate([samples[skew:], np.repeat(samples[-1:], skew)])
    return np.concatenate([np.repeat(samples[:1], -skew), samples[:skew]])


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
        try:
            self._socket.settimeout(timeout)  # (also fails on a connection closed meanwhile)
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
    every answer asked for; after a timeout the request is taken back."""

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
