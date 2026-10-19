# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The microcontrollers openSciLab emulates and how their pins are named.

A board (``mcu.uno``) names its pins as the Arduino does (D13, A0); a chip of KiCad's libraries
(ATmega328P-P) by its port (PB5, PC0). Either way a pin is a port, a bit and perhaps an analog input.

In the netlist every connected pin of a microcontroller is an output stage the co-simulation drives
(``external`` sources: the level, whether it drives, its pull-up) - see :func:`netlist_lines`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PinMap:
    port: str
    bit: int
    #: its analog input (ADC channel), ``None``: none
    adc: int | None = None
    #: only analog (A6/A7 of a Nano): no port bit
    analog_only: bool = False


@dataclass(frozen=True)
class Board:
    name: str
    title: str
    #: simavr's name of the chip
    core: str
    frequency: float = 16e6
    #: pin name -> its port, bit, analog input
    pins: dict[str, PinMap] = field(default_factory=dict)
    #: pins that supply the circuit: name -> volts (the board's 5V and 3V3 pins)
    supplies: dict[str, float] = field(default_factory=dict)


def _uno_pins(nano: bool = False) -> dict[str, PinMap]:
    pins = {f"D{n}": PinMap("D", n) for n in range(8)}
    pins.update({f"D{n}": PinMap("B", n - 8) for n in range(8, 14)})
    pins.update({f"A{n}": PinMap("C", n, n) for n in range(6)})
    if nano:
        pins.update({"A6": PinMap("C", 6, 6, True), "A7": PinMap("C", 7, 7, True)})
    return pins


_MEGA = ["E0", "E1", "E4", "E5", "G5", "E3", "H3", "H4", "H5", "H6", "B4", "B5", "B6", "B7", "J1", "J0", "H1", "H0", "D3", "D2", "D1", "D0", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "C7", "C6", "C5", "C4", "C3", "C2", "C1", "C0", "D7", "G2", "G1", "G0", "L7", "L6", "L5", "L4", "L3", "L2", "L1", "L0", "B3", "B2", "B1", "B0"]


def _mega_pins() -> dict[str, PinMap]:
    pins = {f"D{n}": PinMap(code[0], int(code[1])) for n, code in enumerate(_MEGA)}
    pins.update({f"A{n}": PinMap("F", n, n) for n in range(8)})
    pins.update({f"A{n}": PinMap("K", n - 8, n) for n in range(8, 16)})
    return pins


def _chip_pins(ports: str, adc: dict[str, int]) -> dict[str, PinMap]:
    pins = {}
    for port in ports:
        for bit in range(8):
            name = f"P{port}{bit}"
            pins[name] = PinMap(port, bit, adc.get(name))
    pins.update({f"ADC{n}": PinMap("C", n, n, True) for n in (6, 7)})
    return pins


BOARDS: dict[str, Board] = {
    "mcu.uno": Board("mcu.uno", "Arduino Uno", "atmega328p", 16e6, _uno_pins(), {"5V": 5.0, "3V3": 3.3}),
    "mcu.nano": Board("mcu.nano", "Arduino Nano", "atmega328p", 16e6, _uno_pins(nano=True), {"5V": 5.0, "3V3": 3.3}),
    "mcu.mega2560": Board("mcu.mega2560", "Arduino Mega 2560", "atmega2560", 16e6, _mega_pins(), {"5V": 5.0, "3V3": 3.3}),
    "atmega328p": Board("atmega328p", "ATmega328P", "atmega328p", 16e6,
                        _chip_pins("BCD", {f"PC{n}": n for n in range(6)})),
    "atmega168": Board("atmega168", "ATmega168", "atmega168", 16e6, _chip_pins("BCD", {f"PC{n}": n for n in range(6)})),
    "atmega2560": Board("atmega2560", "ATmega2560", "atmega2560", 16e6,
                        _chip_pins("ABCDEFGHJKL", {**{f"PF{n}": n for n in range(8)}, **{f"PK{n}": n + 8 for n in range(8)}})),
    "atmega1280": Board("atmega1280", "ATmega1280", "atmega1280", 16e6,
                        _chip_pins("ABCDEFGHJKL", {**{f"PF{n}": n for n in range(8)}, **{f"PK{n}": n + 8 for n in range(8)}})),
}

