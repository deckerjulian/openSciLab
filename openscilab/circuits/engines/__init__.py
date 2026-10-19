# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The simulation of circuits with ngspice (no Qt).

* :mod:`.ngspice` - the library through ctypes, one per process
* :mod:`.history` - what a run produced, kept for the probes (channels) and their latest values
* :mod:`.live` - the live simulation: a transient that runs on, in real or in virtual time, whose
  values change while it runs
* :mod:`.analysis` - operating point, DC sweep, AC sweep, transient, noise and S-parameters

ngspice can do one thing at a time (:data:`engine_lock`): a live run or an analysis.
"""

from __future__ import annotations

import threading


from ...core import vendor

#: one run of ngspice at a time: (the lock, the title of who holds it)
engine_lock = threading.RLock()
_owner: list[str] = [""]


class EngineBusy(RuntimeError):
    """ngspice runs something else."""


def acquire(owner: str) -> None:
    if not engine_lock.acquire(blocking=False):
        raise EngineBusy(f"{_owner[0] or 'another circuit'} is being simulated")
    _owner[0] = owner


def release() -> None:
    _owner[0] = ""
    try:
        engine_lock.release()
    except RuntimeError:
        pass


def owner() -> str:
    return _owner[0]


def find_ngspice() -> str | None:
    """Where libngspice is (``None``: nowhere, see :func:`openscilab.core.vendor.ensure`)."""
    return vendor.find("ngspice")


def available() -> bool:
    return find_ngspice() is not None


def version() -> int:
    """The version of the ngspice found (0: none)."""
    from . import ngspice

    try:
        return ngspice.instance().version
    except ngspice.NgSpiceError:
        return 0
