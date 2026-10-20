# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The SPICE netlist of a circuit (for ngspice).

* Each component writes the lines of its template (``library.py``) with the nets of its pins;
  a pin that is not on the placed unit of a part (the supply of a dual op-amp) takes the net of
  that pin on another unit of the same part, else the net named like the pin (as KiCad's hidden
  power pins do).
* Digital pins (XSPICE) meet their analog net through bridges: an input reads it with the
  thresholds of the circuit's supply, an output drives it.
* Supplies with a voltage get their source; nets with a single pin get a high resistance to ground
  (SPICE cannot solve a floating node); probes save their vectors.
* Models come from the model libraries (:mod:`.catalog.models`), with what they need.

Everything is written in a fixed order: the same circuit is always the same netlist.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import CircuitError, values
from .library import ComponentSpec, render
from .model import ANALYSES, Component, Schematic
from .nets import Netlist, Problem, extract
from .symbols import Pin

_SAFE = re.compile(r"[^A-Za-z0-9_]")


@dataclass(frozen=True)
class DevicePin:
    """A pin of a connected device in the circuit (``device.input`` / ``device.output``)."""

    component: str
    #: "in": the device reads the pin, its value drives the net; "out": the device drives the pin from the net
    direction: str
    #: the device (its name in the Devices section; "": the only one connected) and its pin
    device: str
    pin: str
    #: "digital" or "analog"
    signal: str
    #: a device input: the ``external`` source of the netlist its value goes into (lower case)
    source: str = ""
    #: a device output: the vector of the net it follows
    vector: str = ""


@dataclass
class Probe:
    """What a probe measures: a channel of the circuit."""

    component: str
    #: the name of its channel
    name: str
    #: "voltage", "current", "logic" or "scope" (a channel of an oscilloscope)
    kind: str
    #: the SPICE vector (``v(out)``, ``i(vam1)``)
    vector: str
    net: str = ""
    #: of an oscilloscope: its channel (CH1 ...) and the vector of its GND when that is not ground
    channel: str = ""
    reference: str = ""


@dataclass
class Circuit:
    """A netlist with what the simulation and the editor need to know about it."""

    lines: list[str] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    netlist: Netlist | None = None
    #: net name (as shown) -> its name in SPICE
    nets: dict[str, str] = field(default_factory=dict)
    #: component -> the vector of the current through it (LEDs, wires)
    currents: dict[str, str] = field(default_factory=dict)
    #: component -> its netlist lines (for changes while running: :func:`alterations`)
    elements: dict[str, list[str]] = field(default_factory=dict)
    #: the supply of the digital parts (V)
    vcc: float = 5.0
    #: the microcontrollers to run with it (``circuits/mcu/boards.py``)
    mcus: list = field(default_factory=list)
    #: start as switched on (every capacitor empty, no operating point first: ngspice's ``uic``)
    power_on: bool = False
    #: the pins of connected devices in the circuit (hardware in the loop)
    device_pins: list = field(default_factory=list)
    #: live, the currents of every element are measured (the current view, ``circuits/currents.py``): their
    #: device currents are saved and ammeters sit in front of subcircuits - slower, so only when shown
    show_currents: bool = False
    #: the parts that light with their current (LEDs, lamps): only their currents are computed while it
    #: runs live - ngspice evaluates every saved ``@part[i]`` at every step, which slows it down several times
    lights: set = field(default_factory=set)

    @property
    def errors(self) -> list[Problem]:
        return [problem for problem in self.problems if problem.severity == "error"]

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"

    def vector_of_net(self, net: str) -> str:
        name = self.nets.get(net, net)
        return "0" if name == "0" else f"v({name})"


def data_name(vector: str) -> str:
    """The name ngspice gives the points of ``vector`` while it runs: ``v(out)`` → ``out``,
    ``i(vam1)`` → ``vam1#branch``; device vectors (``@d1[id]``) stay."""
    text = vector.strip().lower()
    if text.startswith("v(") and text.endswith(")"):
        return text[2:-1]
    if text.startswith("i(") and text.endswith(")"):
        return f"{text[2:-1]}#branch"
    return text


def safe_name(name: str, taken: set[str]) -> str:
    text = str(name).replace("+", "p").replace("-", "m").replace("~", "n")
    text = _SAFE.sub("_", text) or "n"
    if text[0].isdigit() and text != "0":
        text = f"n{text}"
    candidate = text
    number = 2
    while candidate.lower() in taken and candidate != "0":
        candidate = f"{text}_{number}"
        number += 1
    taken.add(candidate.lower())
    return candidate