GROUND = ("GND", "AGND", "GND1", "GND2", "GND3", "VSS")


def pin_of(board: Board, label: str) -> PinMap | None:
    """The port pin a pin name of a symbol means (``D13``, ``D13/SCK``, ``PB5``, ``PB5 (SCK)``)."""
    for part in re.split(r"[/(),\s]+", label.upper()):
        if part in board.pins:
            return board.pins[part]
    return None


@dataclass
class McuPin:
    """A pin of a placed microcontroller with what it is connected to."""

    name: str
    port: str
    bit: int
    adc: int | None
    analog_only: bool
    #: the net in SPICE
    net: str
    #: names of its external sources: level, drive, pull-up
    level: str
    drive: str
    pullup: str


@dataclass
class McuInfo:
    """What the co-simulation needs to run a placed microcontroller (picklable)."""

    ref: str
    board: str
    core: str
    frequency: float
    firmware: str
    vcc: float
    pins: list[McuPin] = field(default_factory=list)


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text).lower()


def netlist_lines(ref: str, board: Board, pin_nets: dict[str, tuple[str, str]], vcc: float, firmware: str
                  ) -> tuple[list[str], McuInfo]:
    """The netlist of a placed microcontroller: ``pin_nets`` maps its pin numbers to (pin name, SPICE net).

    Each connected port pin: a level source and an output resistance behind a switch the drive source
    closes, and a pull-up behind a switch of its own; supply pins of a board are sources, ground pins
    are tied to ground."""
    info = McuInfo(ref, board.name, board.core, board.frequency, firmware, vcc)
    lines = [f"V{ref}_vcc {_safe(ref)}_vcc 0 DC {vcc:g}"]
    for number, (label, net) in sorted(pin_nets.items()):
        upper = label.upper()
        if net == "0":
            continue
        if upper in GROUND:
            lines.append(f"R{ref}_g{_safe(number)} {net} 0 1m")
            continue
        # (KiCad's modules call the pins +5V and +3V3 / +3.3V; VCC of a module is its 5 V)
        supply_key = upper.lstrip("+").replace(".", "")
        supply = board.supplies.get("5V" if supply_key in ("VCC", "5V0") else supply_key)
        if supply is not None:
            lines.append(f"V{ref}_s{_safe(number)} {_safe(ref)}_s{_safe(number)} 0 DC {supply:g}")
            lines.append(f"R{ref}_s{_safe(number)} {_safe(ref)}_s{_safe(number)} {net} 50m")
            continue
        found = pin_of(board, label)
        if found is None:
            continue
        stem = f"{_safe(ref)}_{_safe(label.split('/')[0])}"
        level, drive, pullup = f"v{stem}_l", f"v{stem}_d", f"v{stem}_u"
        info.pins.append(McuPin(label, found.port, found.bit, found.adc, found.analog_only, net, level, drive, pullup))
        if found.analog_only:
            lines.append(f"R{stem}_in {net} 0 100Meg")
            continue
        lines += [
            f"{level} {stem}_l 0 DC 0 external",
            f"{drive} {stem}_d 0 DC 0 external",
            f"{pullup} {stem}_u 0 DC 0 external",
            f"R{stem}_o {stem}_l {stem}_o 25",
            f"S{stem}_o {stem}_o {net} {stem}_d 0 osl_mcu_switch",
            f"R{stem}_p {_safe(ref)}_vcc {stem}_p 35k",
            f"S{stem}_p {stem}_p {net} {stem}_u 0 osl_mcu_switch",
            f"C{stem}_in {net} 0 10p",
        ]
    lines.append(".model osl_mcu_switch SW(Ron=1 Roff=1G Vt=0.5 Vh=0.1)")
    return lines, info
