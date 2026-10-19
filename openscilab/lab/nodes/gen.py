# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generator nodes: waveforms, arbitrary points, patterns, protocol transmitters and replays on the
generator outputs of instruments (``GeneratorFacet``)."""

from __future__ import annotations

import functools
import math
import os
from typing import Any, Optional

import numpy as np

from ...core import signals
from ...core import waveform as waves
from ...core.instrument import GeneratorFacet, InstrumentError
from ..engine.runtime import NodeError, NodeRuntime
from .device import DEVICE_INPUT
from .registry import In, Out, Param, node

def _output_params(kinds: str = "") -> list:
    """'output' (offering the outputs of ``kinds``, e.g. ``pattern`` or ``analog+square``) and 'duration'."""
    return [Param("output", "str", description="The generator output (empty: the first that fits)",
                  suggest="outputs" + (f":{kinds}" if kinds else ""), label="output"),
            Param("duration", "quantity", 0, "s", description="Stop after it (0: play until the flow ends)",
                  label="duration", advanced=True)]


_OUTPUT_PARAMS = _output_params()
_OUTPUTS = [Out("sync", signals.EVENT, "The output started (its device time): arm a capture with it"),
            Out("done", signals.EVENT, "The output stopped")]


class _GeneratorNode(NodeRuntime):
    """Starts waveforms on an output; stops it after 'duration' and when the flow ends."""

    async def setup(self) -> None:
        self._generator: Optional[GeneratorFacet] = None
        self._output: Optional[str] = None

    def generator(self) -> GeneratorFacet:
        instrument = self.ctx.instrument()
        facet = instrument.facet(GeneratorFacet)
        if facet is None:
            raise NodeError(f"{self.node.id}: {instrument.name} has no generator")
        return facet

    def output_for(self, generator: GeneratorFacet, waveform: waves.Waveform) -> str:
        wanted = self.p("output")
        if wanted:
            return str(wanted)
        kind = "pattern" if waveform.is_pattern else "analog"
        for info in generator.outputs():
            if info.kind == kind or (kind == "analog" and info.kind == "square"
                                     and waveform.kind in (waves.SQUARE, waves.PULSE)):
                return info.name
        raise NodeError(f"{self.node.id}: {self.ctx.instrument().name} has no {kind} output")

    def fit(self, waveform: waves.Waveform, output) -> waves.Waveform:
        """``waveform`` as it plays on ``output`` (an ``OutputInfo``); nodes adapt what was not given."""
        return waveform

    async def play(self, waveform: waves.Waveform, done: Any = None) -> None:
        """Plays ``waveform``; after 'duration' (or its own length) it stops and 'done' says ``done``
        (default: the output)."""
        generator = self.generator()
        output = self.output_for(generator, waveform)
        try:
            waveform = self.fit(waveform, generator.output(output))
            started = await self.ctx.device_call(self.ctx.instrument(), generator.start, output, waveform)
        except InstrumentError as error:
            raise NodeError(str(error)) from None
        self._generator, self._output = generator, output
        at = float(started) if isinstance(started, (int, float)) else self.ctx.now()
        self.ctx.emit("sync", signals.Event(times=[at], data=[waveform.describe()]))
        duration = self.q("duration") or 0.0
        if not duration and math.isfinite(waveform.duration) and waveform.repeat > 0:
            duration = waveform.duration
        if duration > 0:
            await self.ctx.sleep(duration)
            self.stop()
            self.ctx.emit("done", signals.Event(times=[self.ctx.now()], data=[output if done is None else done]))

    def stop(self) -> None:
        if self._generator is not None and self._output is not None:
            try:
                self._generator.stop(self._output)
            except InstrumentError:
                pass
        self._generator = self._output = None

    async def finish(self) -> None:
        self.stop()

    def cleanup(self) -> None:
        self.stop()  # also after an error: the output does not keep playing


def _number(value: Any) -> float:
    if isinstance(value, (signals.Scalar, signals.Bool)):
        return float(value.value)
    if isinstance(value, signals.Event):
        return float(value.data[-1])
    return float(value)


@node("generator.waveform", title="Waveform",
      description="A standard waveform (sine, square, triangle, ramp, pulse with 'duty', DC, noise with "
                  "'amplitude' as its standard deviation) on a generator output. Values at 'frequency' and "
                  "'amplitude' change it while it plays; 'start' starts it (without a wire it starts with the "
                  "flow). On a square-only output (PWM) a square or pulse without 'amplitude' and 'offset' "
                  "swings over the output's whole range.",
      inputs=[DEVICE_INPUT, In("start", signals.EVENT, "Start it (without a wire: with the flow)"),
              In("frequency", signals.ANY, "A new frequency while it plays (Hz)"),
              In("amplitude", signals.ANY, "A new amplitude while it plays (V)")],
      outputs=_OUTPUTS,
      params=_output_params("analog+square") + [
          Param("kind", "choice", waves.SINE, choices=waves.ANALOG_KINDS[:-1], label="shape",
                description="Sine, square, triangle, ramp, pulse, DC or noise"),
          Param("frequency", "quantity", "1 kHz", "Hz", label="frequency", description="Periods per second"),
          Param("amplitude", "quantity", "1 V", "V", label="amplitude",
                description="Half the swing from lowest to highest (noise: the standard deviation)"),
          Param("offset", "quantity", "0 V", "V", label="offset", description="The middle of the swing"),
          Param("duty", "float", 0.5, only_if={"kind": (waves.SQUARE, waves.PULSE)}, label="duty cycle",
                minimum=0.0, maximum=1.0, description="The share of the period at the high level: 0..1")],
      icon="wave", summary=("kind", "frequency", "amplitude"), keywords=("sine", "square", "function generator", "awg"))
class WaveformNode(_GeneratorNode):
    async def setup(self) -> None:
        await super().setup()
        self.frequency = self.q("frequency")
        self.amplitude = self.q("amplitude")

    def waveform(self) -> waves.Waveform:
        kind = str(self.p("kind", waves.SINE))
        if kind not in waves.ANALOG_KINDS[:-1]:
            raise NodeError(f"kind: {kind!r} is none of {', '.join(waves.ANALOG_KINDS[:-1])}")
        try:
            return waves.Waveform(kind=kind, frequency=self.frequency or 0.0, amplitude=self.amplitude or 0.0,
                                  offset=self.q("offset") or 0.0, duty=float(self.p("duty", 0.5)))
        except waves.WaveformError as error:
            raise NodeError(str(error)) from None

    def fit(self, waveform: waves.Waveform, output) -> waves.Waveform:
        given = self.node.params
        if output.kind == "square" and "amplitude" not in given and "offset" not in given \
                and not self.ctx.wired("amplitude"):
            low, high = output.voltage_range
            return waves.Waveform(kind=waveform.kind, frequency=waveform.frequency, amplitude=(high - low) / 2,
                                  offset=(high + low) / 2, duty=waveform.duty)
        return waveform

    async def run(self) -> None:
        if not self.ctx.wired("start"):
            await self.play(self.waveform())

    async def on_input(self, port: str, value: Any) -> None:
        if port == "frequency":
            self.frequency = _number(value)
        elif port == "amplitude":
            self.amplitude = _number(value)
        if port == "start" or self._generator is not None:
            await self.play(self.waveform())


@node("generator.arbitrary", title="Arbitrary",
      description="Arbitrary points on an analog output: a formula over one pass (x from 0 to 1, numpy "
                  "functions), a CSV file (one column of volts, or time and volts), or the analog signal "
                  "arriving at 'signal'. 'frequency' sets the passes per second; without it a CSV file with "
                  "times and a signal play at their own rate.",
      inputs=[DEVICE_INPUT, In("signal", signals.ANALOG, "The points to play (an analog signal)")],
      outputs=_OUTPUTS,
      params=_output_params("analog") + [
          Param("formula", "str", "sin(2*pi*x)", unless_wired="signal", only_if={"file": None}, label="formula",
                description="Volts over one pass, x from 0 to 1 (numpy functions)"),
          Param("file", "path", unless_wired="signal", label="CSV file",
                description="One column of volts, or time and volts"),
          Param("frequency", "quantity", "1 kHz", "Hz", label="frequency", description="Passes per second"),
          Param("points", "int", 1024, unless_wired="signal", only_if={"file": None}, label="points",
                description="Points of one pass of the formula", minimum=2, advanced=True)],
      icon="wave", summary=("formula", "frequency"), keywords=("arbitrary", "awg", "formula", "csv"))
class ArbitraryNode(_GeneratorNode):
    def given_frequency(self) -> Optional[float]:
        """'frequency' when it was written: else a CSV file's times or the signal's rate set it."""
        return self.q("frequency") if self.node.params.get("frequency") not in (None, "") else None

    async def run(self) -> None:
        if self.ctx.wired("signal"):
            return
        try:
            if self.p("file"):
                waveform = waves.from_csv(self.ctx.path(str(self.p("file"))), frequency=self.given_frequency())
            else:
                waveform = waves.from_formula(str(self.p("formula")), self.q("frequency") or 1000.0,
                                              int(self.p("points", 1024)))
        except (waves.WaveformError, OSError) as error:
            raise NodeError(str(error)) from None
        await self.play(waveform)

    async def on_input(self, port: str, value: Any) -> None:
        await self.play(waves.from_analog(value, frequency=self.given_frequency()))


@node("generator.pattern", title="Pattern",
      description="A digital pattern on a pattern output: SDL text per pin ('tracks', e.g. {D0: 'l5;h5;'}) "
                  "at 'rate', or the capture arriving at 'capture'. 'repeat': passes (0: until stopped).",
      inputs=[DEVICE_INPUT, In("capture", signals.CAPTURE, "A capture to play as the pattern")],
      outputs=_OUTPUTS,
      params=_output_params("pattern") + [
          Param("tracks", "dict", description="SDL per pin: Edit pattern… builds them from blocks (clock, square "
                                              "wave, bytes, ...)",
                suggest="pins:DOUT", editor="pattern", unless_wired="capture", label="pattern"),
          Param("rate", "quantity", "1 MHz", "Hz", unless_wired="capture", label="rate",
                description="Steps of the pattern per second"),
          Param("repeat", "int", 0, description="Passes (0: until stopped)", label="repeat", minimum=0)],
      icon="channels", summary=("tracks", "rate", "repeat"), keywords=("digital", "sdl", "bits", "clock"))
class PatternNode(_GeneratorNode):
    async def run(self) -> None:
        if self.ctx.wired("capture"):
            return
        tracks = self.p("tracks") or {}
        if not tracks:
            raise NodeError(f"{self.node.id}: no tracks")
        try:
            waveform = waves.pattern_from_sdl({str(pin): str(text) for pin, text in tracks.items()},
                                              self.q("rate"), repeat=int(self.p("repeat", 0)))
        except waves.WaveformError as error:
            raise NodeError(f"{self.node.id}: {error}") from None
        await self.play(waveform)

    async def on_input(self, port: str, value: Any) -> None:
        waveform = waves.pattern_from_capture(value)
        waveform.repeat = int(self.p("repeat", 0))
        await self.play(waveform)


def _payload(value: Any, default: Any) -> Any:
    """The bytes to send for a value at 'data': its text or bytes, a number as one byte; an event
    without data (a tick) sends the 'data' parameter."""
    if isinstance(value, signals.Event):
        value = value.data[-1] if value.data else None
    if value is None:
        return default
    if isinstance(value, (signals.Scalar, signals.Bool)):
        return bytes([int(value.value) & 0xFF])
    if isinstance(value, (int, np.integer)):
        return bytes([int(value) & 0xFF])
    return value


class _TransmitNode(_GeneratorNode):
    #: ``uart``, ``spi`` or ``i2c``
    protocol = ""

    def tracks(self, data: Any) -> dict[str, np.ndarray]:
        raise NotImplementedError

    def tx_pins(self) -> dict[str, str]:
        """The pins by their role (``tx``; ``sck``, ``mosi``, ``miso``, ``cs``; ``scl``, ``sda``)."""
        raise NotImplementedError

    def tx_settings(self) -> dict[str, Any]:
        return {}

    async def run(self) -> None:
        if not self.ctx.wired("data"):
            await self.send(self.p("data", ""))

    async def on_input(self, port: str, value: Any) -> None:
        await self.send(_payload(value, self.p("data", "")))

    async def send(self, data: Any) -> None:
        instrument = self.ctx.instrument()
        generator = instrument.facet(GeneratorFacet)
        if generator is not None and not self.p("output") and generator.transmits(self.protocol):
            # the device sends it with its own UART, SPI or I²C (and an I²C or SPI target answers)
            try:
                payload = waves._as_bytes(data)
                send = functools.partial(generator.transmit, self.protocol, payload, self.tx_pins(),
                                         **self.tx_settings())
                received = await self.ctx.device_call(instrument, send)
            except (InstrumentError, ValueError, TypeError) as error:
                raise NodeError(str(error)) from None
            self.ctx.emit("sync", signals.Event(times=[self.ctx.now()],
                                                data=[f"{self.protocol.upper()} {len(payload)} byte(s)"]))
            # a device that sends in the background says when it is through (a real one answers then)
            end = getattr(generator, "transmit_end", None)
            if end is not None and end > self.ctx.now():
                await self.ctx.sleep_until(end)
            now = self.ctx.now()
            if self.protocol != "uart":  # (UART only sends)
                self.ctx.emit("received", signals.Event(times=[now], data=[bytes(received)]))
            self.ctx.emit("done", signals.Event(times=[now], data=[len(payload)]))
            return
        try:
            tracks = self.tracks(data)
            count = len(waves._as_bytes(data))
        except (waves.WaveformError, ValueError, TypeError) as error:
            raise NodeError(str(error)) from None
        waveform = waves.pattern(tracks, self.q("rate"), repeat=1)
        await self.play(waveform, done=count)


_TX_PARAMS = _output_params("pattern") + [
    Param("data", "str", "Hello", description="Text, or bytes as a list", label="data", unless_wired="data"),
    Param("rate", "quantity", "1 MHz", "Hz", label="pattern rate", advanced=True,
          description="Samples per second of the pattern (devices without a UART, SPI or I²C of their own)")]
_TX_INPUT = In("data", signals.ANY, "Send each value (text, bytes, numbers)")
_TX_OUTPUTS = [Out("sync", signals.EVENT, "The transmission started"),
               Out("done", signals.EVENT, "The transmission is through (data: the bytes sent)"),
               Out("received", signals.EVENT, "The bytes read back (SPI: on MISO, I²C: 'read' bytes; "
                                              "with the device's own SPI or I²C)")]


@node("generator.uart", title="UART",
      description="Sends the bytes of 'data' (or of each value at 'data') as 8N1 UART frames on 'pin': with "
                  "the device's own UART where it has one (TX_UART), else as a pattern on a pattern output.",
      inputs=[DEVICE_INPUT, _TX_INPUT], outputs=_TX_OUTPUTS,
      params=_TX_PARAMS + [Param("pin", "str", "D0", suggest="pins:DOUT", label="TX pin",
                                 description="The pin that sends"),
                           Param("baud", "quantity", "115200 Hz", "Hz", label="baud", description="Bits per second")],
      icon="arrow-right", summary=("pin", "baud", "data"), keywords=("send", "serial", "tx", "transmit"))
class UartNode(_TransmitNode):
    protocol = "uart"

    def tracks(self, data):
        return waves.uart_tracks(data, self.q("baud"), self.q("rate"), pin=str(self.p("pin", "D0")))

    def tx_pins(self):
        return {"tx": str(self.p("pin", "D0"))}

    def tx_settings(self):
        return {"baud": self.q("baud")}


@node("generator.spi", title="SPI",
      description="Sends 'data' as SPI mode 0 (MSB first) on the pins 'pins' (CS, SCK, MOSI); with the "
                  "device's own SPI (TX_SPI) the bytes on 'miso' come back at 'received'.",
      inputs=[DEVICE_INPUT, _TX_INPUT], outputs=_TX_OUTPUTS,
      params=_TX_PARAMS + [Param("pins", "list", ["D0", "D1", "D2"], suggest="pins:DOUT", label="pins (CS, SCK, MOSI)",
                                 description="In this order: chip select, clock, data out"),
                           Param("miso", "str", "", suggest="pins:DIN", label="MISO pin",
                                 description="The pin data comes back on (the device's own SPI)"),
                           Param("clock", "quantity", "100 kHz", "Hz", label="clock", description="Bits per second")],
      icon="arrow-right", summary=("pins", "clock", "data"), keywords=("send", "transmit", "mosi"))
class SpiNode(_TransmitNode):
    protocol = "spi"

    def _pins(self) -> list[str]:
        pins = [str(pin) for pin in self.p("pins") or ["D0", "D1", "D2"]]
        if len(pins) != 3:
            raise ValueError("pins: CS, SCK and MOSI")
        return pins

    def tracks(self, data):
        return waves.spi_tracks(data, self.q("clock"), self.q("rate"), pins=tuple(self._pins()))

    def tx_pins(self):
        cs, sck, mosi = self._pins()
        pins = {"cs": cs, "sck": sck, "mosi": mosi}
        if self.p("miso"):
            pins["miso"] = str(self.p("miso"))
        return pins

    def tx_settings(self):
        return {"frequency": self.q("clock")}


@node("generator.i2c", title="I²C",
      description="Writes 'data' to the I²C 'address' on the pins 'pins' (SCL, SDA) and reads 'read' bytes "
                  "back (devices with their own I²C, TX_I2C; else the write as a pattern).",
      inputs=[DEVICE_INPUT, _TX_INPUT], outputs=_TX_OUTPUTS,
      params=_TX_PARAMS + [Param("pins", "list", ["D0", "D1"], suggest="pins:DOUT", label="pins (SCL, SDA)",
                                 description="In this order: clock, data"),
                           Param("address", "any", 0x50, description="The 7 bit address: 72 or 0x48",
                                 label="address"),
                           Param("read", "int", 0, label="read bytes", minimum=0,
                                 description="Bytes read back after the write"),
                           Param("clock", "quantity", "100 kHz", "Hz", label="clock", description="Bits per second")],
      icon="arrow-right", summary=("address", "pins", "data"), keywords=("send", "write", "i2c", "twi", "sensor"))
class I2cNode(_TransmitNode):
    protocol = "i2c"

    def address(self) -> int:
        value = self.p("address", 0x50)
        try:
            return int(value, 0) if isinstance(value, str) else int(value)  # ("0x48" too)
        except ValueError:
            raise NodeError(f"address: {value!r} is no number (72, 0x48)") from None

    def _pins(self) -> list[str]:
        pins = [str(pin) for pin in self.p("pins") or ["D0", "D1"]]
        if len(pins) != 2:
            raise ValueError("pins: SCL and SDA")
        return pins

    def tracks(self, data):
        return waves.i2c_tracks(self.address(), data, self.q("clock"), self.q("rate"), pins=tuple(self._pins()))

    def tx_pins(self):
        scl, sda = self._pins()
        return {"scl": scl, "sda": sda}

    def tx_settings(self):
        return {"address": self.address(), "frequency": self.q("clock"), "read": int(self.p("read", 0))}


@node("generator.replay", title="Replay",
      description="Plays a capture: its digital channels on a pattern output under their own names, or only "
                  "the channels of 'pins' under new names (e.g. {CH1: D0}); or one analog channel ('analog') on "
                  "an analog output.",
      inputs=[DEVICE_INPUT, In("capture", signals.CAPTURE, "The capture to play")], outputs=_OUTPUTS,
      params=_OUTPUT_PARAMS + [Param("pins", "dict", description="Capture channel → output pin",
                                     suggest="upstream_channels", editor="mapping", values="pins:DOUT", label="pins"),
                               Param("analog", "str", description="The analog channel to play (instead of the pins)",
                                     suggest="upstream_channels", label="analog channel"),
                               Param("repeat", "int", 1, label="repeat", description="Passes (0: until stopped)",
                                     minimum=0)],
      icon="repeat", summary=("pins", "analog", "repeat"), keywords=("play", "capture", "playback"))
class ReplayNode(_GeneratorNode):
    async def on_input(self, port: str, value: Any) -> None:
        repeat = int(self.p("repeat", 1))
        analog = self.p("analog")
        if analog:
            if analog not in value.analog:
                raise NodeError(f"{self.node.id}: the capture has no analog channel {analog!r}")
            waveform = waves.from_points(value.analog[analog], rate=value.rate, repeat=repeat)
        else:
            mapping = {str(key): str(pin) for key, pin in (self.p("pins") or {}).items()}
            names = list(mapping) or list(value.digital)
            try:
                waveform = waves.pattern_from_capture(value, names)
            except waves.WaveformError as error:
                raise NodeError(f"{self.node.id}: {error}") from None
            waveform.tracks = {mapping.get(name, name): levels for name, levels in waveform.tracks.items()}
            waveform.repeat = repeat
        await self.play(waveform)


@node("generator.file", title="Waveform file",
      description="Plays the waveform arriving at 'waveform' (from a *.wave.yaml 'file' without a wire); "
                  "'sync' marks every start, e.g. to arm a capture.",
      inputs=[DEVICE_INPUT, In("waveform", signals.ANY, "A waveform to play (instead of the file)"),
              In("stop", signals.EVENT, "Stop playing")], outputs=_OUTPUTS,
      params=_OUTPUT_PARAMS + [Param("file", "path", description="A *.wave.yaml or *.sdl file", unless_wired="waveform",
                                     label="file"),
                               Param("pin", "str", "D0", description="A *.sdl file: the pin it plays on",
                                     suggest="pins:DOUT", only_if={"file": "*.sdl"}, label="pin"),
                               Param("rate", "quantity", "1 MHz", "Hz", description="A *.sdl file: its sample rate",
                                     only_if={"file": "*.sdl"}, label="rate")],
      icon="play", summary=("file", "output"), keywords=("wave.yaml", "sdl", "play"))
class OutputNode(_GeneratorNode):
    async def run(self) -> None:
        if self.ctx.wired("waveform"):
            return
        path = self.p("file")
        if not path:
            raise NodeError(f"{self.node.id}: no waveform (a file or a wire at 'waveform')")
        path = self.ctx.path(str(path))
        if not os.path.exists(path):
            raise NodeError(f"{self.node.id}: no file {path}")
        try:
            waveform = waves.load(path, pin=str(self.p("pin", "D0")), rate=self.q("rate") or 1e6)
        except waves.WaveformError as error:
            raise NodeError(str(error)) from None
        await self.play(waveform)

    async def on_input(self, port: str, value: Any) -> None:
        if port == "stop":
            self.stop()
            self.ctx.emit("done", signals.Event(times=[self.ctx.now()], data=[None]))
            return
        if not isinstance(value, waves.Waveform):
            raise NodeError(f"{self.node.id}: 'waveform' takes waveforms, not {type(value).__name__}")
        await self.play(value)


NODES = [cls.node_spec for cls in (WaveformNode, ArbitraryNode, PatternNode, UartNode, SpiNode, I2cNode,
                                   ReplayNode, OutputNode)]
