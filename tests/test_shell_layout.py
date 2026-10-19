"""The parts of the shell: tabs with their close button on the left, the docks at their widths, the
node palette beside a flow that is edited, an activity bar that is arranged with a right click, collapsible parts of the
sidebar, the flow's overview."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QTabBar, QToolButton


def settle(times: int = 10) -> None:
    for _ in range(times):
        QApplication.processEvents()


def test_tabs_close_on_their_left(shell):
    first, second = shell.new_flow(), shell.new_data_view()
    group = shell.area.group_of(first)
    bar = group.tabBar()
    settle()
    for index in range(bar.count()):
        holder = bar.tabButton(index, QTabBar.LeftSide)
        assert holder is not None and bar.tabButton(index, QTabBar.RightSide) is None
        # inside the tab, not against its edge, and centred on the text
        tab, button = bar.tabRect(index), holder.findChild(QToolButton, "tab-close")
        corner = holder.mapTo(bar, button.geometry().topLeft())
        assert corner.x() - tab.left() >= 4
        assert abs((corner.y() + button.height() / 2) - tab.center().y()) <= 2
    holder = bar.tabButton(group.indexOf(second), QTabBar.LeftSide)
    holder.findChild(QToolButton, "tab-close").click()
    settle()
    assert second not in shell.area.documents() and first in shell.area.documents()


def test_the_node_palette_shows_beside_a_flow_that_is_edited(shell):
    assert "nodes" not in shell.activity_bar.keys()  # (no section of its own)
    flow = shell.new_flow()
    settle()
    assert shell.nodes_dock.isVisible()
    flow.set_view("YAML")
    settle()
    assert not shell.nodes_dock.isVisible()  # (the text is edited, not the graph)
    flow.set_view("Graph")
    view = shell.new_data_view()
    settle()
    assert not shell.nodes_dock.isVisible()
    shell.area.activate(flow)
    settle()
    assert shell.nodes_dock.isVisible()
    shell.action_nodes.setChecked(False)  # Ctrl+Shift+N
    assert not shell.nodes_dock.isVisible()
    shell.action_nodes.setChecked(True)
    assert shell.nodes_dock.isVisible() and view in shell.area.documents()


def wait_for_width(qtbot, condition, widths) -> None:
    """Wait until ``condition`` holds; a timeout says how wide everything was."""
    try:
        qtbot.waitUntil(condition, timeout=5000)
    except qtbot.TimeoutError:
        raise AssertionError(f"the docks did not take their widths: {widths()}") from None


def test_the_docks_start_at_their_widths(shell, qtbot):
    from openscilab.ui.theme import size

    shell.resize(1600, 1000)
    qtbot.waitUntil(lambda: shell.width() == 1600, timeout=5000)

    def widths() -> str:
        return (f"window {shell.width()}, sidebar {shell.sidebar_dock.width()} of {shell.dock_width('sidebar')}, "
                f"inspector {shell.inspector_dock.width()} of {shell.dock_width('inspector')}, "
                f"nodes {shell.nodes_dock.width()} of {shell.dock_width('node-palette')}")

    # in a wide window the docks have their widths of the theme - or what their contents need, which
    # depends on the platform's fonts (Linux needed more than 250 px for the sidebar)
    for name in ("sidebar", "inspector", "node-palette"):
        assert shell.dock_width(name) >= size(name)
    # the sidebar starts folded away and opens at its width (not with the room of the hidden node
    # palette as well, nor as wide as a layout left it)
    assert not shell.sidebar_dock.isVisible()
    wait_for_width(qtbot, lambda: shell.inspector_dock.width() == shell.dock_width("inspector"), widths)
    shell.toggle_sidebar()
    wait_for_width(qtbot, lambda: shell.sidebar_dock.width() == shell.dock_width("sidebar"), widths)
    shell.resizeDocks([shell.sidebar_dock], [shell.width() // 2], Qt.Horizontal)  # dragged far too wide
    shell.toggle_sidebar()
    shell.toggle_sidebar()
    wait_for_width(qtbot, lambda: shell.sidebar_dock.width() == shell.dock_width("sidebar"), widths)
    shell.new_flow()
    wait_for_width(qtbot, lambda: shell.nodes_dock.width() == shell.dock_width("node-palette"), widths)
    assert shell.sidebar_dock.width() == shell.dock_width("sidebar")
    shell.sidebar_dock.setVisible(False)
    shell.reset_layout()
    wait_for_width(qtbot, lambda: (shell.sidebar_dock.width(), shell.inspector_dock.width())
                   == (shell.dock_width("sidebar"), shell.dock_width("inspector")), widths)


def test_in_a_small_window_the_docks_leave_the_document_its_room(shell, qtbot):
    from openscilab.ui.shell.main_window import DOCK_SHARES

    shell.resize(1100, 800)
    qtbot.waitUntil(lambda: shell.width() == 1100, timeout=5000)
    docks = {"sidebar": shell.sidebar_dock, "inspector": shell.inspector_dock}
    for name, dock in docks.items():
        # a share of the window - unless its contents need more
        assert shell.dock_width(name) <= max(round(DOCK_SHARES[name] * 1100), dock.minimumSizeHint().width())
    shell.toggle_sidebar()
    shell.size_docks()
    qtbot.waitUntil(lambda: shell.sidebar_dock.width() == shell.dock_width("sidebar"), timeout=5000)
    assert shell.sidebar_dock.width() + shell.inspector_dock.width() <= shell.width() / 2


def test_a_hidden_section_does_not_widen_the_sidebar(shell):
    sidebar = shell.sidebar
    sidebar.show_section("circuits")  # (the catalog of components: the widest section)
    wide = sidebar.minimumSizeHint().width()
    sidebar.show_section("project")
    assert sidebar.minimumSizeHint().width() <= wide
    assert sidebar.minimumSizeHint().width() == sidebar.currentWidget().minimumSizeHint().width()


def test_the_activity_bar_is_arranged(shell):
    bar = shell.activity_bar
    assert bar.keys() == ["project", "devices", "circuits"]  # (Circuits: a section of openSciLab's own plugin)
    assert [key for key, _action, bottom in bar.ordered() if bottom] == ["start", "settings"]
    bar.move("devices", -1)
    assert [key for key, _a, bottom in bar.ordered() if not bottom] == ["devices", "project", "circuits"]
    bar.move("devices", 1)
    bar.set_shown("devices", False)
    assert not bar.shown("devices") and bar.section_action("devices") not in bar.actions()
    bar.set_show_labels(True)
    from openscilab.ui.shell.activity_bar import ActivityBar

    again = ActivityBar()  # (kept)
    assert again.config["hidden"] == ["devices"] and again.config["labels"] is True
    for key in ("project", "devices", "circuits"):
        bar.set_shown(key, False)
    assert bar.shown("devices") or bar.shown("project") or bar.shown("circuits")  # one section stays
    menu = bar.context_menu("project")
    assert "Move up" in [action.text() for action in menu.actions()]
    bar.reset()
    assert bar.config == {} and bar.shown("devices")


def test_parts_of_the_sidebar_open_and_close(shell):
    section = shell.project_section
    part = section.documents_part
    assert part.is_open and not part.content.isHidden()
    part.set_open(False)
    assert part.content.isHidden()
    from openscilab.ui.shell.sections import _closed

    assert "documents" in _closed()
    part.set_open(True)
    assert "documents" not in _closed()
    shell.new_flow()
    settle()
    assert part.count_label.text() == "1"


def test_the_overview_of_a_flow_draws_its_nodes(shell):
    shell.open_example("13-time/01-align-instruments")
    settle(20)
    flow = next(document for document in shell.area.documents() if document.document_kind == "flow")
    shell.area.activate(flow)
    settle(20)
    image = flow.view.minimap.grab().toImage()
    colours = {image.pixelColor(x, y).name() for x in range(0, image.width(), 3) for y in range(0, image.height(), 3)}
    assert len(colours) > 8  # (nodes in the colours of their kinds, wires, the view)


def test_a_message_has_no_empty_space_beside_its_icon(shell):
    from PySide6.QtWidgets import QLabel, QMessageBox

    from openscilab.ui import messages

    box = messages._box(shell, QMessageBox.Question, "Unsaved changes", "The flow was not saved. Save it?", "Details")
    box.addButton(QMessageBox.Ok)
    box.show()
    settle()
    icon = next(label for label in box.findChildren(QLabel) if not label.pixmap().isNull())
    text = box.findChild(QLabel, "qt_msgbox_label")
    assert icon.width() < 100 and text.width() >= messages.MESSAGE_WIDTH
    box.close()


def _mouse(kind, widget, position, buttons):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    button = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    return QMouseEvent(kind, QPointF(position), widget.mapToGlobal(QPointF(position)), button, buttons, Qt.NoModifier)


def test_a_tab_dragged_off_its_bar_gets_a_window_of_its_own_a_double_click_does_not(shell):
    first, second = shell.new_flow(), shell.new_flow()
    group = shell.area.group_of(first)
    bar = group.tabBar()
    settle()
    # dragged down, out of the bar: a window of its own
    start = bar.tabRect(group.indexOf(second)).center()
    bar.mousePressEvent(_mouse(QEvent.MouseButtonPress, bar, start, Qt.LeftButton))
    bar.mouseMoveEvent(_mouse(QEvent.MouseMove, bar, start + QPoint(0, 10), Qt.LeftButton))
    assert not shell.area.is_detached(second)  # (a small move stays in the bar)
    bar.mouseMoveEvent(_mouse(QEvent.MouseMove, bar, start + QPoint(0, 120), Qt.LeftButton))
    settle()
    assert shell.area.is_detached(second) and group.indexOf(second) < 0
    # double-clicked: it stays (the tab's menu has "Open in a new window")
    point = bar.tabRect(group.indexOf(first)).center()
    bar.mouseDoubleClickEvent(_mouse(QEvent.MouseButtonDblClick, bar, point, Qt.LeftButton))
    settle()
    assert not shell.area.is_detached(first)
    shell.area.attach(second)
    assert not shell.area.is_detached(first) and not shell.area.is_detached(second)
