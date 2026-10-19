# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Conversion nodes: the editor offers them when a wire's types do not fit (``core.signals.CONVERSIONS``)."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from ...core import signals
from ..engine.runtime import NodeError, NodeRuntime
from .registry import In, Out, Param, collect, node


class _Convert(NodeRuntime):
    source = signals.ANY
    target = signals.ANY

    def options(self) -> dict:
        return {}

    async def on_input(self, port: str, value: Any) -> None:
        if isinstance(value, (int, float)) and self.source == signals.SCALAR:
            value = signals.Scalar(value=float(value), at=self.ctx.now())
        if isinstance(value, bool) and self.source == signals.BOOL:
            value = signals.Bool(value=value, at=self.ctx.now())
        if not isinstance(value, signals.TYPES[self.source]):
            raise NodeError(f"{self.node.id}: expects {self.source}, got {type(value).__name__}")
        conversion = signals.conversion(self.source, self.target)
        self.ctx.emit("out", conversion.function(value, **self.options()))


@node("convert.analog", title="Analog", description="Logic levels as voltages: 0 becomes 'low', 1 becomes 'high'.",
      inputs=[In("in", signals.DIGITAL, "A digital signal", optional=False)],
      outputs=[Out("out", signals.ANALOG, "The levels as voltages")],
      params=[Param("low", "quantity", 0.0, "V", label="0 is", description="The voltage of level 0"),
              Param("high", "quantity", 3.3, "V", label="1 is", description="The voltage of level 1")],
      icon="wave", summary=("low", "high"), keywords=("digital to analog", "volts", "levels"))
class ToAnalog(_Convert):
    source, target = signals.DIGITAL, signals.ANALOG

    def options(self) -> dict:
        return {"low": self.q("low"), "high": self.q("high")}


@node("convert.events", title="Events", description="The edges of a digital signal as events.",
      inputs=[In("in", signals.DIGITAL, "A digital signal", optional=False)],
      outputs=[Out("out", signals.EVENT, "An event for every edge (its new level as the value)")],
      params=[Param("kind", "choice", "both", choices=("both", "rising", "falling"), label="edges",
                    description="Which edges become events")],
      icon="wave", summary=("kind",), keywords=("edges", "transitions"))
class Edges(_Convert):
    source, target = signals.DIGITAL, signals.EVENT

    async def setup(self) -> None:
        #: the last level of the previous block and when it ended (a stream: an edge between two
        #: blocks is an edge too)
        self.last: Optional[tuple[int, float]] = None

    def options(self) -> dict:
        return {"kind": self.p("kind", "both")}

    async def on_input(self, port: str, value: Any) -> None:
        if not isinstance(value, signals.Digital):
            raise NodeError(f"expects {self.source}, got {type(value).__name__}")
        kind = self.p("kind", "both")
        found = value.edges(kind)
        if len(value) and value.time.is_known:
            start = value.time.time_of(0)
            last = self.last
            rate = value.rate or 0.0
            follows = last is not None and rate > 0 and abs(start - last[1]) < 1.5 / rate
            first = int(value.values[0])
            if follows and first != last[0] and (kind == "both" or (kind == "rising") == (first == 1)):
                found = signals.Event(name=found.name, times=np.concatenate([[start], found.times]),
                                      data=[first] + list(found.data))
            self.last = (int(value.values[-1]), value.time.end(len(value)) if value.time.is_uniform else start)
        self.ctx.emit("out", found)


@node("convert.number", title="Number", description="1 for true, 0 for false.",
      inputs=[In("in", signals.BOOL, "A truth value", optional=False)],
      outputs=[Out("out", signals.SCALAR, "1 or 0")], icon="arrow-right", keywords=("bool", "true", "false"))
class ToScalar(_Convert):
    source, target = signals.BOOL, signals.SCALAR


@node("convert.bit", title="Bit",
      description="One channel of the states (values taken on the edges of a clock) as a digital signal, "
                  "one sample per state.",
      inputs=[In("in", signals.STATES, "The states of a capture in state mode", optional=False)],
      outputs=[Out("out", signals.DIGITAL, "The channel, one sample per state")],
      params=[Param("bit", "any", 0, description="The channel's name (D3), or its number (0: the first)",
                    label="channel", suggest="upstream_channels")],
      icon="channels", summary=("bit",), keywords=("state", "channel", "bit"))
class StateBit(_Convert):
    source, target = signals.STATES, signals.DIGITAL

    async def on_input(self, port: str, value: Any) -> None:
        if isinstance(value, signals.States):
            self.index = self.bit_of(value)
        await super().on_input(port, value)

    def bit_of(self, states: signals.States) -> int:
        bit = self.p("bit", 0)
        if isinstance(bit, str) and not bit.strip().isdigit():
            if bit not in states.channels:
                raise NodeError(f"the states have no channel {bit!r} ({', '.join(states.channels) or 'none named'})")
            return states.channels.index(bit)
        index = int(bit)
        if index < 0 or index >= (len(states.channels) or 32):
            raise NodeError(f"the states have {len(states.channels)} channels (bits 0 to {len(states.channels) - 1}), "
                            f"no bit {index}")
        return index

    def options(self) -> dict:
        return {"bit": self.index}


@node("convert.channel", title="Channel",
      description="One channel of a capture: 'channel' by its name, else the first one of 'kind' (any, digital, "
                  "analog).",
      inputs=[In("in", signals.CAPTURE, "A capture", optional=False)],
      outputs=[Out("out", signals.ANY, "The channel: a digital or analog signal")],
      params=[Param("channel", "str", "", suggest="upstream_channels", label="channel",
                    description="The channel by its name (empty: the first of 'kind')"),
              Param("kind", "choice", "any", choices=("any", "digital", "analog"), label="kind",
                    description="Without a name: the first channel of this kind")],
      icon="channels", summary=("channel",), keywords=("extract", "pick", "select"))
class Channel(NodeRuntime):
    async def on_input(self, port: str, value: Any) -> None:
        if not isinstance(value, signals.Capture):
            raise NodeError("expects a capture")
        kind = self.p("kind", "any")
        choices = {"digital": list(value.digital), "analog": list(value.analog)}.get(kind, value.channels)
        name = self.p("channel") or next(iter(choices), None)
        if name is None:
            raise NodeError(f"the capture has no {'' if kind == 'any' else kind + ' '}channels")
        if name not in value.channels:
            raise NodeError(f"the capture has no channel {name} (it has {', '.join(value.channels)})")
        self.ctx.emit("out", value.channel(name))


NODES = collect(globals())
