# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GPIO nodes: output, input, pulse, PWM and analog output of devices with those facets – and the
monitor of a device (``device.monitor``, here beside the pin nodes it shares its code with)."""

from __future__ import annotations

from typing import Any

from ...core import signals, units
from ...core.instrument import (
    MODE_OUTPUT,
    AnalogOutFacet,
    CaptureFacet,
    GpioFacet,
    InstrumentError,
    MonitorFacet,
)
from ..engine.runtime import NodeError, NodeRuntime
from .device import DEVICE_INPUT, _warn
from .registry import In, Out, Param, node


def number(value: Any) -> float:
    """A value arriving at an input as a number (scalars, truth values, events with data, plain numbers)."""
    if isinstance(value, (signals.Scalar, signals.Bool)):
        return float(value.value)
    if isinstance(value, signals.Event):
        data = [item for item in value.data if item is not None]
        if not data:
            raise NodeError("an event without a value")
        return float(data[-1])
    try:
        return float(value)
    except (TypeError, ValueError):
        raise NodeError(f"not a number: {value!r}") from None


def level_of(value: Any) -> int:
    """A logic level: 1, 0, true, false, high, low (a number: 0.5 or more is 1)."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("high", "on", "true", "h"):
            return 1
        if text in ("low", "off", "false", "l"):
            return 0
    try:
        return 1 if float(value) >= 0.5 else 0
    except (TypeError, ValueError):
        raise NodeError(f"level: {value!r} is no level (1, 0, high, low)") from None


def _pin_lists(instrument) -> tuple[list[str], list[str]]:
    """The digital pins and the analog inputs of ``instrument``, by name, each in device order."""
    capture = instrument.facet(CaptureFacet)
    driver = getattr(capture, "driver", None)
    analog = list(driver.analog_channel_names()) if driver is not None and getattr(
        driver, "analog_channel_count", 0) else []
    digital = [info.name for info in instrument.pins() if info.name not in analog]
    return digital, analog


def pin_names(instrument) -> list[str]:
    """The pins of ``instrument`` and its analog inputs, by name."""
    digital, analog = _pin_lists(instrument)
    return digital + [name for name in analog if name not in digital]


def _looks_analog(name: str) -> bool:
    """``True`` for a name that reads as an analog input (``A0``, ``AI3``, ``AIN2``)."""
    upper = name.upper()
    return any(upper.startswith(prefix) and upper[len(prefix):].isdigit()
               for prefix in ("A", "AI", "AIN"))


def match_pins(instrument, names, analog: bool = False, warn=None) -> list[tuple[str, str]]:
    """``(name asked for, pin of the device)`` for each of ``names``.

    A name the device does not have is matched to the next of its pins (or analog inputs, with
    ``analog``) that no earlier name in the list took, so a flow written for one device runs on
    another; the substitution is warned about. The label keeps the name that was asked for.
    """
    digital, analogs = _pin_lists(instrument)
    available = analogs if analog else digital
    known = set(digital) | set(analogs)
    kind = "analog input" if analog else "pin"
    result, used = [], set()
    for raw in names or []:
        name = str(raw)
        if name in known:
            result.append((name, name))
            used.add(name)
            continue
        same = next((pin for pin in available if pin.lower() == name.lower()), None)
        pick = same or next((pin for pin in available if pin not in used), None)
        if pick is None:
            result.append((name, name))  # nothing free - let the device raise its own clear error
            continue
        used.add(pick)
        _warn(warn, f"{instrument.name} has no {kind} {name!r}; uses {pick!r} instead")
        result.append((name, pick))
    return result


def known_pin(instrument, name: str, warn=None) -> str:
    """The pin of ``instrument`` to use for ``name``.

    The name itself when the device has it; otherwise the first pin of the matching kind (an analog
    input for an analog-looking name, else a digital pin), so a flow written for one device runs on
    another. A device with no pins at all still raises.
    """
    digital, analog = _pin_lists(instrument)
    known = digital + analog
    if not known or name in known:
        return name
    same = next((item for item in known if item.lower() == name.lower()), None)
    if same is not None:
        return same
    available = analog if (name in analog or _looks_analog(name)) else digital
    pick = next((pin for pin in available), None) or known[0]
    _warn(warn, f"{instrument.name} has no pin {name!r}; uses {pick!r} instead")
    return pick


