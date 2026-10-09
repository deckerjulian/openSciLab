# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Built-in plugin: Rigol DHO900 oscilloscopes over the network or USB (``driver/rigoldho``).

``rigol:192.168.1.20``
    An oscilloscope (through its bridge app when it runs; ``rigol:<address>:<port>`` for another port).
``rigol:usb``
    The oscilloscope on USB (USBTMC); ``rigol:usb:<serial number>`` or ``rigol:usb:<bus>:<address>``
    when there are several.
``rigol-sim:bridge``
    A simulated DHO924S with the bridge app (``rigol-sim:direct`` without it, ``rigol-sim:usb`` on USB).
"""

from __future__ import annotations

from ..driver import kinds
from ..driver.base import AnalyzerDriverBase, DeviceConnectionError


def open_rigol(rest: str) -> AnalyzerDriverBase:
    from ..driver.rigoldho.driver import open_network, open_usb

    rest = rest.strip()
    if rest.lower() == "usb" or rest.lower().startswith("usb:"):
        return open_usb(rest[4:])
    if not rest:
        raise DeviceConnectionError("Give the address of the oscilloscope: rigol:<address>, or rigol:usb.")
    return open_network(rest)


def detect() -> list[tuple[str, str]]:
    """The oscilloscopes on USB (those on the network are found by their bridge app's beacon)."""
    from ..driver.rigoldho.driver import usb_instruments, usb_selector

    return [(f"usb:{usb_selector(info)}", info.label) for info in usb_instruments()]


def open_simulated(rest: str) -> AnalyzerDriverBase:
    from ..driver.simulated.scpi_shell import open_shell

    try:
        return open_shell(rest or "bridge")
    except ValueError as error:
        raise DeviceConnectionError(str(error)) from None


kinds.register("rigol", open_rigol, title="Rigol DHO900", detect=detect, simulation="dho924s")
kinds.register("rigol-sim", open_simulated, title="Simulated Rigol DHO924S", simulation="dho924s")


def setup_ui() -> None:
    from ..ui.devices import register_backend
    from ..ui.devices.rigoldho import RigolBackend

    register_backend(RigolBackend())
