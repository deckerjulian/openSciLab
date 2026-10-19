# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Circuits: schematics, their simulation and emulated microcontrollers (no Qt).

* :mod:`.model` - a circuit: components, wires, labels; :mod:`.yaml_io` its file (``*.circuit.yaml``)
* :mod:`.symbols` - what a component looks like; :mod:`.library` what it is (pins, parameters, model)
* :mod:`.catalog` - every component there is: openSciLab's own, KiCad's symbol libraries, SPICE models
* :mod:`.nets` - which pins the wires connect; :mod:`.spice` the netlist of a circuit
* :mod:`.engines` - ngspice: analyses and the live simulation; :mod:`.source` the circuit as a device
* :mod:`.mcu` - emulated microcontrollers (simavr) running a firmware file

The user interface is in ``openscilab/ui/circuits``; ``openscilab/plugins/circuits.py`` adds both to
the application (docs/circuits.md).
"""

#: the grid of a schematic (scene units); pins sit on every second step (100 mil in KiCad)
GRID = 10
#: scene units per millimetre (KiCad's 2.54 mm pin pitch is 20 units)
UNITS_PER_MM = 20 / 2.54
#: the suffix of circuit files
SUFFIX = ".circuit.yaml"
#: the folder of a project for them
FOLDER = "circuits"


class CircuitError(ValueError):
    """A circuit, a component or a file that cannot be used as it is."""
