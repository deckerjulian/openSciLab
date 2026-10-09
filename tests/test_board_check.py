"""A Pico on a board its firmware does not fit: the finding, the refusal when it is opened, the
confirmed risk and the warning (core/board_check.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from openscilab.core import board_check
from openscilab.driver.base import BoardMismatchError

ON_BOARD = {"BOARD": "PICO_2", "CHIP": "RP2350", "UNIQUE_ID": "ED2A70A708D7247B",
            "TRIGGER_CHAIN": "PULLED_DOWN", "CHANNELS_DRIVEN": "24/24"}


def test_a_bare_image_on_the_board_with_level_shifters_is_a_mismatch():
    finding = board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", ON_BOARD)
    assert finding.shifter_board and finding.mismatch
    assert finding.fitting_board.name == board_check.SHIFTER_BOARD
    assert "level shifters" in finding.message and "24/24" in finding.message
    # its own image fits; a bare board fits every image
    assert not board_check.examine("OPENSCILAB_PICO_LOGICANALYZER_V2_V8_0", ON_BOARD).mismatch
    bare = board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", dict(ON_BOARD, TRIGGER_CHAIN="FREE"))
    assert not bare.mismatch and not bare.shifter_board
    assert board_check.examine("OPENSCILAB_PICO_LOGICANALYZER_V2_V8_0",
                               dict(ON_BOARD, TRIGGER_CHAIN="FREE")).shifter_image_elsewhere
    # firmware that does not measure its board: nothing known
    assert board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", {"BOARD": "PICO_2"}) is None
    # an RP2040 on such a board: there is no image for it
    rp2040 = board_check.examine("OPENSCILAB_PICO_PICO_V8_0", dict(ON_BOARD, BOARD="PICO", CHIP="RP2040"))
    assert rp2040.mismatch and rp2040.fitting_board is None and "no image" in rp2040.message


def test_the_risk_is_confirmed_per_board_and_image():
    finding = board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", ON_BOARD)
    assert not board_check.accepted(finding)
    board_check.accept(finding)
    assert board_check.accepted(finding)
    other_board = board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", dict(ON_BOARD, UNIQUE_ID="0000000000000001"))
    assert not board_check.accepted(other_board)
    other_image = board_check.examine("OPENSCILAB_PICO_PICO_V8_0", ON_BOARD)
    assert not board_check.accepted(other_image)
    board_check.revoke(finding)
    assert not board_check.accepted(finding)


def test_opening_the_board_is_refused_until_the_risk_is_confirmed(monkeypatch):
    from openscilab.driver.pico.analyzer import PicoDriver

    driver = PicoDriver.__new__(PicoDriver)
    driver._version = "OPENSCILAB_PICO_PICO_2_V8_0"
    driver.connection_string = "/dev/fake"
    monkeypatch.setattr(PicoDriver, "capabilities", lambda self: frozenset({"DEVICEINFO"}))
    monkeypatch.setattr(PicoDriver, "device_details", lambda self: dict(ON_BOARD))
    with pytest.raises(BoardMismatchError) as raised:
        driver.check_board()
    assert raised.value.location == "/dev/fake" and raised.value.finding.mismatch
    board_check.accept(raised.value.finding)
    driver.check_board()  # (confirmed: opened at the user's own risk)
    assert driver.board_finding.mismatch


def test_the_warning_takes_the_tick_before_the_risk(shell):
    from openscilab.ui.dialogs import board_warning_dialog as warning

    finding = board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", ON_BOARD)
    dialog = warning.BoardWarningDialog(finding, None, warning.OPENING)
    assert not dialog.accept_button.isEnabled()
    dialog.accept_button.click()
    assert dialog.choice is None
    dialog.risk_box.setChecked(True)
    assert dialog.accept_button.isEnabled()
    dialog.accept_button.click()
    assert dialog.choice == warning.ACCEPT
    dialog = warning.BoardWarningDialog(finding, None, warning.OPENING)
    dialog.install_button.click()
    assert dialog.choice == warning.INSTALL


def test_connecting_shows_the_warning_and_opens_after_the_confirmed_risk(shell, monkeypatch):
    from openscilab.ui.dialogs import board_warning_dialog as warning

    finding = board_check.examine("OPENSCILAB_PICO_PICO_2_V8_0", ON_BOARD)
    error = BoardMismatchError("Wrong firmware", "OPENSCILAB_PICO_PICO_2_V8_0", "/dev/fake", finding)
    opened = []
    asked = []
    monkeypatch.setattr(warning, "ask", lambda parent, found, context, image_name="": asked.append(context)
                        or warning.ACCEPT)
    assert shell.resolve_board_mismatch(error, lambda: opened.append(True) or "instrument") == "instrument"
    assert asked == [warning.OPENING] and opened and board_check.accepted(finding)
    monkeypatch.setattr(warning, "ask", lambda *args, **kwargs: None)
    assert shell.resolve_board_mismatch(error, lambda: pytest.fail("not opened when cancelled")) is None
