# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""SPICE model libraries: every ``.model`` and ``.subckt`` of ``.lib``, ``.mod``, ``.sub``, ``.cir``,
``.sp`` and ``.spice`` files, found by name.

A netlist takes the definitions it needs, with what they need in turn (a subcircuit's models and
inner subcircuits), from the libraries in the order they were added: the circuit's own first, then
those of the settings, KiCad's, and openSciLab's standard models last. Names are compared as SPICE
does, without regard to case.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

SUFFIXES = (".lib", ".mod", ".sub", ".cir", ".sp", ".spice", ".ckt", ".model", ".subckt", ".txt")
#: the kinds of devices of ``.model`` lines (what part they describe)
MODEL_KINDS = {
    "D": "diode", "NPN": "npn", "PNP": "pnp", "NMOS": "nmos", "PMOS": "pmos", "NJF": "njfet", "PJF": "pjfet",
    "VDMOS": "vdmos", "SW": "switch", "CSW": "switch", "R": "resistor", "C": "capacitor", "L": "inductor",
}
_TOKEN = re.compile(r"\S+")


@dataclass
class Definition:
    name: str
    #: "model" or "subckt"
    kind: str
    #: of a model: its device (D, NPN, VDMOS ...; VDMOS models with ``pchan`` are "PVDMOS")
    device: str = ""
    #: of a subcircuit: the names of its pins in their order
    pins: tuple[str, ...] = ()
    #: the lines as written (continuations joined)
    lines: list[str] = field(default_factory=list)
    file: str = ""
    #: the comment lines right before it (a description)
    comment: str = ""
    #: the names it uses (models, subcircuits), upper case
    uses: set[str] = field(default_factory=set)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def part(self) -> str:
        """What it is: diode, npn, nmos, subckt ..."""
        if self.kind == "subckt":
            return "subckt"
        if self.device == "VDMOS":
            return "pmos" if self.pchan else "nmos"
        return MODEL_KINDS.get(self.device, self.device.lower())

    @property
    def pchan(self) -> bool:
        return self.device == "VDMOS" and bool(re.search(r"\bpchan\b", self.text, re.IGNORECASE))


def _logical_lines(text: str) -> list[str]:
    """Lines with their continuations (``+``) joined; comments (``*``, ``;``, ``$ ``) kept apart."""
    result: list[str] = []
    last_code = -1  # (the line a continuation belongs to: the last one that is no comment)
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("+") and last_code >= 0:
            result[last_code] += " " + stripped[1:].strip()
        else:
            result.append(line)
            if stripped and not stripped.startswith(("*", ";")):
                last_code = len(result) - 1
    return result


def _strip_comment(line: str) -> str:
    for marker in (";", " $ ", "\t$ "):
        position = line.find(marker)
        if position >= 0:
            line = line[:position]
    return line


def parse(text: str, file: str = "") -> list[Definition]:
    """The definitions of a model library (``.include``/``.lib`` sections are not followed)."""
    found: list[Definition] = []
    comment: list[str] = []
    current: Definition | None = None
    depth = 0  # (subcircuits inside a subcircuit belong to it)
    defined_inside: set[str] = set()
    for line in _logical_lines(text):
        stripped = line.strip()
        if not stripped:
            if current is None:
                comment = []
            continue
        if stripped.startswith("*"):
            if current is None:
                comment.append(stripped.lstrip("*").strip())
            else:
                current.lines.append(line)
            continue
        code = _strip_comment(stripped)
        words = code.split()
        if not words:
            continue
        keyword = words[0].lower()
        if current is not None:
            current.lines.append(line)
            if keyword == ".subckt" and len(words) >= 2:
                depth += 1
                defined_inside.add(words[1].upper())
            elif keyword == ".ends":
                if depth:
                    depth -= 1
                else:
                    current.uses -= defined_inside  # (models and subcircuits defined inside, before or after use)
                    found.append(current)
                    current = None
            elif keyword == ".model" and len(words) >= 2:
                defined_inside.add(words[1].upper())
            else:
                for word in words[1:]:
                    current.uses.add(word.split("=")[0].upper())
            continue
        if keyword == ".model" and len(words) >= 3:
            device = re.split(r"[\s(]", words[2], maxsplit=1)[0].upper()
            found.append(Definition(words[1], "model", device, (), [line], file, " ".join(comment)))
            comment = []
        elif keyword == ".subckt" and len(words) >= 2:
            pins = []
            for word in words[2:]:
                if "=" in word or word.lower() in ("params:", "param:"):
                    break
                pins.append(word)
            current = Definition(words[1], "subckt", "", tuple(pins), [line], file, " ".join(comment))
            comment = []
            depth = 0
            defined_inside = set()
        else:
            comment = []
    return found


class ModelLibrary:
    """Definitions by name from several files (see the module)."""

    def __init__(self) -> None:
        self._definitions: dict[str, Definition] = {}
        self.files: list[str] = []
        self.problems: list[str] = []

    def add_file(self, path: str, replace: bool = False) -> int:
        """Add the definitions of ``path``; earlier ones win unless ``replace``. Returns how many."""
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as error:
            self.problems.append(f"{path}: {error}")
            return 0
        self.files.append(path)
        return self.add_text(text, path, replace)

    def add_text(self, text: str, file: str = "", replace: bool = False) -> int:
        count = 0
        for definition in parse(text, file):
            key = definition.name.upper()
            if replace or key not in self._definitions:
                self._definitions[key] = definition
                count += 1
        return count

    def add_folder(self, folder: str) -> int:
        count = 0
        if not os.path.isdir(folder):
            return 0
        for current, _dirs, names in os.walk(folder):
            for name in sorted(names):
                if name.lower().endswith(SUFFIXES):
                    count += self.add_file(os.path.join(current, name))
        return count

    def get(self, name: str) -> Definition | None:
        return self._definitions.get(str(name).upper())

    def __contains__(self, name: str) -> bool:
        return str(name).upper() in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    def definitions(self) -> list[Definition]:
        return list(self._definitions.values())

    def resolve(self, names: Iterable[str]) -> tuple[list[Definition], list[str]]:
        """The definitions ``names`` need, dependencies before what uses them, and the names not
        found."""
        ordered: list[Definition] = []
        missing: list[str] = []
        seen: set[str] = set()

        def visit(name: str, wanted: bool) -> None:
            key = name.upper()
            if key in seen:
                return
            definition = self._definitions.get(key)
            if definition is None:
                if wanted:
                    missing.append(name)
                return
            seen.add(key)
            for used in sorted(definition.uses):
                visit(used, False)
            ordered.append(definition)

        for name in names:
            visit(name, True)
        return ordered, missing


def standard_library() -> ModelLibrary:
    """openSciLab's standard models."""
    from ..library import MODELS_DIR

    library = ModelLibrary()
    library.add_folder(MODELS_DIR)
    return library
