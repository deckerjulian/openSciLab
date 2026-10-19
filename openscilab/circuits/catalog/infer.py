# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""How a part of a foreign library is simulated, inferred as KiCad does - and a bit further.

1. Its ``Sim.*`` fields (KiCad 7+): ``Sim.Device`` (R, C, L, V, I, D, NPN, PNP, NMOS, PMOS, NJFET,
   PJFET, SUBCKT), ``Sim.Type``, ``Sim.Pins`` (``1=K 2=A``: the pins of the symbol to those of the
   model), ``Sim.Params``, ``Sim.Name`` and ``Sim.Library``.
2. Else its reference and pins: R, C, L with a value; diodes with pins K and A; transistors with
   B, C, E or G, D, S - the model of the part's value when a model library has it, a standard
   model ("generic") else.
3. Families openSciLab knows: operational amplifiers and comparators (by the kinds of their pins:
   ``+``, ``-``, the output, ``V+``/``V-``), linear regulators (78xx, xx-3.3 ...), the 555, switches
   and push buttons (operable while the circuit runs), the gates of the 74xx and 4000 series, and
   the microcontrollers openSciLab emulates.

Everything else can be drawn but not simulated (its component says so).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .. import values
from ..library import ControlSpec, ParamSpec, SimModel
from ..symbols import Pin, Symbol


@dataclass
class Inferred:
    model: SimModel | None = None
    params: tuple[ParamSpec, ...] = ()
    role: str = ""
    controls: tuple[ControlSpec, ...] = ()
    board: str = ""
    show: tuple[str, ...] = ("value",)
    current: str = ""
    lights: bool = False
    extra: dict = field(default_factory=dict)


def _clean(name: str) -> str:
    return re.sub(r"~\{([^}]*)\}", r"\1", name or "").strip().upper()


def _pin(pins: list[Pin], *names: str) -> Pin | None:
    wanted = [name.upper() for name in names]
    for name in wanted:
        for pin in pins:
            if _clean(pin.name) == name or _clean(pin.name).split("/")[0] == name:
                return pin
    return None


def _value_param(value: str, label: str, unit: str, default: str, live: bool = True) -> ParamSpec:
    text = value if values.is_value(value) else default
    return ParamSpec("value", label, text, unit, f"The {label.lower()}.", live=live)


def _model_param(value: str, label: str = "Model", description: str = "") -> ParamSpec:
    return ParamSpec("value", label, value, kind="model",
                     description=description or "The name of the model (a model library of the circuit can add more).")


def parse_pins(text: str) -> dict[str, str]:
    """``Sim.Pins``: symbol pin number -> model pin name."""
    result = {}
    for item in (text or "").split():
        number, _sep, name = item.partition("=")
        if number and name:
            result[number] = name
    return result


def parse_params(text: str) -> dict[str, str]:
    """``Sim.Params``: ``dc=0 ampl=1 f=1k pwl="0 0 1m 5"``."""
    result = {}
    try:
        items = shlex.split(text or "")
    except ValueError:
        items = (text or "").split()
    for item in items:
        key, sep, value = item.partition("=")
        if sep:
            result[key.strip().lower()] = value.strip()
    return result


def _definition(models, name: str, parts: tuple[str, ...] = ()):
    """The definition ``name`` of the model libraries when it is one of ``parts`` (``()``: any)."""
    if models is None or not name or name not in models:
        return None
    definition = models.get(name)
    if parts and definition.part not in parts:
        return None
    return definition


def _has_model(models, name: str, parts: tuple[str, ...] = ()) -> bool:
    return _definition(models, name, parts) is not None


def _first_model(models, *names: str, parts: tuple[str, ...] = ()) -> str | None:
    for name in names:
        definition = _definition(models, name, parts)
        if definition is not None:
            return definition.name
    return None


def _own_model(template: str, model: str, definitions: tuple[str, ...]) -> tuple[str, tuple]:
    """A part with a model of its own (``.model {ref}_m …`` from its Sim.Params): the template names it
    directly - ``{value}`` would need a second rendering - and there is no model parameter."""
    if definitions and model == "{ref}_m":
        return template.replace("{value}", "{ref}_m"), ()
    return template, (_model_param(model),)


