# Copyright (C) 2026 Julian Decker
# Copyright (C) Agustín Giménez Bernad (gusmanb), original LogicAnalyzer
#
# Part of openSciLab, a port and extension of his software;
# the changes are described in docs/improvements.md and CHANGELOG.md.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The look of every window: colours, text sizes, spacing and the style sheet.

This module is the only place with colours and sizes (``tests/test_theme_tokens.py`` checks it;
the rules are in ``docs/design.md``). Everything else asks for them:

* **Colours** by name: :func:`token` (``"TEXT"``, ``"type.analog"``, ``"group.measure"``,
  ``"display.grid.major"`` ...) or :func:`qcolor`; the palette of the shell (UPPERCASE names) and
  the tokens of the lab (dotted names) answer through the same function, in the theme of the run.
  The waveform display of the data view (``display.*``) stays dark in both themes, like the screen
  of an instrument.
* **Spacing** in steps of 4 px: :func:`space`; **icons** in four sizes: :data:`ICON`.
* **Styles** through dynamic properties instead of inline colours:

  * ``role`` on labels: ``hint``, ``heading``, ``title``, ``section``, ``error``, ``warning``,
    ``success`` and the ``chip-*`` badges; on frames: ``banner-info``, ``banner-warning``,
    ``banner-error``, ``card``, ``tile`` and ``viewbar``;
  * ``variant`` on buttons: ``primary`` (the main action of a window), ``danger`` (stops or destroys
    something), ``link`` and ``tool`` (a flat button of a tool bar).

  Use :func:`set_role` / :func:`set_variant` to change them after the widget is shown.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Optional

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget

from .. import __version__

log = logging.getLogger(__name__)

#: The colours of both themes. The application is drawn in one of them, chosen at startup from the
#: preferences (*Settings → Appearance*; "system" follows the operating system).
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "BACKGROUND": "#303033", "PANEL": "#242427", "PANEL_LIGHT": "#2b2b2f", "INPUT": "#1f1f22",
        "BORDER": "#46464c", "BORDER_STRONG": "#5c5c63", "BORDER_SOFT": "#3a3a3f",
        "TEXT": "#e8e8ea", "TEXT_MUTED": "#a3a3ab", "TEXT_DISABLED": "#6f6f76",
        "ACCENT": "#3b7ddd", "ACCENT_HOVER": "#4b8ae6", "ACCENT_PRESSED": "#2f6cc4", "SELECTION": "#35609a",
        "DANGER": "#c14848", "DANGER_HOVER": "#d05656", "SUCCESS": "#7fd18b", "WARNING": "#e8b04b", "ERROR": "#ff7f7f",
        "BUTTON_HOVER": "#36363b", "BUTTON_PRESSED": "#404046", "ON_ACCENT": "#ffffff",
        "PRIMARY_DISABLED": "#2d3b50", "PRIMARY_DISABLED_TEXT": "#7d8796",
        "SCROLL": "#55555c", "SCROLL_HOVER": "#6c6c74", "CHECKED": "#263652", "CHECKED_BORDER": "#3b5577",
        "CHIP_OK": "#1f5a2b", "CHIP_OK_TEXT": "#d6f5dc", "CHIP_MEDIUM": "#6b4a14", "CHIP_MEDIUM_TEXT": "#fbe6c2",
        "CHIP_HIGH": "#7a2323", "CHIP_HIGH_TEXT": "#ffd9d9",
        "BANNER_INFO": "#26344a", "BANNER_INFO_BORDER": "#3b5577", "BANNER_WARNING": "#3d331f",
        "BANNER_WARNING_BORDER": "#6e5626", "BANNER_ERROR": "#432626", "BANNER_ERROR_BORDER": "#7a3838",
        "SLIDER_HANDLE": "#e6e6e6",
    },
    "light": {
        "BACKGROUND": "#f5f5f7", "PANEL": "#ebebee", "PANEL_LIGHT": "#e2e2e7", "INPUT": "#ffffff",
        "BORDER": "#c8c8cf", "BORDER_STRONG": "#a4a4ad", "BORDER_SOFT": "#d6d6dc",
        "TEXT": "#1c1c20", "TEXT_MUTED": "#5a5a63", "TEXT_DISABLED": "#9c9ca4",
        "ACCENT": "#2f6cc4", "ACCENT_HOVER": "#3b7ddd", "ACCENT_PRESSED": "#255aa6", "SELECTION": "#bcd3f3",
        "DANGER": "#c14848", "DANGER_HOVER": "#d05656", "SUCCESS": "#1f7a35", "WARNING": "#9a6200", "ERROR": "#c62828",
        "BUTTON_HOVER": "#e4e4e9", "BUTTON_PRESSED": "#d8d8de", "ON_ACCENT": "#ffffff",
        "PRIMARY_DISABLED": "#c4d4ec", "PRIMARY_DISABLED_TEXT": "#f4f7fc",
        "SCROLL": "#bdbdc5", "SCROLL_HOVER": "#9d9da6", "CHECKED": "#dce8f8", "CHECKED_BORDER": "#9dbbe6",
        "CHIP_OK": "#d4f0da", "CHIP_OK_TEXT": "#145226", "CHIP_MEDIUM": "#f7e6c4", "CHIP_MEDIUM_TEXT": "#6b4a14",
        "CHIP_HIGH": "#f8d6d6", "CHIP_HIGH_TEXT": "#7a2323",
        "BANNER_INFO": "#e3edfb", "BANNER_INFO_BORDER": "#a7c3ea", "BANNER_WARNING": "#fbf1dc",
        "BANNER_WARNING_BORDER": "#e2c27e", "BANNER_ERROR": "#fbe3e3", "BANNER_ERROR_BORDER": "#e5a3a3",
        "SLIDER_HANDLE": "#ffffff",
    },
}


