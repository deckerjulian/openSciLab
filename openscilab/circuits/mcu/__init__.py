# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Emulated microcontrollers in circuits (no Qt).

* :mod:`.firmware` - the flash image of a ``.hex`` or ``.elf`` file
* :mod:`.avr` - an AVR emulated by simavr (``libopenscilab_avr``, ``packaging/simavr``)
* :mod:`.boards` - the boards and chips openSciLab emulates, their pins, their netlist
* :mod:`.cosim` - microcontrollers and ngspice in lockstep (in the engine process)
"""
