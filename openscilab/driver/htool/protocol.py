# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The frames of the HTOOL HT008 (the protocol of the LTDZ MAX2870 sources it descends from).

A frame is 9 bytes at 115200 baud: ``55 55``, the whole megahertz (16 bit, big endian), the
kilohertz beyond them (0–999, 16 bit), the power level, ``0D 0A``. The HT008 counts its levels from
0 (the LTDZ from 1). The first thing the HT008 gets after it is switched on decides how it listens
until it is switched off: a frame puts it under the computer's control (it takes that frame without
an answer, every further frame with an echo of 9 bytes and ``OK``); anything else leaves it at its
SCPI commands, which only set up a sweep. Qt free.
"""

from __future__ import annotations

import math

BAUD = 115200
HEAD = b"\x55\x55"
TAIL = b"\r\n"
FRAME_LENGTH = 9
#: what the HT008 ends its answer to a frame with
ACKNOWLEDGE = b"OK"
#: frequencies it makes (Hz), and the step of a frame
MIN_FREQUENCY = 23.5e6
MAX_FREQUENCY = 6e9
RESOLUTION = 1e3
#: output power of CH1 (dBm) for the level bytes 0, 1, 2, 3
LEVELS_DBM = (-4.0, -1.0, 2.0, 5.0)
#: the load the output power is given for (ohms)
LOAD_OHMS = 50.0
#: what it outputs after it is switched on (the frame that opens it keeps this)
POWER_ON_FREQUENCY = 3e9
POWER_ON_LEVEL = 0


def frame(frequency: float, level: int) -> bytes:
    """The frame for ``frequency`` (Hz, rounded to whole kilohertz) at level byte ``level``."""
    if not 0 <= level < len(LEVELS_DBM):
        raise ValueError(f"the HT008 has the power levels 0 to {len(LEVELS_DBM) - 1}, not {level}")
    kilohertz = round_frequency(frequency) // 1000
    if not MIN_FREQUENCY <= kilohertz * 1000 <= MAX_FREQUENCY:
        raise ValueError(f"the HT008 makes {MIN_FREQUENCY / 1e6:g} MHz to {MAX_FREQUENCY / 1e9:g} GHz")
    megahertz, rest = divmod(kilohertz, 1000)
    return HEAD + megahertz.to_bytes(2, "big") + rest.to_bytes(2, "big") + bytes([level]) + TAIL


def round_frequency(frequency: float) -> int:
    """``frequency`` (Hz) on the kilohertz steps of a frame."""
    return round(frequency / RESOLUTION) * int(RESOLUTION)


def parse(data: bytes) -> tuple[float, int]:
    """Frequency (Hz) and level byte of a frame."""
    if len(data) != FRAME_LENGTH or not data.startswith(HEAD) or not data.endswith(TAIL):
        raise ValueError(f"not a frame of the HT008: {data.hex(' ')}")
    megahertz, rest = int.from_bytes(data[2:4], "big"), int.from_bytes(data[4:6], "big")
    return megahertz * 1e6 + rest * 1e3, data[6]


def dbm_to_volts(dbm: float) -> float:
    """Peak volts of a sine of ``dbm`` into :data:`LOAD_OHMS`."""
    return math.sqrt(2 * LOAD_OHMS * 1e-3 * 10 ** (dbm / 10))


def volts_to_dbm(volts: float) -> float:
    """Power (dBm) of a sine of ``volts`` peak into :data:`LOAD_OHMS`."""
    if volts <= 0:
        return -math.inf
    return 10 * math.log10(volts ** 2 / (2 * LOAD_OHMS) / 1e-3)


def nearest_level(dbm: float) -> int:
    """The level byte whose power is nearest to ``dbm``."""
    return min(range(len(LEVELS_DBM)), key=lambda level: abs(LEVELS_DBM[level] - dbm))
