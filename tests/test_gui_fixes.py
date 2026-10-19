"""Fixes of the window: the header tools, menus in tool bars, the menu bar of a data view, rows that
line up with the tracks, the selection over every track and zooming to it."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from openscilab.ui.theme import build_stylesheet

C64 = os.path.join(os.path.dirname(__file__), "..", "examples", "c64-demo.lac")


def settle() -> None:
    for _ in range(20):
        QApplication.processEvents()


def test_the_header_holds_tools_instead_of_device_names(shell):
    # one New button whose menu is File → New, in the same order
    assert shell.new_tool.menu() is shell.new_menu
    assert [action.text().replace("&", "") for action in shell.new_menu.actions() if action.text()] == [
        "Flow", "Panel", "Data view", "Waveform", "Circuit", "Project from an example..."]  # (Circuit: a plugin's)
    assert shell.examples_tool.menu() is shell.examples_menu
    keys = [item.key for item in shell.header.ordered()]
    # the search right after the logo, then new, open and save, the examples, the run controls
    assert keys.index("logo") < keys.index("search") < keys.index("new") < keys.index("open") \
        < keys.index("examples") < keys.index("run") < keys.index("time")
    shell.new_tool.click()  # (its button: a flow; its arrow: the menu)
    assert type(shell.active_document()).__name__ == "FlowDocument"
    assert not hasattr(shell, "device_chips")
    assert shell.save_tool.toolTip().startswith("Save")


def test_menus_in_tool_bars_and_tab_buttons_are_opaque():
    sheet = build_stylesheet()
    # the overflow menu of a full tool bar is a child of it: "QToolBar QWidget" made it transparent
    assert sheet.index("QToolBar QMenu") > sheet.index("QToolBar QWidget")
    assert "QTabBar QToolButton" in sheet and "QTabBar::tear" in sheet


def test_a_data_view_keeps_the_menus_of_the_application(shell):
    view = shell.new_data_view()
    assert not hasattr(view, "menuBar")  # (no menu bar of its own: on macOS it took the shell's place)
    titles = [action.text() for action in shell.menuBar().actions()]
    assert "E&xamples" in titles and "&Data" in titles


def test_the_rows_above_the_tracks_line_up_with_them(shell):
    view = shell.new_data_view()
    view.open_capture_file(C64)
    settle()
    bar = view.scroll_area.verticalScrollBar()
    assert bar.isVisible()  # 39 channels: the tracks scroll
    assert view.sample_marker.width() == view.sample_viewer.width()  # the ruler had the scroll bar's width more
    assert all(gutter.width() == bar.width() for gutter in view._gutters)


def test_the_selection_shows_over_every_track_and_can_be_zoomed_to(shell):
    from openscilab.ui.widgets.sample_marker import Selection

    view = shell.new_data_view()
    view.open_capture_file(C64)
    settle()
    model = view.model
    model.set_view(0, 4000)
    assert not view.fit_selection_button.isEnabled()
    view.sample_marker.selection = Selection(1000, 1999)
    view.sample_marker._share_selection()
    assert model.selection == (1000, 1999) and view.fit_selection_button.isEnabled()
    assert not view.sample_viewer.grab().isNull()
    view.action_fit_selection.trigger()
    assert model.first_sample <= 1000 and model.first_sample + model.visible_samples >= 2000
    assert model.visible_samples < 1300  # the selection fills the window (with a little room)
    view.sample_marker.clear_selection()
    assert model.selection is None and not view.fit_selection_button.isEnabled()


# ------------------------------------------------- capturing in the data view
def test_the_logic_analyzer_template_captures_with_a_connected_device(shell, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from openscilab.driver.simulated import open_simulated

    first = open_simulated("free")
    shell.hub.add(first)
    view = shell.open_example("00-start/02-logic-analyzer")
    assert view.instrument is first  # the only one: taken at once
    second = open_simulated("uno")
    shell.hub.add(second)
    asked = []

    def choose(parent, title, label, items, current, editable):
        asked.append(items)
        return items[1], True

    monkeypatch.setattr(QInputDialog, "getItem", choose)
    view = shell.open_example("00-start/02-logic-analyzer")
    assert asked[0][-1].startswith("No device") and view.instrument is second
    assert shell.run_button.text() == "Capture" and shell.run_button.isEnabled()


def test_the_device_card_has_no_capture_tab_and_its_functions_are_in_details(shell):
    from PySide6.QtWidgets import QToolButton

    from openscilab.driver.simulated import open_simulated

    instrument = open_simulated("free")
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    tabs = [card.tabs.tabText(index) for index in range(card.tabs.count())]
    assert "Capture" not in tabs and tabs[-1] == "Overview"
    assert not hasattr(card, "device_button")  # no more Device menu in the header
    buttons = card.device_box.findChildren(QToolButton)
    assert [button.defaultAction() for button in buttons] == card.device_actions
    assert card.data_button.text() == "Data view"
    card.data_button.click()
    assert shell.active_document() is card.controller.view


def test_one_typeface_everywhere_and_the_mark_alone(shell):
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QLabel

    from openscilab.core import typeface
    from openscilab.ui.shell.main_window import LOGO_SIZE
    from openscilab.ui.theme import FONT_FAMILY, load_fonts, tabular_font

    if not typeface.installed():
        pytest.skip("the typeface could not be fetched (no connection)")
    assert load_fonts() == FONT_FAMILY == "Inter Tight"
    assert QFontInfo(QApplication.instance().font()).family() == FONT_FAMILY
    assert QFontInfo(tabular_font()).family() == FONT_FAMILY  # (numbers, code and the console too)
    assert QFontInfo(shell.console.python.output.font()).family() == FONT_FAMILY  # (the console)
    labels = shell.logo.findChildren(QLabel)
    assert [label.text() for label in labels] == [""]  # (the mark, without the name)
    assert labels[0].pixmap().deviceIndependentSize().width() == LOGO_SIZE
