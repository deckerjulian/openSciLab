# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A simulated Rigol DHO900 on a local TCP port: its SCPI server and, optionally, the bridge app.

:class:`ScpiShell` answers the commands of :data:`openscilab.driver.rigoldho.scpi.COMMANDS` with
the device model of the simulator profile ``dho924s``: ``:SINGle`` captures (the sweep becomes
SINGLE: it waits for the trigger set up, ``:TFORce`` forces it), ``:WAVeform:DATA?`` returns its
samples (digital channels one byte per sample, analog channels 8 bit raw), the generator drives
CH1. With ``bridge=True`` it also answers the ``:BRIDge`` commands of ``docs/protocols.md``
(snapshot cache, overviews, compressed tiles). Unknown commands go to the error queue and get no
answer, as on the instrument. Not simulated: edge triggers on the analog channels (refused with a
settings conflict) and the logic thresholds (stored and reported only: the digital channels read 1
above half of the logic level). For tests: ``arm_delay`` (seconds the trigger status stays as it
was after ``:SINGle``), ``snapshot_delay`` (seconds the bridge takes to read the acquisition),
``la_rate`` (a sample rate of the logic channels of their own).

Addresses: ``rigol-sim:direct`` and ``rigol-sim:bridge``.
"""

from __future__ import annotations

import itertools
import logging
import re
import socketserver
import struct
import threading
import time
from typing import Optional

import numpy as np

from ..base import CaptureError
from ..models import AnalogChannel, AnalyzerChannel, CaptureSession, TriggerType
from ..rigoldho import codec, scpi
from ...core import waveform as waves

log = logging.getLogger("openscilab.driver")

#: volts of one step of the 8 bit raw values, and the raw value of 0 V
Y_INC = 10.0 / 256
Y_REF = 128.0


def _pattern(template: str) -> re.Pattern:
    text = re.escape(template).replace(r"\{n\}", r"(\d+)").replace(r"\{value\}", r"(.+)")
    return re.compile(f"^{text}$", re.IGNORECASE)


PATTERNS = {name: _pattern(template) for name, template in scpi.COMMANDS.items()}
#: the memory depths the instrument takes (points)
DEPTHS = (1_000, 10_000, 100_000, 1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000)
#: the functions of the generator (its short and long forms)
FUNCTIONS = {"SIN": waves.SINE, "SINUSOID": waves.SINE, "SQU": waves.SQUARE, "SQUARE": waves.SQUARE,
             "RAMP": waves.RAMP, "NOIS": waves.NOISE, "NOISE": waves.NOISE, "DC": waves.DC}


#: read-only answers of the front panel the simulator has no state for
PANEL_TEXTS = {":TRIGGER:MODE?": "EDGE"}


def panel_defaults() -> dict[str, str]:
    """The settings of the front panel (``driver/rigoldho/controls.py``) as a fresh DHO has them."""
    from ..rigoldho.controls import settings_for

    values = {}
    for setting in settings_for(4, 16):
        command, kind = setting["command"].upper(), setting["kind"]
        if kind == "number":
            values[command] = "1.000000E-3" if "TIMEBASE:MAIN:SCALE" in command else \
                "0" if "OFFSET" in command or "LEVEL" in command else "1.000000E0"
        elif kind == "bool":
            values[command] = "1" if command == ":CHANNEL1:DISPLAY" else "0"
        elif kind == "choice":
            values[command] = "1" if command.endswith(":PROBE") else setting["choices"][0][0]
    return values


#: Divisions the modified firmware NK v0.5.2 spreads the memory depth over (measured on a DHO924S:
#: 100k at 100 us/div gave 6.25 MSa/s instead of 10)
FIRMWARE_SPAN_DIVISIONS = 16.0
#: its sample rates are steps of 1.25 GSa/s / (1, 2, 2.5, 4, 5, 8 · 10^k)
FIRMWARE_RATES = sorted({1.25e9 / (m * 10 ** k) for k in range(0, 9) for m in (1, 2, 2.5, 4, 5, 8)}, reverse=True)


def firmware_rate(rate: float) -> float:
    """The largest rate of the firmware's steps that is not above ``rate`` (the smallest below those)."""
    return next((step for step in FIRMWARE_RATES if step <= rate), FIRMWARE_RATES[-1])


