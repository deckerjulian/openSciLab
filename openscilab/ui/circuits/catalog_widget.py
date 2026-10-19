# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The catalog of components to search: openSciLab's own, KiCad's libraries, SPICE models.

A search line, filters (only what can be simulated, the source), the results with what they are, and
a preview of the chosen component. Drag one onto a circuit, or double-click it (Enter) to place it.
KiCad's libraries are read in the background the first time (``circuits/catalog``); without a KiCad
installation they are installed into the vendor folder when the settings allow it.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QMimeData, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...circuits.catalog import Catalog, Entry, default_catalog
from ...circuits.model import Component
from ..icons import icon
from ..theme import set_role, size, smaller_font, space
from .items import ComponentItem
from .view import COMPONENT_MIME

SOURCES = (("", "All sources"), ("builtin", "openSciLab"), ("kicad", "KiCad"), ("spice", "SPICE models"),
           ("user", "Yours"))
QUALITY = {"exact": "", "generic": "standard model", "": "not simulated"}


class _Bridge(QObject):
    progress = Signal(str, float)
    done = Signal(int)


class _Indexer:
    """Reads KiCad's libraries once per application (all browsers share it)."""

    bridge: _Bridge | None = None
    thread: threading.Thread | None = None
    state = "idle"  # idle, running, done
    text = ""
    share = 0.0
    #: why the last indexing failed ("": it did not)
    failure = ""

    @classmethod
    def start(cls, catalog: Catalog) -> _Bridge:
        if cls.bridge is None:
            cls.bridge = _Bridge()
        if cls.state == "idle":
            cls.state = "running"
            bridge = cls.bridge

            def report(text: str, share: float) -> None:
                cls.text, cls.share = text, share
                try:
                    bridge.progress.emit(text, share)
                except RuntimeError:
                    pass

            def run() -> None:
                from ...core import preferences, vendor

                count = 0
                failed = ""
                try:
                    if preferences.get("circuits.use_kicad") and not catalog.kicad_folders():
                        try:
                            vendor.ensure("kicad", report)
                            catalog._kicad_libraries = {}
                        except Exception as error:  # noqa: BLE001 - said in the status, tried again next time
                            failed = f"KiCad's symbols could not be installed: {error}"
                    count = catalog.build_index(report, separate=True)
                except Exception as error:  # noqa: BLE001
                    failed = failed or f"the catalog could not be read: {error}"
                finally:
                    cls.state = "idle" if failed else "done"
                    cls.failure = failed
                    try:
                        bridge.done.emit(count)
                    except RuntimeError:
                        pass

            cls.thread = threading.Thread(target=run, name="openscilab-catalog", daemon=True)
            cls.thread.start()
        return cls.bridge


