# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""One-off: the node types, ports and parameters of openSciLab 0.1 named after the scheme of
``docs/design.md`` (``gen.tx_uart`` → ``generator.uart``, ``gpio.write`` → ``gpio.output`` …).

Flow files (``*.flow.yaml``) are rewritten structurally (types, parameter keys, the ports of wires,
also in subflows), panels (``*.panel.yaml``) get their bindings renamed through the types of their
flow's nodes, Python and Markdown files get the type names and the DSL (``n.gen.tx_uart(``) replaced.

    python tools/rename_nodes.py examples docs tests README.md

The tables below are the record of what was renamed; there is no compatibility layer (old files
are converted with this script).
"""

from __future__ import annotations

import os
import re
import sys

#: old type -> new type
NODES = {
    "device.instrument": "device.device",
    "gpio.write": "gpio.output",
    "gpio.read": "gpio.input",
    "gpio.dac": "gpio.analog_output",
    "gen.waveform": "generator.waveform",
    "gen.arbitrary": "generator.arbitrary",
    "gen.pattern": "generator.pattern",
    "gen.replay": "generator.replay",
    "gen.output": "generator.file",
    "gen.tx_uart": "generator.uart",
    "gen.tx_spi": "generator.spi",
    "gen.tx_i2c": "generator.i2c",
    "control.counter": "measure.count",
    "control.limit": "control.range",
    "dsp.threshold": "signal.threshold",
    "dsp.debounce": "signal.debounce",
    "dsp.filter": "signal.filter",
    "dsp.average": "signal.average",
    "dsp.derivative": "signal.derivative",
    "dsp.integral": "signal.integral",
    "dsp.fft": "signal.fft",
    "dsp.resample": "signal.resample",
    "dsp.math": "signal.math",
    "data.file": "data.write",
    "data.file_read": "data.read",
    "structure.bundle": "data.bundle",
    "structure.unbundle": "data.unbundle",
    "view.strip_chart": "view.chart",
    "report.image": "report.chart",
    "convert.edges": "convert.events",
    "convert.state_bit": "convert.bit",
    "convert.to_analog": "convert.analog",
    "convert.to_scalar": "convert.number",
    "timing.sync": "time.sync",
    "timing.align": "time.align",
    "timing.calibrate": "time.latency",
    "remote.receive": "remote.input",
    "remote.set": "remote.output",
    "remote.call": "remote.command",
    "remote.sync": "remote.align",
}
#: old group -> new group (types not in NODES keep their name after the dot: decode.uart -> decoder.uart)
GROUPS = {"gen": "generator", "dsp": "signal", "decode": "decoder", "timing": "time"}

#: (new type, old port) -> new port
PORTS = {
    ("measure.count", "count"): "out",
    ("control.range", "ok"): "result",
    ("control.range", "violation"): "failed",
    ("report.check", "pass"): "result",
    ("report.write", "passed"): "result",
    ("report.write", "write"): "trigger",
    ("data.buffer", "read"): "trigger",
    ("data.read", "read"): "trigger",
    ("gpio.analog_output", "volts"): "value",
    ("remote.align", "edges"): "matched",  # (only the output: the input 'edges' is kept, see below)
    ("time.align", "edges"): "matched",
}
#: ports whose old name stays on the input side (``edges`` in, ``matched`` out)
INPUT_KEEPS = {("remote.align", "edges"), ("time.align", "edges")}

#: (new type, old parameter) -> new parameter
PARAMS = {
    ("gpio.pwm", "freq"): "frequency",
    ("gpio.analog_output", "volts"): "value",
    ("control.compare", "op"): "operator",
    ("report.check", "name"): "title",
}


def new_type(old: str) -> str:
    if old in NODES:
        return NODES[old]
    group, dot, name = old.partition(".")
    return f"{GROUPS[group]}.{name}" if dot and group in GROUPS else old


def new_port(node_type: str, port: str, output: bool) -> str:
    key = (node_type, port)
    if not output and key in INPUT_KEEPS:
        return port
    return PORTS.get(key, port)


# ------------------------------------------------------------------- flows
def convert_flow_text(text: str) -> str:
    """A ``*.flow.yaml`` with the new names (types, parameters, the ports of wires; subflows too)."""
    import yaml

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return text
    _convert_flow(data)
    from openscilab.lab import yaml_io

    flow = yaml_io.from_data(data)
    return yaml_io.dumps(flow)


def _convert_flow(data: dict) -> None:
    nodes = data.get("nodes") or {}
    types = {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        old = str(node.get("type", ""))
        node["type"] = new = new_type(old)
        types[node_id] = new
        for (kind, old_param), new_param in PARAMS.items():
            if kind == new and old_param in node:
                node[new_param] = node.pop(old_param)
    edges = data.get("edges") or []
    data["edges"] = [_convert_edge(edge, types) for edge in edges]
    # the ports a subflow shows outside: inputs of its nodes, outputs of its nodes
    for section, output in (("inputs", False), ("outputs", True)):
        refs = data.get(section)
        if isinstance(refs, dict):
            data[section] = {name: _ref(str(ref), types, output) for name, ref in refs.items()}
    for body in (data.get("subflows") or {}).values():
        if isinstance(body, dict):
            _convert_flow(body)


def _convert_edge(edge, types: dict):
    if isinstance(edge, str) and "->" in edge:
        source, target = (part.strip() for part in edge.split("->", 1))
        return f"{_ref(source, types, True)} -> {_ref(target, types, False)}"
    if isinstance(edge, dict):
        return {key: _ref(str(value), types, key in ("from", "source")) for key, value in edge.items()}
    return edge


def _ref(ref: str, types: dict, output: bool) -> str:
    node, dot, port = ref.partition(".")
    if not dot or node not in types:
        return ref
    return f"{node}.{new_port(types[node], port, output)}"


# ------------------------------------------------------------------- panels
def convert_panel(path: str) -> bool:
    from openscilab.lab import panel_model, yaml_io

    panel = panel_model.load(path)
    flow_file = panel_model.flow_path(panel, path)
    if not flow_file or not os.path.exists(flow_file):
        return False
    types = {node_id: node.type for node_id, node in yaml_io.load(flow_file).nodes.items()}
    changed = False
    for widget in panel.widgets:
        node, dot, port = widget.bind.partition(".")
        if dot and node in types:
            output = widget.kind not in panel_model.CONTROL_KINDS
            bind = f"{node}.{new_port(types[node], port, output)}"
            changed |= bind != widget.bind
            widget.bind = bind
    if changed:
        panel_model.save(panel, path)
    return changed


# -------------------------------------------------------------------- text
def _decoder_ids() -> list[str]:
    """The ids of the protocol decoders (``decode.uart`` became ``decoder.uart``)."""
    try:
        from openscilab.lab.nodes import decode

        return sorted({spec.type.split(".", 1)[1] for spec in decode.nodes()}, key=len, reverse=True)
    except Exception:  # noqa: BLE001 - without the decoders: the common ones
        return ["uart", "spi", "i2c", "can", "onewire_link", "usb_packet", "jtag", "eeprom24xx", "counter", "c64bus"]


#: an old type, not where it is a call of something else (``gpio.write(pin, 1)`` of a GPIO facet)
_TYPE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(old) for old in sorted(NODES, key=len, reverse=True))
                   + r")(?![\w(])")
_DECODER = re.compile(r"(?<![\w.])decode\.(" + "|".join(re.escape(name) for name in _decoder_ids()) + r")(?![\w(])")
_DSL = re.compile(r"\bn\.(gen|dsp|decode|timing|device|gpio|control|data|structure|view|report|convert|remote)"
                  r"\.([a-z_0-9]+)\(")


def convert_text(text: str) -> str:
    """Type names in Python and Markdown (``"gen.tx_uart"``, ``n.gen.tx_uart(``, ``decode.uart``)."""
    text = _DSL.sub(lambda match: f"n.{new_type(match.group(1) + '.' + match.group(2))}(", text)
    text = _TYPE.sub(lambda match: NODES[match.group(1)], text)
    return _DECODER.sub(lambda match: f"decoder.{match.group(1)}", text)


def main(paths: list[str]) -> int:
    changed = 0
    for root in paths:
        files = [root] if os.path.isfile(root) else [os.path.join(folder, name) for folder, _dirs, names in
                                                     os.walk(root) for name in names]
        for path in sorted(files):
            if "__pycache__" in path:
                continue
            with open(path, encoding="utf-8") as handle:
                try:
                    text = handle.read()
                except UnicodeDecodeError:
                    continue
            if path.endswith((".flow.yaml", ".flow.yml")):
                new = convert_flow_text(text)
            elif path.endswith((".py", ".md")):
                new = convert_text(text)
            else:
                continue
            if new != text:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(new)
                changed += 1
                print("converted", path)
    for root in paths:
        for folder, _dirs, names in os.walk(root) if os.path.isdir(root) else []:
            for name in names:
                if name.endswith(".panel.yaml") and convert_panel(os.path.join(folder, name)):
                    changed += 1
                    print("converted", os.path.join(folder, name))
    print(f"{changed} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