def _mos_template(models, model: str, d: str, g: str, s: str) -> tuple[str, str]:
    """The line of a MOSFET for ``model``: a VDMOS (three terminals), a level 1/BSIM model (bulk to
    source) or a vendor subcircuit (drain, gate, source); and its quality."""
    definition = _definition(models, model)
    if definition is None or definition.part in ("nmos", "pmos") and definition.device == "VDMOS":
        return f"{{ref:M}} {{pin.{d}}} {{pin.{g}}} {{pin.{s}}} {{value}}", "exact" if definition else "generic"
    if definition.kind == "subckt":
        return f"{{ref:X}} {{pin.{d}}} {{pin.{g}}} {{pin.{s}}} {{value}}", "generic"
    return f"{{ref:M}} {{pin.{d}}} {{pin.{g}}} {{pin.{s}}} {{pin.{s}}} {{value}}", "exact"


# ------------------------------------------------------------- Sim.* fields
def from_sim_fields(properties: dict[str, str], symbol: Symbol, models) -> Inferred | None:
    device = properties.get("Sim.Device", "").strip().upper()
    if not device or properties.get("Sim.Enable", "1").strip() == "0":
        return None
    kind = properties.get("Sim.Type", "").strip().upper()
    pin_map = parse_pins(properties.get("Sim.Pins", ""))
    params = parse_params(properties.get("Sim.Params", ""))
    value = properties.get("Value", "")
    name = properties.get("Sim.Name", "").strip()
    pins = symbol.pins_of(1) if symbol.units <= 1 else symbol.pins

    def pin_for(model_pin: str, fallback_index: int) -> str:
        for number, model_name in pin_map.items():
            if model_name.upper() == model_pin.upper():
                return number
        return pins[fallback_index].number if fallback_index < len(pins) else str(fallback_index + 1)

    if device in ("R", "C", "L"):
        a, b = pin_for("+", 0), pin_for("-", 1)
        key = {"R": "r", "C": "c", "L": "l"}[device]
        default = params.get(key, value)
        if kind == "POT":
            return _pot(symbol, pin_map, params, value)
        unit = {"R": "Ω", "C": "F", "L": "H"}[device]
        label = {"R": "Resistance", "C": "Capacitance", "L": "Inductance"}[device]
        return Inferred(SimModel(f"{{ref:{device}}} {{pin.{a}}} {{pin.{b}}} {{value}}"),
                        (_value_param(default, label, unit, "1k" if device == "R" else "100n" if device == "C" else "10m"),),
                        current=f"@{{ref:{device}}}[i]")
    if device in ("V", "I"):
        a, b = pin_for("+", 0), pin_for("-", 1)
        spec_params, text = _source(kind or "DC", params, value)
        if text is None:
            return None
        letter = device
        first, second = (a, b) if device == "V" else (b, a)
        return Inferred(SimModel(f"{{ref:{letter}}} {{pin.{first}}} {{pin.{second}}} {text}"), spec_params,
                        show=tuple(p.name for p in spec_params[:2]), current="i({ref:V})" if device == "V" else "")
    if device == "D":
        k, a = pin_for("K", 0), pin_for("A", 1)
        model = name or value
        definitions = ()
        if kind and kind != "SPICE" or (properties.get("Sim.Params") and not name):
            definitions = (f".model {{ref}}_m D({_model_args(params)})",)
            model = "{ref}_m"
        template = f"{{ref:D}} {{pin.{a}}} {{pin.{k}}} {{value}}"
        if _definition(models, model) is not None and _definition(models, model).kind == "subckt":
            template = f"{{ref:X}} {{pin.{a}}} {{pin.{k}}} {{value}}"  # (a vendor diode as a subcircuit)
        template, model_params = _own_model(template, model, definitions)
        return Inferred(SimModel(template, quality="exact" if _has_model(models, model) or definitions else "generic",
                                 definitions=definitions),
                        model_params, current="@{ref:D}[id]",
                        lights="LED" in properties.get("Value", "").upper() or "LED" in properties.get("Description", "").upper())
    if device in ("NPN", "PNP"):
        c, b, e = pin_for("C", 0), pin_for("B", 1), pin_for("E", 2)
        model, definitions = _device_model(name or value, device, kind, params, models, "NPN" if device == "NPN" else "PNP",
                                           parts=("npn", "pnp"))
        template, model_params = _own_model(f"{{ref:Q}} {{pin.{c}}} {{pin.{b}}} {{pin.{e}}} {{value}}", model, definitions)
        return Inferred(SimModel(template, definitions=definitions,
                                 quality="exact" if _has_model(models, model) or definitions else "generic"),
                        model_params, current="@{ref:Q}[ic]")
    if device in ("NMOS", "PMOS"):
        d, g, s = pin_for("D", 0), pin_for("G", 1), pin_for("S", 2)
        model, definitions = _device_model(name or value, "VDMOS", kind, params, models,
                                           "NMOS" if device == "NMOS" else "PMOS",
                                           extra="pchan" if device == "PMOS" else "", parts=("nmos", "pmos", "subckt"))
        template, quality = _mos_template(models, model, d, g, s)
        template, model_params = _own_model(template, model, definitions)
        return Inferred(SimModel(template, definitions=definitions, quality="exact" if definitions else quality),
                        model_params, current="@{ref:M}[id]")
    if device in ("NJFET", "PJFET"):
        d, g, s = pin_for("D", 0), pin_for("G", 1), pin_for("S", 2)
        model, definitions = _device_model(name or value, "NJF" if device == "NJFET" else "PJF", kind, params, models, "",
                                           parts=("njfet", "pjfet"))
        template, model_params = _own_model(f"{{ref:J}} {{pin.{d}}} {{pin.{g}}} {{pin.{s}}} {{value}}", model, definitions)
        return Inferred(SimModel(template, definitions=definitions), model_params)
    if device == "SUBCKT" and name:
        definition = models.get(name) if models is not None else None
        order = list(definition.pins) if definition is not None else list(dict.fromkeys(pin_map.values()))
        numbers = []
        for model_pin in order:
            number = next((n for n, m in pin_map.items() if m.upper() == model_pin.upper()), None)
            if number is None:
                return None
            numbers.append(number)
        args = " ".join(f"{key}={value}" for key, value in params.items())
        template = "{ref:X} " + " ".join(f"{{pin.{n}}}" for n in numbers) + " {value}" + (f" {args}" if args else "")
        return Inferred(SimModel(template, quality="exact" if definition is not None else "none"),
                        (_model_param(name, "Subcircuit"),))
    return None