class SymbolPreview(QGraphicsView):
    """A component drawn small."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setInteractive(False)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setMinimumHeight(size("catalog-preview"))
        self._item: ComponentItem | None = None

    def show_spec(self, spec, unit: int = 1) -> None:
        self.scene().clear()
        self._item = None
        if spec is None:
            return
        item = ComponentItem(Component(spec.prefix + "?", spec.type, unit=unit), spec, None)
        item.setFlags(item.flags() & ~(item.GraphicsItemFlag.ItemIsSelectable | item.GraphicsItemFlag.ItemIsMovable))
        self.scene().addItem(item)
        self._item = item
        self._fit()

    def _fit(self) -> None:
        if self._item is None:
            return
        rect = self._item.body_rect().adjusted(-24, -24, 24, 24)
        self.scene().setSceneRect(rect)
        self.fitInView(rect, Qt.KeepAspectRatio)
        if self.transform().m11() > 2.5:
            self.resetTransform()
            self.scale(2.5, 2.5)
            self.centerOn(rect.center())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()


class ResultList(QListWidget):
    """The results; dragging one carries its type."""

    def __init__(self, browser: ComponentBrowser) -> None:
        super().__init__(browser)
        self.browser = browser
        self.setDragEnabled(True)
        self.setUniformItemSizes(True)

    def mimeData(self, items):
        data = QMimeData()
        if items:
            type_name = items[0].data(Qt.UserRole)
            data.setData(COMPONENT_MIME, f"{type_name}\n{self.browser.unit()}".encode())
        return data

    def startDrag(self, actions) -> None:
        item = self.currentItem()
        if item is None:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([item]))
        drag.exec(Qt.CopyAction)


class ComponentBrowser(QWidget):
    """See the module."""

    #: a component was chosen: (type, unit)
    chosen = Signal(str, int)

    def __init__(self, catalog: Catalog | None = None, parent: QWidget | None = None,
                 preview: bool = True, limit: int = 250) -> None:
        super().__init__(parent)
        self.catalog = catalog or default_catalog()
        self.limit = limit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(space(1), space(1), space(1), space(1))
        layout.setSpacing(space(1))
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search components: resistor, LM358, 2N3904, 74HC00 ...")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        filters.setSpacing(space(1))
        self.simulated = QCheckBox("Simulated", self)
        self.simulated.setToolTip("Only components that can be simulated")
        self.simulated.setChecked(True)
        filters.addWidget(self.simulated)
        self.source = QComboBox(self)
        for key, title in SOURCES:
            self.source.addItem(title, key)
        self.source.setToolTip("Where the components come from")
        filters.addWidget(self.source, 1)
        layout.addLayout(filters)
        self.results = ResultList(self)
        layout.addWidget(self.results, 1)
        self.status = QLabel("", self)
        set_role(self.status, "hint")
        self.status.setFont(smaller_font(self.status.font()))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(space(1))
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        unit_row = QHBoxLayout()
        self.unit_label = QLabel("Unit", self)
        self.unit_box = QSpinBox(self)
        self.unit_box.setRange(1, 1)
        self.unit_box.setToolTip("Which unit of a component with several (the gates of a 7400, the halves of a dual op-amp)")
        unit_row.addWidget(self.unit_label)
        unit_row.addWidget(self.unit_box, 1)
        layout.addLayout(unit_row)
        self.preview = SymbolPreview(self) if preview else None
        if self.preview is not None:
            layout.addWidget(self.preview)
        self.details = QLabel("", self)
        self.details.setWordWrap(True)
        set_role(self.details, "hint")
        self.details.setFont(smaller_font(self.details.font()))
        layout.addWidget(self.details)
        self._set_unit_visible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._timer.start())
        self.simulated.toggled.connect(lambda _on: self.refresh())
        self.source.currentIndexChanged.connect(lambda _index: self.refresh())
        self.results.currentItemChanged.connect(lambda current, _previous: self._show(current))
        self.results.itemActivated.connect(self._activate)
        self.unit_box.valueChanged.connect(lambda _value: self._show(self.results.currentItem()))
        self.search.returnPressed.connect(lambda: self._activate(self.results.currentItem()))
        self.refresh()
        self._start_index()

    def sizeHint(self) -> QSize:
        return QSize(size("catalog-width"), size("catalog-height"))

    # -------------------------------------------------------------- index
    def _start_index(self) -> None:
        if self.catalog is not default_catalog() or self.catalog.indexed:
            return
        bridge = _Indexer.start(self.catalog)
        bridge.progress.connect(self._progress)
        bridge.done.connect(self._indexed)
        if _Indexer.state == "running":
            self._progress(_Indexer.text or "Reading KiCad's symbol libraries", _Indexer.share)

    def _progress(self, text: str, share: float) -> None:
        self.progress.setVisible(share < 1.0)
        self.progress.setValue(int(share * 100))
        self.status.setText(text + " ..." if share < 1.0 else "")

    def _indexed(self, _count: int) -> None:
        self.progress.setVisible(False)
        self.refresh()

    # ------------------------------------------------------------- search
    def refresh(self) -> None:
        entries = self.catalog.search(self.search.text(), self.limit + 1, simulated=self.simulated.isChecked(),
                                      source=self.source.currentData() or "")
        more = len(entries) > self.limit
        entries = entries[: self.limit]
        current = self.results.currentItem().data(Qt.UserRole) if self.results.currentItem() else None
        self.results.clear()
        for entry in entries:
            self._add(entry)
        if not entries:
            item = QListWidgetItem("No component matches", self.results)
            item.setFlags(Qt.NoItemFlags)
        if current:
            for row in range(self.results.count()):
                if self.results.item(row).data(Qt.UserRole) == current:
                    self.results.setCurrentRow(row)
                    break
        if self.results.currentItem() is None and entries:
            self.results.setCurrentRow(0)
        total = len(self.catalog.entries())
        indexing = _Indexer.state == "running"
        if _Indexer.failure and not indexing:
            self.status.setText(_Indexer.failure)
        elif not indexing:
            self.status.setText(f"{'More than ' if more else ''}{len(entries)} of {total} components"
                                + (" · type to narrow" if more else ""))

    def _add(self, entry: Entry) -> None:
        quality = QUALITY.get(entry.simulation, "")
        text = entry.title
        detail = entry.category + (f" · {quality}" if quality else "")
        item = QListWidgetItem(icon("chip" if entry.source == "kicad" else "nodes" if entry.source == "spice" else "layers"),
                               f"{text}\n{detail}", self.results)
        item.setData(Qt.UserRole, entry.type)
        tip = entry.description or entry.title
        if quality:
            tip += f"\n({quality})"
        item.setToolTip(tip)
        if not entry.simulation:
            item.setForeground(self.palette().placeholderText())

    def _show(self, item: QListWidgetItem | None) -> None:
        type_name = item.data(Qt.UserRole) if item is not None else None
        spec = self.catalog.spec(type_name) if type_name else None
        units = spec.symbol.units if spec is not None else 1
        self._set_unit_visible(units > 1)
        if units != self.unit_box.maximum():
            blocked = self.unit_box.blockSignals(True)
            self.unit_box.setRange(1, max(units, 1))
            self.unit_box.blockSignals(blocked)
        if self.preview is not None:
            self.preview.show_spec(spec, self.unit())
        if spec is None:
            self.details.setText("")
            return
        lines = [spec.description or spec.title]
        if spec.model is None and not spec.simulated:
            lines.append("Drawn only: it has no simulation model.")
        elif spec.model is not None and spec.model.quality == "generic":
            lines.append("Simulated with a standard model of its kind.")
        if spec.datasheet and spec.datasheet not in ("~", ""):
            lines.append(f"<a href='{spec.datasheet}'>Data sheet</a>")
        self.details.setText("<br>".join(lines))
        self.details.setOpenExternalLinks(True)

    def _set_unit_visible(self, visible: bool) -> None:
        self.unit_label.setVisible(visible)
        self.unit_box.setVisible(visible)

    def unit(self) -> int:
        return self.unit_box.value() if self.unit_box.isVisible() else 1

    def _activate(self, item: QListWidgetItem | None) -> None:
        if item is not None and item.data(Qt.UserRole):
            self.chosen.emit(item.data(Qt.UserRole), self.unit())

    def focus_search(self, text: str = "") -> None:
        if text:
            self.search.setText(text)
        self.search.setFocus()
        self.search.end(False)
