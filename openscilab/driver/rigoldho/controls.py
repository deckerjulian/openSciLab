# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The front panel of the DHO900 as a :class:`SettingsFacet`: run and stop, time base, the analog
channels, the edge trigger, the acquisition - through SCPI on the driver's connection (the DHO's
server answers one connection at a time, so no second one is opened).

The commands are those of the programming guide that the command table of a DHO924S (modified
firmware NK v0.5.2) has and that answered on it; ``:CHANnel<n>:BWLimit?`` is left out (it answers
nothing there).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from .scpi import ScpiError
from ...core import units
from ...core.instrument import InstrumentError, SettingsFacet

if TYPE_CHECKING:  # pragma: no cover
    from .driver import RigolDhoDriver

log = logging.getLogger("openscilab.driver")

#: seconds a query of a setting may take
QUERY_TIMEOUT_S = 2.0
PROBE_RATIOS = ("0.001", "0.002", "0.005", "0.01", "0.02", "0.05", "0.1", "0.2", "0.5", "1", "2", "5", "10", "20",
                "50", "100", "200", "500", "1000", "2000", "5000", "10000", "20000", "50000")
AVERAGES = tuple(str(1 << power) for power in range(1, 17))


def _setting(name: str, label: str, group: str, kind: str, command: str, **extra: Any) -> dict:
    return dict(name=name, label=label, group=group, kind=kind, command=command, **extra)


def settings_for(analog: int, digital: int) -> list[dict]:
    """The settings of a DHO with ``analog`` channels and ``digital`` logic channels."""
    run = "Run control"
    result = [
        _setting("run", "Run", run, "action", ":RUN"),
        _setting("stop", "Stop", run, "action", ":STOP"),
        _setting("single", "Single", run, "action", ":SINGle"),
        _setting("force", "Force trigger", run, "action", ":TFORce"),
        _setting("auto", "Auto setup", run, "action", ":AUToset"),
        _setting("clear", "Clear", run, "action", ":CLEar"),
        _setting("status", "Trigger status", run, "text", ":TRIGger:STATus?"),
        _setting("timebase_scale", "Time/div", "Horizontal", "number", ":TIMebase:MAIN:SCALe", unit="s",
                 minimum=1e-9, maximum=1000.0, steps="125"),
        _setting("timebase_offset", "Delay", "Horizontal", "number", ":TIMebase:MAIN:OFFSet", unit="s",
                 minimum=-1e4, maximum=1e4),
        _setting("roll", "Roll mode (from 50 ms/div)", "Horizontal", "bool", ":TIMebase:ROLL"),
    ]
    for number in range(1, analog + 1):
        group = f"CH{number}"
        result += [
            _setting(f"ch{number}_display", "On", group, "bool", f":CHANnel{number}:DISPlay"),
            _setting(f"ch{number}_scale", "Volts/div", group, "number", f":CHANnel{number}:SCALe", unit="V",
                     minimum=1e-4, maximum=100.0, steps="125"),
            _setting(f"ch{number}_offset", "Offset", group, "number", f":CHANnel{number}:OFFSet", unit="V",
                     minimum=-1000.0, maximum=1000.0),
            _setting(f"ch{number}_coupling", "Coupling", group, "choice", f":CHANnel{number}:COUPling",
                     choices=[["DC", "DC"], ["AC", "AC"], ["GND", "GND"]]),
            _setting(f"ch{number}_probe", "Probe", group, "choice", f":CHANnel{number}:PROBe",
                     choices=[[ratio, f"{ratio}×"] for ratio in PROBE_RATIOS]),
            _setting(f"ch{number}_invert", "Invert", group, "bool", f":CHANnel{number}:INVert"),
        ]
    sources = [[f"CHAN{number}", f"CH{number}"] for number in range(1, analog + 1)] + \
              [[f"D{number}", f"D{number}"] for number in range(digital)]
    trigger = "Trigger"
    result += [
        _setting("trigger_mode", "Mode", trigger, "text", ":TRIGger:MODE?"),
        _setting("trigger_source", "Source", trigger, "choice", ":TRIGger:EDGE:SOURce", choices=sources),
        _setting("trigger_slope", "Slope", trigger, "choice", ":TRIGger:EDGE:SLOPe",
                 choices=[["POS", "Rising"], ["NEG", "Falling"], ["RFAL", "Either"]]),
        _setting("trigger_level", "Level", trigger, "number", ":TRIGger:EDGE:LEVel", unit="V",
                 minimum=-1000.0, maximum=1000.0),
        _setting("trigger_sweep", "Sweep", trigger, "choice", ":TRIGger:SWEep",
                 choices=[["AUTO", "Auto"], ["NORM", "Normal"], ["SING", "Single"]]),
        _setting("trigger_coupling", "Coupling", trigger, "choice", ":TRIGger:COUPling",
                 choices=[["DC", "DC"], ["AC", "AC"], ["LFR", "LF reject"], ["HFR", "HF reject"]]),
        _setting("trigger_holdoff", "Holdoff", trigger, "number", ":TRIGger:HOLDoff", unit="s",
                 minimum=8e-9, maximum=10.0),
        _setting("trigger_noise", "Noise reject", trigger, "bool", ":TRIGger:NREJect"),
    ]
    acquisition = "Acquisition"
    result += [
        _setting("acquire_type", "Mode", acquisition, "choice", ":ACQuire:TYPE",
                 choices=[["NORM", "Normal"], ["PEAK", "Peak"], ["AVER", "Average"], ["HRES", "High resolution"]]),
        _setting("averages", "Averages", acquisition, "choice", ":ACQuire:AVERages",
                 choices=[[count, count] for count in AVERAGES]),
        _setting("memory_depth", "Memory depth", acquisition, "text", ":ACQuire:MDEPth?"),
        _setting("sample_rate", "Sample rate", acquisition, "text", ":ACQuire:SRATe?"),
    ]
    if digital:
        result.append(_setting("la_enable", "Logic channels on", acquisition, "bool", ":LA:ENABle"))
    return result