def _model_args(params: dict[str, str]) -> str:
    return " ".join(f"{key}={value}" for key, value in params.items() if key not in ("model", "lib", "type"))


def _device_model(name: str, device: str, kind: str, params: dict, models, generic: str,
                  extra: str = "", parts: tuple[str, ...] = ()) -> tuple[str, tuple[str, ...]]:
    if _has_model(models, name, parts):
        return models.get(name).name, ()
    if params and kind not in ("", "SPICE"):
        args = (extra + " " + _model_args(params)).strip()
        return "{ref}_m", (f".model {{ref}}_m {device}({args})",)
    return (generic or name), ()


def _pot(symbol: Symbol, pin_map: dict[str, str], params: dict[str, str], value: str) -> Inferred:
    def number(model_pin: str, index: int) -> str:
        for n, m in pin_map.items():
            if m.lower() == model_pin:
                return n
        pins = symbol.pins
        return pins[index].number if index < len(pins) else str(index + 1)

    r0, wiper, r1 = number("r0", 0), number("wiper", 1), number("r1", 2)
    total = params.get("r", params.get("rtot", value))
    position = params.get("pos", params.get("wpos", "0.5"))
    return Inferred(
        SimModel(f"{{ref:R}}a {{pin.{r0}}} {{pin.{wiper}}} {{{{max(value*position, 1e-3)}}}}\n"
                 f"{{ref:R}}b {{pin.{wiper}}} {{pin.{r1}}} {{{{max(value*(1-position), 1e-3)}}}}"),
        (_value_param(total, "Resistance", "Ω", "10k"),
         ParamSpec("position", "Position", position, minimum=0, maximum=1, live=True,
                   description="Where the wiper is: 0 at one end, 1 at the other.")),
        controls=(ControlSpec("knob", "position"),), show=("value", "position"))