def _system_mode() -> str:
    """Whether the operating system shows dark windows ("dark") or light ones ("light")."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        application = QGuiApplication.instance()
        if application is not None:
            scheme = application.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return "light"
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
    except Exception:  # noqa: BLE001 - older Qt: the platform below
        log.debug("_system_mode: older Qt: the platform below", exc_info=True)
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            result = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                                    text=True, timeout=2, check=False)  # (fails in light mode: no such key)
            return "dark" if "Dark" in result.stdout else "light"
        if sys.platform.startswith("win"):
            import winreg  # type: ignore[import-not-found]

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except Exception:  # noqa: BLE001 - unknown: the default
        log.debug("_system_mode: unknown: the default", exc_info=True)
    return "dark"


def _chosen_mode() -> str:
    """The theme of this start: the environment (``OPENSCILAB_THEME``), else the preference."""
    choice = os.environ.get("OPENSCILAB_THEME", "")
    if not choice:
        try:
            from ..core import preferences

            choice = preferences.get("appearance.theme")
        except Exception:  # noqa: BLE001 - damaged preferences: the default
            log.debug("_chosen_mode: damaged preferences: the default", exc_info=True)
            choice = "dark"
    if choice == "system":
        return _system_mode()
    return choice if choice in PALETTES else "dark"


#: The theme the application is drawn in (fixed for a run; a change applies after a restart)
THEME_MODE = _chosen_mode()
COLORS = PALETTES[THEME_MODE]
#: The waveform display keeps the dark colours in both themes (like the screen of an instrument)
DISPLAY = PALETTES["dark"]

BACKGROUND = COLORS["BACKGROUND"]
PANEL = COLORS["PANEL"]
PANEL_LIGHT = COLORS["PANEL_LIGHT"]
INPUT = COLORS["INPUT"]
BORDER = COLORS["BORDER"]
BORDER_STRONG = COLORS["BORDER_STRONG"]
TEXT = COLORS["TEXT"]
TEXT_MUTED = COLORS["TEXT_MUTED"]
TEXT_DISABLED = COLORS["TEXT_DISABLED"]
ACCENT = COLORS["ACCENT"]
ACCENT_HOVER = COLORS["ACCENT_HOVER"]
ACCENT_PRESSED = COLORS["ACCENT_PRESSED"]
SELECTION = COLORS["SELECTION"]
DANGER = COLORS["DANGER"]
DANGER_HOVER = COLORS["DANGER_HOVER"]
SUCCESS = COLORS["SUCCESS"]
WARNING = COLORS["WARNING"]
ERROR = COLORS["ERROR"]

_CHECK_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 14 14'>"
    "<path d='M3 7.2l2.6 2.6L11 4.4' fill='none' stroke='#ffffff' stroke-width='2' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)
_ARROW_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'>"
    f"<path d='M1 1l4 4 4-4' fill='none' stroke='{TEXT_MUTED}' stroke-width='1.6' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)


def _asset(name: str, content: str) -> Optional[str]:
    """Write a small SVG next to the other theme assets; Qt style sheets only load files."""
    directory = os.path.join(tempfile.gettempdir(), f"openscilab-theme-{__version__}-{THEME_MODE}")
    path = os.path.join(directory, name)
    try:
        os.makedirs(directory, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
    except OSError:
        return None
    return path.replace("\\", "/")


def _image_rule(path: Optional[str]) -> str:
    return f"image: url({path});" if path else ""


#: the text size (px) the spacing of the style sheet was drawn for: smaller text makes it tighter
DRAWN_FOR = 12


def font_size() -> int:
    """The text size of the application in pixels (*Settings → Appearance*, 10 by default; 0 there: 12)."""
    try:
        from ..core import preferences

        size = int(preferences.get("appearance.font_size"))
    except Exception:  # noqa: BLE001
        log.debug("font_size: ignored", exc_info=True)
        size = 0
    return size if 9 <= size <= 24 else 12


#: The typeface of the whole application: Inter Tight (rsms, SIL Open Font License), bundled in
#: ``openscilab/ui/fonts`` with its license as static weights (regular, medium, semibold, bold, italic)
#: so that every platform draws the same letters; fixed-width text (console, editors, readouts) keeps
#: the system's.
FONT_FAMILY = "Inter Tight"
FONT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_loaded_family: Optional[str] = None


def load_fonts() -> str:
    """Register the bundled typeface with Qt (once; needs a ``QGuiApplication``) and return its family,
    or the family of the default font when the files are missing or unreadable."""
    global _loaded_family
    if _loaded_family is not None:
        return _loaded_family
    from PySide6.QtGui import QFont, QFontDatabase

    families: set[str] = set()
    try:
        names = sorted(name for name in os.listdir(FONT_FOLDER) if name.lower().endswith((".ttf", ".otf")))
    except OSError:
        names = []
    for name in names:
        font_id = QFontDatabase.addApplicationFont(os.path.join(FONT_FOLDER, name))
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
        else:
            log.warning("the font %s could not be loaded", name)
    _loaded_family = FONT_FAMILY if FONT_FAMILY in families else QFont().family()
    return _loaded_family


def apply_font(application) -> None:
    """Make the bundled typeface the font of ``application`` (its size comes from the style sheet)."""
    from PySide6.QtGui import QFont

    font = QFont(application.font())
    font.setFamily(load_fonts())
    font.setHintingPreference(QFont.PreferNoHinting)  # (the shapes as designed, as on macOS)
    application.setFont(font)


def tabular_font(font=None):
    """The application's typeface (a copy of ``font``, else of the application's) with digits of one
    width (the OpenType feature ``tnum``): for numbers that stand in columns or change while they are
    read, for code and the console - everything is written in the one typeface."""
    from PySide6.QtGui import QFont, QGuiApplication

    if font is None:
        application = QGuiApplication.instance()
        font = application.font() if application is not None else QFont()
    result = QFont(font)
    result.setFamily(load_fonts())
    try:
        result.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):  # (Qt before 6.7: the digits of Inter Tight are close to even)
        pass
    return result


def density(size: Optional[int] = None) -> float:
    """How tight the controls are: 1 at 12 px text, smaller with smaller text (never larger)."""
    return min((size or font_size()) / DRAWN_FOR, 1.0)


def scaled(pixels: int, size: Optional[int] = None) -> int:
    """``pixels`` of spacing at the density of ``size`` (lines of 1-2 px stay as they are)."""
    if pixels <= 2:
        return pixels
    return max(2, round(pixels * density(size)))


def icon_px(base: int = 16, size: Optional[int] = None) -> int:
    """An icon of ``base`` pixels at the density of the text (even sizes: icons stay sharp)."""
    return max(10, 2 * round(base * density(size) / 2))


#: The four icon sizes of the application (before :func:`icon_px` scales them to the text):
#: ``small`` in lists, tabs and the header of a part, ``normal`` on buttons and in tool bars,
#: ``large`` on tiles and section headings, ``activity`` in the activity bar.
ICON = {"small": 12, "normal": 16, "large": 18, "activity": 20}

#: one step of the spacing grid (px at 12 px text)
SPACE_STEP = 4
#: the fixed sizes the layouts share (px at 12 px text; :func:`size` scales them with the text)
SIZES = {
    # the docks of the window in a new layout - at most a share of the window (shell DOCK_SHARES), at
    # least what their contents need
    "sidebar": 250,
    "inspector": 260,
    "node-palette": 230,  # beside a flow that is edited
    "page-list": 150,  # the list of pages of a dialog (settings, capture setup)
    "recent": 220,  # the column of recent files on the start page
    "form-label": 110,  # a label column beside fields
    "swatch": 14,  # a coloured square that opens a colour picker
    "icon-button": 32,  # a button that shows only an icon beside a field or list
    "tab-close": 16,  # the close button of a tab
    "note": 64,  # a field for a few lines of text
    "note-tall": 80,
    "meter": 4,  # the bar of a load meter
    "minimap-width": 190,
    "minimap-height": 124,
    # Circuits
    "catalog-width": 260,  # the catalog of components (sidebar part), its preview
    "catalog-height": 520,
    "catalog-preview": 110,
    "results-list": 260,  # the list of analyses beside their charts
    "scope-side": 250,  # the column of settings and readouts beside an oscilloscope's screen
    "scope-screen-width": 320,
    "scope-screen-height": 200,
    "speed-slider": 110,  # the slider of a circuit's speed in its tool bar
    "smith-chart": 260,
    "search-popup-width": 340,  # the catalog search over the canvas
    "search-popup-height": 560,
}


def size(name: str, text: Optional[int] = None) -> int:
    """One of the shared :data:`SIZES`, at the density of the text."""
    return scaled(SIZES[name], text)


def space(steps: float = 1, size: Optional[int] = None) -> int:
    """``steps`` of the 4 px spacing grid, at the density of the text (``space(2)`` = 8 px)."""
    return scaled(round(steps * SPACE_STEP), size)


def smaller_font(font, steps: float = 1.0, minimum: float = 7.0):
    """A copy of ``font`` ``steps`` points smaller (labels painted beside the main text)."""
    from PySide6.QtGui import QFont

    smaller = QFont(font)
    smaller.setPointSizeF(max(font.pointSizeF() - steps, minimum))
    return smaller


def scaled_font(font, factor: float):
    """A copy of ``font`` ``factor`` times as large (the big numbers of a display)."""
    from PySide6.QtGui import QFont

    larger = QFont(font)
    larger.setPointSizeF(font.pointSizeF() * factor)
    return larger


_SPACING = re.compile(r"(?<![\w-])((?:padding|margin|spacing|min-height|min-width|max-height|max-width|height|width"
                      r"|border-radius)(?:-(?:top|bottom|left|right))?\s*:\s*)([^;{}]+);")


def _compact(sheet: str, size: int) -> str:
    """The spacing of ``sheet`` (drawn for 12 px text) for text of ``size`` px."""
    if density(size) >= 1.0:
        return sheet

    def rule(match: "re.Match") -> str:
        value = re.sub(r"(\d+)px", lambda number: f"{scaled(int(number.group(1)), size)}px", match.group(2))
        return f"{match.group(1)}{value};"

    return _SPACING.sub(rule, sheet)


def build_stylesheet(size: Optional[int] = None) -> str:
    """The style sheet of the chosen theme with text of ``size`` pixels (default: the preference);
    its spacing follows the text (:func:`density`)."""
    size = size or font_size()
    return _compact(_stylesheet(size), size)


def _stylesheet(size: int) -> str:
    C = COLORS
    check = _image_rule(_asset("check.svg", _CHECK_SVG))
    arrow = _image_rule(_asset("arrow-down.svg", _ARROW_SVG))
    icon = icon_px(16, size)
    return f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
    font-size: {size}px;
}}
QMainWindow, QDialog {{
    background-color: {BACKGROUND};
}}
QPushButton {{
    icon-size: {icon}px;
}}
QLabel, QCheckBox, QRadioButton {{
    background-color: transparent;
}}
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{
    color: {TEXT_DISABLED};
}}

/* ------------------------------------------------------------ text roles */
QLabel[role="hint"] {{ color: {TEXT_MUTED}; }}
QLabel[role="heading"] {{ font-size: {size + 1}px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: {size + 8}px; font-weight: 600; }}
QLabel[role="section"] {{ color: {TEXT_MUTED}; font-size: {size - 1}px; font-weight: 600; }}
QLabel[role="error"] {{ color: {ERROR}; }}
QLabel[role="warning"] {{ color: {WARNING}; }}
QLabel[role="success"] {{ color: {SUCCESS}; }}
QLabel[role="value"] {{ font-size: {round(size * 2.2)}px; font-weight: 700; }}
QLabel[role="value-large"] {{ font-size: {size * 4}px; font-weight: 700; }}
{_tone_rules()}
QLabel[role^="chip"] {{
    border-radius: 9px;
    padding: 2px 9px;
    font-weight: 600;
}}
QLabel[role="chip-ok"] {{ background-color: {C['CHIP_OK']}; color: {C['CHIP_OK_TEXT']}; }}
QLabel[role="chip-medium"] {{ background-color: {C['CHIP_MEDIUM']}; color: {C['CHIP_MEDIUM_TEXT']}; }}
QLabel[role="chip-high"] {{ background-color: {C['CHIP_HIGH']}; color: {C['CHIP_HIGH_TEXT']}; }}
QLabel[role="chip-neutral"] {{ background-color: {PANEL_LIGHT}; color: {TEXT_MUTED}; border: 1px solid {BORDER}; }}

/* --------------------------------------------------------------- frames */
QFrame[role="card"] {{
    background-color: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QFrame[role^="banner"] {{
    border-radius: 5px;
    border: 1px solid {BORDER};
}}
QFrame[role="banner-info"] {{ background-color: {C['BANNER_INFO']}; border-color: {C['BANNER_INFO_BORDER']}; }}
QFrame[role="banner-warning"] {{ background-color: {C['BANNER_WARNING']}; border-color: {C['BANNER_WARNING_BORDER']}; }}
QFrame[role="banner-error"] {{ background-color: {C['BANNER_ERROR']}; border-color: {C['BANNER_ERROR_BORDER']}; }}
QFrame[role^="banner"] QLabel {{ background-color: transparent; }}
QFrame[role="card"] QLabel {{ background-color: transparent; }}
QFrame[role="tile"] {{
    background-color: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame[role="tile"]:hover, QFrame[role="tile"]:focus {{
    border-color: {ACCENT};
    background-color: {C['BUTTON_HOVER']};
}}
QFrame[role="tile"] QLabel {{ background-color: transparent; }}
QLabel[role="tile-title"] {{ font-weight: 600; }}
QLabel[role="tile-footer"] {{ color: {TEXT_MUTED}; font-size: {size - 1}px; }}

/* ------------------------------------------------------------ menus/bars */
QMenuBar {{
    background-color: {PANEL};
    color: {TEXT};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background-color: {PANEL_LIGHT};
}}
QMenu {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    padding: 4px 0;
}}
QMenu::item {{
    padding: 5px 24px 5px 20px;
}}
QMenu::item:selected {{
    background-color: {SELECTION};
}}
QMenu::item:disabled {{
    color: {TEXT_DISABLED};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}
QToolBar {{
    background-color: {PANEL};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    spacing: 6px;
}}
QToolBar QWidget {{
    background-color: transparent;
}}
QToolBar QDialog {{
    background-color: {BACKGROUND};
}}
QToolBar QMenu {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
}}
QToolBar::separator {{
    width: 1px;
    background: {BORDER};
    margin: 4px 6px;
}}
/* the tool bars of the documents and the header (ui/widgets/toolbar.py): flat buttons in groups */
QToolBar[adaptive="true"] {{
    padding: 4px 8px;
    spacing: 2px;
}}
QToolBar#dataview-view-toolbar {{
    border-bottom: none;
    border-top: 1px solid {BORDER};
    padding: 2px 8px;
}}
QToolBar[adaptive="true"]::separator {{
    margin: 5px 6px;
}}
QToolBar[adaptive="true"] QToolButton, QToolBar[adaptive="true"] QPushButton[variant="tool"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 7px;
    min-height: 20px;
}}
QToolBar[adaptive="true"] QToolButton:hover:!disabled,
QToolBar[adaptive="true"] QPushButton[variant="tool"]:hover:!disabled {{
    background-color: {C['BUTTON_HOVER']};
    border-color: {BORDER};
}}
QToolBar[adaptive="true"] QToolButton:pressed, QToolBar[adaptive="true"] QPushButton[variant="tool"]:pressed {{
    background-color: {C['BUTTON_PRESSED']};
}}
QToolBar[adaptive="true"] QToolButton:checked, QToolBar[adaptive="true"] QPushButton[variant="tool"]:checked {{
    background-color: {C['CHECKED']};
    border-color: {C['CHECKED_BORDER']};
}}
QToolBar[adaptive="true"] QToolButton[popupMode="1"] {{
    padding-right: 18px;
}}
QToolBar[adaptive="true"] QToolButton::menu-button {{
    border: none;
    width: 16px;
}}
QToolBar[adaptive="true"] QToolButton#toolbar-more::menu-indicator {{
    image: none;
    width: 0px;
}}
QToolBar[adaptive="true"] QToolButton#toolbar-more {{
    padding: 4px 6px;
}}
QStatusBar {{
    background-color: {PANEL};
    border-top: 1px solid {BORDER};
    color: {TEXT_MUTED};
}}
QStatusBar QLabel {{
    color: {TEXT_MUTED};
    padding: 0 6px;
}}
QToolTip {{
    background-color: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}

/* -------------------------------------------------------------- buttons */
QPushButton, QToolButton {{
    background-color: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 12px;
    min-height: 20px;
}}
QToolButton {{
    padding: 3px 6px;
}}
QPushButton:hover:!disabled, QToolButton:hover:!disabled {{
    background-color: {C['BUTTON_HOVER']};
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {C['BUTTON_PRESSED']};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {TEXT_DISABLED};
    border-color: {C['BORDER_SOFT']};
}}
QPushButton:focus, QToolButton:focus {{
    border-color: {ACCENT};
}}
QPushButton::menu-indicator {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 6px;
    {arrow}
}}
QPushButton[variant="primary"] {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: {C['ON_ACCENT']};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover:!disabled {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {ACCENT_PRESSED};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {C['PRIMARY_DISABLED']};
    border-color: {C['PRIMARY_DISABLED']};
    color: {C['PRIMARY_DISABLED_TEXT']};
}}
QPushButton[variant="danger"] {{
    background-color: {DANGER};
    border-color: {DANGER};
    color: {C['ON_ACCENT']};
    font-weight: 600;
}}
QPushButton[variant="danger"]:hover:!disabled {{
    background-color: {DANGER_HOVER};
    border-color: {DANGER_HOVER};
}}
QPushButton[variant="danger"]:disabled {{
    background-color: {PANEL_LIGHT};
    border-color: {C['BORDER_SOFT']};
    color: {TEXT_DISABLED};
}}
QPushButton[variant="link"] {{
    background: transparent;
    border: none;
    color: {ACCENT_HOVER};
    padding: 2px 4px;
}}
QPushButton[variant="link"]:hover:!disabled {{
    background: transparent;
    text-decoration: underline;
}}
QPushButton[align="left"] {{ text-align: left; }}
QToolButton[variant="flat"], QPushButton[variant="flat"] {{
    background: transparent;
    border: none;
    padding: 0;
    min-height: 0;
}}
QFrame[role="accent-line"] {{ background-color: {ACCENT}; border: none; }}
QListWidget[role="plain"] {{ background: transparent; border: none; }}

/* --------------------------------------------------------------- inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background-color: {INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: {SELECTION};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {TEXT_DISABLED};
    background-color: {PANEL_LIGHT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    {arrow}
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
    outline: none;
}}
QListWidget, QTreeWidget, QTableView {{
    background-color: {INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
    alternate-background-color: {PANEL_LIGHT};
    outline: none;
}}
QListWidget::item {{
    padding: 4px 6px;
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
    background-color: {SELECTION};
    color: {TEXT};
}}
QTreeWidget::item {{
    padding: 2px 0;
}}
QTableView {{
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background-color: {PANEL};
    color: {TEXT_MUTED};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
    font-weight: 600;
}}
QCheckBox, QRadioButton {{
    spacing: 6px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    background-color: {INPUT};
    border: 1px solid {BORDER_STRONG};
}}
QCheckBox::indicator {{
    border-radius: 3px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    {check}
}}
QRadioButton::indicator:checked {{
    background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {C['ON_ACCENT']}, stop:0.38 {C['ON_ACCENT']}, stop:0.48 {ACCENT}, stop:1 {ACCENT});
    border-color: {ACCENT};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {PANEL_LIGHT};
    border-color: {C['BORDER_SOFT']};
}}

/* ----------------------------------------------------------- containers */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT};
}}
QGroupBox > QWidget {{
    font-weight: normal;
}}
QScrollArea {{
    border: none;
}}
QScrollBar:horizontal {{
    background: {PANEL};
    height: 12px;
    margin: 0;
}}
QScrollBar:vertical {{
    background: {PANEL};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle {{
    background: {C['SCROLL']};
    border-radius: 4px;
    min-width: 28px;
    min-height: 28px;
    margin: 2px;
}}
QScrollBar::handle:hover {{
    background: {C['SCROLL_HOVER']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C['SLIDER_HANDLE']};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {BORDER};
    top: 0;
}}
QTabBar {{
    qproperty-drawBase: 0;
    background-color: {PANEL};
}}
QTabBar::tab {{
    background: {PANEL};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    margin: 0;
    padding: 7px 12px 5px 12px;
    color: {TEXT_MUTED};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:disabled {{
    color: {BORDER_STRONG};
}}
QTabWidget#document-group > QTabBar::tab {{
    background: {PANEL};
    padding: 6px 12px 6px 0;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    color: {TEXT_MUTED};
}}
QTabWidget#document-group > QTabBar::tab:selected {{
    background: {BACKGROUND};
    color: {TEXT};
}}
QTabBar QToolButton {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    margin: 1px 0;
}}
QTabBar QToolButton:hover {{
    background-color: {PANEL_LIGHT};
}}
QTabBar::tear {{
    width: 0;
    border: none;
}}
QProgressBar {{
    background-color: {INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}
QPushButton[variant="tool"] {{
    background-color: transparent;
    border: 1px solid transparent;
    padding: 4px 6px;
}}
QPushButton[variant="tool"]:hover:!disabled {{
    background-color: {C['BUTTON_HOVER']};
    border-color: {BORDER};
}}
QPushButton[variant="tool"]:checked {{
    background-color: {C['CHECKED']};
    border-color: {C['CHECKED_BORDER']};
}}
QFrame[role="viewbar"] {{
    background-color: {PANEL};
    border-top: 1px solid {BORDER};
}}
QFrame[role="viewbar"] QLabel {{
    background-color: transparent;
}}
QMainWindow::separator {{
    background-color: {BORDER};
    width: 1px;
    height: 1px;
}}
QMainWindow::separator:hover {{
    background-color: {ACCENT};
}}
QDockWidget {{
    titlebar-close-icon: none;
    color: {TEXT_MUTED};
    font-size: {size - 1}px;
    font-weight: 600;
}}
QDockWidget::title {{
    background-color: {PANEL};
    padding: 5px 8px;
    border-bottom: 1px solid {BORDER};
    text-align: left;
}}
QDockWidget::close-button {{
    background: transparent;
    border: none;
    padding: 0;
}}
"""


