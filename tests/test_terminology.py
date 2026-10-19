"""One word for one thing (docs/design.md, glossary): the texts people read in the application do
not use the words the glossary replaced. Docstrings and comments are left alone; the strings of the
code are checked (texts of buttons, menus, tool tips, messages, node descriptions)."""

from __future__ import annotations

import io
import pathlib
import re
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "openscilab"
#: where people read what is written: the user interface and the node types
CHECKED = [ROOT / "ui", ROOT / "lab" / "nodes"]

#: the word (a regular expression on the text) and the word the glossary says instead
REPLACED = [
    (r"\bdevice card\b", "the device (its document)"),
    (r"\bLogic Analyzer\b", "data view"),
    (r"(?<!Your )\b[Tt]emplates\b", "examples (your templates: 'Your templates')"),
    (r"\bPreferences\b", "settings"),
    (r"\b[Ff]lash(?:ed|ing)? (?:the )?firmware\b", "install the firmware"),
    (r"\bSimulation: ", "Simulated …"),
    (r"\bFast \(virtual time\)", "Virtual time"),
    (r"^.Test signals\.\.\.|\bSimulated capture\b", "Simulated signals"),
]
#: (file, word) that stay: a proper name, a key, a file name
ALLOWED = {
    ("ui/dialogs/device_dialogs.py", r"\bLogic Analyzer\b"),
}


def _strings(path: pathlib.Path):
    """``(line, text)`` of the string literals of ``path`` that are no docstrings."""
    source = path.read_text(encoding="utf-8")
    previous = None
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING:
            # a docstring: a string that is a statement of its own (after a newline, an indent or a colon)
            docstring = previous is None or previous.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                                                               tokenize.NL) or previous.string == ":"
            if not docstring:
                yield token.start[0], token.string
        if token.type not in (tokenize.COMMENT, tokenize.NL):
            previous = token


def _files():
    for base in CHECKED:
        for path in sorted(base.rglob("*.py")):
            yield path, path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("pattern,instead", REPLACED)
def test_the_texts_use_the_words_of_the_glossary(pattern, instead):
    found = []
    expression = re.compile(pattern)
    for path, relative in _files():
        if (relative, pattern) in ALLOWED:
            continue
        for line, text in _strings(path):
            if expression.search(text):
                found.append(f"{relative}:{line}: {text[:100]}")
    assert not found, f"say '{instead}':\n" + "\n".join(found)
