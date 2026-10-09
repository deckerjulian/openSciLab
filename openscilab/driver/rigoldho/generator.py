# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The built-in generator of the DHO900 S models (output GI) as a :class:`GeneratorFacet`, through
the ``:SOURce`` commands of :data:`.scpi.COMMANDS`.

What the instrument offers (the programming guide, and measured on a DHO924S with the modified
firmware NK v0.5.2): sine, square (duty 5–95 %), ramp (``RAMP`` with a symmetry: 50 % is the triangle, 100 % the
rising ramp), noise and DC; up to 25 MHz (sine), 15 MHz (square) and 150 kHz (ramp, noise), and
±2.5 V into high impedance. No arbitrary waveforms, no sweep, no burst. A frequency above the
limit is cut down without an error, so the driver refuses it itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .scpi import ScpiError, command
from ...core import units
from ...core import waveform as waves
from ...core.instrument import CacheEntry, CacheFacet, GeneratorFacet, InstrumentError, OutputInfo

if TYPE_CHECKING:  # pragma: no cover
    from .driver import RigolDhoDriver

OUTPUT = "GI"
#: the function of the instrument and the ramp symmetry (percent) for each kind it can play
FUNCTIONS = {waves.SINE: ("SINusoid", None), waves.SQUARE: ("SQUare", None), waves.PULSE: ("SQUare", None),
             waves.TRIANGLE: ("RAMP", 50), waves.RAMP: ("RAMP", 100), waves.NOISE: ("NOISe", None),
             waves.DC: ("DC", None)}
#: highest frequency (Hz) per kind
MAX_FREQUENCY = {waves.SINE: 25e6, waves.SQUARE: 15e6, waves.PULSE: 15e6, waves.TRIANGLE: 150e3,
                 waves.RAMP: 150e3, waves.NOISE: 150e3, waves.DC: 25e6}
#: duty cycle range of square waves and pulses
DUTY_RANGE = (0.05, 0.95)
INFO = OutputInfo(OUTPUT, "analog", voltage_range=(-2.5, 2.5), resolution=14, max_rate=156_250_000,
                  max_points=0, max_frequency=25_000_000)


class DhoGenerator(GeneratorFacet):
    def __init__(self, driver: "RigolDhoDriver") -> None:
        super().__init__()
        self.driver = driver
        self._running = False

    def outputs(self) -> list[OutputInfo]:
        return [INFO]

    def commands_for(self, waveform: Any) -> list[str]:
        """The SCPI commands that set ``waveform`` up (without switching the output on)."""
        found = waves.problems(waveform, INFO)
        if waveform.kind not in FUNCTIONS:
            found.append(f"the generator of the oscilloscope cannot play {waveform.kind} waveforms (it has sine, "
                         "square, pulse, triangle, ramp, noise and DC)")
        elif waveform.kind != waves.DC and waveform.frequency > MAX_FREQUENCY[waveform.kind]:
            found.append(f"{units.format_quantity(waveform.frequency, 'Hz')} is above the "
                         f"{units.format_quantity(MAX_FREQUENCY[waveform.kind], 'Hz')} of {waveform.kind} waves")
        if waveform.kind in (waves.SQUARE, waves.PULSE) and not DUTY_RANGE[0] <= waveform.duty <= DUTY_RANGE[1]:
            found.append(f"a duty cycle of {waveform.duty * 100:g} % is outside the {DUTY_RANGE[0] * 100:g} to "
                         f"{DUTY_RANGE[1] * 100:g} % of the oscilloscope")
        if waveform.repeat > 0 and waveform.kind not in (waves.DC, waves.NOISE):
            found.append("the generator of the oscilloscope repeats until stopped, not a number of times")
        if found:
            raise InstrumentError("; ".join(dict.fromkeys(found)))
        function, symmetry = FUNCTIONS[waveform.kind]
        result = [command("afg_modulation", value="OFF"), command("afg_function", value=function)]
        if waveform.kind == waves.DC:
            return result + [command("afg_offset", value=f"{waveform.offset:.6g}")]
        if symmetry is not None:
            result.append(command("afg_ramp_symmetry", value=symmetry))
        low, high = waveform.voltage_range()
        if waveform.kind == waves.NOISE:
            low, high = waveform.offset - abs(waveform.amplitude), waveform.offset + abs(waveform.amplitude)
        result += [command("afg_frequency", value=f"{waveform.frequency:.6g}"),
                   command("afg_amplitude", value=f"{high - low:.6g}"),
                   command("afg_offset", value=f"{(high + low) / 2:.6g}")]
        if waveform.kind in (waves.SQUARE, waves.PULSE):
            duty = waveform.duty if waveform.kind == waves.SQUARE or waveform.amplitude >= 0 else 1 - waveform.duty
            result.append(command("afg_square_duty", value=f"{duty * 100:.6g}"))
        result.append(command("afg_phase", value=f"{waveform.phase:.6g}"))
        return result

    def start(self, output: str, waveform: Any) -> None:
        self.output(output)
        lines = self.commands_for(waveform)
        connection = self.driver.connection
        try:
            connection.errors()  # (an earlier error is not this waveform's)
            for line in lines:
                connection.write(line)
            errors = connection.errors()
            if errors:
                connection.write(command("afg_output", value="OFF"))
                connection.errors()
            else:
                connection.write(command("afg_output", value="ON"))
                errors = connection.errors()
        except ScpiError as error:
            raise InstrumentError(f"the oscilloscope does not answer ({error})") from error
        if errors:
            raise InstrumentError("the oscilloscope refused the waveform: " + "; ".join(errors))
        self._running = True

    def stop(self, output: str) -> None:
        self.output(output)
        try:
            self.driver.connection.write(command("afg_output", value="OFF"))
            self.driver.connection.errors()  # a round trip: the output is off when this returns
        except ScpiError as error:
            raise InstrumentError(f"the oscilloscope does not answer ({error})") from error
        self._running = False

    def running(self, output: str) -> bool:
        return self._running


class DhoCache(CacheFacet):
    """The snapshot cache of the bridge app."""

    def __init__(self, driver: "RigolDhoDriver") -> None:
        super().__init__()
        from .fetcher import BridgeCache

        self.cache = BridgeCache(driver)

    def _call(self, function, *arguments):
        try:
            return function(*arguments)
        except ScpiError as error:
            raise InstrumentError(str(error)) from error

    def entries(self) -> list[CacheEntry]:
        return [CacheEntry(*item) for item in self._call(self.cache.entries)]

    def delete(self, entry_id=None) -> None:
        self._call(self.cache.delete, entry_id)

    def set_limit(self, size: int) -> None:
        self._call(self.cache.set_limit, size)