_SOURCE_PARAMS = {
    "DC": (("dc", "Voltage", "0"),),
    "SIN": (("dc", "Offset", "0"), ("ampl", "Amplitude", "1"), ("f", "Frequency", "1k"), ("td", "Delay", "0"),
            ("theta", "Damping", "0"), ("phase", "Phase", "0")),
    "PULSE": (("y1", "Low", "0"), ("y2", "High", "1"), ("td", "Delay", "0"), ("tr", "Rise time", "1n"),
              ("tf", "Fall time", "1n"), ("tw", "Width", "500u"), ("per", "Period", "1m")),
    "EXP": (("y1", "Start", "0"), ("y2", "End", "1"), ("td1", "Rise delay", "0"), ("tau1", "Rise time constant", "1u"),
            ("td2", "Fall delay", "1m"), ("tau2", "Fall time constant", "1u")),
}


def _source(kind: str, params: dict[str, str], value: str) -> tuple[tuple[ParamSpec, ...], str | None]:
    kind = kind.upper()
    if kind == "PWL":
        points = params.get("pwl", "0 0")
        return (ParamSpec("pwl", "Points", points, kind="text", description="Pairs of time and value."),), "PWL({pwl})"
    if kind not in _SOURCE_PARAMS:
        return (), None
    specs = []
    for key, label, default in _SOURCE_PARAMS[kind]:
        found = params.get(key, value if kind == "DC" and key == "dc" and values.is_value(value) else default)
        specs.append(ParamSpec(key, label, found, description=f"{label} (SPICE {kind} {key}).", live=kind == "DC"))
    ac = params.get("ac", "")
    if ac:
        specs.append(ParamSpec("ac", "AC amplitude", ac, description="The amplitude in AC analyses."))
    names = [key for key, _label, _default in _SOURCE_PARAMS[kind]]
    if kind == "DC":
        text = "DC {dc}"
    else:
        text = f"DC {{{names[0]}}} {kind}(" + " ".join(f"{{{name}}}" for name in names) + ")"
    if ac:
        text += " AC {ac}"
    return tuple(specs), text


# ----------------------------------------------------------- families
_REGULATOR = re.compile(r"[-_](\d+)[.V](\d+)|[-_](\d{1,2})(?:\.0|V0)?$|(?:^|[^0-9])(?:78|LM78|L78|MC78|UA78)\D{0,2}(\d\d)")
_GATES_74 = {"00": ("d_nand", 2), "02": ("d_nor", 2), "04": ("d_inverter", 1), "14": ("d_inverter", 1),
             "08": ("d_and", 2), "32": ("d_or", 2), "86": ("d_xor", 2), "10": ("d_nand", 3), "11": ("d_and", 3),
             "20": ("d_nand", 4), "21": ("d_and", 4), "27": ("d_nor", 3), "125": ("d_buffer", 1), "244": ("d_buffer", 1),
             "07": ("d_buffer", 1), "17": ("d_buffer", 1), "34": ("d_buffer", 1), "05": ("d_inverter", 1), "06": ("d_inverter", 1)}
_GATES_4000 = {"4011": ("d_nand", 2), "4001": ("d_nor", 2), "4069": ("d_inverter", 1), "4081": ("d_and", 2),
               "4071": ("d_or", 2), "4070": ("d_xor", 2), "4093": ("d_nand", 2), "4106": ("d_inverter", 1),
               "4049": ("d_inverter", 1), "4050": ("d_buffer", 1), "4023": ("d_nand", 3), "4025": ("d_nor", 3),
               "4073": ("d_and", 3), "4075": ("d_or", 3), "4012": ("d_nand", 4), "4002": ("d_nor", 4),
               "4077": ("d_xnor", 2)}
#: the microcontrollers openSciLab emulates: name pattern -> board (circuits/mcu/boards.py)
MCUS = ((re.compile(r"^ATMEGA328P?(?=-|$)", re.IGNORECASE), "atmega328p"),  # (not the 328PB: other ports, a second UART)
        (re.compile(r"^ATMEGA168P?A?(?=-|$)", re.IGNORECASE), "atmega168"),
        (re.compile(r"^ATMEGA2560(?=-|$)", re.IGNORECASE), "atmega2560"), (re.compile(r"^ATMEGA1280(?=-|$)", re.IGNORECASE), "atmega1280"),
        (re.compile(r"^Arduino_UNO", re.IGNORECASE), "mcu.uno"), (re.compile(r"^Arduino_Nano_v3", re.IGNORECASE), "mcu.nano"))


