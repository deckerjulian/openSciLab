# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""S-expressions as KiCad writes them: ``(symbol "R" (pin passive line (at 0 3.81 270)))``.

:func:`parse` gives nested lists of atoms (``str``; quoted strings unescaped), :func:`find` and
friends look into them.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Union

Node = Union[str, list]

_TOKENS = re.compile(r'\(|\)|"((?:[^"\\]|\\.)*)"|([^\s()"]+)')
_ESCAPE = re.compile(r"\\(.)")


class SexprError(ValueError):
    pass


def parse(text: str) -> list:
    """All top-level expressions of ``text``."""
    stack: list[list] = [[]]
    for match in _TOKENS.finditer(text):
        token = match.group(0)
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                raise SexprError("a ) without its (")
            done = stack.pop()
            stack[-1].append(done)
        elif match.group(1) is not None:
            quoted = match.group(1)
            stack[-1].append(_ESCAPE.sub(r"\1", quoted) if "\\" in quoted else quoted)
        else:
            stack[-1].append(match.group(2))
    if len(stack) != 1:
        raise SexprError("an ( without its )")
    return stack[0]


def head(node: Node) -> str:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else ""


def children(node: Node, name: str) -> Iterator[list]:
    if isinstance(node, list):
        for child in node[1:]:
            if isinstance(child, list) and child and child[0] == name:
                yield child


def find(node: Node, name: str) -> list | None:
    return next(children(node, name), None)


def value(node: Node, name: str, default: str = "") -> str:
    """The first atom of the child ``name``: ``value(pin, "length")``."""
    child = find(node, name)
    if child is not None and len(child) > 1 and isinstance(child[1], str):
        return child[1]
    return default


def numbers(node: list | None, count: int, default: float = 0.0) -> list[float]:
    """The first ``count`` atoms after the head as numbers."""
    result = []
    for index in range(1, count + 1):
        try:
            result.append(float(node[index]) if node is not None and index < len(node) else default)
        except (TypeError, ValueError):
            result.append(default)
    return result


def flag(node: Node, name: str) -> bool:
    """``hide`` as an atom (KiCad 6-8) or ``(hide yes)`` (KiCad 9+)."""
    if not isinstance(node, list):
        return False
    for child in node[1:]:
        if child == name:
            return True
        if isinstance(child, list) and child and child[0] == name:
            return len(child) == 1 or child[1] in ("yes", "true")
    return False
