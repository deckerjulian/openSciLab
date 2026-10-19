# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The circuits that run now, by their file (or the title of an unsaved one): what a ``circuit:``
device of a flow or the data view attaches to (``circuits/source.py``). No Qt."""

from __future__ import annotations

import os
import threading

_running: dict[str, object] = {}
_users: dict[int, int] = {}  # id(simulation) -> how many devices use a run nobody's document owns
_lock = threading.Lock()


def key_of(path_or_title: str) -> str:
    if os.path.sep in path_or_title or path_or_title.endswith(".circuit.yaml"):
        return os.path.realpath(path_or_title)
    return path_or_title


def register(path_or_title: str, simulation) -> None:
    with _lock:
        _running[key_of(path_or_title)] = simulation


def unregister(simulation) -> None:
    with _lock:
        for key in [key for key, value in _running.items() if value is simulation]:
            del _running[key]
        _users.pop(id(simulation), None)


def retain(simulation) -> None:
    """One more device uses a run that was started for devices (no document runs it)."""
    with _lock:
        _users[id(simulation)] = _users.get(id(simulation), 0) + 1


def release(simulation) -> bool:
    """One device less; True when it was the last one (the run can stop)."""
    with _lock:
        left = _users.get(id(simulation), 1) - 1
        if left <= 0:
            _users.pop(id(simulation), None)
            return True
        _users[id(simulation)] = left
        return False


def find(path_or_title: str) -> object | None:
    with _lock:
        return _running.get(key_of(path_or_title))


def names() -> list[str]:
    with _lock:
        return list(_running)
