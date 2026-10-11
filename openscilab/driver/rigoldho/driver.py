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
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import scpi
from .scpi import ScpiConnection, ScpiError, command
from ..base import (
    CAPABILITY_AFG,
    CAPABILITY_DEVICE_SETTINGS,
    CAPABILITY_IMMEDIATE_TRIGGER,
    CAPABILITY_PROGRESSIVE,
    CAPABILITY_REMOTE_SCREEN,
    CAPABILITY_WEB_CONTROL,
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
from ..models import AnalogChannel, AnalyzerChannel, CaptureSession, TriggerType

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
#: tries (and seconds between them) to read a window of a waveform that the instrument does not give yet
DATA_ATTEMPTS = 40
DATA_RETRY_S = 0.1
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
        features = model_features(parts[1]) if len(parts) >= 2 else None
        # the model decides: DHOs sold under another name answer e.g. "NK TECHNOLOGIES,DHO924S,..."
        if len(parts) < 2 or ("RIGOL" not in parts[0].upper() and features is None):
            self.connection.close()
            raise DeviceConnectionError(f"{host} is no Rigol instrument ({self.identity!r})")
        self.model = parts[1]
        self.serial = parts[2] if len(parts) > 2 else ""
        self.firmware = parts[3] if len(parts) > 3 else ""
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
        #: memory depth setting → points it gave (0: refused), see :meth:`_set_depth`
        self._depths: dict[int, int] = {}

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
    def max_loop_count(self) -> int:
        return 0  # (one capture per trigger: no bursts)

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
        items = {CAPABILITY_IMMEDIATE_TRIGGER, CAPABILITY_THRESHOLD, CAPABILITY_WEB_CONTROL,
                 CAPABILITY_DEVICE_SETTINGS, f"{CAPABILITY_REMOTE_SCREEN}dho", f"ANALOG={self.features.analog}"}
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
        # (its pattern trigger takes CH1–CH4 only: the programming guide, and refused on the instrument
        # for D0–D15 also with the logic probe)
        return ()

    def has_external_trigger(self) -> bool:
        return False

    def edge_trigger_channels(self) -> list[int]:
        return list(range(16))

    def analog_trigger_channels(self) -> list[int]:
        return list(range(self.features.analog))

    def web_control_url(self) -> Optional[str]:
        """The instrument's Web Control (its screen and front panel in a browser, port 80): at the
        network address it is reached at. Over USB: where the bridge app with its serial number
        announces itself, else the address it reports (``:LAN:IPADdress?``, that of the LAN port: a
        WLAN adapter's is not among them) if its web server answers there."""
        if not self.over_usb:
            return f"http://{self.host}/"
        address = self.network_address()
        if address is None:
            return None
        try:
            socket.create_connection((address, 80), timeout=1.0).close()
        except OSError:
            return None
        return f"http://{address}/"

    def network_address(self) -> Optional[str]:
        """The instrument's address on the network: the one it is reached at, over USB where the bridge
        app with its serial number announces itself, else the one it reports (``:LAN:IPADdress?``)."""
        if not self.over_usb:
            return self.host
        from . import beacon

        for item in beacon.seen():
            if self.serial and item.serial == self.serial:
                return item.host
        try:
            with self.connection.lock:
                address = self.connection.query(command("lan_address?"), timeout=2.0).strip().strip('"')
        except ScpiError as error:
            log.debug("The network address of the DHO could not be read: %s", error)
            return None
        parts = address.split(".")
        if len(parts) != 4 or not all(part.isdigit() for part in parts) or address in ("0.0.0.0", "127.0.0.1"):
            return None
        return address

    @property
    def bridge_app_installable(self) -> bool:
        """The bridge app can be installed on this instrument (with adb, over the network): a real DHO."""
        return not self._address.startswith("rigol-sim")

    def screenshot(self) -> bytes:
        """The instrument's screen as a PNG (``:DISPlay:DATA?``; about half a second)."""
        with self.connection.lock:
            return self.connection.query_block(command("screen?"), timeout=10.0)

    # --------------------------------------------------------------- capture
    def start_capture(self, session: CaptureSession,
                      completed_handler: Optional[CaptureCompletedHandler] = None) -> CaptureError:
        thread = self._thread
        if self._capturing or (self._abort.is_set() and thread is not None and thread.is_alive()
                               and thread is not threading.current_thread()):
            return CaptureError.BUSY  # (also a stopped capture whose thread still waits for its data)
        if session.device_settings:
            # as set on the instrument: the channels, rate, length and trigger are read in the thread
            if session.loop_count:
                return CaptureError.BAD_PARAMS
            self._capturing = True
            self._abort = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(session, [], [], completed_handler,
                                                                    self._abort, False, 0),
                                            name="openscilab-dho", daemon=True)
            self._thread.start()
            return CaptureError.NONE
        numbers = session.channel_numbers
        analog = sorted({channel.channel_number for channel in session.analog_channels})
        if (not numbers and not analog) or any(not 0 <= n < self.features.digital for n in numbers) \
                or any(not 0 <= n < self.features.analog for n in analog) or session.loop_count:
            return CaptureError.BAD_PARAMS
        total = session.pre_trigger_samples + session.post_trigger_samples
        depth = self.memory_depth(len(numbers), len(analog)) or DEPTH_DIGITAL
        if total > depth or session.frequency <= 0 or session.frequency > MAX_RATE:
            return CaptureError.BAD_PARAMS
        if session.trigger_type not in (TriggerType.IMMEDIATE, TriggerType.EDGE):
            return CaptureError.UNSUPPORTED
        if session.trigger_type == TriggerType.EDGE and (
                not 0 <= session.trigger_channel < 16 if session.trigger_analog_channel is None
                else not 0 <= session.trigger_analog_channel < self.features.analog):
            return CaptureError.BAD_PARAMS
        self._capturing = True
        self._abort = threading.Event()
        force = session.trigger_type == TriggerType.IMMEDIATE
        # (set up in the thread: a refusal arrives with its reason, e.g. no logic probe)
        self._thread = threading.Thread(target=self._run, args=(session, numbers, analog, completed_handler,
                                                                self._abort, force, total),
                                        name="openscilab-dho", daemon=True)
        self._thread.start()
        return CaptureError.NONE

    def _configure(self, session: CaptureSession, numbers: list[int], analog: list[int], total: int) -> None:
        write = self.connection.write
        self.connection.errors()  # (an earlier error is not this capture's)
        edge = session.trigger_type == TriggerType.EDGE
        trigger_analog = session.trigger_analog_channel if edge else None
        # the channel of the trigger stays on also when it is not captured
        digital_on = set(numbers) | ({session.trigger_channel} if edge and trigger_analog is None else set())
        analog_on = set(analog) | ({trigger_analog} if trigger_analog is not None else set())
        write(command("stop"))
        write(command("la_state", value="ON" if digital_on else "OFF"))
        for index in range(16):
            write(command("la_channel", n=index, value="ON" if index in digital_on else "OFF"))
        if session.threshold_voltage is not None:
            for pod in (1, 2):
                write(command("la_threshold", n=pod, value=f"{session.threshold_voltage:g}"))
        for index in range(self.features.analog):
            write(command("channel", n=index + 1, value="ON" if index in analog_on else "OFF"))
        # the logic channels come at LA_PERIOD_FACTOR times the analog period and only that part of
        # the points the instrument names holds them (the rest is old memory): captured without
        # analog channels, the instrument is set up for that many times the rate and samples
        factor = scpi.LA_PERIOD_FACTOR if numbers and not analog else 1
        frequency, total, pre_trigger = session.frequency * factor, total * factor, session.pre_trigger_samples * factor
        # the instrument has fixed memory depths: the smallest that holds the capture, over the time
        # that gives the rate asked for (the capture then has more samples than asked for)
        depth = self._set_depth(total)
        scale = depth / frequency / scpi.DIVISIONS
        if scale >= scpi.ROLL_SCALE:
            raise ScpiError(f"{session.frequency:g} Sa/s with a memory of {depth // factor} points needs {scale:g} s/div: from "
                            f"{scpi.ROLL_SCALE * 1000:g} ms/div the oscilloscope rolls and takes no single capture. "
                            f"Use a rate above {depth / factor / (scpi.DIVISIONS * scpi.ROLL_SCALE):g} Sa/s or fewer samples")
        rate = self._set_timebase(scale, frequency)
        # the trigger is at the centre of the screen; the offset moves it to the pre-trigger samples
        write(command("timebase_offset", value=f"{(depth / 2 - pre_trigger) / rate:.6e}"))
        if session.trigger_type == TriggerType.IMMEDIATE:
            pass  # :SINGle makes the sweep SINGLE whatever it was: the capture is forced (_wait_for_trigger)
        elif trigger_analog is not None:
            write(command("trigger_mode", value="EDGE"))
            write(command("edge_source", value=f"CHANnel{trigger_analog + 1}"))
            write(command("edge_slope", value="NEGative" if session.trigger_inverted else "POSitive"))
            write(command("edge_level", value=f"{session.trigger_level:g}"))
            write(command("trigger_sweep", value="NORMal"))
            # the instrument keeps the level within the channel's range on the screen
            level = float(self.connection.query(command("edge_level?")))
            if abs(level - session.trigger_level) > max(1e-3, 0.01 * abs(session.trigger_level)):
                raise ScpiError(f"CH{trigger_analog + 1} cannot trigger at {session.trigger_level:g} V: the level has "
                                f"to lie within the range of the channel on the oscilloscope's screen (it took "
                                f"{level:g} V); set the volts per division or the offset of CH{trigger_analog + 1} "
                                "on the oscilloscope")
        else:
            write(command("trigger_mode", value="EDGE"))
            write(command("edge_source", value=f"D{session.trigger_channel}"))
            write(command("edge_slope", value="NEGative" if session.trigger_inverted else "POSitive"))
            write(command("trigger_sweep", value="NORMal"))
        errors = self.connection.errors()
        if errors:
            log.warning("The DHO reported while configuring: %s", "; ".join(errors))

    def _take_device_channels(self, session: CaptureSession) -> tuple[list[int], list[int]]:
        """``session.device_settings``: the channels that are on at the instrument become the session's
        (logic channels keep their names); its rate, depth and trigger stay as they are set there."""
        query = self.connection.query
        self.connection.errors()  # (an earlier error is not this capture's)
        numbers = []
        if query(command("la_state?")).strip() in ("1", "ON"):
            numbers = [index for index in range(self.features.digital)
                       if query(command("la_channel?", n=index)).strip() in ("1", "ON")]
        analog = [index for index in range(self.features.analog)
                  if query(command("channel?", n=index + 1)).strip() in ("1", "ON")]
        if not numbers and not analog:
            raise ScpiError("no channel is on at the oscilloscope")
        names = {channel.channel_number: channel for channel in session.capture_channels}
        session.capture_channels = [names.get(number) or AnalyzerChannel(channel_number=number) for number in numbers]
        analog_names = {channel.channel_number: channel for channel in session.analog_channels}
        session.analog_channels = [analog_names.get(number) or AnalogChannel(channel_number=number)
                                   for number in analog]
        return numbers, analog

    def _set_timebase(self, scale: float, frequency: float) -> float:
        """Sets the time base ``scale`` (s/div) and returns the sample rate it gave, which is not below ``frequency``.

        The rate is not depth / (10 · scale): the DHO924S (NK v0.5.2) spreads the depth over about
        16 divisions (with the logic analyzer on, over twice that) and its rates are steps, so a
        rate asked for can come out at a third. The rate is read back and the time base shortened
        until it is not below what was asked for (the capture then has the depth at a higher
        rate, which :meth:`_run` reads back as well)."""
        write, query = self.connection.write, self.connection.query
        rate = 0.0
        # (``:ACQuire:SRATe?`` follows the time base only while the instrument acquires: not when
        # stopped, and not while it waits for a trigger - after a capture the sweep is SINGLE. The
        # trigger is set up after this, and :SINGle sets the sweep anyway.)
        write(command("trigger_sweep", value="AUTO"))
        write(command("run"))
        try:
            for _ in range(scpi.RATE_TRIES):
                write(command("timebase_scale", value=f"{scale:.6e}"))
                rate = self._settled_rate()
                if rate >= frequency * (1 - 1e-6) or rate <= 0:
                    return rate if rate > 0 else frequency
                scale *= rate / frequency * scpi.RATE_SHRINK
        finally:
            write(command("stop"))
        log.warning("The DHO gives %g Sa/s at %g s/div, not the %g Sa/s asked for", rate, scale, frequency)
        return rate

    def _settled_rate(self) -> float:
        """``:ACQuire:SRATe?`` once it is the same :data:`.scpi.RATE_STABLE_READS` times in a row: after a
        change of the time base the instrument answers with the old rate for a while (measured: up to
        well over 0.25 s after a large change)."""
        query = self.connection.query
        deadline = time.monotonic() + scpi.RATE_SETTLE_TIMEOUT_S
        time.sleep(scpi.RATE_SETTLE_S)
        values = [float(query(command("sample_rate?")))]
        while time.monotonic() < deadline:
            if len(values) >= scpi.RATE_STABLE_READS and len(set(values[-scpi.RATE_STABLE_READS:])) == 1:
                break
            time.sleep(scpi.RATE_SETTLE_S)
            values.append(float(query(command("sample_rate?"))))
        return values[-1]

    def _set_depth(self, total: int) -> int:
        """Sets the smallest memory depth that holds ``total`` samples and returns it (points).

        What a setting gives is read back, not assumed: the modified firmware NK v0.5.2 takes a
        tenth or a twentieth of the depth asked for (:data:`.scpi.MEMORY_DEPTHS`). What each setting gave is
        kept, so a later capture asks once."""
        connection = self.connection
        best: Optional[tuple[int, int]] = None  # (setting, points) the largest seen
        for setting in scpi.MEMORY_DEPTHS:
            known = self._depths.get(setting)
            if known is not None and known < total:
                best = max(best or (setting, known), (setting, known), key=lambda item: item[1])
                continue  # (too small: no need to ask again)
            connection.write(command("memory_depth", value=scpi.depth_text(setting)))
            if connection.errors():  # a setting the instrument does not take ("Data out of range")
                self._depths[setting] = 0
                continue
            answer = connection.query(command("memory_depth?"))
            points = scpi.parse_depth(answer)
            if points is None:
                raise ScpiError(f"the memory depth is {answer} instead of {scpi.depth_text(setting)}")
            self._depths[setting] = points
            if points >= total:
                return points
            best = (setting, points)
        if best is None:
            raise ScpiError("the oscilloscope takes no memory depth")
        raise ScpiError(f"{total} samples are more than the {best[1]} the oscilloscope holds with these channels")

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

    def _run(self, session, numbers, analog, handler, abort: threading.Event, force: bool = False,
             total: int = 0) -> None:
        try:
            with self.connection.lock:
                if session.device_settings:
                    numbers, analog = self._take_device_channels(session)
                else:
                    self._configure(session, numbers, analog, total or session.pre_trigger_samples
                                    + session.post_trigger_samples)
                if abort.is_set():
                    return
                self.connection.write(command("single"))
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
        """The samples of ``source`` (``D<n>``, ``CHANnel<n>``) and its preamble (a logic channel's
        ``points`` are those it holds, see :data:`.scpi.LA_PERIOD_FACTOR`; its ``xinc`` is unchanged,
        :meth:`_read_direct` corrects it, in the way the capture needs). A logic channel comes as
        16 bit words (0 or 1), returned as 0/1 bytes."""
        connection = self.connection
        digital = source.startswith("D")
        connection.write(command("wave_source", value=source))
        if digital and (connection.errors() or
                        connection.query(command("wave_source?")).strip().upper() != source.upper()):
            raise ScpiError(f"the oscilloscope does not give out {source} (it refuses it as :WAVeform:SOURce; "
                            "without the logic probe, and the modified firmware NK v0.5.2 also with it unless its "
                            "library is patched): capture the analog channels, D0–D15 can still trigger them")
        # the points of the preamble are those of the window (STARt to STOP, still set by the read
        # before, maybe beyond this acquisition so neither can be set): switching the mode opens it
        # over the whole acquisition
        connection.write(command("wave_mode_normal"))
        connection.write(command("wave_mode"))
        connection.write(command("wave_format_word" if digital else "wave_format"))
        preamble = scpi.parse_preamble(connection.query(command("wave_preamble?")))
        points = int(preamble["points"])
        if digital:
            # the instrument writes only the first part of what it names: the rest of the words is
            # old memory (it shows up as stretches of one level that are not in the signal)
            points //= scpi.LA_PERIOD_FACTOR
            preamble["points"] = points
        data = np.zeros(points, dtype=np.uint8)
        for start in range(0, points, scpi.MAX_POINTS_PER_READ):
            if abort.is_set():
                break
            stop = min(start + scpi.MAX_POINTS_PER_READ, points)
            wanted = (stop - start) * (2 if digital else 1)
            # the instrument says STOP a moment before the waveform is there to read: it then
            # answers with an empty block ("Wave data is empty" in its log), which must not be
            # taken for a signal of zeros
            for _ in range(DATA_ATTEMPTS):
                # STARt never behind STOP: STOP first when the window moves on
                connection.write(command("wave_stop", value=stop))
                connection.write(command("wave_start", value=start + 1))
                connection.write(command("wave_stop", value=stop))
                block = connection.query_block(command("wave_data?"), timeout=30.0)
                if len(block) >= wanted or abort.wait(DATA_RETRY_S):
                    break
            if len(block) < wanted and not abort.is_set():
                raise ScpiError(f"the oscilloscope gave {len(block)} of the {wanted} bytes of {source} "
                                f"(points {start + 1} to {stop})")
            if digital:
                words = np.frombuffer(block[:len(block) // 2 * 2], dtype="<u2")
                data[start:start + len(words)] = words != 0
            else:
                data[start:start + len(block)] = np.frombuffer(block, dtype=np.uint8)
        return data, preamble

    def _read_direct(self, session, numbers, analog, abort) -> None:
        sources = []  # (name, preamble)
        digital_entries = []  # (channel, preamble), the capture's logic channels
        for channel in session.capture_channels:
            values, preamble = self._read_source(f"D{channel.channel_number}", abort)
            channel.samples = (values != 0).astype(np.uint8)
            digital_entries.append((channel, preamble))
            sources.append((f"D{channel.channel_number}", preamble))
        for channel in session.analog_channels:
            values, preamble = self._read_source(f"CHANnel{channel.channel_number + 1}", abort)
            self.set_analog(channel, values, preamble["yinc"], preamble["yorig"], preamble["yref"])
            sources.append((ANALOG[channel.channel_number], preamble))
        if digital_entries and session.analog_channels:
            # with the analog channels: the logic channels' real sample period is twice the
            # analog one's (measured), but the instrument's own preamble for them already matches
            # the analog one's (same xinc, same trigger sample, as if both were sampled alike):
            # repeating every logic sample lines it up with the analog channels' finer time axis,
            # not changing the (already matching) preamble.
            # they also come a few samples late or early (scpi.LA_SKEW): moved in line
            analog_xinc = next(preamble["xinc"] for name, preamble in sources if not name.startswith("D"))
            skew = scpi.la_skew(1.0 / analog_xinc) if analog_xinc > 0 else 0
            for channel, preamble in digital_entries:
                channel.samples = scpi.shift_samples(np.repeat(channel.samples, scpi.LA_PERIOD_FACTOR), skew)
                preamble["points"] = int(preamble["points"]) * scpi.LA_PERIOD_FACTOR
        else:
            # alone, the instrument's own preamble understates their real sample period by
            # LA_PERIOD_FACTOR (measured): session.frequency (below) needs the real one. This
            # also halves the trigger sample the same preamble gives, which is correct too
            # (measured through a known edge's position): _configure set the instrument up for
            # twice the rate and samples, so the capture has the samples, rate and pre-trigger
            # samples that were asked for.
            for _, preamble in digital_entries:
                preamble["xinc"] *= scpi.LA_PERIOD_FACTOR
        if sources:
            points, trigger = same_timing(sources)
            # the rate of the data: that of the logic channels is not the one ``:ACQuire:SRATe?`` gives
            xinc = sources[0][1]["xinc"]
            if xinc > 0:
                session.frequency = int(round(1.0 / xinc))
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
            from .controls import DhoSettings
            from .generator import DhoCache, DhoGenerator

            self._facets = ([DhoSettings(self)] + ([DhoGenerator(self)] if self.features.generator else [])
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
