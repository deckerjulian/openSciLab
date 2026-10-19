# Copyright (C) 2026 Julian Decker
# Copyright (C) Agustín Giménez Bernad (gusmanb), original LogicAnalyzer
#
# Part of openSciLab, a port and extension of his software;
# the changes are described in docs/improvements.md and CHANGELOG.md.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Colours of the waveform display of the data view (port of ``Classes/AnalyzerColors.cs``).

The values live in :mod:`openscilab.ui.theme` (``display.*``); this module gives them the names
the painters of the display use.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

from ..core import regions
from ..driver.models import AnalyzerChannel
from .theme import DISPLAY_CHANNELS, contrast, qcolor

#: Waveform background: two close tones, so the rows stay apart without stripes
BG_CHANNEL_COLORS = (qcolor("display.background"), qcolor("display.background.alternate"))
#: Line between two channel rows
ROW_SEPARATOR_COLOR = qcolor("display.row.separator")
#: Vertical time grid of the waveform, at the labelled ticks of the ruler and between them
GRID_MAJOR_COLOR = qcolor("display.grid.major")
GRID_MINOR_COLOR = qcolor("display.grid.minor")
USER_LINE_COLOR = qcolor("display.line.user")
TRIGGER_LINE_COLOR = qcolor("display.line.trigger")
BURST_LINE_COLOR = qcolor("display.line.burst")
SAMPLE_LINE_COLOR = qcolor("display.sample.line")
TEXT_COLOR = qcolor("display.text")
SELECTION_COLOR = qcolor("display.selection")
#: the edges of the selection over the tracks
SELECTION_EDGE = qcolor("display.selection.edge")
#: Measurement cursors A and B, named markers (bookmarks) and the matches of a search
CURSOR_COLORS = {"A": qcolor("display.cursor.a"), "B": qcolor("display.cursor.b")}
BOOKMARK_COLOR = qcolor("display.bookmark")
SEARCH_HIT_COLOR = qcolor("display.search.hit")
SEARCH_CURRENT_COLOR = qcolor("display.search.current")
#: a level 0 drawn as text (listing)
LOW_LEVEL_COLOR = qcolor("display.level.low")
# (a region holds its colour as numbers; see core/regions.py)
DEFAULT_REGION_COLOR = QColor(*regions.DEFAULT_REGION_COLOR)


def region_color(region) -> QColor:
    """The colour of a region (the capture holds it as red, green, blue and alpha)."""
    return QColor(*region.region_color)


#: Channel colours of a new capture (``theme.DISPLAY_CHANNELS``); captures keep the colours stored
#: in their files.
PALETTE: tuple[QColor, ...] = tuple(QColor(value) for value in DISPLAY_CHANNELS)


def get_color(index: int) -> QColor:
    return PALETTE[index % len(PALETTE)]


def get_channel_color(channel: AnalyzerChannel) -> QColor:
    if channel.channel_color is None:
        return get_color(channel.channel_number)
    return color_from_uint(channel.channel_color)


def color_from_uint(value: int) -> QColor:
    """Convert an ARGB integer (the format used by the .lac files) to a colour."""
    return QColor(
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
        (value >> 24) & 0xFF,
    )


def color_to_uint(color: QColor) -> int:
    return (color.alpha() << 24) | (color.red() << 16) | (color.green() << 8) | color.blue()


def find_contrast(color: QColor) -> QColor:
    """Black or white, whichever reads better on ``color``."""
    return contrast(color)