def part_base(component_id: str, spec: ComponentSpec) -> str:
    """The reference of the part a unit belongs to (``U1A`` → ``U1``)."""
    if spec.symbol.units > 1 and re.match(r"^.*\d[A-Z]$", component_id):
        return component_id[:-1]
    return component_id


def _params(component: Component, spec: ComponentSpec) -> dict[str, str]:
    params = spec.defaults()
    params.update({key: value for key, value in component.params.items() if value != "" or key not in params})
    return params


def analysis_line(kind: str, params: dict[str, str], nets: dict[str, str] | None = None) -> str:
    """The line of an analysis; ``nets`` (net name as shown -> its name in SPICE) resolves the output
    net of a noise analysis."""
    merged = dict(ANALYSES.get(kind, {}))
    merged.update({key: value for key, value in (params or {}).items() if value != ""})

    def number(key: str) -> str:
        return values.spice_number(merged[key])

    if kind == "op":
        return ".op"
    if kind == "dc":
        return f".dc {merged['source']} {number('start')} {number('stop')} {number('step')}"
    if kind == "ac":
        return f".ac {merged.get('scale', 'dec')} {int(float(number('points')))} {number('start')} {number('stop')}"
    if kind == "tran":
        return f".tran {number('step')} {number('stop')}"
    if kind == "sp":
        return f".sp {merged.get('scale', 'dec')} {int(float(number('points')))} {number('start')} {number('stop')}"
    if kind == "noise":
        output = (merged.get("output") or "").strip()
        if not output:
            raise CircuitError("the noise analysis needs an output net")
        if nets is not None:
            if output not in nets:
                raise CircuitError(f"the noise output {output!r} names no net of the circuit")
            output = nets[output]
        return (f".noise v({output}) {merged['source']} dec {int(float(number('points')))} "
                f"{number('start')} {number('stop')}")
    raise CircuitError(f"no analysis {kind!r}")