def _tone_rules() -> str:
    """Text in the colour of a token (:func:`set_tone`): device states, run states, results."""
    return "\n".join(f'QLabel[tone="{name}"] {{ color: {value}; }}' for name, value in TOKENS[THEME_MODE].items()
                     if name.startswith(("device.", "run.", "status.")))


def set_tone(widget: QWidget, name: Optional[str]) -> QWidget:
    """Colour the text of a label with a token of the lab (``"device.connected"``, ``"run.error"``,
    ``"status.warning"``); ``None`` for the normal colour."""
    if widget.property("tone") != name:
        widget.setProperty("tone", name)
        _repolish(widget)
    return widget


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def apply_palette(target) -> None:
    """Colours the style sheet cannot set: links in rich text labels."""
    palette = target.palette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        palette.setColor(group, QPalette.Link, QColor(ACCENT_HOVER))
        palette.setColor(group, QPalette.LinkVisited, QColor(ACCENT_HOVER))
    target.setPalette(palette)


def set_role(widget: QWidget, role: Optional[str]) -> QWidget:
    """Give a label or frame one of the semantic roles of the style sheet."""
    if widget.property("role") != role:
        widget.setProperty("role", role)
        _repolish(widget)
    return widget


def set_variant(button: QWidget, variant: Optional[str]) -> QWidget:
    """Mark a button as ``primary``, ``danger`` or ``link`` (``None`` for a normal button)."""
    if button.property("variant") != variant:
        button.setProperty("variant", variant)
        _repolish(button)
        if button.property("iconName"):
            from .icons import refresh_icon  # icons import the theme colours

            refresh_icon(button)
    return button


