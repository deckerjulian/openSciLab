"""Colours, fonts and sizes come from the theme (docs/design.md, rule 5).

Only ``openscilab/ui/theme.py`` holds literal colours; the rest of the user interface asks it for
them by name (``token``, ``qcolor``), sizes by name (``space``, ``ICON``, constants of a module)
and fonts from the widget's own (``smaller_font``, ``scaled_font``, style roles).
"""

from __future__ import annotations

import pathlib
import re

import pytest

UI = pathlib.Path(__file__).resolve().parent.parent / "openscilab" / "ui"
#: artwork, not chrome: the colours of the application's icon (a string that starts with this)
ARTWORK = {"icons.py": "APP_ICON_SVG = "}

HEX = re.compile(r"""["'](?:[^"'\n]*\s)?#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b""")
QCOLOR_NUMBERS = re.compile(r"QColor\(\s*\d")
FONT_SIZE = re.compile(r"set(?:Point|Pixel)Size(?:F)?\(\s*\d")
FONT_FAMILY = re.compile(r"QFont\(\s*[\"']")
FIXED_SIZE = re.compile(r"setFixed(?:Width|Height|Size)\(\s*[1-9]")
STYLE_SHEET = re.compile(r"\.setStyleSheet\(")
#: style sheets that hold the theme itself, or a colour the user chose for their data
STYLE_SHEET_ALLOWED = {
    "shell/main_window.py",  # applies the theme to its window
    "dialogs/capture_dialog.py",  # the colour of a channel, chosen by the user
    "dialogs/region_dialog.py",  # the colour of a region, chosen by the user
    "flow/inspector.py",  # the colour of a group, chosen by the user
}


def _files():
    for path in sorted(UI.rglob("*.py")):
        if path.name == "theme.py":
            continue
        yield path, path.relative_to(UI).as_posix()


def _lines(path):
    """The lines of a module without its artwork (a triple-quoted string that starts at ARTWORK)."""
    artwork = ARTWORK.get(path.name)
    inside = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if artwork and line.startswith(artwork):
            inside = True
            continue
        if inside:
            inside = '"""' not in line
            continue
        yield number, line


@pytest.mark.parametrize("pattern,what", [
    (HEX, "a literal colour"),
    (QCOLOR_NUMBERS, "a colour from numbers"),
    (FONT_SIZE, "a literal text size"),
    (FONT_FAMILY, "a font family"),
    (FIXED_SIZE, "a literal size (name it: a constant, space(), ICON)"),
])
def test_no_literal_look_outside_the_theme(pattern, what):
    found = [f"{relative}:{number}: {line.strip()}" for path, relative in _files()
             for number, line in _lines(path) if pattern.search(line) and not line.lstrip().startswith("#")]
    assert not found, f"{what} outside openscilab/ui/theme.py:\n" + "\n".join(found)


def test_style_sheets_only_where_the_theme_or_the_user_chose_them():
    found = [f"{relative}:{number}" for path, relative in _files() if relative not in STYLE_SHEET_ALLOWED
             for number, line in _lines(path) if STYLE_SHEET.search(line)]
    assert not found, "inline style sheets (use a role, variant or tone of the theme):\n" + "\n".join(found)


def test_every_token_has_a_light_and_a_dark_value():
    from openscilab.ui import theme

    assert set(theme.TOKENS["dark"]) == set(theme.TOKENS["light"])
    assert set(theme.PALETTES["dark"]) == set(theme.PALETTES["light"])


def test_node_groups_and_signal_types_are_told_apart():
    """Two groups (or two types) of the flow editor never get nearly the same hue."""
    from PySide6.QtGui import QColor

    from openscilab.ui import theme

    for mode in ("dark", "light"):
        for prefix in ("group.", "type."):
            hues = {name: QColor(value).hslHue() for name, value in theme.TOKENS[mode].items()
                    if name.startswith(prefix) and QColor(value).hslSaturation() > 60}
            names = sorted(hues)
            for index, first in enumerate(names):
                for second in names[index + 1:]:
                    distance = abs(hues[first] - hues[second])
                    distance = min(distance, 360 - distance)
                    assert distance >= 12 or {first, second} == {"type.scalar", "type.device"}, \
                        f"{mode}: {first} and {second} look alike"


def test_token_answers_palette_and_lab_names():
    from openscilab.ui import theme

    assert theme.token("TEXT") == theme.TEXT
    assert theme.token("type.analog").startswith("#")
    assert theme.group_color("measure") == theme.token("group.measure")
    assert theme.group_color("a-plugin") == theme.token("group.plugin")
    assert theme.space(2, 12) == 8