class _PinNode(NodeRuntime):
    def facet(self, cls):
        instrument = self.ctx.instrument()
        facet = instrument.facet(cls)
        if facet is None:
            raise NodeError(f"{instrument.name} has no {cls.title}")
        return facet

    def pin(self) -> str:
        pin = self.p("pin")
        if not pin:
            raise NodeError("no pin")
        return known_pin(self.ctx.instrument(), str(pin), self.ctx.log)

    async def act(self, action) -> Any:
        """A call of the device (a real one answers in a thread, see ``NodeContext.device_call``)."""
        try:
            return await self.ctx.device_call(self.ctx.instrument(), action)
        except InstrumentError as error:
            raise NodeError(str(error)) from None

    def done(self, value: float) -> None:
        self.ctx.emit("done", signals.Event(times=[self.ctx.now()], data=[value]))


@node("gpio.output", title="Output",
      description="Drives an output pin to the value arriving at 'value' (≥ 0.5 is 1). Without a wire "
                  "it sets 'level' once when the flow starts.",
      inputs=[DEVICE_INPUT, In("value", signals.ANY, "The level to drive: 0 or 1 (a number of 0.5 or more is 1)")],
      outputs=[Out("done", signals.EVENT, "The level was set (the level as its value)")],
      params=[Param("pin", "str", required=True, suggest="pins:DOUT", label="pin", description="The output pin"),
              Param("level", "choice", 1, choices=(1, 0), label="level", unless_wired="value",
                    description="The level set once at the start: 1 (high) or 0 (low)")],
      icon="power", summary=("pin", "level"), keywords=("write", "set", "digital", "high", "low", "pin"))
class WriteNode(_PinNode):
    async def run(self) -> None:
        if not self.ctx.wired("value"):
            await self.write(level_of(self.p("level", 1)))

    async def on_input(self, port: str, value: Any) -> None:
        await self.write(1 if number(value) >= 0.5 else 0)

    async def write(self, level: int) -> None:
        gpio = self.facet(GpioFacet)
        pin = self.pin()
        if gpio.mode(pin) != MODE_OUTPUT:
            await self.act(lambda: gpio.set_mode(pin, MODE_OUTPUT))
        await self.act(lambda: gpio.write(pin, level))
        self.done(level)


@node("gpio.pulse", title="Pulse",
      description="'count' pulses of 'width', one every 'period', on a pin for every value at "
                  "'trigger' (once at the start without a wire). The device times the pulses exactly.",
      inputs=[DEVICE_INPUT, In("trigger", signals.EVENT, "Pulse again for every value")],
      outputs=[Out("done", signals.EVENT, "The pulses are over")],
      params=[Param("pin", "str", required=True, suggest="pins:DOUT", label="pin", description="The output pin"),
              Param("width", "quantity", "10 ms", "s", label="width", description="How long a pulse lasts"),
              Param("level", "choice", 1, choices=(1, 0), label="level",
                    description="The level of the pulse: 1 (high) or 0 (low)"),
              Param("count", "int", 1, label="count", description="Pulses per trigger", minimum=1),
              Param("period", "quantity", "0 s", "s", only_if={"count": ">1"}, label="period",
                    description="From the start of one pulse to the start of the next")],
      icon="play", summary=("pin", "width", "count"), keywords=("strobe", "trigger", "pin"))
class PulseNode(_PinNode):
    async def run(self) -> None:
        if not self.ctx.wired("trigger"):
            await self.pulse()

    async def on_input(self, port: str, value: Any) -> None:
        await self.pulse()

    async def pulse(self) -> None:
        gpio = self.facet(GpioFacet)
        width = self.q("width")
        if not width or width <= 0:
            raise NodeError("width must be positive")
        count = int(self.p("count", 1))
        period = self.q("period") or 0.0
        if count < 1:
            raise NodeError("count: at least 1 pulse")
        if count > 1 and period <= width:
            raise NodeError("several pulses need a period longer than the width")
        level = level_of(self.p("level", 1))
        if count == 1:
            await self.act(lambda: gpio.pulse(self.pin(), width, level))
        else:
            await self.act(lambda: gpio.pulse(self.pin(), width, level, count, period))
        await self.ctx.sleep((count - 1) * period + width)
        self.done(width)


