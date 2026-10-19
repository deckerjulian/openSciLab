# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Values of components as people write them.

``10k``, ``4.7 uF``, ``100 mV`` (openSciLab, ``core/units``), ``1meg``, ``2.2u`` (SPICE) and ``4k7``,
``2R2``, ``1u5`` (KiCad, the letter as the decimal point). ``M`` is mega as in the rest of
openSciLab (in SPICE it would be milli): :func:`spice_number` writes plain numbers into a netlist, so
the netlist never depends on that.
"""

from __future__ import annotations

import math
import re

from ..core import units

_SPICE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(meg|mil|[afpnuµμmkKMGT])?\s*([A-Za-zΩ°%/]*)\s*$")
_FACTORS = {"a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
            "k": 1e3, "K": 1e3, "M": 1e6, "meg": 1e6, "G": 1e9, "T": 1e12, "mil": 25.4e-6}
#: KiCad's style: the prefix (or R, the unit of a resistance) as the decimal point: 4k7, 2R2, 1u5
_INFIX = re.compile(r"^\s*(\d+)([RrpnuµμmkKMG])(\d+)\s*([A-Za-zΩ]*)\s*$")


def parse(value, unit: str = "") -> float:
    """``value`` as a number in the base unit; raises :class:`ValueError` for what is no value."""
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is no value")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".") if str(value).count(",") == 1 and "." not in str(value) \
        else str(value).strip()
    if not text:
        raise ValueError("no value")
    try:
        return units.parse(text, unit)
    except units.UnitError:
        pass
    match = _INFIX.match(text)
    if match is not None:
        whole, letter, fraction, _unit = match.groups()
        factor = 1.0 if letter in "Rr" else _FACTORS[letter]
        return float(f"{whole}.{fraction}") * factor
    match = _SPICE.match(text)
    if match is None:
        raise ValueError(f"{value!r} is no value (e.g. 10k, 4.7 uF, 100 mV)")
    number, prefix, _rest = match.groups()
    return float(number) * (_FACTORS.get(prefix, 1.0) if prefix else 1.0)


def parse_optional(value, unit: str = "") -> float | None:
    try:
        return parse(value, unit)
    except ValueError:
        return None


def is_value(value) -> bool:
    return parse_optional(value) is not None


def spice_number(value) -> str:
    """A value for a netlist: a plain number (``10k`` → ``10000``); text that is no value is kept (a
    model name, an expression)."""
    number = parse_optional(value)
    if number is None:
        return str(value).strip()
    if number == 0:
        return "0"
    if math.isfinite(number) and number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.9g}"


def format_value(number: float, unit: str = "", digits: int = 4) -> str:
    """``4700, "Ω"`` → ``4.7 kΩ``."""
    return units.format_quantity(number, unit, digits)


def display(number: float, unit: str = "") -> str:
    """A measured value as people read it: three digits, noise below a nano-unit as 0 (``0.9999999 V`` is
    ``1 V``, not ``1e+03 mV``)."""
    if abs(number) < 1e-9:
        number = 0.0
    return units.format_quantity(float(f"{number:.3g}"), unit, 3)
