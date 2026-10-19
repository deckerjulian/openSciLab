# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""How fast a circuit runs in real time: a slider in its tool bar (the circuit's *speed* setting).

Slower than the clock to watch the current flow or an edge rise, faster for a slow circuit (a
capacitor charging for minutes) - the computer sets the upper limit. Moved while the circuit runs, it
runs on at the new speed at once.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSlider, QWidget

from ...circuits import values
from ..theme import set_role, space

#: what the slider offers: simulated seconds per second
SPEEDS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
TIP = ("How fast the circuit runs: simulated seconds per second (1×: as fast as the clock). Slower to watch "
       "the current flow, faster for a slow circuit - the computer sets the upper limit. Only in real time.")


def speed_text(speed: float) -> str:
    """``0.01×``, ``1×``, ``20×``."""
    return f"{speed:.4g}×"


def nearest(speed: float) -> int:
    """The step of the slider closest to ``speed`` (on a logarithmic scale)."""
    speed = max(speed, 1e-9)
    return min(range(len(SPEEDS)), key=lambda index: abs(math.log(SPEEDS[index] / speed)))


class SpeedControl(QWidget):
    """*Speed* with a slider and the speed it is at; :attr:`changed` with the speed chosen."""

    changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(space(2), 0, space(2), 0)
        layout.setSpacing(space(2))
        title = QLabel("Speed", self)
        set_role(title, "hint")
        layout.addWidget(title)
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, len(SPEEDS) - 1)
        self.slider.setPageStep(1)
        self.slider.setFixedWidth(110)
        layout.addWidget(self.slider)
        self.label = QLabel(self)
        # (as wide as the widest text: the bar does not move while the slider does)
        self.label.setMinimumWidth(self.label.fontMetrics().horizontalAdvance("0.005×") + 2)
        layout.addWidget(self.label)
        self.setToolTip(TIP)
        self.speed = 1.0
        self.set_speed(1.0)
        self.slider.valueChanged.connect(self._moved)

    def set_speed(self, speed: float) -> None:
        """Show ``speed`` (the circuit's setting) without telling :attr:`changed`."""
        self.speed = speed
        self.slider.blockSignals(True)
        self.slider.setValue(nearest(speed))
        self.slider.blockSignals(False)
        self.label.setText(speed_text(speed))

    def _moved(self, index: int) -> None:
        self.speed = SPEEDS[index]
        self.label.setText(speed_text(self.speed))
        self.changed.emit(self.speed)

    def fill_menu(self, menu: QMenu) -> None:
        """The speeds as a submenu (the bar too narrow for the slider)."""
        submenu = menu.addMenu(f"Speed ({speed_text(self.speed)})")
        submenu.setEnabled(self.isEnabled())
        group = QActionGroup(submenu)
        for speed in SPEEDS:
            action = QAction(speed_text(speed), submenu)
            action.setCheckable(True)
            action.setChecked(math.isclose(speed, self.speed))
            action.triggered.connect(lambda _checked=False, speed=speed: self._chosen(speed))
            group.addAction(action)
            submenu.addAction(action)

    def _chosen(self, speed: float) -> None:
        self.set_speed(speed)
        self.changed.emit(speed)


def setting_speed(text: str) -> float:
    """The circuit's *speed* setting as a number (1 when it is empty or no number)."""
    number = values.parse_optional(text or "")
    return number if number is not None and number > 0 else 1.0
