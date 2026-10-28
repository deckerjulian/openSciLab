# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The HTOOL HT008 RF signal source in the device list: the ports of its USB serial chip."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from ...core.instrument import Instrument
from . import DeviceBackend, DeviceEntry

BACKEND_ID = "ht008"


class Ht008Backend(DeviceBackend):
    id = BACKEND_ID

    def detected(self) -> list[DeviceEntry]:
        # nothing is opened here: the CH340 is also on Arduino clones, which opening restarts
        from ...driver.htool.instrument import detect

        return [DeviceEntry(BACKEND_ID, "serial", port, label) for port, label in detect()]

    def open_instrument(self, entry: DeviceEntry, parent: QWidget) -> Optional[Instrument]:
        from ...driver.htool.instrument import open_ht008
        from .. import background

        port = str(entry.value)
        return background.run(parent, f"Connecting to the HT008 on {port}...", lambda: open_ht008(port))

    def connect(self, entry: DeviceEntry, parent: QWidget):
        raise ValueError("the HT008 is a signal source: it has no capture driver")