def firmware_depth(depth: int) -> int:
    """What the modified firmware NK v0.5.2 of the DHO924S takes for a depth asked for: a tenth up
    to 10M, a twentieth above (measured)."""
    return depth // 10 if depth <= 10_000_000 else depth // 20


class _Snapshot:
    def __init__(self, number: int, digital: Optional[np.ndarray], analog: dict[int, np.ndarray], meta: dict) -> None:
        self.id = str(number)
        self.time = int(time.time())
        self.digital = digital
        self.analog = analog
        self.meta = meta
        self.points = int(meta["points"])


class ScpiShell:
    def __init__(self, bridge: bool = False) -> None:
        from . import open_simulated

        self.bridge = bridge
        self.instrument = open_simulated("dho924s")
        self.device = self.instrument.simulated_driver
        self.device.profile = dict(self.device.profile, progressive=None)  # the instrument has it all at once
        self.lock = threading.RLock()
        #: the answer to *IDN?
        self.identity = "RIGOL TECHNOLOGIES,DHO924S,DHO9SIM0001,00.01.02"
        self.errors: list[str] = []
        self.status = "STOP"
        self.la_on = False
        self.digital_on: set[int] = set()
        self.analog_on: set[int] = set()
        self.depth = 10_000
        self.scale = 1e-3
        self.offset = 0.0
        self.trigger: dict[str, str] = {"sweep": "AUTO", "mode": "EDGE", "source": "D0", "slope": "POSITIVE"}
        self.thresholds: dict[int, float] = {}
        self.data_digital: Optional[np.ndarray] = None
        self.data_analog: dict[int, np.ndarray] = {}
        self.rate = 1e6
        self.trigger_index = 0
        self.source = "D0"
        self.window = (1, 1000)
        self.afg: dict[str, str] = {}
        self.snapshots: list[_Snapshot] = []
        self.cache_limit = 1 << 30
        self.arm_delay = 0.0
        #: a capture was taken up and no status was asked since (see the status query)
        self._taken_up_unseen = False
        self.snapshot_delay = 0.0
        self.snapshot_started = threading.Event()
        self.la_rate: Optional[float] = None
        #: the next this many ``:WAVeform:DATA?`` answer with an empty block
        self.empty_reads = 0
        #: ``:WAVeform:FORMat`` (a logic channel as BYTE gives its upper byte: always 0, as the DHO924S does)
        self.wave_format = "BYTE"
        #: D0–D15 can be read (without the logic probe, and on the DHO924S with the modified firmware
        #: NK v0.5.2 also with it, ``:WAVeform:SOURce D<n>`` is refused)
        self.probe = True
        #: volts of the analog channels' range on the screen (± around 0): the edge trigger level
        #: stays within it
        self.analog_range = 4.0
        #: the settings of the front panel (header in capitals → value as sent)
        self.panel = panel_defaults()
        #: the memory depths of the modified firmware NK v0.5.2 (:func:`firmware_depth`)
        self.firmware_depths = False
        #: the rates of the modified firmware: the depth over 16 divisions, in steps (see :func:`firmware_rate`)
        self.firmware_rates = False
        #: counts :SINGle, :RUN and :STOP: what a capture started before them reports is dropped
        self.generation = 0
        #: the capture waiting for its trigger: (session, digital channels, generation)
        self._waiting: Optional[tuple] = None
        self._numbers = itertools.count(1)
        shell = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                for raw in self.rfile:
                    line = raw.decode("ascii", "replace").strip()
                    if not line:
                        continue
                    try:
                        answer = shell.handle(line)
                    except Exception as error:  # noqa: BLE001 - an error of the simulation: in the queue
                        log.debug("Simulated DHO: %s failed: %s", line, error)
                        shell.errors.append(f'-200,"{error}"')
                        answer = None
                    if answer is None:
                        continue
                    data = answer if isinstance(answer, bytes) else answer.encode() + b"\n"
                    try:
                        self.wfile.write(data)
                    except OSError:
                        return

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name="openscilab-dho-shell", daemon=True).start()

    # ------------------------------------------------------------ commands
    def handle(self, line: str):
        with self.lock:
            if line.upper().startswith(":BRID"):
                if not self.bridge:
                    self.errors.append('-113,"Undefined header"')
                    return None
                return self._bridge(line)
            header, _, argument = line.strip().partition(" ")
            key = header.upper()
            for name, pattern in PATTERNS.items():
                match = pattern.match(line)
                if match:
                    if key in self.panel and argument:
                        self.panel[key] = argument.strip()  # (the front panel shows what a capture set)
                    return self._command(name, match.groups())
            # the settings of the front panel (driver/rigoldho/controls.py): kept as they were sent
            if key.endswith("?") and key[:-1] in self.panel:
                return self.panel[key[:-1]]
            if key in self.panel and argument:
                self.panel[key] = argument.strip()
                return None
            if key in PANEL_TEXTS:
                return PANEL_TEXTS[key]
            if key in (":AUTOSET", ":CLEAR"):
                return None
            self.errors.append('-113,"Undefined header"')
            return None

    def _command(self, name: str, groups: tuple):
        value = groups[-1] if groups else ""
        if name == "identify":
            return self.identity
        if name == "error":
            return self.errors.pop(0) if self.errors else '0,"No error"'
        if name == "clear":
            self.errors.clear()
        elif name in ("run", "stop"):
            self.generation += 1
            self._waiting = None
            if name == "stop" and self.device.is_capturing:
                self.device.stop_capture()
            self.status = "STOP" if name == "stop" else "RUN"
        elif name == "single":
            self._single()
        elif name == "force":
            self._force()
        elif name == "status":
            if self._taken_up_unseen:
                # the first look after the instrument took a capture up sees that it did (the DHO shows
                # TD for a while; the simulator may have finished already, on a busy computer before it)
                self._taken_up_unseen = False
                if self.status == "STOP":
                    return "TD"
            return self.status
        elif name == "la_state":
            self.la_on = value.upper() in ("ON", "1")
        elif name == "la_channel":
            (self.digital_on.add if value.upper() in ("ON", "1") else self.digital_on.discard)(int(groups[0]))
        elif name == "la_threshold":
            self.thresholds[int(groups[0])] = float(value)
        elif name == "channel":
            (self.analog_on.add if value.upper() in ("ON", "1") else self.analog_on.discard)(int(groups[0]) - 1)
        elif name == "la_state?":
            return "1" if self.la_on else "0"
        elif name == "la_channel?":
            return "1" if int(groups[0]) in self.digital_on else "0"
        elif name == "channel?":
            return "1" if int(groups[0]) - 1 in self.analog_on else "0"
        elif name == "memory_depth":
            depth = scpi.parse_depth(value)
            if self.firmware_depths and depth in scpi.MEMORY_DEPTHS:
                self.depth = firmware_depth(depth)
            elif not self.firmware_depths and depth in DEPTHS:
                self.depth = depth
            else:
                self.errors.append('-222,"Data out of range"')
        elif name == "memory_depth?":
            return f"{self.depth:.4E}"
        elif name == "screen?":
            return scpi.block(self._screen())
        elif name == "lan_address?":
            return "0.0.0.0"  # (the simulated instrument has no network port)
        elif name == "sample_rate?":
            return f"{self._screen_rate():.6e}"
        elif name == "timebase_scale":
            self.scale = float(value)
        elif name == "timebase_offset":
            self.offset = float(value)
        elif name == "trigger_sweep":
            self.trigger["sweep"] = value.upper()
        elif name == "trigger_mode":
            self.trigger["mode"] = value.upper()
        elif name == "edge_source":
            self.trigger["source"] = value.upper()
        elif name == "edge_slope":
            self.trigger["slope"] = value.upper()
        elif name == "edge_level":
            # (like the instrument: within the range of the channel on the screen)
            self.trigger["level"] = f"{min(max(float(value), -self.analog_range), self.analog_range):g}"
        elif name == "edge_level?":
            return f"{float(self.trigger.get('level', 0)):.6E}"
        elif name == "wave_source":
            if value.upper().startswith("D") and not self.probe:
                self.errors.append('-200,"Command execute failed"')
            else:
                self.source = value.upper().replace("CHANNEL", "CHAN")
        elif name == "wave_source?":
            return self.source
        elif name in ("wave_format", "wave_format_word"):
            self.wave_format = "WORD" if name == "wave_format_word" else "BYTE"
        elif name in ("wave_mode", "wave_mode_normal"):
            self.window = (1, max(self._points(), 1))  # (like the DHO924S: the window over the acquisition)
        elif name == "wave_start":
            if int(value) > self.window[1]:
                self.errors.append('-200,"Command execute failed"')
            else:
                self.window = (int(value), self.window[1])
        elif name == "wave_stop":
            if int(value) < self.window[0] or int(value) > max(self._points(), 1):
                self.errors.append('-200,"Command execute failed"')
            else:
                self.window = (self.window[0], int(value))
        elif name == "wave_preamble?":
            # (like the DHO924S: the points of the window, the origin that of the acquisition)
            points = self.window[1] - self.window[0] + 1
            digital = self.source.startswith("D")
            rate = self.la_rate if self.la_rate and digital else self.rate
            # (like the DHO924S: a logic channel's preamble is computed as if it were sampled at
            # this rate, its real one being half that - LA_PERIOD_FACTOR, applied by the driver)
            period = 1 / rate
            return (f"0,2,{points},1,{period:.9e},{-self.trigger_index / rate:.9e},0,"
                    f"{Y_INC:.9e},0,{Y_REF:g}")
        elif name == "wave_data?":
            if self.empty_reads > 0:
                # (like the DHO924S just after the capture: STOP, but the waveform is not there yet)
                self.empty_reads -= 1
                return scpi.block(b"")
            values = self._source_values()
            first, last = self.window
            window = values[first - 1:last]
            if self.source.startswith("D"):
                # a logic channel: one 16 bit word per sample, or (BYTE) only its upper byte
                window = window.astype("<u2") if self.wave_format == "WORD" else np.zeros(len(window), np.uint8)
            return scpi.block(window.tobytes())
        elif name.startswith("afg_"):
            return self._afg(name, value)
        return None

    def _screen(self) -> bytes:
        """A PNG of the screen (1024 x 600): dark, the trigger status as a coloured bar on top."""
        import zlib

        width, height = 1024, 600
        colour = {"STOP": (200, 40, 40), "WAIT": (230, 160, 30)}.get(self.status, (60, 180, 75))
        dark = b"\x00" + bytes((16, 16, 20)) * width
        bar = b"\x00" + bytes(colour) * width
        raw = bar * 24 + dark * (height - 24)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))

    def _points(self) -> int:
        if self.data_digital is not None:
            return len(self.data_digital)
        return len(next(iter(self.data_analog.values()))) if self.data_analog else 0

    def _source_values(self) -> np.ndarray:
        if self.source.startswith("D"):
            number = int(self.source[1:])
            if self.data_digital is None:
                return np.zeros(0, np.uint8)
            return ((self.data_digital >> np.uint16(number)) & 1).astype(np.uint8)
        number = int(self.source[4:]) - 1
        return self.data_analog.get(number, np.zeros(0, np.uint8))

    # --------------------------------------------------------------- capture
    def _single(self) -> None:
        self.trigger["sweep"] = "SINGLE"  # as on the instrument: whatever the sweep was
        self.generation += 1
        self._waiting = None
        self._taken_up_unseen = False
        if self.arm_delay > 0:
            # an instrument slow to take the capture up: the status stays as it was meanwhile
            timer = threading.Timer(self.arm_delay, self._arm, args=(self.generation,))
            timer.daemon = True
            timer.start()
            return
        self._arm(self.generation)

    def _arm(self, generation: int) -> None:
        with self.lock:
            if generation == self.generation:
                self._in_thread(self._take_up, generation)

    def _in_thread(self, function, *arguments) -> None:
        """``function`` in a thread of the shell: an error goes to the error queue, as in :meth:`handle`."""
        try:
            function(*arguments)
        except Exception as error:  # noqa: BLE001 - an error of the simulation: in the queue
            log.debug("Simulated DHO: %s failed: %s", function.__name__, error)
            self.errors.append(f'-200,"{error}"')
            self._waiting = None
            self.status = "STOP"

    def _screen_rate(self) -> float:
        """The rate the depth and the time base give."""
        span = FIRMWARE_SPAN_DIVISIONS if self.firmware_rates else scpi.DIVISIONS
        rate = min(self.depth / max(self.scale * span, 1e-12), float(self.device.max_frequency))
        return firmware_rate(rate) if self.firmware_rates else rate

    def _take_up(self, generation: int) -> None:
        total = self.depth
        rate = self._screen_rate()
        session = CaptureSession(frequency=max(int(round(rate)), 1))
        pre = int(round(total / 2 - self.offset * session.frequency))
        session.pre_trigger_samples = min(max(pre, 0), total - 1)
        session.post_trigger_samples = total - session.pre_trigger_samples
        numbers = sorted(self.digital_on) if self.la_on else []
        if not numbers:
            numbers = [0]  # the samples of the device need a digital channel; dropped below
        session.capture_channels = [AnalyzerChannel(channel_number=number) for number in numbers]
        session.analog_channels = [AnalogChannel(channel_number=number) for number in sorted(self.analog_on)]
        mode = self.trigger["mode"]
        if mode == "EDGE" and not self.trigger["source"].startswith("D"):
            # an analog channel: its signal repeats, so the simulator takes the capture at once
            session.trigger_type = TriggerType.IMMEDIATE
        elif mode == "EDGE" and self.trigger["source"].startswith("D"):
            session.trigger_type = TriggerType.EDGE
            session.trigger_channel = int(self.trigger["source"][1:])
            session.trigger_inverted = self.trigger["slope"].startswith("NEG")
            if session.trigger_channel not in numbers:
                session.capture_channels.append(AnalyzerChannel(channel_number=session.trigger_channel))
        else:
            session.trigger_type = TriggerType.IMMEDIATE
        self.status = "WAIT"
        self._taken_up_unseen = True
        self._waiting = (session, numbers, generation)
        self._start(session, numbers, generation)

    def _start(self, session: CaptureSession, numbers: list[int], generation: int) -> None:
        self.rate = session.frequency
        error = self.device.start_capture(session, lambda args: self._captured(args, numbers, generation))
        if error != CaptureError.NONE:
            self.errors.append(f'-221,"Settings conflict: {error.value}"')
            self._waiting = None
            self.status = "STOP"

    def _force(self) -> None:
        """``:TFORce``: the capture waiting for its trigger is taken at once."""
        if self.status != "WAIT" or self._waiting is None:
            return
        session, numbers, generation = self._waiting
        self._waiting = None
        self.device.stop_capture()
        forced = CaptureSession(frequency=session.frequency, pre_trigger_samples=session.pre_trigger_samples,
                                post_trigger_samples=session.post_trigger_samples)
        forced.capture_channels = [AnalyzerChannel(channel_number=item.channel_number)
                                   for item in session.capture_channels]
        forced.analog_channels = [AnalogChannel(channel_number=item.channel_number) for item in session.analog_channels]
        forced.trigger_type = TriggerType.IMMEDIATE

        def restart() -> None:
            deadline = time.monotonic() + 5.0
            while self.device.is_capturing and time.monotonic() < deadline:
                time.sleep(0.005)  # (the waiting capture ends)
            with self.lock:
                if generation == self.generation and self.status == "WAIT":  # (not done meanwhile)
                    self._in_thread(self._start, forced, numbers, generation)

        threading.Thread(target=restart, name="openscilab-dho-shell-force", daemon=True).start()

    def _captured(self, args, numbers: list[int], generation: int) -> None:
        with self.lock:
            session = args.session
            if generation != self.generation:
                return  # stopped, or another capture since
            if not args.success:
                return  # no trigger within the simulator's search: the instrument would still wait
            self._waiting = None
            # (like the DHO924S: the logic channels' words are LA_PERIOD_FACTOR analog samples
            # long and only the first part of the points the preamble names is the capture's; the
            # rest is old memory, here all ones)
            words = np.full(session.pre_trigger_samples + session.post_trigger_samples, 0xFFFF, dtype=np.uint16)
            valid = len(words) // scpi.LA_PERIOD_FACTOR
            words[:valid] = 0
            for channel in session.capture_channels:
                if channel.channel_number in self.digital_on and self.la_on:
                    held = channel.samples[::scpi.LA_PERIOD_FACTOR][:valid]
                    words[:len(held)] |= held.astype(np.uint16) << np.uint16(channel.channel_number)
            self.data_digital = words if self.la_on and self.digital_on else None
            scale, offset, _bits = self.device.analog_scale()
            self.data_analog = {}
            for channel in session.analog_channels:
                volts = channel.raw.astype(np.float64) * scale + offset
                self.data_analog[channel.channel_number] = np.clip(np.round(volts / Y_INC + Y_REF), 0, 255).astype(np.uint8)
            self.trigger_index = session.pre_trigger_samples
            self.rate = session.frequency
            self.status = "STOP"

    # ------------------------------------------------------------ generator
    def _afg(self, name: str, value: str):
        generator = self.instrument.generator
        if name == "afg_output?":
            return "1" if generator.running("GI") else "0"
        if name == "afg_output":
            if value.upper() in ("ON", "1"):
                generator.start("GI", self._afg_waveform())
            else:
                generator.stop("GI")
            return None
        if name == "afg_function" and value.upper() not in FUNCTIONS:
            self.errors.append('-222,"Data out of range"')
            return None
        self.afg[name] = value
        return None

    def _afg_waveform(self) -> waves.Waveform:
        settings = self.afg
        kind = FUNCTIONS.get(settings.get("afg_function", "SINUSOID").upper(), waves.SINE)
        if kind == waves.RAMP and float(settings.get("afg_ramp_symmetry", 50)) == 50:
            kind = waves.TRIANGLE
        options = dict(frequency=float(settings.get("afg_frequency", 1000)),
                       amplitude=float(settings.get("afg_amplitude", 2.0)) / 2,
                       offset=float(settings.get("afg_offset", 0.0)),
                       phase=float(settings.get("afg_phase", 0.0)))
        if kind == waves.SQUARE and "afg_square_duty" in settings:
            options["duty"] = float(settings["afg_square_duty"]) / 100
        return waves.Waveform(kind=kind, **options)

    # ---------------------------------------------------------------- bridge
    def _bridge(self, line: str):
        header, _, argument = line.partition(" ")
        header = header.upper()
        arguments = [item.strip() for item in argument.split(",")] if argument.strip() else []
        if header.startswith(":BRID:VERS") or header.startswith(":BRIDGE:VERS"):
            return "OPENSCILAB_BRIDGE,0.1.0-sim,1"
        if ":BENC" in header:
            return "25000000"
        if ":SNAP" in header:
            return self._snapshot(arguments)
        if ":CACH" in header and ":LIST" in header:
            return ";".join(f"{item.id},{item.time},{item.points}" for item in reversed(self.snapshots))
        if ":CACH" in header and ":DEL" in header:
            if arguments and arguments[0].upper() == "ALL":
                self.snapshots.clear()
            else:
                self.snapshots = [item for item in self.snapshots if item.id not in arguments]
            return "OK"
        if ":CACH" in header and ":LIM" in header:
            self.cache_limit = int(float(arguments[0]))
            return "OK"
        if ":META" in header:
            item = self._find(arguments[0])
            return ";".join(f"{key}={value}" for key, value in item.meta.items()) if item else "ERR unknown snapshot"
        if ":OVER" in header:
            item = self._find(arguments[0])
            if item is None:
                return "ERR unknown snapshot"
            values = self._channel(item, arguments[1])
            level = int(arguments[2])
            if arguments[1].upper() == "D":
                return scpi.block(codec.overview_digital(values, level).astype("<u2").tobytes())
            return scpi.block(codec.overview_analog(values, level).astype(np.uint8).tobytes())
        if ":TILE" in header:
            item = self._find(arguments[0])
            if item is None:
                return "ERR unknown snapshot"
            values = self._channel(item, arguments[1])
            start, count = int(arguments[2]), int(arguments[3])
            part = values[start:start + count]
            encoded = codec.encode_digital(part) if arguments[1].upper() == "D" else codec.encode_analog(part)
            return scpi.block(encoded)
        return "ERR unknown bridge command"

    def _find(self, number: str) -> Optional[_Snapshot]:
        return next((item for item in self.snapshots if item.id == number), None)

    @staticmethod
    def _channel(item: _Snapshot, name: str) -> np.ndarray:
        if name.upper() == "D":
            return item.digital if item.digital is not None else np.zeros(item.points, np.uint16)
        return item.analog[int(name.upper().replace("CH", "")) - 1]

    def _snapshot(self, arguments: list[str]) -> str:
        self.snapshot_started.set()
        if self.snapshot_delay > 0:
            time.sleep(self.snapshot_delay)  # (the app reading a long acquisition)
        if self.status != "STOP" or (self.data_digital is None and not self.data_analog):
            return "ERR no stopped acquisition"
        bits = []
        for name in arguments:
            match = re.fullmatch(r"D(\d+)(?:-D?(\d+))?", name.upper())
            if match:
                bits += range(int(match.group(1)), int(match.group(2) or match.group(1)) + 1)
        off = [bit for bit in bits if not self.la_on or bit not in self.digital_on]
        if off:
            # the app reads every channel on its own: one that is off has no data
            return f"ERR asked for {self._points()} points from 1, got 0 (D{off[0]} is off)"
        channels = [name.upper() for name in arguments if name.upper().startswith("CH")]
        if bits and channels and self.la_rate and self.la_rate != self.rate:
            return (f"ERR the channels were sampled at different rates or triggers (D{bits[0]}: {self.la_rate:g} "
                    f"Sa/s, trigger at {self.trigger_index}; {channels[0]}: {self.rate:g} Sa/s, trigger at "
                    f"{self.trigger_index}); snapshot them separately")
        points = self._points()
        rate, trigger = self.rate, self.trigger_index
        alone = not any(name.upper().startswith("CH") for name in arguments)
        if bits and alone and self.data_digital is not None:
            # (like the app: only the first part of a logic channel's points is the capture's, at
            # LA_PERIOD_FACTOR times the period of its preamble)
            points //= scpi.LA_PERIOD_FACTOR
            rate, trigger = rate / scpi.LA_PERIOD_FACTOR, round(trigger / scpi.LA_PERIOD_FACTOR)
        meta = {"points": points, "rate": f"{rate:g}", "trigger": trigger,
                "channels": ",".join(arguments)}
        analog = {}
        for name in arguments:
            if name.upper().startswith("CH"):
                number = int(name[2:]) - 1
                if number in self.data_analog:
                    analog[number] = self.data_analog[number]
                    meta[f"CH{number + 1}.yinc"] = f"{Y_INC:.9e}"
                    meta[f"CH{number + 1}.yorig"] = "0"
                    meta[f"CH{number + 1}.yref"] = f"{Y_REF:g}"
        mask = np.uint16(sum(1 << bit for bit in set(bits)))
        digital = None
        if bits and self.data_digital is not None:
            held = (self.data_digital & mask)[:self._points() // scpi.LA_PERIOD_FACTOR]
            digital = held if alone else np.repeat(held, scpi.LA_PERIOD_FACTOR)[:points]
        item = _Snapshot(next(self._numbers), digital, analog, meta)
        self.snapshots.append(item)
        return item.id

    # ----------------------------------------------------------------- close
    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.instrument.close()


class ShellTmcPort:
    """The USB device port of the simulated DHO: the USBTMC transfers (``driver/usbtmc.py``) of what
    :meth:`ScpiShell.handle` answers - messages with their headers, the requests for an answer (an
    answer goes to the oldest open one), INITIATE_ABORT_BULK_IN, INITIATE_CLEAR. ``chunk``: the
    bytes one bulk read hands over at most (libusb may split a transfer)."""

    def __init__(self, shell: ScpiShell, chunk: int = 0) -> None:
        self.shell = shell
        self.chunk = chunk
        self._incoming = bytearray()
        self._answers = bytearray()
        self._requests: list[tuple[int, int]] = []  # (tag, size) of the open REQUEST_DEV_DEP_MSG_IN
        self._pending = bytearray()  # a transfer not handed over completely yet
        self._padding = 0
        self._ready = threading.Condition()
        #: answers come this late after their command (s), to try timeouts
        self.delay = 0.0
        self._available_at = 0.0
        self.cleared = 0
        self.aborted = 0

    def write(self, data: bytes, timeout: float) -> None:
        from .. import usbtmc

        message, tag, _inverse, size, attributes = struct.unpack_from("<BBBxIB", data)
        if message == usbtmc.REQUEST_DEV_DEP_MSG_IN:
            with self._ready:
                self._requests.append((tag, size))
                self._ready.notify_all()
            return
        self._incoming += data[usbtmc.HEADER_SIZE:usbtmc.HEADER_SIZE + size]
        if not attributes & usbtmc.EOM:
            return
        text, self._incoming = bytes(self._incoming).decode("ascii", "replace"), bytearray()
        for line in text.splitlines():
            if not line.strip():
                continue
            answer = self.shell.handle(line.strip())
            if answer is not None:
                with self._ready:
                    self._answers += answer if isinstance(answer, bytes) else answer.encode() + b"\n"
                    self._available_at = time.monotonic() + self.delay
                    self._ready.notify_all()

    def read(self, size: int, timeout: float) -> bytes:
        from .. import usbtmc

        if not self._pending:
            deadline = time.monotonic() + timeout
            with self._ready:
                while not self._requests or not self._answers or time.monotonic() < self._available_at:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise usbtmc.UsbTmcTimeout("no answer")
                    self._ready.wait(min(remaining, 0.01))
                tag, limit = self._requests.pop(0)
                data = bytes(self._answers[:limit])
                del self._answers[:limit]
                end = usbtmc.EOM if not self._answers else 0
                self._pending += usbtmc.header(usbtmc.DEV_DEP_MSG_IN, tag, len(data), end) + data
                self._padding = (-len(self._pending)) % 4
        count = min(size, self.chunk or size, len(self._pending))
        piece = bytes(self._pending[:count])
        del self._pending[:count]
        if not self._pending:
            piece += b"\x00" * self._padding  # (the alignment bytes end the transfer, in its last packet)
        return piece

    def control_in(self, request: int, value: int, length: int, timeout: float) -> bytes:
        from .. import usbtmc

        if request == usbtmc.INITIATE_CLEAR:
            with self._ready:
                self._answers.clear()
                self._incoming.clear()
                self._pending.clear()
                self._requests.clear()
            self.cleared += 1
            return bytes([usbtmc.STATUS_SUCCESS])
        if request == usbtmc.CHECK_CLEAR_STATUS:
            return bytes([usbtmc.STATUS_SUCCESS, 0])
        return bytes([0x80])  # (failed: not supported)

    def control_in_endpoint(self, request: int, value: int, length: int, timeout: float) -> bytes:
        from .. import usbtmc

        if request == usbtmc.INITIATE_ABORT_BULK_IN:
            with self._ready:
                found = [item for item in self._requests if item[0] == value]
                self._requests = [item for item in self._requests if item[0] != value]
            if not found:
                return bytes([usbtmc.STATUS_TRANSFER_NOT_IN_PROGRESS, value])
            self.aborted += 1
            return bytes([usbtmc.STATUS_SUCCESS, value])
        if request == usbtmc.CHECK_ABORT_BULK_IN_STATUS:
            return bytes([usbtmc.STATUS_SUCCESS]) + bytes(7)
        return bytes([0x80])

    def clear_halt(self) -> None:
        pass

    def close(self) -> None:
        pass


def open_shell(mode: str = "bridge"):
    """A :class:`~openscilab.driver.rigoldho.driver.RigolDhoDriver` connected to a simulated DHO
    (``mode``: ``bridge`` or ``direct`` over the network, ``usb`` over its USBTMC port); disposing
    the driver ends the simulation."""
    from ..rigoldho.driver import RigolDhoDriver

    if mode not in ("bridge", "direct", "usb"):
        raise ValueError(f"unknown simulated DHO {mode!r} (bridge, direct, usb)")
    shell = ScpiShell(bridge=mode == "bridge")
    try:
        if mode == "usb":
            from .. import usbtmc
            from ..rigoldho import scpi

            port = ShellTmcPort(shell)
            link = usbtmc.UsbTmcLink(port, "Rigol DHO924S (simulated, USB)")
            connection = scpi.ScpiConnection(timeout=5.0, transport=scpi.UsbTransport(link))
            driver = RigolDhoDriver("simulated USB", address="rigol-sim:usb", connection=connection)
            driver.usb_port = port
        else:
            driver = RigolDhoDriver("127.0.0.1", shell.port, address=f"rigol-sim:{mode}")
    except Exception:
        shell.close()
        raise
    driver.shell = shell
    driver.web_control_url = lambda: None  # (no web server: its screen comes over SCPI)
    original = driver.dispose

    def dispose() -> None:
        original()
        shell.close()

    driver.dispose = dispose
    return driver

