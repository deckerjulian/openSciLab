# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A flow written for one device runs on another: a channel or pin name the device does not have is
matched to the next suitable channel of the device (by kind, in order), with a warning. The label
stays the name that was asked for, so decoders, views and panels that read it still find it."""

from __future__ import annotations

import pytest

from openscilab.driver.simulated import open_simulated
from openscilab.lab.nodes.device import channel_numbers, split_channels
from openscilab.lab.nodes.gpio import known_pin, match_pins


@pytest.fixture
def pico():
    instrument = open_simulated("pico", fast=True, clock=lambda: 0.0)
    yield instrument
    instrument.close()


@pytest.fixture
def daq():
    instrument = open_simulated("daq", fast=True, clock=lambda: 0.0)
    yield instrument
    instrument.close()


def collect():
    warnings: list[str] = []
    return warnings, warnings.append


def test_a_foreign_capture_name_takes_the_first_free_channel(pico):
    warnings, warn = collect()
    assert channel_numbers(pico, ["D9"], warn) == [("D9", 0)]  # the label stays D9
    assert "D9" in warnings[0] and "GP2" in warnings[0]


def test_foreign_names_keep_their_order(pico):
    _warnings, warn = collect()
    assert channel_numbers(pico, ["D2", "D9"], warn) == [("D2", 0), ("D9", 1)]


def test_a_known_name_keeps_its_channel_and_a_foreign_one_skips_it(pico):
    _warnings, warn = collect()
    # GP5 is the device's channel 3; the foreign D9 takes the first free channel, not GP5's.
    assert channel_numbers(pico, ["GP5", "D9"], warn) == [("GP5", 3), ("D9", 0)]


def test_a_native_name_is_not_substituted_and_is_silent(pico):
    warnings, warn = collect()
    assert channel_numbers(pico, ["GP5"], warn) == [("GP5", 3)]
    assert warnings == []


def test_running_out_of_channels_still_raises(pico):
    from openscilab.lab.engine.runtime import NodeError

    count = pico.capture.channel_count
    with pytest.raises(NodeError):
        channel_numbers(pico, [f"X{i}" for i in range(count + 1)])


def test_an_analog_name_the_device_lacks_reads_an_analog_input(daq):
    warnings, warn = collect()
    digital, analog = split_channels(daq, ["A0"], warn)
    assert digital == [] and analog == [("A0", 0)]  # A0 -> the first analog input, label kept
    assert "A0" in warnings[0]


def test_a_foreign_pin_name_takes_the_first_pin(pico):
    warnings, warn = collect()
    pairs = match_pins(pico, ["D13"], analog=False, warn=warn)
    assert [label for label, _pin in pairs] == ["D13"]  # the label is kept for the port
    assert pairs[0][1].startswith("GP") and warnings  # a real pin of the device, with a warning
    assert known_pin(pico, "D13").startswith("GP")


def test_match_pins_keeps_a_native_name(pico):
    warnings, warn = collect()
    assert match_pins(pico, ["GP5"], analog=False, warn=warn) == [("GP5", "GP5")]
    assert warnings == []