# ------------------------------------------------------------------ lab tokens
#: Colour tokens of the lab (shell, devices, flows, charts), for the dark theme and a light variant.
#: Widgets read them through :func:`token`, never as literal colours. Node groups (``group.*``) are
#: muted header tints 20-25° of hue apart; signal types (``type.*``) are the saturated colours of
#: ports and wires, so a node's group and the types of its ports are never confused.
TOKENS = {
    "dark": {
        # device status (sidebar, header chips, device card)
        "device.connected": "#7fd18b",
        "device.simulated": "#9b8cff",
        "device.disconnected": "#6f6f76",
        "device.busy": "#4b8ae6",
        "device.error": "#ff7f7f",
        # signal types: wires and ports of the flow graph
        "type.digital": "#4b8ae6",
        "type.analog": "#7fd18b",
        "type.scalar": "#e8b04b",
        "type.bool": "#e07fd1",
        "type.event": "#ff9a62",
        "type.states": "#58c4c4",
        "type.capture": "#c9c9cf",
        "type.table": "#b0a3ff",
        "type.any": "#a3a3ab",
        "type.device": "#f2c94c",
        # execution state of nodes and flows
        "run.idle": "#6f6f76",
        "run.waiting": "#e8b04b",
        "run.running": "#4b8ae6",
        "run.done": "#7fd18b",
        "run.error": "#ff7f7f",
        "run.breakpoint": "#d05656",
        # node groups of the flow editor (header tint, palette, minimap)
        "group.generator": "#be6360",
        "group.gpio": "#be8560",
        "group.device": "#bea260",
        "group.time": "#bbbe60",
        "group.control": "#98be60",
        "group.decoder": "#63be60",
        "group.measure": "#60bea6",
        "group.remote": "#60b1be",
        "group.signal": "#6097be",
        "group.view": "#6077be",
        "group.convert": "#6c60be",
        "group.data": "#9260be",
        "group.report": "#be609b",
        "group.structure": "#8a8a92",
        "group.subflow": "#a3a3ab",
        "group.plugin": "#a3a3ab",
        # series of charts (strip charts, XY, panels): one palette everywhere
        "trace.0": "#4b8ae6",
        "trace.1": "#7fd18b",
        "trace.2": "#e8b04b",
        "trace.3": "#e07fd1",
        "trace.4": "#58c4c4",
        "trace.5": "#ff9a62",
        "trace.6": "#b0a3ff",
        "trace.7": "#c9c9cf",
        # results of checks and self-tests
        "status.ok": "#7fd18b",
        "status.warning": "#e8b04b",
        "status.fail": "#ff7f7f",
        # shell surfaces
        "shell.activity": "#1c1c1f",
        # painted parts of documents
        "canvas.dots": "rgba(255,255,255,14)",
        "canvas.hint": "rgba(255,255,255,110)",
        # circuits (the schematic editor)
        "circuit.symbol": "#d6d6dc",
        "circuit.body": "#2a2a30",
        "circuit.wire": "#7fd18b",
        "circuit.wire.selected": "#e8b04b",
        "circuit.junction": "#7fd18b",
        "circuit.pin": "#a3a3ab",
        "circuit.pin.open": "#ff7f7f",
        "circuit.label": "#e8b04b",
        "circuit.text": "#c9c9cf",
        "circuit.probe": "#58c4c4",
        "circuit.value": "#9bc4ff",
        "circuit.high": "#ff7f7f",
        "circuit.low": "#4b8ae6",
        "circuit.current": "#e8b04b",
        "circuit.selection": "#4b8ae6",
        "circuit.unsimulated": "#a3a3ab",
        "circuit.comment": "#b9a97a",
        "circuit.voltage.positive": "#3fe06a",
        "circuit.voltage.zero": "#7c7c86",
        "circuit.voltage.negative": "#ff4d5a",
        "circuit.flow": "#ffd84d",
        "circuit.group": "#8a8aa0",
        "circuit.group.blue": "#4b8ae6",
        "circuit.group.green": "#5fbf6f",
        "circuit.group.yellow": "#e8c04b",
        "circuit.group.red": "#e46a6a",
        "circuit.group.purple": "#a983e8",
        "circuit.group.gray": "#9a9aa6",
        "comment.fill": "#4a4430",
        "comment.border": "#6e5626",
        "comment.border.selected": "#e8b04b",
        "comment.text": "#fbe6c2",
        "wire.invalid": "#ff7f7f",
        "led.off": "#3a3a3f",
        "led.on": "#7fd18b",
        "screen": "#000000",
        "shadow": "#000000",
        "pin.idle": "#2a2a2e",
        "pin.output": "#5a4520",
        "pin.high": "#7fd18b",
        "chart.grid": "rgba(255,255,255,18)",
        "code.key": "#4b8ae6",
        "code.number": "#e8b04b",
        "code.string": "#7fd18b",
        "code.symbol": "#e07fd1",
        "code.comment": "#77777f",
    },
    "light": {
        "device.connected": "#1f8a3a",
        "device.simulated": "#5a48d6",
        "device.disconnected": "#8a8a92",
        "device.busy": "#1f62c4",
        "device.error": "#c62828",
        "type.digital": "#1f62c4",
        "type.analog": "#1f8a3a",
        "type.scalar": "#a86b00",
        "type.bool": "#a53a96",
        "type.event": "#c4581a",
        "type.states": "#1d8585",
        "type.capture": "#55555e",
        "type.table": "#5a48d6",
        "type.any": "#77777f",
        "type.device": "#9a6b00",
        "run.idle": "#8a8a92",
        "run.waiting": "#a86b00",
        "run.running": "#1f62c4",
        "run.done": "#1f8a3a",
        "run.error": "#c62828",
        "run.breakpoint": "#b33a3a",
        "group.generator": "#993633",
        "group.gpio": "#995c33",
        "group.device": "#997a33",
        "group.time": "#969933",
        "group.control": "#709933",
        "group.decoder": "#369933",
        "group.measure": "#339980",
        "group.remote": "#338b99",
        "group.signal": "#336e99",
        "group.view": "#334c99",
        "group.convert": "#413399",
        "group.data": "#693399",
        "group.report": "#993374",
        "group.structure": "#77777f",
        "group.subflow": "#5a5a63",
        "group.plugin": "#5a5a63",
        "trace.0": "#1f62c4",
        "trace.1": "#1f8a3a",
        "trace.2": "#a86b00",
        "trace.3": "#a53a96",
        "trace.4": "#1d8585",
        "trace.5": "#c4581a",
        "trace.6": "#5a48d6",
        "trace.7": "#55555e",
        "status.ok": "#1f7a35",
        "status.warning": "#9a6200",
        "status.fail": "#c62828",
        "shell.activity": "#e4e4e8",
        "canvas.dots": "rgba(0,0,0,30)",
        "canvas.hint": "rgba(0,0,0,120)",
        "circuit.symbol": "#2a2a30",
        "circuit.body": "#fbfbfd",
        "circuit.wire": "#1f8a3a",
        "circuit.wire.selected": "#a86b00",
        "circuit.junction": "#1f8a3a",
        "circuit.pin": "#55555e",
        "circuit.pin.open": "#c62828",
        "circuit.label": "#a86b00",
        "circuit.text": "#3a3a42",
        "circuit.probe": "#1d8585",
        "circuit.value": "#1f62c4",
        "circuit.high": "#c62828",
        "circuit.low": "#1f62c4",
        "circuit.current": "#a86b00",
        "circuit.selection": "#1f62c4",
        "circuit.unsimulated": "#77777f",
        "circuit.comment": "#7a6a3a",
        "circuit.voltage.positive": "#169a3c",
        "circuit.voltage.zero": "#9a9aa4",
        "circuit.voltage.negative": "#d42a3a",
        "circuit.flow": "#d99a00",
        "circuit.group": "#8a8a9a",
        "circuit.group.blue": "#3b78d8",
        "circuit.group.green": "#3fa04f",
        "circuit.group.yellow": "#d8a420",
        "circuit.group.red": "#d04848",
        "circuit.group.purple": "#8a5fd0",
        "circuit.group.gray": "#80808c",
        "comment.fill": "#fbf1d6",
        "comment.border": "#e2c27e",
        "comment.border.selected": "#9a6200",
        "comment.text": "#4a3a10",
        "wire.invalid": "#c62828",
        "led.off": "#d6d6dc",
        "led.on": "#1f8a3a",
        "screen": "#000000",
        "shadow": "#000000",
        "pin.idle": "#e2e2e7",
        "pin.output": "#f2dfb4",
        "pin.high": "#1f8a3a",
        "chart.grid": "rgba(0,0,0,22)",
        "code.key": "#1f62c4",
        "code.number": "#9a6200",
        "code.string": "#1f7a35",
        "code.symbol": "#a53a96",
        "code.comment": "#77777f",
    },
}

