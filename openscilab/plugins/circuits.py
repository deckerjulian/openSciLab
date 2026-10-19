# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Circuits: schematics, their live simulation (ngspice) and emulated microcontrollers - openSciLab's
own plugin of the kind that adds a section (``ui/sections.py``), registered like any other.

* a folder ``circuits/`` in every project,
* a kind of device ``circuit:<file>``: the probes of a running circuit as channels (data view, flows),
* in the application: the *Circuits* section, the kind of documents *Circuit* (``*.circuit.yaml``)
  and its icons.
"""

from __future__ import annotations

from ..circuits import FOLDER, SUFFIX
from ..driver import kinds
from ..lab import project

project.register_folder(FOLDER, __name__)


def open_circuit_driver(rest: str):
    from ..circuits.source import open_circuit_driver as open_driver

    return open_driver(rest)


def open_circuit_instrument(rest: str, **options):
    from ..circuits.source import open_circuit_instrument as open_instrument

    return open_instrument(rest, **options)


def detect_circuits() -> list[tuple[str, str]]:
    """The circuits that run now (their documents): ``circuit:<file>`` in the device list."""
    import os

    from ..circuits import running

    return [(key, f"Circuit {os.path.basename(key).replace(SUFFIX, '')} (running)") for key in running.names()]


kinds.register("circuit", open_circuit_driver, title="Circuit", instrument=open_circuit_instrument, simulator=True,
               simulation=None, detect=detect_circuits)

ICONS = {
    "circuit": "<path d='M2 12h4'/><rect x='6' y='9' width='8' height='6' rx='1'/><path d='M14 12h3'/>"
               "<path d='M17 7v10'/><path d='M20 9v6'/><path d='M17 12h0'/>",
    "wire": "<path d='M4 18h6V6h10'/><circle cx='4' cy='18' r='1.6' fill='{color}'/><circle cx='20' cy='6' r='1.6' fill='{color}'/>",
    "pointer": "<path d='M5 3l14 8-6 2-2 6z'/>",
}


def setup_ui() -> None:
    from ..ui.icons import register_icon
    from ..ui.sections import register_document_type, register_section

    for name, svg in ICONS.items():
        register_icon(name, svg)

    def section(services):
        from ..ui.circuits.section import CircuitsSection

        return CircuitsSection(services)

    def open_file(path, services):
        from ..ui.circuits.document import open_circuit_file

        return open_circuit_file(path, services)

    def new(services):
        from ..ui.circuits.document import new_circuit

        return new_circuit(services)

    from ..circuits import CircuitError

    register_section("circuits", "Circuits", "circuit", section, shortcut="Ctrl+Shift+C",
                     on_show=lambda widget: widget.refresh())
    register_document_type("circuit", "Circuit", (SUFFIX,), open_file, new, icon="circuit",
                           filter=f"Circuits (*{SUFFIX})", folder=FOLDER,
                           errors=(CircuitError, OSError, ValueError, TypeError, AttributeError),
                           description="A schematic, simulated live (analog, digital, microcontrollers)")
