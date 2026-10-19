# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structure nodes: comments and groups on the canvas, bundles of several values on one wire."""

from __future__ import annotations

from typing import Any

from ...core import signals
from ..engine.runtime import NodeError, NodeRuntime
from .registry import In, Out, Param, collect, node


class _Passive(NodeRuntime):
    pass


@node("structure.comment", title="Comment", description="A note on the canvas.",
      params=[Param("text", "code", "Note", label="text", description="What the note says"),
              Param("size", "list", [200, 80], editor="hidden", label="size", description="Width and height")],
      icon="pencil", keywords=("note", "text", "sticky"))
class CommentNode(_Passive):
    pass


@node("structure.group", title="Group", description="A frame around nodes that belong together.",
      params=[Param("title", "str", "Group", label="title", description="The name of the group"),
              Param("size", "list", [360, 240], editor="hidden", label="size", description="Width and height"),
              Param("color", "str", "", editor="color", label="color", description="The colour of its frame")],
      icon="layers", keywords=("frame", "box", "section"))
class GroupNode(_Passive):
    pass


def _bundle_ports(params: dict):
    return [In(str(name), signals.ANY, f"The field {name}") for name in params.get("fields") or []], []


def _unbundle_ports(params: dict):
    return [], [Out(str(name), signals.ANY, f"The field {name}") for name in params.get("fields") or []]


# (data nodes, beside the structure nodes they share their way of naming ports with)
@node("data.bundle", title="Bundle",
      description="Several values on one wire: a mapping of the 'fields', sent when every field has a value "
                  "(or on every value with 'partial').",
      outputs=[Out("out", signals.ANY, "The fields as one value")],
      params=[Param("fields", "list", ["a", "b"], required=True, label="fields",
                    description="An input per field: names, or outputs of the flow to wire (▾)",
                    suggest="flow_outputs", editor="inputs"),
              Param("partial", "bool", False, label="partial",
                    description="Send on every value, before every field has one")],
      ports=_bundle_ports, icon="layers", summary=("fields",), keywords=("combine", "join", "record"))
class BundleNode(NodeRuntime):
    async def on_input(self, port: str, value: Any) -> None:
        fields = [str(name) for name in self.p("fields") or []]
        values = {name: self.ctx.latest(name) for name in fields}
        if self.p("partial", False) or all(item is not None for item in values.values()):
            self.ctx.emit("out", values)


@node("data.unbundle", title="Unbundle", description="The fields of a bundle on wires of their own.",
      inputs=[In("in", signals.ANY, "A bundle", optional=False)],
      params=[Param("fields", "list", ["a", "b"], required=True, suggest="upstream_fields", label="fields",
                    description="The fields of the bundle that get an output")],
      ports=_unbundle_ports, icon="layers", summary=("fields",), keywords=("split", "fields"))
class UnbundleNode(NodeRuntime):
    async def on_input(self, port: str, value: Any) -> None:
        if not isinstance(value, dict):
            raise NodeError(f"{self.node.id}: expects a bundle")
        for name in (str(field) for field in self.p("fields") or []):
            if value.get(name) is not None:
                self.ctx.emit(name, value[name])


#: node types the canvas draws as notes and frames instead of nodes with ports
DECORATIONS = ("structure.comment", "structure.group")

NODES = collect(globals())
