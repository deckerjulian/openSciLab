"""Blocks of a digital pattern (core/pattern_blocks.py): levels, SDL with the comment that names
them, read back; SDL changed by hand stays text."""

from __future__ import annotations

import numpy as np
import pytest

from openscilab.core import pattern_blocks as pb
from openscilab.core import waveform as waves


def test_each_kind_gives_its_levels():
    rate = 1000.0
    assert pb.Block("clock", {"period": 4, "start": "high"}).levels(8, rate).tolist() == [1, 1, 0, 0] * 2
    assert pb.Block("square", {"high": 3, "low": 1}).levels(8, rate).tolist() == [1, 1, 1, 0] * 2
    assert pb.Block("square", {"high": 3, "low": 1, "start": "low"}).levels(4, rate).tolist() == [0, 1, 1, 1]
    assert pb.Block("constant", {"level": "high"}).levels(3, rate).tolist() == [1, 1, 1]
    # bit 1 of a count that steps every 2 samples: 4 low, 4 high
    assert pb.Block("counter", {"bit": 1, "step": 2}).levels(8, rate).tolist() == [0] * 4 + [1] * 4
    # 0xA0 MSB first, a sample a bit, then idle high
    assert pb.Block("bits", {"data": "A0", "idle": "high"}).levels(10, rate).tolist() == [1, 0, 1, 0, 0, 0, 0, 0, 1, 1]
    uart = pb.Block("uart", {"data": "U", "baud": 500}).levels(28, rate)
    assert uart.tolist()[:2] == [1, 1] and 0 in uart  # idle high, then the start bit
    with pytest.raises(pb.BlockError, match="twice the baud"):
        pb.Block("uart", {"data": "U", "baud": 1000}).levels(14, rate)


def test_a_pattern_of_blocks_round_trips_through_sdl():
    blocks = {"D3": pb.Block("clock", {"period": 2}), "D34": pb.Block("square", {"high": 4, "low": 4}),
              "D5": pb.Block("bits", {"data": "A5 0F", "bit": 2}), "D6": pb.Block("uart", {"data": "Hi", "baud": 50}),
              "D7": pb.Block("counter", {"bit": 2, "step": 3}), "D8": pb.Block("constant", {"level": "high"})}
    tracks = pb.tracks(blocks, 200, 200.0)
    assert tracks["D3"] == "// clock period=2 start=low\n{l1,h1,}100;"
    back, length = pb.read_tracks(tracks, 200.0)
    assert length == 200 and {pin: (block.kind, block.settings) for pin, block in back.items()} == \
        {pin: (block.kind, block.settings) for pin, block in blocks.items()}
    pattern = waves.pattern_from_sdl(tracks, 200.0)
    for pin, block in blocks.items():
        assert np.array_equal(np.asarray(pattern.tracks[pin]), block.levels(200, 200.0)), pin


def test_sdl_changed_by_hand_or_without_a_comment_stays_text():
    tracks = pb.tracks({"D3": pb.Block("clock")}, 10, 1000.0)
    edited = tracks["D3"].replace("5;", "4;")
    assert pb.from_sdl(edited, 10, 1000.0) == pb.Block("sdl", {"text": edited})
    assert pb.from_sdl("{l1,h1,}5;", 10, 1000.0).kind == "sdl"
    assert pb.from_sdl("// clock period=x\nl1;", 10, 1000.0).kind == "sdl"  # (an unreadable comment)


def test_the_length_fits_the_longest_block_and_bad_settings_are_refused():
    assert pb.pattern_length({"a": pb.Block("clock", {"period": 6}), "b": pb.Block("bits", {"data": "FF", "bit": 3})},
                             1000.0) == 24
    with pytest.raises(pb.BlockError, match="hex"):
        pb.Block("bits", {"data": "xyz"}).levels(8, 1000.0)
    with pytest.raises(pb.BlockError, match="at least 2"):
        pb.Block("clock", {"period": 1}).levels(8, 1000.0)
    with pytest.raises(pb.BlockError, match="no block"):
        pb.Block("triangle")
