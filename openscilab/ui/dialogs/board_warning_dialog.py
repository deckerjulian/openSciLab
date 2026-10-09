# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The warning when a Pico sits on a board its firmware is not made for (``core/board_check.py``).

It offers to install the firmware for the board; using the board with its firmware anyway takes a
tick that confirms the risk first - the button stays off until then.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QWidget

from ...core.board_check import BoardFinding
from ..icons import set_icon
from ..theme import set_variant
from .common import Banner, dialog_layout, hint

#: the choices of the dialog (``None``: cancelled)
INSTALL = "install"
ACCEPT = "accept"

#: what the warning is about
OPENING = "open"
FLASHING = "flash"
FLASHED = "flashed"

RISK_TEXT = ("I understand that this firmware can drive the channel pins against the level shifters of the "
             "board and damage them or the Pico. I use it at my own risk.")


class BoardWarningDialog(QDialog):
    """The warning (see the module); :attr:`choice` is :data:`INSTALL`, :data:`ACCEPT` or ``None``."""

    def __init__(self, finding: BoardFinding, parent: Optional[QWidget] = None, context: str = OPENING,
                 image_name: str = "") -> None:
        super().__init__(parent)
        self.finding = finding
        self.choice: Optional[str] = None
        self.setWindowTitle(finding.title)
        self.setMinimumWidth(560)
        layout = dialog_layout(self)

        banner = Banner("error", self)
        lead = {
            OPENING: "This board is not opened: its firmware does not fit it.",
            FLASHING: f"The firmware you are installing ({image_name or 'this image'}) does not fit this board.",
            FLASHED: f"The firmware just installed ({image_name or 'this image'}) does not fit this board.",
        }.get(context, "")
        banner.set_message(f"<b>{finding.title}.</b> {lead}")
        layout.addWidget(banner)

        message = QLabel(finding.message, self)
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(message)

        fitting = finding.fitting_board
        if fitting is None:
            layout.addWidget(hint("There is no firmware image for this board and chip yet.", self))

        self.risk_box = QCheckBox(RISK_TEXT, self)
        self.risk_box.setObjectName("board-warning-risk")
        layout.addWidget(self.risk_box)

        buttons = QDialogButtonBox(self)
        self.install_button: Optional[QPushButton] = None
        if fitting is not None:
            self.install_button = buttons.addButton("Install the firmware for this board", QDialogButtonBox.AcceptRole)
            self.install_button.setObjectName("board-warning-install")
            set_variant(self.install_button, "primary")
            set_icon(self.install_button, "chip")
            self.install_button.setDefault(True)
            self.install_button.clicked.connect(lambda: self._choose(INSTALL))
        cancel = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        cancel.clicked.connect(self.reject)
        self.accept_button = buttons.addButton(
            "Install it anyway" if context == FLASHING else "Use it at my own risk", QDialogButtonBox.DestructiveRole)
        self.accept_button.setObjectName("board-warning-accept")
        set_variant(self.accept_button, "danger")
        self.accept_button.setEnabled(False)
        self.accept_button.setAutoDefault(False)
        self.accept_button.clicked.connect(lambda: self._choose(ACCEPT))
        self.risk_box.toggled.connect(self.accept_button.setEnabled)
        if self.install_button is None:
            cancel.setDefault(True)
        layout.addWidget(buttons)

    def _choose(self, choice: str) -> None:
        if choice == ACCEPT and not self.risk_box.isChecked():
            return  # (the button is off until the risk is confirmed)
        self.choice = choice
        self.accept()


def _exec(dialog: BoardWarningDialog) -> None:
    """Shows the dialog (replaced in tests)."""
    dialog.exec()


def ask(parent: Optional[QWidget], finding: BoardFinding, context: str = OPENING,
        image_name: str = "") -> Optional[str]:
    """Shows the warning; :data:`INSTALL`, :data:`ACCEPT` (the risk confirmed) or ``None``."""
    dialog = BoardWarningDialog(finding, parent, context, image_name)
    _exec(dialog)
    return dialog.choice