@node("gpio.pwm", title="PWM",
      description="PWM on a pin: 'frequency' and the duty cycle (0..1) from 'duty' or the parameter; "
                  "a duty of 0 stops it.",
      inputs=[DEVICE_INPUT, In("duty", signals.ANY, "The duty cycle: 0..1")],
      outputs=[Out("done", signals.EVENT, "The PWM was set (the duty cycle as its value)")],
      params=[Param("pin", "str", required=True, suggest="pins:PWM", label="pin", description="A pin that can do PWM"),
              Param("frequency", "quantity", "1 kHz", "Hz", label="frequency", description="Periods per second"),
              Param("duty", "float", 0.5, label="duty cycle", unless_wired="duty", minimum=0.0, maximum=1.0,
                    description="The share of the period at 1: 0..1 (0.25 or 25 %)")],
      icon="wave", summary=("pin", "frequency", "duty"), keywords=("pulse width", "dimmer", "motor"))
class PwmNode(_PinNode):
    async def run(self) -> None:
        if not self.ctx.wired("duty"):
            try:
                duty = units.parse(self.p("duty", 0.5))
            except units.UnitError as error:
                raise NodeError(f"duty: {error}") from None
            await self.pwm(duty)

    async def on_input(self, port: str, value: Any) -> None:
        await self.pwm(number(value))

    async def pwm(self, duty: float) -> None:
        if not 0.0 <= duty <= 1.0:
            raise NodeError(f"the duty cycle is 0..1 (0 % to 100 %), not {duty:g}")
        gpio = self.facet(GpioFacet)
        frequency = self.q("frequency")
        if not frequency or frequency <= 0:
            raise NodeError("the frequency must be positive")
        await self.act(lambda: gpio.pwm(self.pin(), frequency, duty))
        self.done(duty)


@node("gpio.input", title="Input",
      description="Reads a pin for every value at 'trigger' (once at the start without a wire): the "
                  "level of a digital pin, the voltage of an analog input ('analog').",
      inputs=[DEVICE_INPUT, In("trigger", signals.EVENT, "Read again for every value")],
      outputs=[Out("value", signals.SCALAR, "The level (0 or 1) or the voltage")],
      params=[Param("pin", "str", required=True, suggest="pins:DIN", label="pin", description="The pin to read"),
              Param("analog", "bool", False, label="analog", description="Read the voltage of an analog input")],
      icon="target", summary=("pin", "analog"), keywords=("read", "level", "voltage", "adc", "pin"))
class ReadNode(_PinNode):
    async def run(self) -> None:
        if not self.ctx.wired("trigger"):
            await self.read()

    async def on_input(self, port: str, value: Any) -> None:
        await self.read()

    async def read(self) -> None:
        pin = self.pin()
        if self.p("analog", False):
            from ...core.instrument import AnalogInFacet

            facet = self.facet(AnalogInFacet)
            values: dict = {}
            await self.act(lambda: values.update(facet.read([pin])))
            self.ctx.emit("value", signals.Scalar(name=pin, unit="V", value=values[pin], at=self.ctx.now()))
            return
        gpio = self.facet(GpioFacet)
        levels: list = []
        await self.act(lambda: levels.append(gpio.read(pin)))
        self.ctx.emit("value", signals.Scalar(name=pin, value=float(levels[0]), at=self.ctx.now()))


@node("gpio.analog_output", title="Analog output",
      description="Sets an analog output to the voltage arriving at 'value' (or the parameter once).",
      inputs=[DEVICE_INPUT, In("value", signals.ANY, "The voltage")],
      outputs=[Out("done", signals.EVENT, "The voltage was set (what the output gives as its value)")],
      params=[Param("pin", "str", required=True, suggest="pins:DAC", label="pin", description="An analog output"),
              Param("value", "quantity", "0 V", "V", unless_wired="value", label="voltage",
                    description="The voltage set once at the start")],
      icon="sliders", summary=("pin", "value"), keywords=("dac", "voltage", "set"))