def parse_value(setting: dict, answer: str) -> Any:
    """The answer of the instrument as the value of ``setting``."""
    text = answer.strip().strip('"')
    kind = setting["kind"]
    if kind == "number":
        return float(text)
    if kind == "bool":
        return text.upper() in ("1", "ON")
    if kind == "choice":
        values = [value for value, _label in setting["choices"]]
        try:
            number = float(text)
            for value in values:  # (numbers: 1.000000E1 is "10")
                try:
                    if abs(float(value) - number) <= 1e-9 * max(abs(number), 1.0):
                        return value
                except ValueError:
                    break
        except ValueError:
            pass
        for value in values:
            if value.upper() == text.upper():
                return value
        return text
    if setting["name"] == "memory_depth":
        try:
            return f"{units.format_quantity(float(text), '')}pts"
        except ValueError:
            return text
    if setting["name"] == "sample_rate":
        try:
            return units.format_quantity(float(text), "Sa/s")
        except ValueError:
            return text
    return text


def _same(setting: dict, taken: Any, value: Any) -> bool:
    if setting["kind"] == "number":
        try:
            return abs(float(taken) - float(value)) <= 1e-6 * max(abs(float(value)), 1e-12)
        except (TypeError, ValueError):
            return False
    if setting["kind"] == "bool":
        return bool(taken) == bool(value)
    return str(taken) == str(value)


def value_text(setting: dict, value: Any) -> str:
    """``value`` as the instrument takes it."""
    kind = setting["kind"]
    if kind == "number":
        return f"{float(value):.6g}"
    if kind == "bool":
        return "ON" if value else "OFF"
    return str(value)


class DhoSettings(SettingsFacet):
    def __init__(self, driver: "RigolDhoDriver") -> None:
        super().__init__()
        self.driver = driver
        self._settings = settings_for(driver.features.analog, driver.features.digital)
        self._by_name = {setting["name"]: setting for setting in self._settings}

    def settings(self) -> list[dict]:
        return [{key: value for key, value in setting.items() if key != "command"} for setting in self._settings]

    def _setting(self, name: str, kinds: tuple[str, ...]) -> dict:
        setting = self._by_name.get(name)
        if setting is None or setting["kind"] not in kinds:
            raise InstrumentError(f"the oscilloscope has no setting {name!r}")
        return setting

    def _query(self, command: str) -> str:
        return self.driver.connection.query(command if command.endswith("?") else command + "?",
                                            timeout=QUERY_TIMEOUT_S)

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for setting in self._settings:
            if setting["kind"] == "action":
                continue
            try:
                result[setting["name"]] = parse_value(setting, self._query(setting["command"]))
            except (ScpiError, ValueError) as error:
                log.debug("Reading %s of the DHO: %s", setting["command"], error)
                result[setting["name"]] = None
        return result

    def set(self, name: str, value: Any) -> Any:
        setting = self._setting(name, ("number", "choice", "bool"))
        if setting["kind"] == "choice" and str(value) not in [item[0] for item in setting["choices"]]:
            raise InstrumentError(f"{setting['label']}: {value!r} is not one of the choices")
        connection = self.driver.connection
        try:
            with connection.lock:
                connection.errors()  # (an earlier error is not this setting's)
                connection.write(f"{setting['command']} {value_text(setting, value)}")
                errors = connection.errors()
                taken = parse_value(setting, self._query(setting["command"]))
        except (ScpiError, ValueError) as error:
            raise InstrumentError(f"the oscilloscope does not answer ({error})") from error
        if errors and not _same(setting, taken, value):
            raise InstrumentError(f"the oscilloscope refused {setting['label']} = {value}: " + "; ".join(errors))
        if errors:  # (the probe ratio is taken with "Command execute failed")
            log.debug("The DHO took %s = %s, reporting %s", setting["command"], value, "; ".join(errors))
        return taken

    def action(self, name: str) -> None:
        setting = self._setting(name, ("action",))
        try:
            self.driver.connection.write(setting["command"])
        except ScpiError as error:
            raise InstrumentError(f"the oscilloscope does not answer ({error})") from error

    def setting(self, name: str) -> Optional[dict]:
        return self._by_name.get(name)
