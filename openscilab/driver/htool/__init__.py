# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""HTOOL HT008: an RF signal source of 23.5 MHz to 6 GHz (MAX2871 synthesizer, GD32 controller,
CH340 USB serial chip), as an instrument with a generator output and settings (``ht008:<port>``).

``protocol.py`` has its frames, ``instrument.py`` the connection and the facets. What was found on
an HT008 with firmware V6.0.1 is in ``docs/protocols.md`` (*HTOOL HT008*).
"""