#: The waveform display of the data view (traces, rulers, overview, the charts of its captures):
#: dark in both themes, like the screen of an instrument.
DISPLAY_TOKENS = {
    "display.background": "#1c1c1f",
    "display.background.alternate": "#1f1f23",
    "display.row.separator": "#2c2c31",
    "display.grid.major": "#3a3a40",
    "display.grid.minor": "#28282d",
    "display.grid.plot": "#34343a",
    "display.sample.line": "#34343a",
    "display.text": "#ffffff",
    "display.text.dim": "#969aa0",
    "display.line.user": "#5ad1e6",
    "display.line.trigger": "#f0a53c",
    "display.line.burst": "#e1e1e8",
    "display.burst": "#e0af1d",
    "display.burst.fill": "rgba(224,175,29,128)",
    "display.selection": "rgba(90,140,220,70)",
    "display.selection.edge": "rgba(120,170,255,160)",
    "display.cursor.a": "#62c4ff",
    "display.cursor.b": "#ff80c4",
    "display.bookmark": "#8cd678",
    "display.search.hit": "rgba(255,214,90,150)",
    "display.search.current": "#ffd65a",
    "display.ruler.background": "#242428",
    "display.ruler.border": "#3a3a40",
    "display.ruler.major": "#8c8c96",
    "display.ruler.minor": "#52525a",
    "display.ruler.text": "#c8c8d0",
    "display.level.low": "#787880",
    "display.bus": "#8aaeff",
    "display.bus.text": "#9696a0",
    "display.shade": "rgba(0,0,0,55)",
    "display.hover": "rgba(255,255,255,10)",
    "display.hover.edge": "rgba(255,255,255,20)",
    "display.hatch": "rgba(255,255,255,22)",
    "display.band": "rgba(255,255,255,8)",
    "display.band.strong": "rgba(255,255,255,18)",
    "display.popup": "rgba(18,18,20,235)",
    "display.popup.border": "rgba(255,255,255,120)",
    "display.ink": "#000000",
    "display.ink.dark": "#141418",
}
#: Colours of the channels of a new capture: 16 distinct hues of similar brightness, softer than pure
#: RGB, which read well on the dark display and next to each other (channel n: entry n % 16).
DISPLAY_CHANNELS = (
    "#f2a33a", "#56b6f7", "#6ccb85", "#ef6f78", "#ad8af0", "#48ccc0", "#f0cf58", "#e685cb",
    "#8aaeff", "#a2cf62", "#ff9466", "#5fc4e6", "#d8a472", "#b3a5f2", "#76d3a6", "#e3db78",
)
#: Colours of the analog tracks of the display (after the digital palette)
DISPLAY_ANALOG = ("#f5c542", "#4fc3f7", "#e57373", "#81c784", "#ba68c8", "#ffb74d", "#4db6ac", "#f06292")
for _mode in TOKENS:
    TOKENS[_mode].update(DISPLAY_TOKENS)
