# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The schematic canvas: navigation of ``ui/widgets/canvas_view.py``, the keys of the editor and
components dropped from the catalog."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsView, QWidget

from ...circuits import GRID
from ..theme import qcolor
from ..widgets.canvas_view import CanvasView
from .scene import SchematicScene, snap

#: the zoom of a new schematic, and the most "fit" zooms in
DEFAULT_ZOOM = 2.0
MAX_FIT_ZOOM = 2.5
#: what the catalog drags: the type of a component (and its unit after a newline)
COMPONENT_MIME = "application/x-openscilab-component"


class SchematicView(CanvasView):
    grid_step = GRID * 2
    grid_min_zoom = 0.35

    def __init__(self, scene: SchematicScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.schematic_scene = scene
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        #: called with keys the document handles (rotate, mirror, delete ...): name -> callable
        self.actions_by_key: dict[int, callable] = {}
        #: typing on the canvas searches the catalog: search(text)
        self.search = None
        self._tool_cursor = Qt.ArrowCursor
        self._content_timer = QTimer(self)
        self._content_timer.setSingleShot(True)
        self._content_timer.setInterval(0)
        self._content_timer.timeout.connect(self._content_settled)
        scene.changed.connect(self._content_changed)
        scene.tool_changed.connect(self._tool_changed)
        self.scale(DEFAULT_ZOOM, DEFAULT_ZOOM)

    def fit(self) -> None:
        """The whole circuit, as large as fits (a schematic is drawn small: up to three times)."""
        rect = self.minimap_bounds().adjusted(-40, -40, 40, 40)
        if rect.isValid() and not rect.isEmpty():
            self.fitInView(rect, Qt.KeepAspectRatio)
            if self.transform().m11() > MAX_FIT_ZOOM:
                self.resetTransform()
                self.scale(MAX_FIT_ZOOM, MAX_FIT_ZOOM)
                self.centerOn(rect.center())
        self.grow_scene()
        self.minimap.refit()

    def _content_changed(self, *_rects) -> None:
        # (the scene changes 30 times a second while it runs: the canvas size and the minimap follow once per
        # pass through the event loop)
        if not self._content_timer.isActive():
            self._content_timer.start()

    def _content_settled(self) -> None:
        self.grow_scene()
        self.minimap.setVisible(bool(self.schematic_scene.components))
        self.minimap.viewport().update()

    def _tool_changed(self, tool: str) -> None:
        self.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self._tool_cursor = Qt.CrossCursor if tool in ("wire", "label", "place") else Qt.ArrowCursor
        self.viewport().setCursor(self._tool_cursor)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if self._panning is not None:
            self.viewport().setCursor(Qt.ClosedHandCursor)  # (the viewport's cursor is the one seen)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self._panning is None:
            self.viewport().setCursor(self._tool_cursor)

    def minimap_bounds(self) -> QRectF:
        bounds = QRectF()
        for item in self.schematic_scene.components.values():
            bounds = bounds.united(item.body_rect().translated(item.pos()))
        for item in self.schematic_scene.wires.values():
            bounds = bounds.united(item.sceneBoundingRect())
        for item in self.schematic_scene.labels.values():
            bounds = bounds.united(item.sceneBoundingRect())
        for item in self.schematic_scene.groups.values():
            bounds = bounds.united(item.frame())
        return bounds

    def paint_minimap(self, painter: QPainter, mapped) -> None:
        wire = QColor(qcolor("circuit.wire"))
        wire.setAlpha(170)
        painter.setPen(QPen(wire, 1))
        for item in self.schematic_scene.wires.values():
            points = item.wire.points
            for a, b in zip(points, points[1:]):
                painter.drawLine(mapped(QPointF(*a)), mapped(QPointF(*b)))
        body = QColor(qcolor("circuit.symbol"))
        body.setAlpha(150)
        painter.setPen(Qt.NoPen)
        painter.setBrush(body)
        for item in self.schematic_scene.components.values():
            rect = item.body_rect().translated(item.pos())
            painter.drawRect(QRectF(mapped(rect.topLeft()), mapped(rect.bottomRight())))

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        if not self.schematic_scene.components and self.schematic_scene.tool == "select":
            self.draw_hint(painter, "Drag a component from the Circuits section, or type to search the catalog.\n"
                                    "Press on a pin to draw a wire from it.")

    def keyPressEvent(self, event) -> None:
        if self.navigation_key(event):
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self.schematic_scene.cancel()
            return
        handler = self.actions_by_key.get(key)
        plain = not (event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier | Qt.ShiftModifier))
        if handler is not None and plain and handler() is not False:
            return  # (False: nothing to do with it - the letter is typed into the search instead)
        text = event.text()
        if text and text.isprintable() and not text.isspace() and self.search is not None \
                and not (event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier)):
            self.search(text)
            return
        QGraphicsView.keyPressEvent(self, event)

    # ---------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME):
            text = bytes(event.mimeData().data(COMPONENT_MIME)).decode("utf-8")
            type_name, _sep, unit = text.partition("\n")
            x, y = snap(self.mapToScene(event.position().toPoint()))
            self.schematic_scene.controller.add_component(type_name, x, y, 0, False, int(unit or 1))
            event.acceptProposedAction()
            self.setFocus()
            return
        super().dropEvent(event)