def emit(schematic: Schematic, resolve: Callable[[str], ComponentSpec | None], models=None,
         analysis: str | None = None, analysis_params: dict[str, str] | None = None,
         extra_lines: tuple[str, ...] = (), title: str = "", base_folder: str = "") -> Circuit:
    """The netlist of ``schematic``; ``analysis`` adds its line (``None``: none, for the live run)."""
    result = Circuit()
    netlist = extract(schematic, resolve)
    result.netlist = netlist
    result.problems.extend(netlist.problems)
    try:
        result.vcc = values.parse(schematic.setting("vcc"))
    except ValueError:
        result.problems.append(Problem("error", f"the supply {schematic.setting('vcc')!r} is no voltage"))
    taken: set[str] = {"0"}
    for name in sorted(netlist.nets, key=lambda n: (n != "0", n)):
        result.nets[name] = "0" if name == "0" else safe_name(name, taken)

    def net_name(name: str) -> str:
        if name not in result.nets:
            result.nets[name] = "0" if name == "0" else safe_name(name, taken)
        return result.nets[name]

    lines: list[str] = [f"* {title or schematic.name} (openSciLab)"]
    definitions: list[str] = []
    wanted_models: list[tuple[str, str]] = []  # (model, component)
    saves: list[str] = []
    specs = {ref: resolve(component.type) for ref, component in schematic.components.items()}
    bridges = False
    supplies: dict[str, tuple[str, str]] = {}
    for ref in sorted(schematic.components, key=_natural):
        component = schematic.components[ref]
        spec = specs.get(ref)
        if spec is None:
            continue  # (reported by the nets)
        params = _params(component, spec)
        if spec.role == "ground":
            continue
        if spec.role in ("probe.v", "probe.logic"):
            pin = spec.symbol.pins_of(component.unit)[0] if spec.symbol.pins_of(component.unit) else None
            net = netlist.net_of(ref, pin.number) if pin is not None else None
            if net is None:
                continue
            name = params.get("name") or net
            result.probes.append(Probe(ref, name, "logic" if spec.role == "probe.logic" else "voltage",
                                       result.vector_of_net(net), net))
            continue
        if spec.role == "probe.scope":
            pins = {pin.label.upper(): pin for pin in spec.symbol.pins_of(component.unit)}
            ground = pins.get("GND")
            ground_net = netlist.net_of(ref, ground.number) if ground is not None else None
            reference = result.vector_of_net(ground_net) if ground_net not in (None, "0") else ""
            for label in ("CH1", "CH2", "CH3", "CH4"):
                pin = pins.get(label)
                net = netlist.net_of(ref, pin.number) if pin is not None else None
                if net is None or len(netlist.nets.get(net, ())) < 2:
                    continue  # (a channel without a wire)
                result.probes.append(Probe(ref, f"{ref}_{label}", "scope", result.vector_of_net(net), net, label,
                                           reference))
            continue
        if spec.role == "power":
            voltage = str(params.get("voltage", "")).strip()
            net = next((netlist.net_of(ref, pin.number) for pin in spec.symbol.pins_of(component.unit)), None)
            if voltage and net and net != "0":
                if values.parse_optional(voltage) is None:
                    result.problems.append(Problem("error", f"{voltage!r} is no voltage", ref))
                elif net in supplies and values.parse_optional(supplies[net][0]) != values.parse_optional(voltage):
                    result.problems.append(Problem("error", f"the net {net} is given {supplies[net][0]} by "
                                                   f"{supplies[net][1]} already", ref))
                else:
                    supplies.setdefault(net, (voltage, ref))
            continue
        if spec.role == "mcu":
            _microcontroller(result, netlist, ref, component, spec, params, net_name, lines, definitions, base_folder,
                             analysis)
            continue
        if spec.model is None:
            if spec.physics:
                result.problems.append(Problem("error", f"{spec.title} is no electrical part ({spec.physics}): "
                                               "the plugin of that physics simulates it, not ngspice", ref))
                continue
            result.problems.append(Problem("error", f"{spec.title} has no simulation model", ref))
            continue
        template = spec.model.template_for(component.unit)
        if not template:
            continue
        pins = _pin_nets(schematic, netlist, specs, ref, component, spec, net_name, result.problems)
        placed_numbers = [pin.number for pin in spec.symbol.pins_of(component.unit)]
        if len(placed_numbers) >= 2 and len({pins.get(number) for number in placed_numbers}) == 1:
            # (every pin on one net - a wire runs through it: it does nothing; written, an element between
            # one node and itself made ngspice's operating point hang, not even a halt got through)
            continue
        bridge_lines: list[str] = []  # (they belong to the component: its pins' currents go through them)
        if spec.model.digital:
            digital_pins = {}
            used = set(re.findall(r"\{pin\.([^}]+)\}", template))
            for pin in spec.symbol.pins:
                if pin.number not in pins or pin.number not in used or pin.number in digital_pins:
                    continue
                node = f"d_{ref}_{_SAFE.sub('_', pin.number)}"
                digital_pins[pin.number] = node
                analog = pins[pin.number]
                if pin.kind in ("output", "tri_state", "open_collector", "open_emitter", "power_out"):
                    bridge_lines.append(f"A{ref}_o{_SAFE.sub('_', pin.number)} [{node}] [{analog}] osl_dac")
                elif pin.kind not in ("power_in", "no_connect"):
                    bridge_lines.append(f"A{ref}_i{_SAFE.sub('_', pin.number)} [{analog}] [{node}] osl_adc")
            lines.extend(bridge_lines)
            pins = digital_pins
            bridges = True
        try:
            text = render(template, ref, pins, params)
            own = [render(line, ref, pins, params) for line in spec.model.definitions]
        except CircuitError as error:
            result.problems.append(Problem("error", str(error), ref))
            continue
        element_lines = [line for line in text.splitlines() if line.strip()]
        if spec.role in ("device.in", "device.out"):
            device_pin = _device_pin(ref, spec, params, pins, element_lines)
            if device_pin is not None:
                result.device_pins.append(device_pin)
            if analysis is not None:  # (an analysis has no device: its input stays at 0 V)
                element_lines = [line.replace(" external", "") for line in element_lines]
        lines.extend(element_lines)
        result.elements[ref] = bridge_lines + element_lines
        for line in own:
            if line not in definitions:
                definitions.append(line)
        for model_name in spec.model.models:
            wanted_models.append((model_name, ref))
        for include in spec.model.includes:
            path = include if os.path.isabs(include) or not base_folder else os.path.join(base_folder, include)
            line = f'.include "{path}"'
            if line not in definitions:
                definitions.append(line)
        for param in spec.params:
            if param.kind == "model" and params.get(param.name) and not params[param.name].startswith("{"):
                wanted_models.append((params[param.name], ref))
        if spec.current:
            try:
                vector = render(spec.current, ref, pins, params).lower()
                result.currents[ref] = vector
                if spec.extra.get("lights") or spec.type == "led":
                    result.lights.add(ref)
                if vector.startswith("@"):
                    saves.append(vector)
            except CircuitError:
                pass
        if spec.role == "probe.i":
            name = params.get("name") or ref
            result.probes.append(Probe(ref, name, "current", result.currents.get(ref, f"i(v{ref.lower()})")))
    for net, (voltage, ref) in sorted(supplies.items()):
        lines.insert(1, f"V_{net_name(net)} {net_name(net)} 0 DC {values.spice_number(voltage)}")
    # nets with one pin: a high resistance to ground, or SPICE cannot solve them
    for name, pins in sorted(netlist.nets.items()):
        if name != "0" and len(pins) == 1 and name not in supplies:
            lines.append(f"R_float_{net_name(name)} {net_name(name)} 0 1G")
    if bridges:
        vcc = result.vcc
        definitions.append(f".model osl_adc adc_bridge(in_low={0.3 * vcc:g} in_high={0.7 * vcc:g})")
        definitions.append(f".model osl_dac dac_bridge(out_low=0 out_high={vcc:g} out_undef={vcc / 2:g} "
                           "t_rise=1n t_fall=1n)")
    # models
    if models is not None and wanted_models:
        names = list(dict.fromkeys(name for name, _ref in wanted_models))
        found, missing = models.resolve(names)
        for definition in found:
            lines.extend(definition.lines)
        missing_upper = {name.upper() for name in missing}
        for name, ref in wanted_models:
            if name.upper() in missing_upper:
                result.problems.append(Problem("error", f"the model {name} is in no model library", ref))
                missing_upper.discard(name.upper())
    lines.extend(definitions)
    lines.extend(extra_lines)
    options = ["noacct"]
    for setting, option in (("temperature", "temp"), ("tolerance", "reltol")):
        number = values.parse_optional(schematic.setting(setting))
        if number is not None and (setting != "tolerance" or number > 0):
            options.append(f"{option}={number:g}")
    lines.append(".options " + " ".join(options))
    # (device currents exist in time and DC only: asked for in an AC sweep, ngspice gives nothing)
    saves = list(dict.fromkeys(saves)) if analysis in (None, "op", "dc", "tran") else []
    lines.append(".save all" + ("".join(f" {vector}" for vector in saves)))
    result.power_on = schematic.setting("start") == "power on"
    if analysis:
        try:
            line = analysis_line(analysis, analysis_params if analysis_params is not None
                                 else schematic.analyses.get(analysis, {}), result.nets)
            lines.append(line + (" uic" if result.power_on and analysis == "tran" else ""))
        except (CircuitError, KeyError, ValueError) as error:
            result.problems.append(Problem("error", f"the analysis {analysis}: {error}"))
    lines.append(".end")
    result.lines = lines
    return result


