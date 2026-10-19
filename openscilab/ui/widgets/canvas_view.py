# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A canvas to draw on: what the flow editor and the circuit editor share.

Navigation as in the data view: two fingers on a trackpad move the canvas, pinch zooms at the fingers
(smart zoom fits), the mouse wheel zooms at the pointer (``Ctrl``/Cmd + wheel moves sideways,
``Shift`` + wheel up and down), ``Ctrl``/Cmd + swipe zooms. The middle button or Space + drag moves
the canvas; ``+``/``-`` zoom. The canvas grows with its content; a dot grid and an overview
(:class:`Minimap`) in the corner. Subclasses say what the overview shows
(:meth:`CanvasView.minimap_bounds`, :meth:`CanvasView.paint_minimap`).
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QWidget

from ..theme import ACCENT_HOVER, BORDER, PANEL, qcolor, size
from . import navigation

#: the canvas reaches at least this far around the origin, and grows with the content
SCENE_MARGIN = 4000
MIN_ZOOM = 0.15
MAX_ZOOM = 3.0
#: one wheel notch zooms by this factor
WHEEL_ZOOM = 1.15


class Minimap(QWidget):
    """The whole content small, with the visible part outlined; click or drag to move there, the
    wheel and gestures navigate the canvas itself."""

    MARGIN = 8.0

    def __init__(self, view: "CanvasView") -> None:
        super().__init__(view)
        self.main = view
        self.setFixedSize(size("minimap-width"), size("minimap-height"))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Overview: click or drag to move there")
        self._bounds = QRectF()

    def viewport(self) -> QWidget:  # (the API of the view it replaced)
        return self

    def refit(self) -> None:
        self.update()

    def _transform(self) -> tuple[float, float, float]:
        """(scale, x offset, y offset) from the scene into this widget."""
        bounds = self._bounds
        room_w, room_h = self.width() - 2 * self.MARGIN, self.height() - 2 * self.MARGIN
        scale = min(room_w / max(bounds.width(), 1.0), room_h / max(bounds.height(), 1.0))
        x = self.MARGIN + (room_w - bounds.width() * scale) / 2 - bounds.left() * scale
        y = self.MARGIN + (room_h - bounds.height() * scale) / 2 - bounds.top() * scale
        return scale, x, y

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        background = QColor(PANEL)
        background.setAlpha(235)
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(frame, 6, 6)
        bounds = self.main.minimap_bounds()
        self._bounds = bounds.adjusted(-40, -40, 40, 40) if bounds.isValid() else bounds
        if not self._bounds.isValid():
            return
        scale, dx, dy = self._transform()

        def mapped(point: QPointF) -> QPointF:
            return QPointF(point.x() * scale + dx, point.y() * scale + dy)

        self.main.paint_minimap(painter, mapped)
        visible = self.main.mapToScene(self.main.viewport().rect()).boundingRect()
        shown = QRectF(mapped(visible.topLeft()), mapped(visible.bottomRight())).intersected(frame.adjusted(2, 2, -2, -2))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(ACCENT_HOVER), 1.5))
        painter.drawRoundedRect(shown, 3, 3)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._move_to(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.buttons() & Qt.LeftButton:
            self._move_to(event.position())

    def _move_to(self, position: QPointF) -> None:
        if not self._bounds.isValid():
            return
        scale, dx, dy = self._transform()
        self.main.centerOn(QPointF((position.x() - dx) / scale, (position.y() - dy) / scale))
        self.main.minimap_moved()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.main.navigate(event, self.main.viewport().rect().center())

    def event(self, event) -> bool:  # noqa: D102
        if event.type() == QEvent.NativeGesture and self.main.gesture(event, self.main.viewport().rect().center()):
            return True
        return super().event(event)


class CanvasView(QGraphicsView):
    """A zoomable, endless canvas with a dot grid and an overview (see the module)."""

    #: the distance of the dots of the grid (scene units); 0: no grid
    grid_step = 32
    #: the grid is hidden below this zoom
    grid_min_zoom = 0.4

    def __init__(self, scene: QGraphicsScene, parent: Optional[QWidget] = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFocusPolicy(Qt.StrongFocus)
        self.viewport().setAttribute(Qt.WA_AcceptTouchEvents, False)
        self._panning: Optional[QPointF] = None
        self._space = False
        self.minimap = Minimap(self)

    # --------------------------------------------------------- the overview
    def minimap_bounds(self) -> QRectF:
        """What the overview shows (scene coordinates); an invalid rectangle: nothing."""
        return self.scene().itemsBoundingRect()

    def paint_minimap(self, painter: QPainter, mapped: Callable[[QPointF], QPointF]) -> None:
        """Draw the content into the overview; ``mapped`` maps a scene point into it."""

    # ------------------------------------------------------------ geometry
    def grow_scene(self) -> None:
        """The canvas reaches a screen beyond the content in every direction, so everything can be
        scrolled to the middle (also far away items, also zoomed out)."""
        scene = self.scene()
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        base = QRectF(-SCENE_MARGIN, -SCENE_MARGIN, 2 * SCENE_MARGIN, 2 * SCENE_MARGIN)
        wanted = scene.itemsBoundingRect().adjusted(-visible.width(), -visible.height(), visible.width(),
                                                    visible.height()).united(base).united(visible)
        if not scene.sceneRect().contains(wanted):
            scene.setSceneRect(scene.sceneRect().united(wanted))

    def zoom_level(self) -> float:
        return self.transform().m11()

    def zoom(self, factor: float) -> None:
        """Zoom at the pointer (or the middle when the pointer is elsewhere)."""
        cursor = self.viewport().mapFromGlobal(self.cursor().pos())
        anchor = cursor if self.viewport().rect().contains(cursor) else self.viewport().rect().center()
        self.zoom_at(factor, QPointF(anchor))

    def zoom_at(self, factor: float, position: QPointF) -> None:
        """Zoom by ``factor`` keeping the point under ``position`` (viewport pixels) in place;
        the zoom stops at its limits instead of skipping the step."""
        current = self.zoom_level()
        target = min(max(current * factor, MIN_ZOOM), MAX_ZOOM)
        if abs(target - current) < 1e-6:
            return
        point = QPoint(int(round(position.x())), int(round(position.y())))
        before = self.mapToScene(point)
        anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.scale(target / current, target / current)
        self.setTransformationAnchor(anchor)
        self.grow_scene()
        moved = self.mapFromScene(before) - point
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + moved.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + moved.y())
        self.minimap.viewport().update()

    def actual_size(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.grow_scene()
        self.centerOn(center)
        self.minimap.viewport().update()

    def pan_by(self, dx: float, dy: float) -> None:
        """Move the content by ``dx``, ``dy`` pixels (as the fingers move)."""
        self.horizontalScrollBar().setValue(int(round(self.horizontalScrollBar().value() - dx)))
        self.verticalScrollBar().setValue(int(round(self.verticalScrollBar().value() - dy)))
        self.minimap.viewport().update()

    def minimap_moved(self) -> None:
        self.minimap.viewport().update()

    def fit(self) -> None:
        rect = self.scene().itemsBoundingRect().adjusted(-60, -60, 60, 60)
        if rect.isValid() and not rect.isEmpty():
            self.fitInView(rect, Qt.KeepAspectRatio)
            if self.transform().m11() > 1.2:
                self.resetTransform()
                self.centerOn(rect.center())
        self.grow_scene()
        self.minimap.refit()

    # -------------------------------------------------------------- input
    def navigate(self, event, anchor: Optional[QPointF] = None) -> None:
        """A wheel event: trackpad swipes move, wheel notches zoom (see the module)."""
        anchor = anchor if anchor is not None else event.position()
        modifiers = event.modifiers()
        if navigation.is_trackpad(event):
            dx, dy = navigation.trackpad_delta(event)
            if modifiers & Qt.ControlModifier:
                if dy:
                    self.zoom_at(2 ** (dy / navigation.PIXELS_PER_DOUBLING), QPointF(anchor))
            else:
                self.pan_by(dx, dy)
            event.accept()
            return
        angle = event.angleDelta()
        step = self.viewport().height() / 8
        control = bool(modifiers & Qt.ControlModifier)
        wheel_zooms = navigation.wheel_mode() == "zoom"
        direction = -1 if navigation.zoom_inverted() else 1
        if modifiers & Qt.ShiftModifier:
            # macOS turns Shift + wheel into a horizontal wheel
            self.pan_by(0, (angle.y() or angle.x()) / navigation.WHEEL_NOTCH * step)
        elif angle.x() and abs(angle.x()) >= abs(angle.y()):
            self.pan_by(angle.x() / navigation.WHEEL_NOTCH * step, 0)
        elif angle.y() and control != wheel_zooms:
            self.zoom_at(WHEEL_ZOOM ** (direction * angle.y() / navigation.WHEEL_NOTCH), QPointF(anchor))
        elif angle.y():
            # moves: sideways with Ctrl/Cmd (zoom mode), up and down otherwise (scroll mode)
            if wheel_zooms:
                self.pan_by(angle.y() / navigation.WHEEL_NOTCH * step, 0)
            else:
                self.pan_by(0, angle.y() / navigation.WHEEL_NOTCH * step)
        event.accept()

    def gesture(self, event, anchor: Optional[QPointF] = None) -> bool:
        """Pinch zooms at the fingers, smart zoom fits; returns whether the event was used."""
        if navigation.is_smart_zoom(event):
            if self.zoom_level() < 0.95 or self.zoom_level() > 1.05:
                self.actual_size()
            else:
                self.fit()
            return True
        factor = navigation.pinch_factor(event)
        if factor is not None:
            self.zoom_at(factor, QPointF(anchor) if anchor is not None else event.position())
            return True
        return event.gestureType() in (Qt.BeginNativeGesture, Qt.EndNativeGesture)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.navigate(event)

    def viewportEvent(self, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.NativeGesture and self.gesture(event):
            return True
        return super().viewportEvent(event)

    def event(self, event) -> bool:  # noqa: A003 - Qt naming
        # a gesture that reaches the view itself (not its viewport): positions are the view's
        if event.type() == QEvent.NativeGesture and self.gesture(
                event, QPointF(self.viewport().mapFrom(self, event.position().toPoint()))):
            return True
        return super().event(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.minimap.move(self.viewport().width() - self.minimap.width() - 10,
                          self.viewport().height() - self.minimap.height() - 10)
        self.minimap.refit()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802 - Qt naming
        super().drawBackground(painter, rect)
        step = self.grid_step
        if not step or self.transform().m11() < self.grid_min_zoom:
            return
        painter.setPen(QPen(qcolor("canvas.dots"), 0))
        left = int(rect.left()) - int(rect.left()) % step
        top = int(rect.top()) - int(rect.top()) % step
        points = []
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                points.append(QPointF(x, y))
                y += step
            x += step
        painter.drawPoints(points)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MiddleButton or (self._space and event.button() == Qt.LeftButton):
            self._panning = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._panning is not None:
            delta = event.position() - self._panning
            self._panning = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            self.minimap.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._panning is not None:
            self._panning = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def navigation_key(self, event) -> bool:
        """Space (to drag) and ``+``/``-``; whether the key was used."""
        key = event.key()
        if key == Qt.Key_Space and not event.isAutoRepeat():
            self._space = True
            return True
        plain = not (event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier))
        if plain and key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
            self.zoom(WHEEL_ZOOM if key != Qt.Key_Minus else 1 / WHEEL_ZOOM)
            return True
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.navigation_key(event):
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space = False
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._space = False  # Space released elsewhere
        super().focusOutEvent(event)

    def draw_hint(self, painter: QPainter, text: str) -> None:
        """``text`` in the middle of an empty canvas (from ``drawForeground``)."""
        painter.save()
        painter.resetTransform()
        painter.setPen(qcolor("canvas.hint"))
        painter.drawText(self.viewport().rect(), Qt.AlignCenter, text)
        painter.restore()
