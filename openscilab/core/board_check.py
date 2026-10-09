# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Does a Pico run the firmware of the board it sits on?

The LogicAnalyzer board of gusmanb puts level shifters in front of the 24 channels: they drive the
GPIOs, so the image of a bare Pico - whose channel pins can be outputs - would drive against them.
Its image (``BOARD_LOGICANALYZER_V2``) makes them inputs only. The firmware measures the board when
it is asked for its details (``INFO:TRIGGER_CHAIN``: the board ties GP0 and GP1 to GND through
8.2 kOhm; ``INFO:CHANNELS_DRIVEN``), see ``firmware/pico/board_check.h``.

:func:`examine` turns the details into a :class:`BoardFinding`. A board with level shifters that
runs another image is refused when it is opened (``BoardMismatchError`` of the Pico driver) until
someone confirms it for this board (:func:`accept`, kept by the unique ID of the chip); the
application then shows that it runs at the user's own risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import firmware, settings

#: ``BOARD_NAME`` of the image for the LogicAnalyzer board V2 of gusmanb (a Pico 2)
SHIFTER_BOARD = "LOGICANALYZER_V2"
SHIFTER_BOARD_TYPE = "BOARD_LOGICANALYZER_V2"
SHIFTER_BOARD_CHIP = "RP2350"
SETTINGS_FILE = "board_check.json"

CHAIN_PULLED_DOWN = "PULLED_DOWN"
CHAIN_FREE = "FREE"


@dataclass(frozen=True)
class BoardFinding:
    """What the firmware of a Pico measured about the board around it."""

    #: ``BOARD_NAME`` of the running image
    firmware_board: str
    #: the trigger chain is pulled down: a board with level shifters (gusmanb)
    shifter_board: bool
    #: ``INFO:TRIGGER_CHAIN`` and ``INFO:CHANNELS_DRIVEN`` as reported
    chain: str
    channels_driven: str
    unique_id: str = ""
    chip: str = ""

    @property
    def mismatch(self) -> bool:
        """A board with level shifters runs an image whose channel pins can be outputs."""
        return self.shifter_board and self.firmware_board != SHIFTER_BOARD

    @property
    def shifter_image_elsewhere(self) -> bool:
        """The image for the board with level shifters on a board without them (safe: its outputs are off)."""
        return self.firmware_board == SHIFTER_BOARD and self.chain == CHAIN_FREE

    @property
    def fitting_board(self) -> Optional[firmware.BoardModel]:
        """The board whose image fits (``None``: there is none for this chip yet)."""
        if self.shifter_board:
            return firmware.board_by_name(SHIFTER_BOARD) if self.chip in ("", SHIFTER_BOARD_CHIP) else None
        return None

    @property
    def title(self) -> str:
        return "Wrong firmware for this board"

    @property
    def message(self) -> str:
        running = firmware.board_by_name(self.firmware_board)
        image = running.label if running else self.firmware_board or "another board"
        fitting = self.fitting_board
        advice = (f"Install the firmware for it ({fitting.label}): it makes the channels inputs only."
                  if fitting is not None else
                  "openSciLab has no image for such a board with this chip yet: do not use the channels as outputs.")
        return (f"This Pico sits on a board with level shifters in front of its channels - the LogicAnalyzer "
                f"board of gusmanb (its trigger chain pulls GP0/GP1 to GND; {self.channels_driven} channels are "
                f"driven from outside). It runs the firmware for the {image}, whose channel pins can be outputs: "
                f"an output, PWM, pulse, pattern or transmitter on them drives against the level shifters and can "
                f"damage them or the Pico. {advice}")


def examine(version: Optional[str], details: Optional[dict]) -> Optional[BoardFinding]:
    """The finding of a Pico from its identification and ``INFO`` details; ``None`` when its firmware
    does not measure the board (older firmware) or measured nothing."""
    details = details or {}
    chain = str(details.get("TRIGGER_CHAIN") or "").strip().upper()
    if chain not in (CHAIN_PULLED_DOWN, CHAIN_FREE):
        return None
    identity = firmware.parse_device_version(version)
    board = identity.board_name if identity is not None else str(details.get("BOARD") or "")
    model = firmware.board_by_name(board)
    return BoardFinding(
        firmware_board=board,
        shifter_board=chain == CHAIN_PULLED_DOWN,
        chain=chain,
        channels_driven=str(details.get("CHANNELS_DRIVEN") or "?"),
        unique_id=str(details.get("UNIQUE_ID") or ""),
        chip=str(details.get("CHIP") or (model.chip if model else "")),
    )


# ------------------------------------------------------- confirmed exceptions
def _key(finding: BoardFinding) -> str:
    return f"{finding.unique_id or '?'}|{finding.firmware_board}"


def _load() -> dict:
    data = settings.get_settings(SETTINGS_FILE)
    return data if isinstance(data, dict) else {}


def accepted(finding: BoardFinding) -> bool:
    """Someone confirmed to use this board with this image at their own risk."""
    return bool(finding.unique_id) and _key(finding) in (_load().get("accepted") or {})


def accept(finding: BoardFinding) -> None:
    """Use this board with its image at one's own risk (kept by the unique ID of its chip)."""
    import time

    data = _load()
    confirmed = dict(data.get("accepted") or {})
    confirmed[_key(finding)] = {"confirmed": time.strftime("%Y-%m-%d %H:%M:%S"), "chain": finding.chain,
                                "channels_driven": finding.channels_driven}
    data["accepted"] = confirmed
    settings.persist_settings(SETTINGS_FILE, data)


def revoke(finding: BoardFinding) -> None:
    """Forget the confirmation: the board is refused again when it is opened."""
    data = _load()
    confirmed = dict(data.get("accepted") or {})
    if confirmed.pop(_key(finding), None) is not None:
        data["accepted"] = confirmed
        settings.persist_settings(SETTINGS_FILE, data)