def _gate(name: str, value: str, symbol: Symbol) -> Inferred | None:
    text = (value or name).upper()
    match = re.search(r"74[A-Z]*?(?:\dG)?(\d{2,3})(?!\d)", text)  # (74LVC1G08: a single gate)
    found = _GATES_74.get(match.group(1)) if match else None
    if found is None:
        match = re.search(r"(?:CD|MC1|HEF)?(4\d{3})(?!\d)", text)
        found = _GATES_4000.get(match.group(1)) if match else None
    if found is None:
        return None
    model, inputs = found
    templates: dict[int, str] = {}
    for unit in range(1, symbol.units + 1):
        pins = [pin for pin in symbol.pins if pin.unit == unit]
        ins = sorted((pin for pin in pins if pin.kind == "input"), key=lambda p: _natural(p.number))
        outs = [pin for pin in pins if pin.kind in ("output", "tri_state", "open_collector")]
        enables = [pin for pin in ins if _clean(pin.name) in ("~{OE}", "OE", "~OE", "E", "~E")]
        ins = [pin for pin in ins if pin not in enables]
        if len(outs) != 1 or len(ins) != inputs:
            continue
        if inputs == 1:
            templates[unit] = f"{{ref:A}} {{pin.{ins[0].number}}} {{pin.{outs[0].number}}} {{ref}}_model"
        else:
            templates[unit] = ("{ref:A} [" + " ".join(f"{{pin.{pin.number}}}" for pin in ins) + f"] {{pin.{outs[0].number}}} {{ref}}_model")
    if not templates:
        return None
    first = min(templates)
    return Inferred(SimModel(templates[first], digital=True, quality="generic",
                             definitions=(f".model {{ref}}_model {model}(rise_delay={{delay}} fall_delay={{delay}})",),
                             units=tuple(sorted(templates.items()))),
                    (ParamSpec("delay", "Delay", "10 ns", "s", "The time from an input to the output.", minimum=0),),
                    show=())


def _natural(text: str):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def _opamp(symbol: Symbol, value: str, models, comparator: bool) -> Inferred | None:
    templates: dict[int, str] = {}
    vcc = next((pin for pin in symbol.pins if _clean(pin.name) in ("V+", "VCC", "VDD", "VS+", "+VS")), None)
    vee = next((pin for pin in symbol.pins if _clean(pin.name) in ("V-", "VEE", "VSS", "VS-", "-VS", "GND")), None)
    if vcc is None or vee is None:
        return None
    for unit in range(1, symbol.units + 1):
        pins = [pin for pin in symbol.pins if pin.unit in (unit, 0)]
        plus = next((pin for pin in pins if _clean(pin.name) in ("+", "IN+", "+IN", "NONINV")), None)
        minus = next((pin for pin in pins if _clean(pin.name) in ("-", "IN-", "-IN", "INV")), None)
        out = next((pin for pin in pins if pin.kind in ("output", "open_collector", "open_emitter")
                    or _clean(pin.name) in ("~", "OUT", "VOUT")), None)
        if plus is None or minus is None or out is None:
            continue
        templates[unit] = (f"{{ref:X}} {{pin.{plus.number}}} {{pin.{minus.number}}} {{pin.{vcc.number}}} "
                           f"{{pin.{vee.number}}} {{pin.{out.number}}} {{value}}")
    if not templates:
        return None
    generic = "COMPARATOR_OC" if comparator else "OPAMP"
    found = _first_model(models, value, re.sub(r"[A-Z]*$", "", value or ""), re.sub(r"[-_].*$", "", value or ""),
                         parts=("subckt",))
    if found is not None and len(models.get(found).pins) != 5:
        found = None  # (the macro models take inp inn vcc vee out: another pin order cannot be used)
    first = min(templates)
    return Inferred(SimModel(templates[first], quality="exact" if found else "generic",
                             units=tuple(sorted(templates.items())) if symbol.units > 1 else ()),
                    (_model_param(found or generic, "Part",
                                  "The model (a macro model of the part when openSciLab or a model library has it)."),))


