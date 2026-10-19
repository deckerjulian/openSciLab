# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Building blocks of a digital pattern: the track of one pin made from a kind and a few settings
(a clock, a square wave, a constant level, a bit of a counter, bytes, UART text) instead of SDL
written by hand. A block is written as SDL behind a comment that names it::

    // clock period=2 start=low
    {l1,h1,}100;

The comment brings the block back when the pattern is opened again; SDL changed by hand (or
without such a comment) stays SDL text. The pattern editor of ``generator.pattern`` builds on this.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import waveform as waves

#: the kinds of blocks, as the editor offers them (``sdl``: the text as it is)
KINDS = ("clock", "square", "constant", "counter", "bits", "uart", "sdl")
#: the settings of each kind and their defaults
DEFAULTS: dict[str, dict[str, Any]] = {
    "clock": {"period": 2, "start": "low"},
    "square": {"high": 4, "low": 4, "start": "high"},
    "constant": {"level": "low"},
    "counter": {"bit": 0, "step": 1},
    "bits": {"data": "55", "bit": 1, "order": "msb", "idle": "low"},
    "uart": {"data": "Hi", "baud": 9600},
    "sdl": {"text": "l1;"},
}
#: SDL comment that names a block
MARK = "//"


class BlockError(ValueError):
    """Settings a block cannot be made from."""


