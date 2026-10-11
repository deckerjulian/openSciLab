# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The *Front panel* tab of a device card: the instrument's screen live, to be touched with the
mouse (capability ``REMOTE_SCREEN=``), and beside it its settings (a :class:`SettingsFacet`:
an oscilloscope's run control, time base, channels, trigger, acquisition).

The screen and the settings are read in threads; the panel only shows what arrives. Both stop
while the tab is not shown - except the screen while it is shown in a window of its own
(*Open in a window*). *Capture with these settings* captures as set on the instrument
(capability ``DEVICE_SETTINGS``).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import units
from ...core.instrument import Instrument, SettingsFacet
from ...driver.base import CAPABILITY_DEVICE_SETTINGS, CAPABILITY_REMOTE_SCREEN, capability_value
from ..dialogs.common import InlineMessage, hint
from ..icons import set_icon
from ..theme import TEXT_MUTED, set_variant

log = logging.getLogger(__name__)

#: milliseconds between two readings of the settings while the tab is shown
REFRESH_MS = 2000
#: seconds a setting the user just changed is not overwritten by a reading
EDIT_GRACE_S = 3.0
#: seconds between two touches sent while the mouse moves pressed
MOVE_INTERVAL_S = 0.03


def submit(executor: ThreadPoolExecutor, call: Callable, *arguments) -> None:
    """``call`` in ``executor``, unless it was shut down (the panel was closed while a reading, a
    write or a touch was still on its way back)."""
    try:
        executor.submit(call, *arguments)
    except RuntimeError:
        log.debug("Dropped %s: the panel is closed", getattr(call, "__name__", call))


def screen_link(instrument: Instrument):
    """What shows the screen of ``instrument`` (``start``, ``stop``, ``touch``, ``touch_supported``,
    ``problem``), ``None`` when it has none. May ask the device (its address): not in the UI thread."""
    capture = instrument.capture
    driver = capture.driver if capture is not None else None
    if driver is None:
        return None
    kind = capability_value(driver.capabilities(), CAPABILITY_REMOTE_SCREEN)
    if kind != "dho":
        return None
    from ...driver.rigoldho.screen import DhoScreen, DhoVideoScreen, ScpiScreen, video_available

    url = driver.web_control_url()
    if url:
        host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        return DhoVideoScreen(host) if video_available() else DhoScreen(host)
    return ScpiScreen(driver.screenshot)


def step_125(value: float, direction: int) -> float:
    """The next value of the 1-2-5 sequence above (``direction`` 1) or below (-1) ``value``."""
    if value <= 0:
        return 1e-3
    exponent = math.floor(math.log10(value) + 1e-9)
    candidates = [mantissa * 10.0 ** power for power in (exponent - 1, exponent, exponent + 1)
                  for mantissa in (1, 2, 5)]
    if direction > 0:
        return min((item for item in candidates if item > value * (1 + 1e-6)), default=value * 2)
    return max((item for item in candidates if item < value * (1 - 1e-6)), default=value / 2)


class ScreenView(QWidget):
    """The latest image of the screen, scaled; mouse presses become touches on it."""

    frame_arrived = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 188)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(False)
        self.image: Optional[QImage] = None
        self.link = None
        self.message = "Connecting to the screen…"
        self.frames = 0
        self._pending = False
        self._pressed = False
        self._last_move = 0.0
        #: touches go out in order, off the UI thread (a connection may take a moment)
        self._sender = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openscilab-touch")
        self.frame_arrived.connect(self._show)

    # ------------------------------------------------------------- images
    def deliver(self, data: Any) -> None:
        """From the thread of the link (the bytes of a JPEG or PNG, or an RGB array): only the
        newest image waits to be shown."""
        self._latest = data
        if self._pending:
            return
        self._pending = True
        self.frame_arrived.emit()

    def _show(self) -> None:
        data = self._latest
        self._pending = False
        if isinstance(data, (bytes, bytearray)):
            image = QImage.fromData(bytes(data))
        else:
            height, width = data.shape[:2]
            image = QImage(data.data, width, height, 3 * width, QImage.Format_RGB888).copy()
        if not image.isNull():
            self.image = image
            self.frames += 1
            self.message = ""
            self.update()

    def _target(self) -> QRectF:
        if self.image is None:
            return QRectF(self.rect())
        width, height = self.image.width(), self.image.height()
        scale = min(self.width() / width, self.height() / height)
        return QRectF((self.width() - width * scale) / 2, (self.height() - height * scale) / 2,
                      width * scale, height * scale)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self.image is not None:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(self._target(), self.image)
        if self.message:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap, self.message)

    # ------------------------------------------------------------- touches
    def _screen_point(self, position: QPointF) -> Optional[tuple[float, float]]:
        if self.image is None:
            return None
        target = self._target()
        if not target.contains(position):
            return None
        return ((position.x() - target.x()) * self.image.width() / target.width(),
                (position.y() - target.y()) * self.image.height() / target.height())

    def _touch(self, kind: str, position: QPointF) -> None:
        link = self.link
        point = self._screen_point(position)
        if link is None or not link.touch_supported or point is None:
            return
        submit(self._sender, link.touch, kind, point[0], point[1])

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._touch("down", event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt
        if self._pressed and time.monotonic() - self._last_move >= MOVE_INTERVAL_S:
            self._last_move = time.monotonic()
            self._touch("move", event.position())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self._touch("up", event.position())

    def close_link(self) -> None:
        self._sender.shutdown(wait=False)


class ScreenWindow(QWidget):
    """The instrument's screen in a window of its own; closing it puts the screen back into the tab."""

    closed = Signal()

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(title)
        self.setObjectName("screen-window")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.resize(1024, 620)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt
        self.closed.emit()
        super().closeEvent(event)


class SettingsPanel(QScrollArea):
    """The settings of a :class:`SettingsFacet`, in boxes by group; read every few seconds while
    shown, written when changed."""

    _values_read = Signal(object)
    _value_set = Signal(str, object, str)

    def __init__(self, facet: SettingsFacet, parent: Optional[QWidget] = None,
                 busy: Optional[Callable[[], bool]] = None) -> None:
        super().__init__(parent)
        self.facet = facet
        #: whether the device is capturing (no readings then: they would only slow it down)
        self.busy = busy or (lambda: False)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(8)
        self.message = InlineMessage(content)
        layout.addWidget(self.message)
        self.widgets: dict[str, QWidget] = {}
        self.settings: dict[str, dict] = {}
        self._edited: dict[str, float] = {}
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openscilab-settings")
        self._reading = False
        groups: dict[str, QGroupBox] = {}
        try:
            settings = facet.settings()
        except Exception as error:  # noqa: BLE001 - every way a device can fail to answer
            settings = []
            self.message.show_error(f"The settings cannot be read: {error}")
        for setting in settings:
            self.settings[setting["name"]] = setting
            group = groups.get(setting["group"])
            if group is None:
                group = QGroupBox(setting["group"], content)
                form = QFormLayout(group)
                form.setContentsMargins(8, 6, 8, 6)
                form.setLabelAlignment(Qt.AlignRight)
                group.actions = None  # (a row of the buttons of the group)
                groups[setting["group"]] = group
                layout.addWidget(group)
            self._add(group, setting)
        layout.addStretch(1)
        self.setWidget(content)
        self._values_read.connect(self._show_values)
        self._value_set.connect(self._show_set)
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    # ----------------------------------------------------------- widgets
    def _add(self, group: QGroupBox, setting: dict) -> None:
        form: QFormLayout = group.layout()
        name, kind = setting["name"], setting["kind"]
        if kind == "action":
            if group.actions is None:
                row = QWidget(group)
                group.actions = QGridLayout(row)
                group.actions.setContentsMargins(0, 0, 0, 0)
                group.actions.setSpacing(4)
                form.addRow(row)
            button = QPushButton(setting["label"], group)
            button.clicked.connect(lambda _checked=False, n=name: self._run(n))
            count = group.actions.count()
            group.actions.addWidget(button, count // 3, count % 3)
            self.widgets[name] = button
            return
        if kind == "text":
            widget = QLabel("–", group)
            widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
        elif kind == "bool":
            widget = QCheckBox(group)
            widget.clicked.connect(lambda checked, n=name: self._write(n, bool(checked)))
        elif kind == "choice":
            widget = QComboBox(group)
            for value, label in setting["choices"]:
                widget.addItem(label, value)
            widget.activated.connect(lambda index, n=name, w=widget: self._write(n, w.itemData(index)))
        else:
            widget = QWidget(group)
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            edit = QLineEdit(widget)
            edit.setToolTip(f"A value in {setting.get('unit') or 'units'}, e.g. 200 m{setting.get('unit', '')}")
            edit.editingFinished.connect(lambda n=name, e=edit: self._write_text(n, e))
            widget.edit = edit
            steps = setting.get("steps") == "125"
            if steps:
                row.addWidget(self._step_button(widget, name, -1))
            row.addWidget(edit, 1)
            if steps:
                row.addWidget(self._step_button(widget, name, 1))
        form.addRow(setting["label"] + ":", widget)
        self.widgets[name] = widget

    def _step_button(self, parent: QWidget, name: str, direction: int) -> QToolButton:
        button = QToolButton(parent)
        button.setText("◀" if direction < 0 else "▶")
        button.setToolTip("Next smaller value (1, 2, 5 …)" if direction < 0 else "Next larger value (1, 2, 5 …)")
        button.clicked.connect(lambda _checked=False: self._step(name, direction))
        return button

    # ----------------------------------------------------------- values
    def refresh(self) -> None:
        if self._reading or self.busy():
            return
        self._reading = True

        def read() -> None:
            try:
                values = self.facet.values()
            except Exception as error:  # noqa: BLE001 - every way a device can fail to answer
                values = error
            self._values_read.emit(values)

        submit(self._worker, read)

    def _show_values(self, values: Any) -> None:
        self._reading = False
        if isinstance(values, Exception):
            self.message.show_error(f"The settings cannot be read: {values}")
            return
        now = time.monotonic()
        for name, value in values.items():
            if value is None or now - self._edited.get(name, 0.0) < EDIT_GRACE_S:
                continue
            self._display(name, value)

    def _display(self, name: str, value: Any) -> None:
        widget = self.widgets.get(name)
        setting = self.settings.get(name)
        if widget is None or setting is None:
            return
        kind = setting["kind"]
        if kind == "text":
            widget.setText(str(value))
        elif kind == "bool":
            widget.setChecked(bool(value))
        elif kind == "choice":
            index = widget.findData(value)
            if index < 0:
                widget.addItem(str(value), value)
                index = widget.count() - 1
            widget.setCurrentIndex(index)
        elif not widget.edit.hasFocus():
            widget.edit.setText(units.format_quantity(float(value), setting.get("unit", "")))
            widget.value = float(value)

    def _write_text(self, name: str, edit: QLineEdit) -> None:
        setting = self.settings[name]
        text = edit.text().strip()
        if not text:
            return
        try:
            value = units.parse(text, setting.get("unit", ""))
        except (ValueError, units.UnitError) as error:
            self.message.show_error(f"{setting['label']}: {error}")
            return
        if getattr(self.widgets[name], "value", None) is not None and \
                abs(self.widgets[name].value - value) <= 1e-12 * max(abs(value), 1.0):
            return  # (unchanged: leaving the field is no change)
        self._write(name, value)

    def _step(self, name: str, direction: int) -> None:
        current = getattr(self.widgets[name], "value", None)
        if current is not None:
            self._write(name, step_125(current, direction))

    def _write(self, name: str, value: Any) -> None:
        self._edited[name] = time.monotonic()
        self.message.clear()
        if self.settings[name]["kind"] == "number":
            # at once, so the next step goes on from here (the value taken replaces it)
            widget = self.widgets[name]
            widget.value = float(value)
            if not widget.edit.hasFocus():
                widget.edit.setText(units.format_quantity(float(value), self.settings[name].get("unit", "")))

        def write() -> None:
            try:
                taken = self.facet.set(name, value)
                self._value_set.emit(name, taken, "")
            except Exception as error:  # noqa: BLE001 - every way a device can fail to answer
                self._value_set.emit(name, None, str(error))

        submit(self._worker, write)

    def _show_set(self, name: str, taken: Any, error: str) -> None:
        if error:
            self.message.show_error(error)
        elif taken is not None:
            self._edited[name] = 0.0
            self._display(name, taken)
        self.refresh()  # (a setting may change others: the scale the offset's range)

    def _run(self, name: str) -> None:
        self.message.clear()

        def run() -> None:
            try:
                self.facet.action(name)
                self._value_set.emit(name, None, "")
            except Exception as error:  # noqa: BLE001 - every way a device can fail to answer
                self._value_set.emit(name, None, str(error))

        submit(self._worker, run)

    def start(self) -> None:
        self.refresh()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def close_panel(self) -> None:
        self.timer.stop()
        self._worker.shutdown(wait=False)


class FrontPanelPage(QSplitter):
    """The tab: the screen (when the instrument shows one) and the settings beside it."""

    _link_ready = Signal(object, str)

    def __init__(self, instrument: Instrument, parent: Optional[QWidget] = None,
                 busy: Optional[Callable[[], bool]] = None, capture: Optional[Callable[[], bool]] = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.instrument = instrument
        self.link = None
        self._closed = False
        self._active = False
        self._busy = busy or (lambda: False)
        self.screen_view: Optional[ScreenView] = None
        #: the screen in a window of its own (*Open in a window*), ``None``: in the tab
        self.screen_window: Optional[ScreenWindow] = None
        self.window_button: Optional[QToolButton] = None
        self.capture_button: Optional[QPushButton] = None
        driver_capture = instrument.capture
        driver = driver_capture.driver if driver_capture is not None else None
        has_screen = driver is not None and capability_value(driver.capabilities(), CAPABILITY_REMOTE_SCREEN) is not None
        can_capture = capture is not None and driver is not None and CAPABILITY_DEVICE_SETTINGS in driver.capabilities()
        if has_screen or can_capture:
            left = QWidget(self)
            column = QVBoxLayout(left)
            column.setContentsMargins(0, 0, 0, 0)
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 4)
            if can_capture:
                self.capture_button = QPushButton("Capture with these settings", left)
                self.capture_button.setObjectName("capture-device-settings")
                set_icon(self.capture_button, "play")
                set_variant(self.capture_button, "primary")
                self.capture_button.setToolTip("Capture as set on the oscilloscope (on its screen or here): the "
                                               "channels that are on, its time base and memory depth, its trigger. "
                                               "The capture opens in the data view.")
                self.capture_button.clicked.connect(lambda: capture())
                header.addWidget(self.capture_button)
            header.addStretch(1)
            if has_screen:
                self.window_button = QToolButton(left)
                self.window_button.setObjectName("screen-window-button")
                self.window_button.setText("Open in a window")
                self.window_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                set_icon(self.window_button, "detach")
                self.window_button.setToolTip("Show the screen in a window of its own (it keeps running while "
                                              "other tabs are shown)")
                self.window_button.clicked.connect(self.toggle_screen_window)
                header.addWidget(self.window_button)
            column.addLayout(header)
            self._column = column
        if has_screen:
            self.screen_view = ScreenView(left)
            column.addWidget(self.screen_view, 1)
            #: in the tab while the screen is in its own window
            self.window_note = QLabel("The screen is shown in a window of its own.", left)
            self.window_note.setAlignment(Qt.AlignCenter)
            self.window_note.setVisible(False)
            column.addWidget(self.window_note, 1)
            self.screen_hint = hint("Click and drag on the screen as on the instrument's touch screen.", left)
            column.addWidget(self.screen_hint)
        if has_screen or can_capture:
            if not has_screen:
                column.addStretch(1)
            self.addWidget(left)
        facet = instrument.facet(SettingsFacet)
        self.settings_panel = SettingsPanel(facet, self, busy) if facet is not None else None
        if self.settings_panel is not None:
            self.settings_panel.setMinimumWidth(300)
            self.addWidget(self.settings_panel)
        if has_screen and self.settings_panel is not None:
            self.setStretchFactor(0, 3)
            self.setStretchFactor(1, 1)
        self._link_ready.connect(self._use_link)
        self._problem_timer = QTimer(self)
        self._problem_timer.setInterval(1000)
        self._problem_timer.timeout.connect(self._show_problem)
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(500)
        self._capture_timer.timeout.connect(self._update_capture_button)
        self._capture_timer.start()

    def _update_capture_button(self) -> None:
        if self.capture_button is not None:
            self.capture_button.setEnabled(not self._busy())

    # ------------------------------------------------------- own window
    def toggle_screen_window(self) -> None:
        """*Open in a window*, or bring the open one to the front."""
        if self.screen_window is not None:
            self.screen_window.raise_()
            self.screen_window.activateWindow()
            return
        self.open_screen_window()

    def open_screen_window(self) -> Optional[ScreenWindow]:
        """The screen into a window of its own: it keeps running there while other tabs are shown."""
        view = self.screen_view
        if view is None or self._closed:
            return None
        window = ScreenWindow(f"{self.instrument.name} – screen", self.window())
        self._column.removeWidget(view)
        window.layout().addWidget(view)
        view.show()
        self.window_note.setVisible(True)
        self.window_button.setText("Show the window")
        self.screen_window = window
        window.closed.connect(self._screen_window_closed)
        window.show()
        self._start_screen()
        return window

    def _screen_window_closed(self) -> None:
        """The window was closed: the screen goes back into the tab (it stops there when the tab is hidden)."""
        window, view = self.screen_window, self.screen_view
        self.screen_window = None
        if window is None or view is None:
            return
        window.layout().removeWidget(view)
        self._column.insertWidget(1, view, 1)
        view.show()
        self.window_note.setVisible(False)
        self.window_button.setText("Open in a window")
        if not self._active and not self._closed:
            self._stop_screen()

    def _start_screen(self) -> None:
        """The screen's link starts (found first, in a thread)."""
        if self.screen_view is None or self._closed:
            return
        if self.link is not None:
            self.link.start(self.screen_view.deliver)
            self._problem_timer.start()
            return
        if getattr(self, "_finding", False):
            return
        self._finding = True

        def find() -> None:
            try:
                link, problem = screen_link(self.instrument), ""
            except Exception as error:  # noqa: BLE001 - every way a device can fail to answer
                link, problem = None, str(error)
            self._link_ready.emit(link, problem)

        threading.Thread(target=find, name="openscilab-screen-link", daemon=True).start()

    def _stop_screen(self) -> None:
        self._problem_timer.stop()
        link = self.link
        if link is not None:
            threading.Thread(target=link.stop, name="openscilab-screen-stop", daemon=True).start()

    # ------------------------------------------------------------ start/stop
    def activate(self) -> None:
        """The tab is shown: the screen and the readings start."""
        if self._closed or self._active:
            return
        self._active = True
        if self.settings_panel is not None:
            self.settings_panel.start()
        self._start_screen()

    def _use_link(self, link, problem: str) -> None:
        self._finding = False
        if self._closed:
            return
        self.link = link
        view = self.screen_view
        if link is None:
            view.message = f"The screen cannot be shown: {problem or 'the instrument has none'}"
            view.update()
            return
        view.link = link
        if not link.touch_supported:
            self.screen_hint.setText("The screen over USB, without touches: connect the instrument to the network to "
                                     "touch it here (and to see it faster).")
        if self._active or self.screen_window is not None:
            link.start(view.deliver)
            self._problem_timer.start()

    def _show_problem(self) -> None:
        link, view = self.link, self.screen_view
        if link is not None and view is not None and link.problem and (view.image is None or not view.frames):
            view.message = f"The screen cannot be reached: {link.problem}"
            view.update()

    def deactivate(self) -> None:
        """Another tab is shown: the screen and the readings stop."""
        if not self._active:
            return
        self._active = False
        if self.settings_panel is not None:
            self.settings_panel.stop()
        if self.screen_window is None:  # (in its own window the screen keeps running)
            self._stop_screen()

    def shutdown(self) -> None:
        self._capture_timer.stop()
        window = self.screen_window
        if window is not None:
            self.screen_window = None
            window.closed.disconnect(self._screen_window_closed)
            window.close()
            window.deleteLater()
        self.deactivate()
        self._stop_screen()
        self._closed = True
        if self.settings_panel is not None:
            self.settings_panel.close_panel()
        if self.screen_view is not None:
            self.screen_view.close_link()


def has_front_panel(instrument: Instrument) -> bool:
    """The instrument has a screen to show or settings of its own."""
    if instrument.facet(SettingsFacet) is not None:
        return True
    capture = instrument.capture
    try:
        return capture is not None and capability_value(capture.driver.capabilities(),
                                                        CAPABILITY_REMOTE_SCREEN) is not None
    except Exception:  # noqa: BLE001 - a device that cannot answer has none
        return False