def _regulator(symbol: Symbol, value: str) -> Inferred | None:
    pins = list(symbol.pins)
    vin = _pin(pins, "VI", "IN", "VIN", "INPUT")
    gnd = _pin(pins, "GND", "ADJ/GND", "COM")
    out = _pin(pins, "VO", "OUT", "VOUT", "OUTPUT")
    if vin is None or gnd is None or out is None:
        return None
    match = _REGULATOR.search((value or "").upper())
    voltage = None
    if match:
        if match.group(1):
            voltage = float(match.group(1))
        elif match.group(2):
            voltage = float(f"{match.group(2)}.{match.group(3)}")
        elif match.group(4):
            voltage = float(match.group(4))
    if voltage is None or not 0.5 <= voltage <= 40:
        return None
    dropout = "2 V" if re.search(r"78", value) else "1.1 V"
    return Inferred(SimModel(f"{{ref:X}} {{pin.{vin.number}}} {{pin.{gnd.number}}} {{pin.{out.number}}} REG_LINEAR "
                             "vout={voltage} dropout={dropout}", models=("REG_LINEAR",), quality="generic"),
                    (ParamSpec("voltage", "Output", f"{voltage:g} V", "V", "The voltage it regulates to."),
                     ParamSpec("dropout", "Dropout", dropout, "V", "How far below the input the output falls at the least.")),
                    show=("voltage",))


def _timer555(symbol: Symbol, value: str) -> Inferred | None:
    pins = list(symbol.pins)
    found = {key: _pin(pins, *names) for key, names in (
        ("vcc", ("VCC", "V+", "VDD")), ("gnd", ("GND",)), ("trig", ("TR", "TRIG", "TRIGGER")), ("thr", ("THR", "THRES", "THRESHOLD")),
        ("dis", ("DIS", "DISCH", "DISCHARGE")), ("cv", ("CV", "CONT", "CONTROL")), ("rst", ("R", "RESET", "~{R}", "RST")),
        ("out", ("Q", "OUT", "OUTPUT")))}
    if any(pin is None for pin in found.values()):
        return None
    order = " ".join(f"{{pin.{found[key].number}}}" for key in ("vcc", "gnd", "trig", "thr", "dis", "cv", "rst", "out"))
    return Inferred(SimModel(f"{{ref:X}} {order} NE555", models=("NE555",), quality="generic"),
                    (ParamSpec("value", "Part", value or "NE555", kind="text", description="The part."),), show=("value",))


