# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Driver for the Rigol DHO900 oscilloscopes (DHO914, DHO914S, DHO924, DHO924S): 16 logic
channels D0–D15, four analog channels CH1–CH4 and, on the S models, the built-in generator.

A capture: thresholds → channels → memory depth and time base → trigger → ``:SINGle`` → the
trigger status until the instrument has taken the capture up (``WAIT``, ``RUN``, ``TD``) and
then shows ``STOP`` → the data. ``:SINGle`` makes the sweep SINGLE, so a capture without trigger
is forced (``:TFORce``) once it waits. With the bridge app on the instrument (port 5560,
``docs/protocols.md``) the data arrive progressively: the capture is complete at once with an
overview of every channel and the samples follow tile by tile (:mod:`.fetcher`). Without it the
driver reads ``:WAVeform:DATA?`` block by block from the instrument's own server (port 5555).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import scpi
from .scpi import ScpiConnection, ScpiError, command
from ..base import (
    CAPABILITY_AFG,
    CAPABILITY_IMMEDIATE_TRIGGER,
    CAPABILITY_PATTERN_GROUPS,
    CAPABILITY_PROGRESSIVE,
    CAPABILITY_THRESHOLD,
    AnalyzerDriverBase,
    AnalyzerDriverType,
    CaptureCompletedArgs,
    CaptureCompletedHandler,
    CaptureError,
    CaptureLimits,
    DeviceConnectionError,
    DeviceSection,
)
from ..models import CaptureSession, TriggerType

log = logging.getLogger("openscilab.driver")

DIGITAL = [f"D{index}" for index in range(16)]
ANALOG = [f"CH{index}" for index in range(1, 5)]
#: highest sample rate (Sa/s) and memory depth of the DHO924S (points), with analog channels
MAX_RATE = 625_000_000
DEPTH_DIGITAL = 31_250_000
DEPTH_ANALOG = {1: 25_000_000, 2: 25_000_000, 3: 10_000_000, 4: 10_000_000}
#: seconds between two looks at the trigger status
STATUS_INTERVAL_S = 0.05
#: seconds between two looks while the instrument takes the capture up (``:SINGle``)
ARM_INTERVAL_S = 0.005
#: seconds the instrument may take to show that it took a capture up
ARM_TIMEOUT_S = 10.0
#: seconds a capture waits for its trigger at most
TRIGGER_TIMEOUT_S = 3600.0
#: seconds :meth:`RigolDhoDriver.stop_capture` waits for the connection (busy while data arrive)
STOP_WAIT_S = 0.5
#: trigger states of a capture taken up: running, waiting for the trigger, triggered
ARMED_STATES = ("RUN", "WAIT", "TD", "AUTO")
#: the DHO900 models this driver knows (see :func:`model_features`)
KNOWN_MODELS = "DHO914, DHO914S, DHO924, DHO924S"


@dataclass(frozen=True)
class ModelFeatures:
    analog: int
    digital: int
    generator: bool


def model_features(model: str) -> Optional[ModelFeatures]:
    """What a model has: DHO800 (DHO802, DHO804, DHO812, DHO814): 2 or 4 analog channels, no logic
    analyzer and no generator; DHO900 (DHO914, DHO924): 4 analog and 16 logic channels, the S
    models also the generator. ``None`` for any other model."""
    name = model.strip().upper()
    match = re.fullmatch(r"DHO8[01]([24])", name)
    if match:
        return ModelFeatures(analog=int(match.group(1)), digital=0, generator=False)
    match = re.fullmatch(r"DHO9[12]4(S?)", name)
    if match:
        return ModelFeatures(analog=4, digital=16, generator=bool(match.group(1)))
    return None