class DacNode(_PinNode):
    async def run(self) -> None:
        if not self.ctx.wired("value"):
            await self.set(self.q("value") or 0.0)

    async def on_input(self, port: str, value: Any) -> None:
        await self.set(number(value))

    async def set(self, volts: float) -> None:
        facet = self.facet(AnalogOutFacet)
        pin = self.pin()
        low, high = facet.voltage_range(pin)
        if not low - 1e-9 <= volts <= high + 1e-9:
            self.ctx.log(f"{units.format_quantity(volts, 'V')} is outside {units.format_quantity(low, 'V')} to "
                         f"{units.format_quantity(high, 'V')}: limited to the range")
        result = await self.act(lambda: facet.set_voltage(pin, volts))
        # what the output gives (limited to its range, in its steps), where the device says it
        self.done(float(result) if isinstance(result, (int, float)) else min(max(volts, low), high))


def _monitor_ports(params: dict):
    names = [str(name) for name in (params.get("pins") or [])] + [str(name) for name in (params.get("analog") or [])]
    return [], [Out(name, signals.SCALAR, f"The value of {name}") for name in names]


@node("device.monitor", title="Monitor",
      description="Reads pins periodically at 'rate' for 'duration' (until the flow ends with 0): a "
                  "value per pin and report (1 s at 10 Hz: 10 reports, the first at once).",
      inputs=[DEVICE_INPUT],
      outputs=[Out("state", signals.EVENT, "Every report: the values of all pins")],
      params=[Param("pins", "list", description="The digital pins to read", suggest="pins:DIN", label="pins"),
              Param("analog", "list", description="The analog inputs to read", suggest="analog",
                    label="analog inputs"),
              Param("rate", "quantity", "10 Hz", "Hz", label="rate", description="Reports per second"),
              Param("duration", "quantity", "1 s", "s", label="duration",
                    description="How long it reads (0: until the flow ends)")],
      ports=_monitor_ports, icon="eye", summary=("pins", "analog", "rate"), keywords=("poll", "watch", "read"))
class MonitorNode(_PinNode):
    async def run(self) -> None:
        monitor = self.facet(MonitorFacet)
        rate = self.q("rate")
        if not rate or rate <= 0:
            raise NodeError(f"{self.node.id}: rate must be positive")
        device = self.ctx.instrument()
        pin_pairs = match_pins(device, self.p("pins") or [], analog=False, warn=self.ctx.log)
        analog_pairs = match_pins(device, self.p("analog") or [], analog=True, warn=self.ctx.log)
        pins = [pin for _label, pin in pin_pairs]
        analog = tuple(pin for _label, pin in analog_pairs)
        # The device answers under its own pin names; the ports (and panels bound to them) keep the
        # names the flow asked for, so map each device name back to its label when emitting.
        label_of = {pin: label for label, pin in (*pin_pairs, *analog_pairs)}
        duration = self.q("duration") or 0.0
        sample = getattr(monitor, "sample", None)
        if sample is None:
            raise NodeError(f"{self.node.id}: the monitor of {self.ctx.instrument().name} reports only while it runs")
        index = 0
        start = self.ctx.now()
        while duration <= 0 or index / rate < duration - 1e-12:
            state = None
            try:
                state = await self.ctx.device_call(device, sample, pins, analog)
            except InstrumentError as error:
                raise NodeError(str(error)) from None
            now = self.ctx.now()
            digital = {label_of.get(name, name): level for name, level in state.digital.items()}
            analog_values = {label_of.get(name, name): volts for name, volts in state.analog.items()}
            for name, level in digital.items():
                if name in self.ctx.output_ports:
                    self.ctx.emit(name, signals.Scalar(name=name, value=float(level), at=now))
            for name, volts in analog_values.items():
                if name in self.ctx.output_ports:
                    self.ctx.emit(name, signals.Scalar(name=name, unit="V", value=volts, at=now))
            self.ctx.emit("state", signals.Event(times=[now], data=[{**digital, **analog_values}]))
            index += 1
            # the n-th report at start + n / rate: reading the pins does not stretch the period
            await self.ctx.sleep_until(start + index / rate)


NODES = [spec.node_spec for spec in (WriteNode, PulseNode, PwmNode, ReadNode, DacNode, MonitorNode)]