#: the series of charts, in order
TRACE_COUNT = 8
#: what the colour picker of a panel widget offers first
DEFAULT_PICK = "#4caf50"
#: white and black for text on a colour of any lightness (:func:`contrast`)
WHITE = "#ffffff"
BLACK = "#000000"


def token(name: str, mode: Optional[str] = None) -> str:
    """The colour of ``name`` in ``mode`` (default: the theme of the run): a token of the lab
    (``"type.analog"``, ``"group.measure"``, ``"display.grid.major"``) or a colour of the shell's
    palette (``"TEXT"``, ``"BORDER"``)."""
    mode = mode or THEME_MODE
    tokens = TOKENS[mode]
    if name in tokens:
        return tokens[name]
    return PALETTES[mode][name]


def to_qcolor(value: str) -> QColor:
    """A colour written as ``#rrggbb`` or ``rgba(r,g,b,a)`` as a ``QColor``."""
    if value.startswith("rgba("):
        red, green, blue, alpha = (int(part) for part in value[5:-1].split(","))
        return QColor(red, green, blue, alpha)
    return QColor(value)


def qcolor(name: str, mode: Optional[str] = None) -> QColor:
    """A token as a ``QColor`` (tokens may be ``#rrggbb`` or ``rgba(r,g,b,a)``)."""
    return to_qcolor(token(name, mode))


