# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a component is: its symbol, pins, parameters and how it is simulated.

openSciLab's own components are YAML files in ``library/`` (one list per family); the catalog
(:mod:`.catalog`) adds KiCad's symbol libraries and SPICE models as :class:`ComponentSpec` as well::

    - type: resistor
      title: Resistor
      category: Passive
      prefix: R
      keywords: res resistance
      symbol: {shapes: [...], pins: [...]}          # see symbols.py
      params:
        - {name: value, label: Resistance, unit: Ω, default: 1k, description: ..., live: true}
      spice: "R{ref} {pin.1} {pin.2} {value}"

``spice`` is the template of the component's netlist lines: ``{ref}`` its reference, ``{pin.N}`` the
net of pin N, ``{name}`` a parameter as a number for SPICE (or as written when it is no value),
``{{expression}}`` a value computed from the parameters (``{{value*position}}``). ``models`` names the
``.model``/``.subckt`` it needs from the model libraries, ``digital`` makes its pins digital (XSPICE).
"""

from __future__ import annotations

import ast
import math
import operator
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import yaml

from . import CircuitError, values
from .symbols import Symbol, symbol_from_data

LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library")
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

#: the roles of special components: they are not parts but connect, name or measure
ROLES = ("ground", "power", "probe.v", "probe.i", "probe.logic", "probe.scope", "port", "mcu", "device.in", "device.out")


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    default: str = ""
    unit: str = ""
    description: str = ""
    #: offered values (the field becomes a choice)
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: changes it while the circuit runs without starting it again (SPICE ``alter``)
    live: bool = False
    #: "quantity" (a value with a unit), "text", "choice", "file", "bool"; "device" / "device_pin": a
    #: connected device and one of its pins (the inspector offers those of the Devices section)
    kind: str = "quantity"
    #: a file parameter: the endings it takes (".hex", ".elf")
    suffixes: tuple[str, ...] = ()

    def check(self, text: str) -> str | None:
        """Why ``text`` is no value of this parameter (``None``: it is)."""
        if self.kind == "quantity":
            number = values.parse_optional(text)
            if number is None:
                return f"{text!r} is no value (e.g. 10k, 4.7 uF, 100 mV)"
            if self.minimum is not None and number < self.minimum:
                return f"at least {values.format_value(self.minimum, self.unit)}"
            if self.maximum is not None and number > self.maximum:
                return f"at most {values.format_value(self.maximum, self.unit)}"
        if self.kind == "choice" and self.choices and text not in self.choices:
            return f"one of {', '.join(self.choices)}"
        return None


@dataclass(frozen=True)
class ControlSpec:
    """Something to operate on the schematic while the circuit runs: a ``knob`` (a parameter from
    ``low`` to ``high``: numbers or names of other parameters, 0 to 1 by default), a ``switch``
    (on/off) or a ``button`` (on while pressed)."""

    kind: str
    param: str
    low: str = "0"
    high: str = "1"

    def bounds(self, params) -> tuple[float, float]:
        """The range of a knob with the parameters ``params`` of its component."""
        def number(text: str, default: float) -> float:
            found = values.parse_optional(str(params.get(text, text)))
            return default if found is None else found

        low, high = number(self.low, 0.0), number(self.high, 1.0)
        return (low, high) if high > low else (low, low + 1.0)


@dataclass(frozen=True)
class SimModel:
    """How a component is simulated."""

    #: netlist lines (see the module)
    template: str
    #: names of ``.model``/``.subckt`` definitions it needs (resolved through the model libraries)
    models: tuple[str, ...] = ()
    #: library files to include as they are
    includes: tuple[str, ...] = ()
    #: pins are digital (XSPICE): they get bridges where they meet analog nets
    digital: bool = False
    #: "exact" (a model of this part), "generic" (a standard model standing in) or "none"
    quality: str = "exact"
    #: model lines of its own (written into the netlist once)
    definitions: tuple[str, ...] = ()
    #: parts with several units (the gates of a 7400): (unit, template) for each
    units: tuple[tuple[int, str], ...] = ()

    def template_for(self, unit: int) -> str:
        """The netlist lines of ``unit`` (a unit without lines - the supply of a 7400 - has "")."""
        if not self.units:
            return self.template
        return dict(self.units).get(unit, "")


@dataclass
class ComponentSpec:
    """A kind of component: what the catalog offers and a circuit places."""

    #: the catalog's name: ``resistor`` (openSciLab's own), ``kicad:Device:R``, ``spice:models:1N4148``
    type: str
    title: str
    category: str = "Other"
    description: str = ""
    keywords: str = ""
    #: the letter(s) of its references (R, C, U ...)
    prefix: str = "U"
    #: the symbol, or a function building it when it is first drawn (catalogs load lazily)
    symbol_source: object = None
    params: tuple[ParamSpec, ...] = ()
    model: SimModel | None = None
    controls: tuple[ControlSpec, ...] = ()
    #: one of :data:`ROLES` or ""
    role: str = ""
    #: where it comes from: "builtin", "kicad", "spice", "user"
    source: str = "builtin"
    datasheet: str = ""
    #: the library it belongs to (KiCad's library, a model file)
    library: str = ""
    #: an emulated microcontroller: the name of its board (``mcu.uno``)
    board: str = ""
    #: the parameters written as its value on the schematic
    show: tuple[str, ...] = ("value",)
    #: the SPICE vector of the current through it (``@{ref:D}[id]``): LEDs light, wires show it
    current: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def symbol(self) -> Symbol:
        source = self.symbol_source
        if callable(source):
            source = source()
            self.symbol_source = source
        return source if isinstance(source, Symbol) else Symbol()

    @property
    def simulated(self) -> bool:
        """Whether a circuit with it can be simulated (it has a model, or it only connects)."""
        return self.model is not None or self.role in ("ground", "power", "probe.v", "probe.logic", "probe.scope",
                                                       "port") or bool(self.physics)

    @property
    def physics(self) -> str:
        """The physics of a plugin that simulates it instead of ngspice (``physics:``, e.g. ``hydraulic``);
        "" for an electrical component."""
        return str(self.extra.get("physics", ""))

    def param(self, name: str) -> ParamSpec | None:
        return next((spec for spec in self.params if spec.name == name), None)

    def defaults(self) -> dict[str, str]:
        return {spec.name: spec.default for spec in self.params}

    def search_text(self) -> str:
        return " ".join((self.type, self.title, self.category, self.description, self.keywords, self.library)).lower()


# ----------------------------------------------------------- templates
_PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}|\{([A-Za-z_][A-Za-z0-9_]*)=([^?}]*)\?([^}]*)\}"
                          r"|\{([A-Za-z_][A-Za-z0-9_.:+\-~#]*)\}")
_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCTIONS = {"max": max, "min": min, "abs": abs, "sqrt": math.sqrt, "log10": math.log10, "exp": math.exp}


def evaluate(expression: str, names: dict[str, float]) -> float:
    """A small arithmetic expression over parameters (no names of Python reach it)."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise CircuitError(f"{node.id!r} is no parameter")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](walk(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
            return float(_FUNCTIONS[node.func.id](*[walk(arg) for arg in node.args]))
        raise CircuitError(f"{expression!r} is no expression of values")

    try:
        return float(walk(ast.parse(expression.strip(), mode="eval")))
    except (SyntaxError, ZeroDivisionError, ValueError, OverflowError) as error:
        raise CircuitError(f"{expression!r}: {error}") from None