def _microcontroller(result: Circuit, netlist: Netlist, ref: str, component: Component, spec: ComponentSpec,
                     params: dict[str, str], net_name, lines: list[str], definitions: list[str], base_folder: str,
                     analysis: str | None) -> None:
    """The output stages of an emulated microcontroller's pins (``circuits/mcu/boards.py``)."""
    import os

    from .mcu.boards import BOARDS, netlist_lines

    board = BOARDS.get(spec.board)
    if board is None:
        result.problems.append(Problem("error", f"openSciLab does not emulate the {spec.title}", ref))
        return
    pin_nets = {}
    for pin in spec.symbol.pins_of(component.unit):
        net = netlist.net_of(ref, pin.number)
        # (a pin alone on its net drives nothing: it gets no output stage, the simulation stays fast)
        if net is not None and len(netlist.nets.get(net, ())) > 1:
            pin_nets[pin.number] = (pin.label or pin.number, net_name(net))
    firmware = str(params.get("firmware", "")).strip()
    if firmware and not os.path.isabs(firmware) and base_folder:
        firmware = os.path.join(base_folder, firmware)
    if firmware and not os.path.isfile(firmware):
        result.problems.append(Problem("error", f"the firmware {os.path.basename(firmware)} is not there", ref))
        firmware = ""
    element_lines, info = netlist_lines(ref, board, pin_nets, result.vcc, firmware)
    if analysis is not None:
        # an analysis has no microcontroller running: its pins are inputs (high impedance)
        element_lines = [line.replace(" external", "") for line in element_lines]
    for line in element_lines:
        if line.startswith(".model"):
            if line not in definitions:
                definitions.append(line)
        else:
            lines.append(line)
    result.elements[ref] = [line for line in element_lines if not line.startswith(".model")]
    if analysis is None:
        result.mcus.append(info)


