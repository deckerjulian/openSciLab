# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Built-in plugin: the HTOOL HT008 RF signal source on a serial port (``driver/htool``).

``ht008:/dev/cu.wchusbserial210``
    The HT008 on that port: a generator output CH1 (23.5 MHz to 6 GHz) and its settings.
"""

from __future__ import annotations

from ..driver import kinds


def open_instrument(rest: str):
    from ..driver.htool.instrument import open_ht008

    return open_ht008(rest)


def detect() -> list[tuple[str, str]]:
    from ..driver.htool.instrument import detect as detect_ports

    return detect_ports()


kinds.register("ht008", instrument=open_instrument, title="HTOOL HT008 RF signal source", detect=detect,
               simulation=None)


def setup_ui() -> None:
    from ..ui.devices import register_backend
    from ..ui.devices.htool import Ht008Backend

    register_backend(Ht008Backend())
