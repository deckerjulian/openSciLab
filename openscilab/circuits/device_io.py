# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pins of connected devices in a running circuit (hardware in the loop). No Qt.

A ``device.input`` is a pin a device reads: its level (digital) or voltage (analog) drives a net of the
circuit through an ``external`` source of the netlist. A ``device.output`` is a pin a device drives: it
follows a net of the circuit - high or low, or the voltage of an analog output.

:class:`DeviceBridge` does both in a thread of its own, :data:`INTERVAL` apart (devices answer over USB
or a network; a few hundred exchanges a second at most): it reads the inputs and hands their values to
the simulation, and writes the outputs that changed. Problems (no such device, a pin that cannot do it,
a device that stopped answering) are kept per component in :attr:`DeviceBridge.problems`. When the
bridge stops, the outputs it drove go back to safe inputs (analog outputs to 0 V).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..core.instrument import (
    MODE_INPUT,
    MODE_OUTPUT,
    PIN_ADC,
    PIN_DAC,
    PIN_DIN,
    PIN_DOUT,
    AnalogInFacet,
    AnalogOutFacet,
    GpioFacet,
    Instrument,
    PinInfo,
)
from .spice import DevicePin

log = logging.getLogger(__name__)

#: seconds between two exchanges with the devices
INTERVAL = 0.02
#: a digital output switches at half the logic supply of the circuit, this share of it apart (hysteresis)
HYSTERESIS = 0.1
#: an analog output is written again when the net moved this far (volts)
ANALOG_STEP = 0.002


def resolve(hub, name: str) -> Instrument | None:
    """The device ``name`` of the hub (by its name or address); ``""``: the only connected device."""
    if hub is None:
        return None
    name = (name or "").strip()
    if not name:
        instruments = hub.instruments()
        return instruments[0] if len(instruments) == 1 else None
    found = hub.find(name)
    if found is not None:
        return found
    return next((instrument for instrument in hub.instruments()
                 if name in (instrument.uri, instrument.name)), None)


def _capabilities(direction: str, signal: str) -> tuple[str, ...]:
    if direction == "in":
        return (PIN_ADC,) if signal == "analog" else (PIN_DIN,)
    return (PIN_DAC,) if signal == "analog" else (PIN_DOUT,)


def pin_names(instrument: Instrument | None, direction: str = "", signal: str = "") -> list[str]:
    """The free pins of ``instrument`` that can be a ``direction`` ("in"/"out") pin of ``signal`` (all with "")."""
    if instrument is None:
        return []
    gpio = instrument.facet(GpioFacet)
    pins = [pin for pin in gpio.pins() if pin.usable] if gpio is not None else []
    if not direction:
        return [pin.name for pin in pins]
    wanted = _capabilities(direction, signal or "digital")
    names = [pin.name for pin in pins if any(pin.can(capability) for capability in wanted)]
    if signal == "analog" and direction == "out" and not names:
        # (an analog output without pins of the GPIO facet: the outputs of its analog facet)
        facet = instrument.facet(AnalogOutFacet)
        names = list(getattr(facet, "pin_names", lambda: [])())
    return names


def _pin_info(instrument: Instrument, name: str) -> PinInfo | None:
    gpio = instrument.facet(GpioFacet)
    if gpio is None:
        return None
    try:
        return gpio.pin(name)
    except Exception:  # (no such pin: said by the caller)
        return None