def type_color(type_name: str, mode: Optional[str] = None) -> str:
    """Colour of a signal type (``Digital``, ``Analog``, ...) for wires and ports."""
    key = f"type.{type_name.lower()}"
    return TOKENS[mode or THEME_MODE].get(key, TOKENS[mode or THEME_MODE]["type.any"])


def group_color(group: str, mode: Optional[str] = None) -> str:
    """The colour of a node group (``measure``, ``signal`` ...); groups of plugins get a neutral one
    unless they named a token of their own."""
    tokens = TOKENS[mode or THEME_MODE]
    return tokens.get(group if group.startswith("group.") else f"group.{group}", tokens["group.plugin"])


def trace_color(index: int, mode: Optional[str] = None) -> str:
    """The colour of the ``index``-th series of a chart (one palette for every chart)."""
    return TOKENS[mode or THEME_MODE][f"trace.{index % TRACE_COUNT}"]


def contrast(color: QColor) -> QColor:
    """Black or white, whichever reads better on ``color``."""
    yiq = (color.red() * 299 + color.green() * 587 + color.blue() * 114) // 1000
    return QColor(BLACK) if yiq >= 128 else QColor(WHITE)


_CLOSE_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'>"
    f"<path d='M3 3l6 6M9 3l-6 6' fill='none' stroke='{TEXT_MUTED}' stroke-width='1.5' "
    "stroke-linecap='round'/></svg>"
)