def render(template: str, ref: str, pins: dict[str, str], params: dict[str, str],
           model: Callable[[str], str] = lambda name: name) -> str:
    """The netlist lines of a component (see the module)."""
    numbers: dict[str, float] = {}
    for name, text in params.items():
        number = values.parse_optional(text)
        if number is not None:
            numbers[name] = number

    def replace(match: re.Match) -> str:
        expression, name = match.group(1), match.group(5)
        if expression is not None:
            return values.spice_number(evaluate(expression, numbers))
        if match.group(2) is not None:  # {name=value?text}: text only when the parameter has that value
            return match.group(4) if str(params.get(match.group(2), "")).strip() == match.group(3).strip() else ""
        if name == "ref":
            return ref
        if name.startswith("ref:"):
            letter = name[4:]
            return ref if ref.upper().startswith(letter.upper()) else f"{letter}{ref}"
        if name.startswith("pin."):
            number = name[4:]
            if number not in pins:
                raise CircuitError(f"{ref}: no pin {number}")
            return pins[number]
        if name.startswith("model:"):
            return model(name[6:])
        if name in params:
            return values.spice_number(params[name])
        raise CircuitError(f"{ref}: no parameter {name!r}")

    return _PLACEHOLDER.sub(replace, template)


# ---------------------------------------------------------------- YAML
def param_from_data(data: dict) -> ParamSpec:
    if "name" not in data:
        raise CircuitError("a parameter needs a name")
    choices = tuple(str(choice) for choice in data.get("choices", ()))
    kind = str(data.get("kind", "choice" if choices else "quantity"))
    return ParamSpec(str(data["name"]), str(data.get("label", data["name"])), str(data.get("default", "")),
                     str(data.get("unit", "")), str(data.get("description", "")), choices,
                     values.parse_optional(data["min"]) if "min" in data else None,
                     values.parse_optional(data["max"]) if "max" in data else None,
                     bool(data.get("live", False)), kind, tuple(str(s) for s in data.get("suffixes", ())))