def infer(library: str, name: str, properties: dict[str, str], symbol: Symbol, models=None) -> Inferred:
    """How the part ``name`` of the KiCad library ``library`` is simulated (see the module)."""
    found = from_sim_fields(properties, symbol, models)
    if found is not None:
        return found
    reference = re.sub(r"[^A-Za-z]", "", properties.get("Reference", "")).upper()
    value = properties.get("Value", name)
    pins = list(symbol.pins)
    upper_name = name.upper()
    description = (properties.get("Description", "") + " " + properties.get("ki_keywords", "")).upper()
    if symbol.power:
        return Inferred(role="ground" if value.upper() in ("GND", "GNDA", "GNDD", "EARTH", "GNDREF", "GNDPWR")
                        else "power",
                        params=(ParamSpec("net", "Net", value, kind="text", description="The name of the net."),
                                ParamSpec("voltage", "Voltage", _voltage_of(value), "V", kind="text", live=True,
                                          description="Its voltage to ground (empty: set by other parts).")),
                        show=("net",))
    for pattern, board in MCUS:
        if pattern.search(name) or pattern.search(value):
            return Inferred(role="mcu", board=board,
                            params=(ParamSpec("firmware", "Firmware", "", kind="file", suffixes=(".hex", ".elf"),
                                              description="The firmware it runs (.hex or .elf)."),),
                            show=("firmware",))
    if reference == "R" and len(pins) == 2 and not library.startswith("Sensor"):
        return Inferred(SimModel(f"{{ref:R}} {{pin.{pins[0].number}}} {{pin.{pins[1].number}}} {{value}}"),
                        (_value_param(value, "Resistance", "Ω", "1k"),), current="@{ref:R}[i]")
    if reference == "RV" and len(pins) == 3:
        return _pot(symbol, {}, {}, value)
    if reference == "C" and len(pins) == 2:
        return Inferred(SimModel(f"{{ref:C}} {{pin.{pins[0].number}}} {{pin.{pins[1].number}}} {{value}}"),
                        (_value_param(value, "Capacitance", "F", "100n"),), current="@{ref:C}[i]")
    if reference in ("L", "FB") and len(pins) == 2:
        return Inferred(SimModel(f"{{ref:L}} {{pin.{pins[0].number}}} {{pin.{pins[1].number}}} {{value}}"),
                        (_value_param(value, "Inductance", "H", "10m"),), current="@{ref:L}[i]")
    if reference == "D" and len(pins) == 2:
        k, a = _pin(pins, "K"), _pin(pins, "A")
        if k is not None and a is not None:
            led = "LED" in upper_name or " LED" in f" {description}"
            zener = "ZENER" in upper_name or "ZENER" in description
            schottky = "SCHOTTKY" in upper_name or "SCHOTTKY" in description
            model = _first_model(models, value, parts=("diode", "subckt")) or (
                "LED_RED" if led else "DZ5V1" if zener else "1N5819" if schottky else "1N4148")
            exact = _has_model(models, value, ("diode", "subckt"))
            letter = "X" if _has_model(models, model, ("subckt",)) else "D"
            return Inferred(SimModel(f"{{ref:{letter}}} {{pin.{a.number}}} {{pin.{k.number}}} {{value}}",
                                     quality="exact" if exact else "generic"),
                            (_model_param(model),), current="@{ref:D}[id]", lights=led)
    if reference == "Q" and len(pins) in (3, 4):
        b, c, e = _pin(pins, "B"), _pin(pins, "C"), _pin(pins, "E")
        g, d, s = _pin(pins, "G"), _pin(pins, "D"), _pin(pins, "S")
        if b is not None and c is not None and e is not None:
            polarity = "PNP" if "PNP" in upper_name or "PNP" in description else "NPN"
            model = _first_model(models, value, parts=("npn", "pnp")) or ("2N3906" if polarity == "PNP" else "2N3904")
            return Inferred(SimModel(f"{{ref:Q}} {{pin.{c.number}}} {{pin.{b.number}}} {{pin.{e.number}}} {{value}}",
                                     quality="exact" if _has_model(models, value, ("npn", "pnp")) else "generic"),
                            (_model_param(model),), current="@{ref:Q}[ic]")
        if g is not None and d is not None and s is not None:
            if "JFET" in upper_name or "JFET" in description:
                return Inferred()
            pchannel = any(word in upper_name or word in description for word in ("PMOS", "P-CH", "P-CHANNEL", "PCH"))
            model = _first_model(models, value, parts=("nmos", "pmos", "subckt")) or ("BS250" if pchannel else "2N7000")
            template, quality = _mos_template(models, model, d.number, g.number, s.number)
            return Inferred(SimModel(template, quality=quality),
                            (_model_param(model),), current="@{ref:M}[id]")
    if reference == "SW" and len(pins) == 2:
        push = "PUSH" in upper_name or "BUTTON" in description or "PUSH" in description
        return Inferred(SimModel(f"{{ref:R}} {{pin.{pins[0].number}}} {{pin.{pins[1].number}}} {{{{0.01 + (1-state)*1e9}}}}"),
                        (ParamSpec("state", "Pressed" if push else "Closed", "0", choices=("0", "1"), live=True,
                                   kind="choice", description="1: closed, 0: open."),),
                        controls=(ControlSpec("button" if push else "switch", "state"),), show=(),
                        current="@{ref:R}[i]")
    if library.startswith(("Amplifier_Operational", "Amplifier_Audio", "Amplifier_Instrumentation", "Comparator")) or \
            "OPAMP" in description or "OPERATIONAL" in description:
        found = _opamp(symbol, value, models, comparator=library.startswith("Comparator") or "COMPARATOR" in description)
        if found is not None:
            return found
    if library.startswith("Regulator_Linear"):
        found = _regulator(symbol, value)
        if found is not None:
            return found
    if library.startswith("Timer") and "555" in (value + name):
        found = _timer555(symbol, value)
        if found is not None:
            return found
    if library.startswith(("74xx", "4xxx", "74xGxx")):
        found = _gate(name, value, symbol)
        if found is not None:
            return found
    return Inferred()


def _voltage_of(net: str) -> str:
    """The voltage a supply's name says: ``+5V`` → 5 V, ``+3V3`` → 3.3 V, ``-12V`` → -12 V."""
    match = re.match(r"^([+-]?)(\d+)(?:V(\d+)|\.(\d+)V?|V)$", net.strip().upper())
    if not match:
        return ""
    sign, whole, frac_v, frac_dot = match.groups()
    number = float(f"{whole}.{frac_v or frac_dot or 0}")
    return f"{'-' if sign == '-' else ''}{number:g} V"