def shell_stylesheet(size: Optional[int] = None) -> str:
    """The style of the parts of the shell (sidebar, documents, palette), for text of ``size`` px."""
    size = size or font_size()
    return _compact(f"""
QTabBar QToolButton#tab-close {{
    border: none;
    border-radius: 3px;
    padding: 0;
    margin: 0;
    background: transparent;
}}
QTabBar QToolButton#tab-close:hover {{
    background-color: {BORDER};
}}
QToolBar#activity-bar {{
    background-color: {TOKENS[THEME_MODE]["shell.activity"]};
    border: none;
    border-right: 1px solid {BORDER};
    padding: 6px 4px;
    spacing: 2px;
}}
QToolBar#activity-bar QToolButton {{
    border: none;
    border-radius: 6px;
    padding: 7px;
    margin: 1px 0;
    color: {TEXT_MUTED};
    font-size: {size - 2}px;
}}
QToolBar#activity-bar[labels="true"] QToolButton {{
    padding: 5px 2px 4px 2px;
    min-width: 46px;
}}
QToolBar#activity-bar QToolButton:hover {{
    background-color: {COLORS['BUTTON_HOVER']};
    color: {TEXT};
}}
QToolBar#activity-bar QToolButton:checked {{
    background-color: {COLORS['CHECKED']};
    color: {TEXT};
}}
QToolBar#header-bar {{
    padding: 3px 8px;
}}
/* two or more buttons that are one switch (Real time | Virtual time) */
QWidget#segmented QToolButton {{
    background-color: {PANEL_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 0;
    padding: 3px 10px;
    margin: 0;
    color: {TEXT_MUTED};
}}
QWidget#segmented QToolButton[segment="first"] {{
    border-top-left-radius: 4px;
    border-bottom-left-radius: 4px;
}}
QWidget#segmented QToolButton[segment="last"] {{
    border-left: none;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}}
QWidget#segmented QToolButton:hover:!disabled {{
    color: {TEXT};
    background-color: {COLORS['BUTTON_HOVER']};
}}
QWidget#segmented QToolButton:checked {{
    background-color: {COLORS['CHECKED']};
    border-color: {COLORS['CHECKED_BORDER']};
    color: {TEXT};
}}
QWidget#segmented QToolButton:disabled {{
    color: {TEXT_DISABLED};
}}
QWidget#part-header {{
    background-color: transparent;
    border-top: 1px solid {BORDER};
}}
QWidget#part-header:hover {{
    background-color: {COLORS['BUTTON_HOVER']};
}}
QLabel[role="part-title"] {{
    color: {TEXT};
    font-weight: 600;
    font-size: {size - 1}px;
}}
QLabel[role="part-count"] {{
    color: {TEXT_MUTED};
    font-size: {size - 1}px;
    padding: 0 4px;
}}
QToolButton#part-button {{
    border: none;
    border-radius: 3px;
    padding: 2px;
}}
QToolButton#part-button:hover {{
    background-color: {BORDER};
}}
QWidget#sidebar QListWidget {{
    background-color: transparent;
    border: none;
}}
QWidget#sidebar QListWidget::item {{
    padding: 3px 6px;
    border-radius: 4px;
}}
QLabel[role="sidebar-title"] {{
    color: {TEXT_MUTED};
    font-size: {size - 1}px;
    font-weight: 600;
    padding: 6px 10px 2px 10px;
}}
QTabWidget#document-group::pane {{
    border: none;
    border-top: 1px solid {BORDER};
}}
QTabWidget#document-group[active="true"] QTabBar::tab:selected {{
    border-top: 2px solid {ACCENT};
}}
QLineEdit#palette-input {{
    font-size: {size + 3}px;
    padding: 8px;
}}
QFrame#command-palette {{
    background-color: {PANEL};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
}}
""", size)

