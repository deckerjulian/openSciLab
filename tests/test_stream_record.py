"""*Record*: a stream to the disk until it is stopped, everything kept (the capture controller, its
buttons in the data view and on the device card)."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from openscilab.driver.base import ACQUISITION_STREAM
from openscilab.driver.models import TriggerType
from openscilab.driver.simulated import open_simulated


def wait(condition, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return condition()


def test_a_recording_is_a_stream_to_the_disk_until_it_is_stopped(shell):
    instrument = open_simulated("pico")
    shell.hub.add(instrument)
    controller = shell.capture_controller(instrument)
    assert controller.can_record()
    session = controller.recording_session()
    channels = session.channel_numbers
    assert session.acquisition_mode == ACQUISITION_STREAM and session.to_disk and not session.continuous
    assert session.trigger_type == TriggerType.IMMEDIATE and session.pre_trigger_samples == 0
    assert session.frequency <= controller.driver.max_frequency_for(channels, ACQUISITION_STREAM)
    limit = controller.driver.get_limits(channels, ACQUISITION_STREAM, to_disk=True).max_post_samples
    assert session.post_trigger_samples == limit  # (as long as the disk has room)


def test_record_and_stop_keep_everything_that_arrived(shell):
    instrument = open_simulated("pico")
    shell.hub.add(instrument)
    controller = shell.capture_controller(instrument)
    assert shell.record_stream(instrument)
    assert controller.is_capturing and controller.recording_since is not None
    text, kind = controller.state_text()
    assert text.startswith("Streaming to disk 00:0") and "free" in text and kind == "busy"
    assert wait(lambda: controller.recorded_samples > 0)
    time.sleep(0.5)
    controller.abort()
    assert wait(lambda: not controller.is_capturing and controller.recording_since is None)
    view = controller.view
    assert wait(lambda: view.model.session is not None and view.model.session.total_samples > 0)
    assert view.model.session.acquisition_mode == ACQUISITION_STREAM


def test_the_record_buttons_follow_the_device(shell):
    instrument = open_simulated("pico")
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    assert not card.stream_record_button.isHidden()
    view = shell.show_data(instrument)
    assert not view.capture_controls.record_button.isHidden() and view.capture_controls.record_button.isEnabled()


def test_the_web_control_of_the_oscilloscope_opens_in_the_browser(shell, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    from openscilab.core.instrument import Instrument
    from openscilab.driver.simulated.scpi_shell import open_shell

    from openscilab.driver.rigoldho.driver import RigolDhoDriver

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    for mode, expected in (("direct", "http://127.0.0.1/"), ("usb", None)):
        driver = open_shell(mode)
        # (the simulator has no web server; the driver's own answer is what counts here)
        driver.web_control_url = lambda d=driver: RigolDhoDriver.web_control_url(d)
        instrument = Instrument.from_driver(driver, uri=driver.address)
        shell.hub.add(instrument)
        card = shell.open_device_card(instrument)
        assert not card.web_control_button.isHidden() and card.web_control_button.isEnabled()
        assert card.open_web_control() == expected
        if expected is None:  # over USB without a network address: said so, nothing opened
            assert "no network address that answers" in card.banner.label.text()
    assert opened == ["http://127.0.0.1/"]
    card = shell.open_device_card(open_simulated("pico"))
    assert card.web_control_button.isHidden()