def _device_pin(ref: str, spec: ComponentSpec, params: dict, pins: dict, element_lines: list[str]):
    net = pins.get("1")
    if net is None:
        return None
    signal = str(params.get("signal") or "digital")
    common = dict(component=ref, device=str(params.get("device") or "").strip(), pin=str(params.get("pin") or "").strip(),
                  signal=signal if signal in ("digital", "analog") else "digital")
    if spec.role == "device.in":
        source = next((line.split()[0] for line in element_lines if line.endswith(" external")), "")
        return DevicePin(direction="in", source=source.lower(), **common)
    return DevicePin(direction="out", vector="0" if net == "0" else f"v({net})", **common)


def _pin_nets(schematic: Schematic, netlist: Netlist, specs: dict, ref: str, component: Component,
              spec: ComponentSpec, net_name: Callable[[str], str], problems: list | None = None) -> dict[str, str]:
    """Pin number -> SPICE net of every pin of the part (see the module for pins not on the unit)."""
    pins: dict[str, str] = {}
    placed = {pin.number for pin in spec.symbol.pins_of(component.unit)}
    base = part_base(ref, spec)
    for pin in spec.symbol.pins:
        if pin.number in pins:
            continue
        net = netlist.net_of(ref, pin.number) if pin.number in placed else None
        if net is None and pin.number not in placed:
            for other_ref, other in schematic.components.items():
                if other_ref != ref and other.type == component.type and part_base(other_ref, spec) == base:
                    net = netlist.net_of(other_ref, pin.number)
                    if net is not None:
                        break
            if net is None:
                net = _pin_global(pin)
                if net is not None and net not in netlist.nets and problems is not None:
                    # (a supply pin of a part whose other units are not placed: nothing drives that net)
                    problems.append(Problem("warning", f"pin {pin.label or pin.number} is on no net: it takes a "
                                                       f"net named {net} that nothing else is on", ref))
        if net is None:
            continue
        pins[pin.number] = net_name(net)
    return pins


def _pin_global(pin: Pin) -> str | None:
    from .nets import GROUND_NAMES

    label = pin.label
    if not label:
        return None
    return "0" if label.upper() in GROUND_NAMES else label


def _natural(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


# --------------------------------------------------------- live changes
_ELEMENT = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s+(.*)$")


def alterations(old: list[str], new: list[str]) -> list[str] | None:
    """ngspice commands that change the elements ``old`` into ``new`` while the simulation runs, or
    ``None`` when that needs a new start (other nets, another model, another kind of source)."""
    if len(old) != len(new):
        return None
    commands = []
    for before, after in zip(old, new):
        if before == after:
            continue
        a, b = _ELEMENT.match(before), _ELEMENT.match(after)
        if a is None or b is None or a.group(1).lower() != b.group(1).lower():
            return None
        name = a.group(1)
        old_words, new_words = a.group(2).split(), b.group(2).split()
        letter = name[0].upper()
        if letter in "RCL" and len(old_words) >= 3 and old_words[:2] == new_words[:2] and len(new_words) >= 3:
            if old_words[3:] != new_words[3:]:
                return None
            commands.append(f"alter {name} = {new_words[2]}")
        elif letter == "E" and len(old_words) == len(new_words) == 5 and old_words[:4] == new_words[:4]:
            commands.append(f"alter {name} gain = {new_words[4]}")  # (a voltage-controlled voltage source)
        elif letter == "F" and len(old_words) == len(new_words) == 4 and old_words[:3] == new_words[:3]:
            commands.append(f"alter {name} gain = {new_words[3]}")  # (a current-controlled current source)
        elif letter in "VI" and old_words[:2] == new_words[:2]:
            old_rest, new_rest = old_words[2:], new_words[2:]
            if len(old_rest) == len(new_rest) >= 2 and old_rest[0].upper() == new_rest[0].upper() == "DC" \
                    and old_rest[2:] == new_rest[2:]:
                commands.append(f"alter {name} dc = {new_rest[1]}")
            else:
                return None
        else:
            return None
    return commands