class DeviceBridge:
    """Exchanges the values of :class:`~.spice.DevicePin`\\ s with their devices (see the module).

    ``push_inputs(values)`` gets what the devices read (external source -> volts); ``net_value(vector)``
    gives the latest voltage of a net (``None`` before it has one); ``vcc`` is the logic supply of the
    circuit (the threshold of digital outputs)."""

    def __init__(self, hub, pins: list[DevicePin], vcc: float, push_inputs: Callable[[dict[str, float]], None],
                 net_value: Callable[[str], float | None], interval: float = INTERVAL) -> None:
        self.hub = hub
        self.vcc = float(vcc)
        self.push_inputs = push_inputs
        self.net_value = net_value
        self.interval = interval
        #: component -> what is wrong with it (shown at the component)
        self.problems: dict[str, str] = {}
        #: component -> the last value read or written (volts), for showing it on the schematic
        self.values: dict[str, float] = {}
        self._lock = threading.Lock()
        self._pins: list[DevicePin] = []
        self._ready: dict[str, tuple[Instrument, PinInfo | None]] = {}
        self._written: dict[str, float] = {}  # (component -> what the output was set to)
        self._driven: dict[str, tuple[Instrument, DevicePin]] = {}  # (outputs to make safe at the end)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.set_pins(pins)

    # ------------------------------------------------------------- pins
    def set_pins(self, pins: list[DevicePin]) -> None:
        """The pins of the circuit (again, after it changed): outputs no longer in it are made safe."""
        with self._lock:
            kept = {pin.component: pin for pin in pins}
            for component, (instrument, old) in list(self._driven.items()):
                new = kept.get(component)
                if new is None or (new.device, new.pin, new.signal, new.direction) != (old.device, old.pin, old.signal,
                                                                                       old.direction):
                    self._make_safe(instrument, old)
                    del self._driven[component]
                    self._written.pop(component, None)
            self._pins = list(pins)
            self._ready = {}
            self.problems = {}

    def _prepare(self, pin: DevicePin) -> tuple[Instrument, PinInfo | None] | None:
        """The device and pin of ``pin``, set up for its use; None (and a problem) when it cannot be."""
        ready = self._ready.get(pin.component)
        if ready is not None:
            return ready
        instrument = resolve(self.hub, pin.device)
        if instrument is None:
            count = len(self.hub.instruments()) if self.hub is not None else 0
            self.problems[pin.component] = (f"no device {pin.device!r} is connected" if pin.device else
                                            "choose its device: several are connected" if count > 1 else
                                            "no device is connected (Devices)")
            return None
        if not pin.pin:
            self.problems[pin.component] = "choose the pin of the device"
            return None
        info = _pin_info(instrument, pin.pin)
        wanted = _capabilities(pin.direction, pin.signal)
        analog_out = pin.direction == "out" and pin.signal == "analog"
        if info is None and not (analog_out and instrument.facet(AnalogOutFacet) is not None):
            self.problems[pin.component] = f"{instrument.name} has no pin {pin.pin}"
            return None
        if info is not None and not any(info.can(capability) for capability in wanted):
            what = {"in": {"digital": "read a level", "analog": "measure a voltage"},
                    "out": {"digital": "drive a level", "analog": "give a voltage"}}[pin.direction][pin.signal]
            self.problems[pin.component] = f"{pin.pin} of {instrument.name} cannot {what}"
            return None
        if info is not None and not info.usable:
            self.problems[pin.component] = f"{pin.pin} of {instrument.name} is in use: {info.reserved}"
            return None
        facet_needed = {("in", "digital"): GpioFacet, ("in", "analog"): AnalogInFacet,
                        ("out", "digital"): GpioFacet, ("out", "analog"): AnalogOutFacet}[(pin.direction, pin.signal)]
        if instrument.facet(facet_needed) is None:
            self.problems[pin.component] = f"{instrument.name} cannot do that ({facet_needed.title})"
            return None
        gpio = instrument.facet(GpioFacet)
        if pin.signal == "digital" and gpio is not None:
            gpio.set_mode(pin.pin, MODE_OUTPUT if pin.direction == "out" else MODE_INPUT)
        if pin.direction == "out":
            self._driven[pin.component] = (instrument, pin)
        self._ready[pin.component] = (instrument, info)
        self.problems.pop(pin.component, None)
        return instrument, info

    # ------------------------------------------------------------ run
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="openscilab-device-pins", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.exchange()
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def exchange(self) -> None:
        """One exchange: read the inputs, hand them on; write the outputs that changed."""
        with self._lock:
            pins = list(self._pins)
            inputs: dict[str, float] = {}
            for pin in pins:
                try:
                    ready = self._prepare(pin)
                    if ready is None:
                        continue
                    instrument, info = ready
                    if pin.direction == "in":
                        volts = self._read(instrument, info, pin)
                        inputs[pin.source] = volts
                        self.values[pin.component] = volts
                    else:
                        self._write(instrument, info, pin)
                except Exception as error:  # noqa: BLE001 - a device that stopped answering: said at the component
                    log.debug("device pin %s", pin.component, exc_info=True)
                    self.problems[pin.component] = f"the device did not answer: {error}"
                    self._ready.pop(pin.component, None)  # (set up again next time)
        if inputs:
            self.push_inputs(inputs)

    def _read(self, instrument: Instrument, info: PinInfo | None, pin: DevicePin) -> float:
        if pin.signal == "analog":
            return float(instrument.require(AnalogInFacet).read([pin.pin])[pin.pin])
        level = instrument.require(GpioFacet).read(pin.pin)
        return float(level) * (info.logic_level if info is not None else 3.3)

    def _write(self, instrument: Instrument, info: PinInfo | None, pin: DevicePin) -> None:
        volts = self.net_value(pin.vector) if pin.vector != "0" else 0.0
        if volts is None:
            return
        volts = float(volts.real if isinstance(volts, complex) else volts)
        before = self._written.get(pin.component)
        if pin.signal == "analog":
            facet = instrument.require(AnalogOutFacet)
            low, high = facet.voltage_range(pin.pin)
            volts = min(max(volts, low), high)
            if before is None or abs(volts - before) >= ANALOG_STEP:
                facet.set_voltage(pin.pin, volts)
                self._written[pin.component] = volts
                self.values[pin.component] = volts
            return
        middle, band = self.vcc / 2, self.vcc * HYSTERESIS / 2
        if before is None:
            level = 1 if volts > middle else 0
        elif before and volts < middle - band:
            level = 0
        elif not before and volts > middle + band:
            level = 1
        else:
            level = int(before)
        if before is None or level != int(before):
            instrument.require(GpioFacet).write(pin.pin, level)
            self._written[pin.component] = float(level)
            self.values[pin.component] = level * (info.logic_level if info is not None else 3.3)

    # ------------------------------------------------------------ end
    def stop(self) -> None:
        """Stop the exchange; the outputs it drove go back to safe inputs (analog outputs to 0 V)."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)
        with self._lock:
            for instrument, pin in self._driven.values():
                self._make_safe(instrument, pin)
            self._driven.clear()
            self._written.clear()
            self._ready.clear()

    @staticmethod
    def _make_safe(instrument: Instrument, pin: DevicePin) -> None:
        try:
            if pin.signal == "analog":
                instrument.require(AnalogOutFacet).set_voltage(pin.pin, 0.0)
            else:
                instrument.require(GpioFacet).set_mode(pin.pin, MODE_INPUT)
        except Exception:  # noqa: BLE001 - a device that is gone has no outputs left to make safe
            log.debug("could not make %s safe", pin.pin, exc_info=True)