def same_timing(sources: list[tuple[str, dict[str, float]]]) -> tuple[int, int]:
    """Points and trigger sample of a capture from the preambles of its sources (name, preamble).
    One capture has one rate: sources sampled at different rates or with the trigger elsewhere
    (the logic channels beside the analog ones) are refused rather than shown out of step."""
    def timing(preamble: dict[str, float]) -> tuple[float, int]:
        if not preamble["xinc"]:
            return 0.0, 0
        return 1.0 / preamble["xinc"], round(preamble["xref"] - preamble["xorig"] / preamble["xinc"])

    first_name, first = sources[0]
    rate, trigger = timing(first)
    for name, preamble in sources[1:]:
        other_rate, other_trigger = timing(preamble)
        if abs(other_rate - rate) > 1e-6 * max(rate, other_rate) or abs(other_trigger - trigger) > 1:
            raise ScpiError(f"{name} has {other_rate:g} Sa/s (trigger at sample {other_trigger}), {first_name} "
                            f"{rate:g} Sa/s (trigger at sample {trigger}): one capture cannot hold both, capture "
                            "them separately")
    return min(int(preamble["points"]) for _, preamble in sources), trigger


class RigolDhoDriver(AnalyzerDriverBase):
    """A DHO900 over the network, directly or through the bridge app, or over USB (USBTMC: give the
    ``connection``, see :func:`open_usb`)."""

    def __init__(self, host: str, port: Optional[int] = None, address: str = "",
                 connection: Optional[ScpiConnection] = None) -> None:
        super().__init__()
        self.host = host
        self._address = address or f"rigol:{host}"
        self.bridge_version: Optional[str] = None
        #: connected over USB (the instrument's USBTMC interface; no bridge app there)
        self.over_usb = connection is not None and isinstance(connection._transport, scpi.UsbTransport)
        self.connection = connection if connection is not None else self._connect(host, port)
        try:
            self.identity = self.connection.query(command("identify"))
        except ScpiError as error:
            self.connection.close()
            raise DeviceConnectionError(f"{host} does not answer: {error}") from error
        parts = [part.strip() for part in self.identity.split(",")]
        if len(parts) < 2 or "RIGOL" not in parts[0].upper():
            self.connection.close()
            raise DeviceConnectionError(f"{host} is no Rigol instrument ({self.identity!r})")
        self.model = parts[1]
        self.serial = parts[2] if len(parts) > 2 else ""
        self.firmware = parts[3] if len(parts) > 3 else ""
        features = model_features(self.model)
        if features is None or not features.digital:
            self.connection.close()
            reason = "has no logic analyzer" if features else "is a model openSciLab does not know"
            raise DeviceConnectionError(f"The Rigol {self.model} at {host} {reason}; openSciLab works with the "
                                        f"DHO900 series ({KNOWN_MODELS}).")
        self.features = features
        self._capturing = False
        self._abort = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fetcher = None
        self._facets: Optional[list] = None

    def _connect(self, host: str, port: Optional[int]) -> ScpiConnection:
        """The bridge app when it answers (on ``port`` or 5560), else the instrument (5555)."""
        tried = []
        for candidate in ([port] if port else [scpi.BRIDGE_PORT, scpi.SCPI_PORT]):
            try:
                connection = ScpiConnection(host, candidate, timeout=5.0)
            except ScpiError as error:
                tried.append(str(error))
                continue
            try:
                answer = connection.query(":BRIDge:VERSion?", timeout=1.0)
                if answer.startswith("OPENSCILAB_BRIDGE"):
                    self.bridge_version = answer.split(",")[1] if "," in answer else "?"
                    return connection
            except ScpiError:
                # the instrument itself does not know the command: it answers nothing (an entry
                # in its error queue); a fresh connection leaves no half answer behind
                connection.close()
                connection = ScpiConnection(host, candidate, timeout=5.0)
                try:
                    connection.errors()
                except ScpiError:
                    pass
            return connection
        raise DeviceConnectionError("; ".join(tried) or f"{host} cannot be reached")

    # -------------------------------------------------------------- identity
    @property
    def device_version(self) -> Optional[str]:
        return f"Rigol {self.model} {self.firmware}".strip()

    @property
    def address(self) -> Optional[str]:
        return self._address

    @property
    def driver_id(self) -> str:
        return "rigol-" + self.model.lower()

    @property
    def driver_type(self) -> AnalyzerDriverType:
        return AnalyzerDriverType.OTHER

    @property
    def is_network(self) -> bool:
        return not self.over_usb

    @property
    def max_frequency(self) -> int:
        return MAX_RATE

    @property
    def min_frequency(self) -> int:
        return 1

    @property
    def blast_frequency(self) -> int:
        return 0

    @property
    def channel_count(self) -> int:
        return len(DIGITAL)

    def channel_names(self) -> list[str]:
        return list(DIGITAL)

    @property
    def buffer_size(self) -> int:
        return DEPTH_DIGITAL

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    @property
    def uses_bridge(self) -> bool:
        return self.bridge_version is not None

    def capabilities(self) -> frozenset[str]:
        items = {CAPABILITY_IMMEDIATE_TRIGGER, CAPABILITY_THRESHOLD, f"{CAPABILITY_PATTERN_GROUPS}0-15",
                 f"ANALOG={self.features.analog}"}
        if self.features.generator:
            items.add(CAPABILITY_AFG)
        if self.uses_bridge:
            items.add(CAPABILITY_PROGRESSIVE)
        return frozenset(items)

    def analog_channel_names(self) -> list[str]:
        return ANALOG[:self.features.analog]

    def describe(self) -> list[DeviceSection]:
        rows = [("Model", self.model), ("Serial number", self.serial or "-"), ("Firmware", self.firmware or "-"),
                ("Connection", f"USB (USBTMC): {self.host}" if self.over_usb else self.host),
                ("Transfer", f"bridge app {self.bridge_version}" if self.uses_bridge
                 else "over USB, directly from the instrument" if self.over_usb
                 else "directly from the instrument (slower; install the bridge app)")]
        return [("Rigol oscilloscope", rows)]

    def device_details(self) -> dict[str, str]:
        return {"Identity": self.identity, "Bridge": self.bridge_version or "-"}

    # ---------------------------------------------------------------- limits
    def memory_depth(self, digital: int, analog: int) -> Optional[int]:
        if analog:
            return DEPTH_ANALOG[min(max(analog, 1), 4)]
        return DEPTH_DIGITAL

    def get_limits(self, channels, acquisition_mode=None, *, to_disk=False, continuous=False) -> CaptureLimits:
        depth = DEPTH_DIGITAL
        return CaptureLimits(min_pre_samples=0, max_pre_samples=depth // 2, min_post_samples=1,
                             max_post_samples=depth)

    def pattern_trigger_groups(self) -> tuple[tuple[int, int], ...]:
        return ((0, 16),)

    def edge_trigger_channels(self) -> list[int]:
        return list(range(16))

    # --------------------------------------------------------------- capture
    def start_capture(self, session: CaptureSession,
                      completed_handler: Optional[CaptureCompletedHandler] = None) -> CaptureError:
        thread = self._thread
        if self._capturing or (self._abort.is_set() and thread is not None and thread.is_alive()
                               and thread is not threading.current_thread()):
            return CaptureError.BUSY  # (also a stopped capture whose thread still waits for its data)
        numbers = session.channel_numbers
        analog = sorted({channel.channel_number for channel in session.analog_channels})
        if (not numbers and not analog) or any(not 0 <= n < self.features.digital for n in numbers) \
                or any(not 0 <= n < self.features.analog for n in analog) or session.loop_count:
            return CaptureError.BAD_PARAMS
        total = session.pre_trigger_samples + session.post_trigger_samples
        depth = self.memory_depth(len(numbers), len(analog)) or DEPTH_DIGITAL
        if total > depth or session.frequency <= 0 or session.frequency > MAX_RATE:
            return CaptureError.BAD_PARAMS
        if session.trigger_type not in (TriggerType.IMMEDIATE, TriggerType.EDGE, TriggerType.COMPLEX,
                                        TriggerType.FAST):
            return CaptureError.UNSUPPORTED
        try:
            self._configure(session, numbers, analog, total)
            self.connection.write(command("single"))
        except ScpiError as error:
            log.warning("Configuring the DHO failed: %s", error)
            return CaptureError.HARDWARE_ERROR
        self._capturing = True
        self._abort = threading.Event()
        force = session.trigger_type == TriggerType.IMMEDIATE
        self._thread = threading.Thread(target=self._run, args=(session, numbers, analog, completed_handler,
                                                                self._abort, force), name="openscilab-dho", daemon=True)
        self._thread.start()
        return CaptureError.NONE

    def _configure(self, session: CaptureSession, numbers: list[int], analog: list[int], total: int) -> None:
        write = self.connection.write
        write(command("stop"))
        write(command("la_state", value="ON" if numbers else "OFF"))
        for index in range(16):
            write(command("la_channel", n=index, value="ON" if index in numbers else "OFF"))
        if session.threshold_voltage is not None:
            for pod in (1, 2):
                write(command("la_threshold", n=pod, value=f"{session.threshold_voltage:g}"))
        for index in range(self.features.analog):
            write(command("channel", n=index + 1, value="ON" if index in analog else "OFF"))
        # the instrument has fixed memory depths: the smallest that holds the capture, over the time
        # that gives the rate asked for (the capture then has more samples than asked for)
        depth = next((value for value in scpi.MEMORY_DEPTHS if value >= total), scpi.MEMORY_DEPTHS[-1])
        write(command("memory_depth", value=scpi.depth_text(depth)))
        answer = self.connection.query(command("memory_depth?"))
        if scpi.parse_depth(answer) != depth:
            raise ScpiError(f"the memory depth is {answer} instead of {scpi.depth_text(depth)}: "
                            + "; ".join(self.connection.errors() or ["the instrument says nothing more"]))
        write(command("timebase_scale", value=f"{depth / session.frequency / scpi.DIVISIONS:.6e}"))
        # the trigger is at the centre of the screen; the offset moves it to the pre-trigger samples
        write(command("timebase_offset", value=f"{(depth / 2 - session.pre_trigger_samples) / session.frequency:.6e}"))
        kind = session.trigger_type
        if kind == TriggerType.IMMEDIATE:
            pass  # :SINGle makes the sweep SINGLE whatever it was: the capture is forced (_wait_for_trigger)
        elif kind == TriggerType.EDGE:
            write(command("trigger_mode", value="EDGE"))
            write(command("edge_source", value=f"D{session.trigger_channel}"))
            write(command("edge_slope", value="NEGative" if session.trigger_inverted else "POSitive"))
            write(command("trigger_sweep", value="NORMal"))
        else:
            levels = ["X"] * 20  # CH1–CH4, D0–D15
            for offset in range(max(session.trigger_bit_count, 1)):
                channel = session.trigger_channel + offset
                if session.trigger_mask is not None and not (session.trigger_mask >> offset) & 1:
                    continue  # (don't care: X)
                if 0 <= channel < 16:
                    levels[4 + channel] = "H" if (session.trigger_pattern >> offset) & 1 else "L"
            write(command("trigger_mode", value="PATTern"))
            write(command("pattern", value=",".join(levels)))
            write(command("trigger_sweep", value="NORMal"))
        errors = self.connection.errors()
        if errors:
            log.warning("The DHO reported while configuring: %s", "; ".join(errors))

    def _wait_for_trigger(self, abort: threading.Event, force: bool = False) -> bool:
        """``True`` once the capture is done, ``False`` when it was stopped.

        Before ``:SINGle`` the instrument was stopped as well (``_configure``), so ``STOP`` counts
        only after it showed that it took the capture up (``RUN``, ``WAIT``, ``TD``): it is looked
        at at once and then every few milliseconds. A capture done before the first look cannot be
        told from the one before; it fails after :data:`ARM_TIMEOUT_S` instead of returning old
        data. ``force`` (no trigger): ``:TFORce`` while it waits for the trigger."""
        now = time.monotonic()
        deadline, arm_deadline = now + TRIGGER_TIMEOUT_S, now + ARM_TIMEOUT_S
        armed = False
        interval = 0.0  # the first look at once: the instrument answers it after :SINGle
        while not abort.wait(interval):
            status = self.connection.query(command("status")).strip().upper()
            if status.startswith(ARMED_STATES):
                armed = True
                if force and status.startswith(("WAIT", "RUN")):
                    self.connection.write(command("force"))  # (again at the next look if it came too early)
            elif status.startswith("STOP"):
                if armed:
                    return True
                if time.monotonic() > arm_deadline:
                    found = self.connection.errors()
                    raise ScpiError("the oscilloscope did not take the capture up (its trigger status stayed STOP"
                                    + (f": {'; '.join(found)})" if found else ")"))
            if time.monotonic() > deadline:
                raise ScpiError("no trigger came")
            interval = STATUS_INTERVAL_S if armed else ARM_INTERVAL_S
        return False

    def _run(self, session, numbers, analog, handler, abort: threading.Event, force: bool = False) -> None:
        try:
            if not self._wait_for_trigger(abort, force):
                return  # stopped
            rate = float(self.connection.query(command("sample_rate?")))
            session.frequency = int(round(rate))
            if self.uses_bridge:
                from .fetcher import BridgeFetcher

                self._fetcher = BridgeFetcher(self, session, numbers, analog)
                self._fetcher.prepare()
                if abort.is_set():
                    return  # stopped while the bridge read the acquisition: not a capture
                self._capturing = False
                self._raise_capture_completed(CaptureCompletedArgs(success=True, session=session), handler)
                self._fetcher.start()
                return
            self._read_direct(session, numbers, analog, abort)
            if abort.is_set():
                return
            self._capturing = False
            self._raise_capture_completed(CaptureCompletedArgs(success=True, session=session), handler)
        except (ScpiError, ValueError) as error:
            if abort.is_set():
                return
            self._capturing = False
            log.debug("DHO capture failed: %s", error)
            self._raise_capture_completed(CaptureCompletedArgs(success=False, session=session, error=str(error)),
                                          handler)

    def _read_source(self, source: str, abort: threading.Event) -> tuple[np.ndarray, dict[str, float]]:
        connection = self.connection
        connection.write(command("wave_source", value=source))
        connection.write(command("wave_mode"))
        connection.write(command("wave_format"))
        preamble = scpi.parse_preamble(connection.query(command("wave_preamble?")))
        points = int(preamble["points"])
        data = np.zeros(points, dtype=np.uint8)
        for start in range(0, points, scpi.MAX_POINTS_PER_READ):
            if abort.is_set():
                break
            stop = min(start + scpi.MAX_POINTS_PER_READ, points)
            # STARt never behind STOP: STOP first when the window moves on
            connection.write(command("wave_stop", value=stop))
            connection.write(command("wave_start", value=start + 1))
            connection.write(command("wave_stop", value=stop))
            block = connection.query_block(command("wave_data?"), timeout=30.0)
            data[start:start + len(block)] = np.frombuffer(block, dtype=np.uint8)
        return data, preamble

    def _read_direct(self, session, numbers, analog, abort) -> None:
        sources = []  # (name, preamble)
        for channel in session.capture_channels:
            values, preamble = self._read_source(f"D{channel.channel_number}", abort)
            channel.samples = (values != 0).astype(np.uint8)
            sources.append((f"D{channel.channel_number}", preamble))
        for channel in session.analog_channels:
            values, preamble = self._read_source(f"CHANnel{channel.channel_number + 1}", abort)
            self.set_analog(channel, values, preamble["yinc"], preamble["yorig"], preamble["yref"])
            sources.append((ANALOG[channel.channel_number], preamble))
        if sources:
            points, trigger = same_timing(sources)
            for channel in session.capture_channels:
                channel.samples = channel.samples[:points]
            for channel in session.analog_channels:
                channel.raw = channel.raw[:points]
            session.pre_trigger_samples = min(max(trigger, 0), points)
            session.post_trigger_samples = points - session.pre_trigger_samples
        session.bursts = None

    @staticmethod
    def set_analog(channel, raw: Optional[np.ndarray], yinc: float, yorig: float, yref: float) -> None:
        """Raw 8 bit values; volts = (raw − yorig − yref) · yinc."""
        if raw is not None:
            channel.raw = np.asarray(raw).astype(np.int16)
            channel.length = None
        channel.scale = float(yinc)
        channel.offset = -(float(yorig) + float(yref)) * float(yinc)
        channel.unit = "V"
        channel.rate = None
        channel.channel_name = channel.channel_name or ANALOG[channel.channel_number]

    def stop_capture(self) -> bool:
        if not self._capturing:
            return False
        self._abort.set()
        self._capturing = False
        # While the capture thread waits for the trigger, the connection is free between two looks
        # at the status. Once the data arrive it is busy for long (through the bridge up to
        # fetcher.SNAPSHOT_TIMEOUT_S), but the acquisition has stopped by then: no :STOP needed,
        # and the thread drops what it reads.
        lock = self.connection.lock
        if not lock.acquire(timeout=STOP_WAIT_S):
            return True
        try:
            self.connection.write(command("stop"))
        except ScpiError:
            pass
        finally:
            lock.release()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)
        return True

    # ------------------------------------------------- progressive transfer
    def resume_transfer(self, session: CaptureSession) -> bool:
        fetcher = self._fetcher
        if fetcher is None or fetcher.session is not session:
            return False
        fetcher.start()
        return True

    def stop_transfer(self) -> None:
        if self._fetcher is not None:
            self._fetcher.stop()

    # ---------------------------------------------------------------- facets
    def instrument_facets(self) -> list:
        if self._facets is None:
            from .generator import DhoCache, DhoGenerator

            self._facets = (([DhoGenerator(self)] if self.features.generator else [])
                            + ([DhoCache(self)] if self.uses_bridge else []))
        return list(self._facets)

    def dispose(self) -> None:
        if self._capturing:
            self.stop_capture()
        if self._fetcher is not None:
            self._fetcher.stop()
        self.connection.close()
        super().dispose()