def spec_from_data(data: dict, source: str = "builtin", library: str = "") -> ComponentSpec:
    if "type" not in data:
        raise CircuitError("a component needs a type")
    model = None
    spice = data.get("spice")
    if spice:
        model = SimModel(str(spice).strip(), tuple(str(m) for m in data.get("models", ())),
                         tuple(str(i) for i in data.get("includes", ())), bool(data.get("digital", False)),
                         str(data.get("quality", "exact")),
                         tuple(str(line) for line in data.get("definitions", ())))
    controls = tuple(ControlSpec(str(item["kind"]), str(item["param"]), str(item.get("min", 0)), str(item.get("max", 1)))
                     for item in ([data["control"]] if isinstance(data.get("control"), dict) else data.get("controls", ())))
    try:
        symbol = symbol_from_data(data.get("symbol") or {})
    except (KeyError, TypeError, ValueError) as error:
        raise CircuitError(f"{data['type']}: its symbol: {error}") from None
    return ComponentSpec(
        type=str(data["type"]), title=str(data.get("title", data["type"])), category=str(data.get("category", "Other")),
        description=str(data.get("description", "")), keywords=str(data.get("keywords", "")),
        prefix=str(data.get("prefix", "U")), symbol_source=symbol,
        params=tuple(param_from_data(item) for item in data.get("params", ())), model=model, controls=controls,
        role=str(data.get("role", "")), source=source, datasheet=str(data.get("datasheet", "")),
        library=library or str(data.get("library", "")), board=str(data.get("board", "")),
        show=tuple(str(name) for name in data.get("show", ("value",))), current=str(data.get("current", "")),
        extra={key: value for key, value in data.items() if key in ("mcu", "digital_model", "rf", "screen", "lights",
                                                                    "physics")})


def load_file(path: str, source: str = "builtin") -> tuple[list[ComponentSpec], list[str]]:
    """The components of one YAML file and the problems with it (a bad entry is left out)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []
    except (OSError, yaml.YAMLError) as error:
        return [], [f"{os.path.basename(path)}: {error}"]
    if isinstance(data, dict):
        data = data.get("components", [data])
    found, problems = [], []
    library = os.path.splitext(os.path.basename(path))[0]
    for item in data if isinstance(data, list) else []:
        try:
            found.append(spec_from_data(item, source, library))
        except (CircuitError, KeyError, TypeError, ValueError) as error:
            problems.append(f"{os.path.basename(path)}: {error}")
    return found, problems


def load_folder(folder: str, source: str = "builtin") -> tuple[list[ComponentSpec], list[str]]:
    found, problems = [], []
    if not os.path.isdir(folder):
        return found, problems
    for current, _dirs, names in os.walk(folder):
        for name in sorted(names):
            if name.endswith((".yaml", ".yml")):
                specs, issues = load_file(os.path.join(current, name), source)
                found.extend(specs)
                problems.extend(issues)
    return found, problems


_builtin: dict[str, ComponentSpec] | None = None
_builtin_problems: list[str] = []


#: folders of components that plugins of openSciLab bring (:func:`register_folder`), read with its own
PLUGIN_FOLDERS: list[str] = []


def register_folder(folder: str) -> None:
    """Add the components of ``folder`` (YAML of this format) to openSciLab's own - for a plugin that brings
    components of a physics of its own). The catalog of the application is read again."""
    folder = os.path.abspath(folder)
    if folder in PLUGIN_FOLDERS:
        return
    PLUGIN_FOLDERS.append(folder)
    global _builtin
    _builtin = None
    from .catalog import reset_default

    reset_default()


def builtin() -> dict[str, ComponentSpec]:
    """openSciLab's own components by type (read once), with those of plugins (:data:`PLUGIN_FOLDERS`)."""
    global _builtin
    if _builtin is None:
        specs, problems = load_folder(LIBRARY_DIR)
        for folder in PLUGIN_FOLDERS:
            more, more_problems = load_folder(folder)
            specs += more
            problems += more_problems
        _builtin = {spec.type: spec for spec in specs}
        _builtin_problems[:] = problems
    return _builtin


def builtin_problems() -> list[str]:
    builtin()
    return list(_builtin_problems)
