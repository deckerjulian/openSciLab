# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A Smith chart: reflection coefficients (S11, S22) over frequency on the circles of constant
resistance and the arcs of constant reactance, normalised to the reference impedance."""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..theme import BORDER, PANEL, TEXT_MUTED, TRACE_COUNT, qcolor, size, smaller_font, trace_color

RESISTANCES = (0.2, 0.5, 1.0, 2.0, 5.0)
REACTANCES = (0.2, 0.5, 1.0, 2.0, 5.0)


class SmithChart(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        #: name -> (frequencies, reflection coefficients)
        self.series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.setMinimumSize(size("smith-chart"), size("smith-chart"))
        self.setToolTip("Smith chart: the reflection coefficient over frequency (the centre is matched)")

    def set_series(self, series: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
        self.series = series
        self.update()

    def _frame(self) -> tuple[QPointF, float]:
        size = min(self.width(), self.height()) - 40
        return QPointF(self.width() / 2, self.height() / 2), max(size / 2, 10)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(PANEL))
        center, radius = self._frame()

        def point(gamma: complex) -> QPointF:
            return QPointF(center.x() + gamma.real * radius, center.y() - gamma.imag * radius)

        unit = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        painter.setPen(QPen(QColor(BORDER), 1.2))
        painter.drawEllipse(unit)
        grid = QPen(qcolor("chart.grid"), 1)
        painter.setPen(grid)
        painter.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()))
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(unit)
        painter.setClipPath(clip)
        for r in RESISTANCES:  # circles of constant resistance
            c, rr = r / (1 + r), 1 / (1 + r)
            painter.drawEllipse(QRectF(center.x() + (c - rr) * radius, center.y() - rr * radius, 2 * rr * radius,
                                       2 * rr * radius))
        for x in REACTANCES:  # arcs of constant reactance (both signs)
            for sign in (1, -1):
                cy, rx = sign / x, 1 / x
                painter.drawEllipse(QRectF(center.x() + (1 - rx) * radius, center.y() - (cy + rx) * radius,
                                           2 * rx * radius, 2 * rx * radius))
        painter.restore()
        painter.setFont(smaller_font(painter.font()))
        painter.setPen(QColor(TEXT_MUTED))
        for r in RESISTANCES:
            gamma = (r - 1) / (r + 1)
            painter.drawText(QRectF(point(complex(gamma, 0)).x() - 14, center.y() + 2, 28, 14), Qt.AlignHCenter, f"{r:g}")
        for x in REACTANCES:
            gamma = (complex(0, x) - 1) / (complex(0, x) + 1)
            spot = point(gamma)
            painter.drawText(QRectF(spot.x() - 14, spot.y() - 16, 28, 14), Qt.AlignHCenter, f"{x:g}j")
        if not self.series:
            painter.drawText(self.rect(), Qt.AlignBottom | Qt.AlignHCenter, "Compute the S-parameters")
            return
        for index, (name, (frequencies, gammas)) in enumerate(self.series.items()):
            color = QColor(trace_color(index % TRACE_COUNT))
            painter.setPen(QPen(color, 1.6))
            path = QPainterPath()
            for k, gamma in enumerate(gammas):
                spot = point(complex(gamma))
                if k == 0:
                    path.moveTo(spot)
                else:
                    path.lineTo(spot)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            if len(gammas):
                painter.setBrush(color)
                painter.drawEllipse(point(complex(gammas[0])), 2.5, 2.5)
            painter.drawText(QRectF(8, 8 + 14 * index, 160, 14), Qt.AlignLeft, name)


def impedance(gamma: complex, z0: float = 50.0) -> complex:
    """The impedance a reflection coefficient means."""
    if abs(1 - gamma) < 1e-12:
        return complex(math.inf, 0)
    return z0 * (1 + gamma) / (1 - gamma)