def open_network(text: str) -> RigolDhoDriver:
    """``<host>`` or ``<host>:<port>``."""
    host, _, port = text.strip().partition(":")
    if not host:
        raise DeviceConnectionError("No address of the oscilloscope given.")
    try:
        return RigolDhoDriver(host, int(port) if port else None, address=f"rigol:{text.strip()}")
    except ScpiError as error:
        raise DeviceConnectionError(str(error)) from error


#: USB vendor ID of Rigol
RIGOL_VENDOR = 0x1AB1


def usb_instruments() -> list:
    """The Rigol instruments with a USBTMC interface on USB (``driver.usbtmc.UsbTmcInfo``)."""
    from .. import usbtmc

    return usbtmc.list_instruments(RIGOL_VENDOR)


def usb_selector(info) -> str:
    """What ``rigol:usb:<selector>`` names ``info`` by: its serial number, else its bus position."""
    return info.serial_number or info.location


def open_usb(selector: str = "", find=None, open_link=None) -> RigolDhoDriver:
    """The oscilloscope on USB: ``""`` the first one, else by serial number or ``<bus>:<address>``."""
    from .. import usbtmc

    selector = selector.strip()
    found = (find or usb_instruments)()
    if selector:
        found = [info for info in found if selector in (info.serial_number, info.location)]
    if not found:
        raise DeviceConnectionError(
            "No Rigol oscilloscope found on USB" + (f" with {selector}" if selector else "") +
            ". Connect its USB device port (on the back) to this computer; on Linux it needs the udev rule "
            "packaging/linux/60-rigol.rules, on Windows the WinUSB driver (e.g. installed with Zadig).")
    info = found[0]
    try:
        link = (open_link or usbtmc.open_link)(info)
    except usbtmc.UsbTmcError as error:
        raise DeviceConnectionError(str(error)) from error
    connection = ScpiConnection(timeout=5.0, transport=scpi.UsbTransport(link))
    try:
        return RigolDhoDriver(info.label, address=f"rigol:usb:{usb_selector(info)}", connection=connection)
    except ScpiError as error:
        connection.close()
        raise DeviceConnectionError(str(error)) from error
