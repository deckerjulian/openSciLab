# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The node reference of ``docs/lab.md`` (the part between ``<!-- nodes:begin -->`` and
``<!-- nodes:end -->``), written from the node registry: what the palette, the inspector and the
documentation say about a node is the same text.

    python tools/gen_node_docs.py          # writes docs/lab.md
    python tools/gen_node_docs.py --check  # exits 1 when docs/lab.md is not up to date
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "lab.md")
BEGIN, END = "<!-- nodes:begin -->", "<!-- nodes:end -->"

#: a sentence before the table of a group
INTROS = {
    "decoder": "Every sigrok protocol decoder is a node (133 of them, e.g. `decoder.uart`, `decoder.i2c`, "
               "`decoder.spi`, `decoder.can`, `decoder.onewire_link`, `decoder.usb_packet`, `decoder.jtag`): a capture "
               "at `in`, the annotations as `events`, a `table` and `text` at the outputs. A stacked decoder "
               "(e.g. `decoder.eeprom24xx` on I²C) takes the capture itself, runs the decoder below it and needs "
               "that decoder's `channels`.",
    "time": "How the samples of several devices get one time axis: [docs/timing.md](timing.md).",
    "remote": "Measuring devices on other computers ([docs/remote.md](remote.md)); they run in real time.",
}


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _ports(ports) -> str:
    return ", ".join(f"`{port.name}`" for port in ports) or "–"


def reference() -> str:
    from openscilab.lab.nodes.registry import GROUPS, Registry

    registry = Registry()
    groups: dict = {}
    for spec in registry.specs(lazy=False):
        groups.setdefault(spec.group, []).append(spec)
    lines = [BEGIN, "", "Every node with its inputs and outputs; the inspector shows what each port and setting "
             "means. Written from the node registry (`python tools/gen_node_docs.py`).", ""]
    for key, title in GROUPS.items():
        specs = groups.get(key, [])
        if not specs and key not in INTROS:
            continue
        lines += [f"### {title}", ""]
        if key in INTROS:
            lines += [INTROS[key], ""]
        if not specs:
            continue
        lines += ["| Node | Title | In | Out | What it does |", "| --- | --- | --- | --- | --- |"]
        for spec in sorted(specs, key=lambda spec: spec.type):
            lines.append(f"| `{spec.type}` | {_cell(spec.title)} | {_ports(spec.inputs)} | {_ports(spec.outputs)} | "
                         f"{_cell(spec.description)} |")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def updated(text: str) -> str:
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + reference() + text[end:]


def main(argv: list[str]) -> int:
    with open(DOC, encoding="utf-8") as handle:
        text = handle.read()
    new = updated(text)
    if "--check" in argv:
        if new != text:
            print("docs/lab.md: the node reference is not up to date (python tools/gen_node_docs.py)")
            return 1
        return 0
    with open(DOC, "w", encoding="utf-8") as handle:
        handle.write(new)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    sys.exit(main(sys.argv[1:]))