@dataclass
class Block:
    kind: str
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise BlockError(f"no block {self.kind!r} (known: {', '.join(KINDS)})")
        self.settings = {**DEFAULTS[self.kind], **self.settings}

    def value(self, name: str) -> Any:
        return self.settings.get(name, DEFAULTS[self.kind].get(name))

    def _count(self, name: str, low: int = 1) -> int:
        try:
            value = int(self.value(name))
        except (TypeError, ValueError):
            raise BlockError(f"{self.kind}: {name} must be a whole number") from None
        if value < low:
            raise BlockError(f"{self.kind}: {name} must be at least {low}")
        return value

    def period(self) -> list[int]:
        """The levels of one period of a periodic block (empty for the others)."""
        if self.kind == "clock":
            period = self._count("period", 2)
            first = 1 if self.value("start") == "high" else 0
            return [first] * (period // 2) + [1 - first] * (period - period // 2)
        if self.kind == "square":
            high, low = self._count("high"), self._count("low")
            run = [1] * high + [0] * low
            return run if self.value("start") != "low" else run[high:] + run[:high]
        if self.kind == "counter":
            half = self._count("step") << self._count("bit", 0)
            return [0] * half + [1] * half
        return []

    def data(self) -> bytes:
        """The bytes of a ``bits`` block (hex, separated by spaces) or the text of a ``uart`` block."""
        text = str(self.value("data") or "")
        if self.kind == "uart":
            return text.encode("utf-8")
        try:
            return bytes(int(item, 16) for item in text.replace(",", " ").split())
        except ValueError:
            raise BlockError(f"bits: data are bytes in hex, e.g. '55 AA' (not {text!r})") from None

    def natural_length(self, rate: float) -> int:
        """Samples the block needs: a period, its bytes or the SDL text."""
        if self.kind == "constant":
            return 1
        if self.kind in ("clock", "square", "counter"):
            return len(self.period())
        return len(self._own_levels(rate))

    def _own_levels(self, rate: float) -> np.ndarray:
        if self.kind == "bits":
            per_bit = self._count("bit")
            bits = []
            for byte in self.data():
                order = range(7, -1, -1) if self.value("order") == "msb" else range(8)
                bits += [(byte >> bit) & 1 for bit in order]
            return np.repeat(np.asarray(bits, dtype=np.uint8), per_bit)
        if self.kind == "uart":
            try:
                return np.asarray(waves.uart_tracks(self.data(), baud=float(self.value("baud")), rate=rate,
                                                    pin="TX")["TX"], dtype=np.uint8)
            except waves.WaveformError as error:
                raise BlockError(f"uart: {error}") from None
        if self.kind == "sdl":
            from ..sdl.parser import SDLError, samples_from_source

            try:
                return np.asarray(samples_from_source(str(self.value("text") or "")), dtype=np.uint8)
            except SDLError as error:
                raise BlockError(f"SDL: {error}") from None
        return np.zeros(0, np.uint8)

    def levels(self, length: int, rate: float) -> np.ndarray:
        """The levels of ``length`` samples: periods repeated; bytes, UART and SDL followed by their
        last level (``idle`` for bytes)."""
        if self.kind == "constant":
            return np.full(length, 1 if self.value("level") == "high" else 0, dtype=np.uint8)
        period = self.period()
        if period:
            return np.resize(np.asarray(period, dtype=np.uint8), length)
        own = self._own_levels(rate)[:length]
        rest = (1 if self.value("idle") == "high" else 0) if self.kind == "bits" else (int(own[-1]) if len(own) else 0)
        return np.concatenate([own, np.full(length - len(own), rest, dtype=np.uint8)])

    def sdl(self, length: int, rate: float) -> str:
        """The block as SDL of ``length`` samples, behind the comment that names it (``sdl``: its text)."""
        if self.kind == "sdl":
            return str(self.value("text") or "")
        return f"{self.comment()}\n{self._body(length, rate)}"

    def _body(self, length: int, rate: float) -> str:
        period = self.period()
        if period and len(period) <= length:
            repeats, rest = divmod(length, len(period))
            runs = waves.levels_to_sdl(period).rstrip(";").split(";")
            text = "{" + ",".join(runs) + ",}" + f"{repeats};"
            return text + (waves.levels_to_sdl(period[:rest]) if rest else "")
        return waves.levels_to_sdl(self.levels(length, rate))

    def comment(self) -> str:
        values = " ".join(f"{name}={shlex.quote(str(self.value(name)))}" for name in DEFAULTS[self.kind])
        return f"{MARK} {self.kind} {values}"


def from_sdl(text: str, length: int, rate: float) -> Block:
    """The block written as ``text`` (with :meth:`Block.sdl` at ``length``), or an ``sdl`` block of
    the text: without the comment, or when the SDL was changed by hand."""
    text = str(text or "")
    first, _, body = text.partition("\n")
    if first.startswith(MARK):
        try:
            words = shlex.split(first[len(MARK):])
            if words and words[0] in KINDS and words[0] != "sdl":
                settings = dict(word.split("=", 1) for word in words[1:] if "=" in word)
                for name, default in DEFAULTS[words[0]].items():
                    if isinstance(default, int) and name in settings:
                        settings[name] = int(settings[name])
                block = Block(words[0], settings)
                if block._body(length, rate).strip() == body.strip():
                    return block
        except (ValueError, BlockError):
            pass
    return Block("sdl", {"text": text})


def pattern_length(blocks: dict[str, Block], rate: float) -> int:
    """Samples a pattern of ``blocks`` needs: the longest one's natural length (a period, its bytes)."""
    return max((block.natural_length(rate) for block in blocks.values()), default=1) or 1


def tracks(blocks: dict[str, Block], length: int, rate: float) -> dict[str, str]:
    """SDL per pin (the ``tracks`` of ``generator.pattern``)."""
    return {pin: block.sdl(length, rate) for pin, block in blocks.items()}


def read_tracks(sources: dict[str, str], rate: float) -> tuple[dict[str, Block], int]:
    """The blocks of ``tracks`` and the length of their pattern (the longest track)."""
    from ..sdl.parser import SDLError, samples_from_source

    length = 0
    for text in sources.values():
        try:
            length = max(length, len(samples_from_source(str(text))))
        except SDLError:
            continue
    length = length or 1
    return {str(pin): from_sdl(str(text), length, rate) for pin, text in sources.items()}, length
