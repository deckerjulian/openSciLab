"""The *Front panel* tab of an oscilloscope: its screen live (the Web Control servers of the DHO, or
:DISPlay:DATA? over USB), touches on it, and its settings beside it."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from openscilab.core import websocket
from openscilab.core.instrument import Instrument, SettingsFacet
from openscilab.driver.rigoldho import screen
from openscilab.driver.simulated.scpi_shell import open_shell
from openscilab.ui.devices import front_panel


def wait(condition, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return condition()


class FakeWebControl:
    """The screen (images on ``record_screen_start``) and touch servers of the DHO's Web Control."""

    def __init__(self) -> None:
        self.screen = self._listen()
        self.control = self._listen()
        self.touches: list[dict] = []
        self.texts: list[str] = []
        self.image = _png()
        for server, serve in ((self.screen, self._serve_screen), (self.control, self._serve_control)):
            threading.Thread(target=self._accept, args=(server, serve), daemon=True).start()

    @staticmethod
    def _listen() -> socket.socket:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        return server

    def _accept(self, server, serve) -> None:
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=self._handshake, args=(client, serve), daemon=True).start()

    @staticmethod
    def _handshake(client, serve) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            request += client.recv(1024)
        key = [line.split(b":", 1)[1].strip() for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key")][0]
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        client.sendall(b"HTTP/1.1 101 Web Socket Protocol Handshake\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        try:
            serve(client)
        except OSError:
            pass

    @staticmethod
    def _read(client) -> str:
        head = client.recv(2)
        if len(head) < 2:
            raise OSError("closed")
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", client.recv(2))[0]
        mask = client.recv(4)
        data = b""
        while len(data) < length:
            data += client.recv(length - len(data))
        return bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)).decode()

    def _serve_screen(self, client) -> None:
        streaming = False
        client.settimeout(0.05)
        while True:
            try:
                text = self._read(client)
                self.texts.append(text)
                streaming = text == "record_screen_start"
            except socket.timeout:
                pass
            if streaming:
                client.sendall(bytes([0x82, 0]))  # (it sends empty messages too)
                client.sendall(bytes([0x82, 127]) + struct.pack(">Q", len(self.image)) + self.image)
                time.sleep(0.05)

    def _serve_control(self, client) -> None:
        while True:
            text = self._read(client)
            if not text:
                return  # (the close frame)
            self.touches.append(json.loads(text))

    def close(self) -> None:
        self.screen.close()
        self.control.close()


def _png() -> bytes:
    from openscilab.driver.simulated.scpi_shell import ScpiShell

    return ScpiShell._screen(type("S", (), {"status": "RUN"})())


def test_the_screen_comes_live_and_touches_go_to_the_instrument(monkeypatch):
    fake = FakeWebControl()
    monkeypatch.setattr(screen, "SCREEN_PORT", fake.screen.getsockname()[1])
    monkeypatch.setattr(screen, "CONTROL_PORT", fake.control.getsockname()[1])
    link = screen.DhoScreen("127.0.0.1")
    frames = []
    link.start(frames.append)
    try:
        assert wait(lambda: len(frames) >= 3)
        assert all(frame[:4] == b"\x89PNG" for frame in frames)  # (the empty messages are dropped)
        assert link.touch("down", 100, 50) and link.touch("move", 2000, -5) and link.touch("up", 120, 60)
        assert wait(lambda: len(fake.touches) == 3)
        assert [touch["type"] for touch in fake.touches] == ["onmousedown", "onmousemove", "onmouseup"]
        assert (fake.touches[1]["clientX"], fake.touches[1]["clientY"]) == (1023, 0)  # (kept on the screen)
    finally:
        link.stop()
        fake.close()
    assert fake.texts[0] == "record_screen_start" and "record_screen_stop" in fake.texts


def test_a_screen_server_that_cannot_be_reached_says_so():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    try:
        websocket.WebSocket("127.0.0.1", port, timeout=1.0)
    except websocket.WebSocketError as error:
        assert "cannot be reached" in str(error)
    else:
        raise AssertionError("connected to nothing")


def test_the_front_panel_of_the_simulated_oscilloscope(shell):
    driver = open_shell("direct")
    instrument = Instrument.from_driver(driver, uri=driver.address)
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    try:
        page = card.front_panel
        assert page is not None and card.tabs.tabText(card.tabs.indexOf(page)) == "Front panel"
        card.tabs.setCurrentWidget(page)
        page.activate()
        # without a web server (the simulator, an instrument only on USB) the screen comes over SCPI
        assert wait(lambda: page.screen_view.frames > 0)
        assert not page.link.touch_supported and "without touches" in page.screen_hint.text()
        panel = page.settings_panel
        assert wait(lambda: panel.widgets["ch1_scale"].edit.text() == "1 V")
        assert panel.widgets["trigger_source"].currentText() == "CH1"
        # a change goes to the instrument and the value it took comes back
        panel.widgets["ch1_scale"].edit.setText("200 mV")
        panel.widgets["ch1_scale"].edit.editingFinished.emit()
        assert wait(lambda: driver.shell.panel[":CHANNEL1:SCALE"] == "0.2")
        panel._step("ch1_scale", 1)
        assert wait(lambda: driver.shell.panel[":CHANNEL1:SCALE"] == "0.5")
        combo = panel.widgets["trigger_slope"]
        combo.setCurrentIndex(combo.findData("NEG"))
        combo.activated.emit(combo.currentIndex())
        assert wait(lambda: driver.shell.panel[":TRIGGER:EDGE:SLOPE"] == "NEG")
        panel._run("stop")
        assert wait(lambda: driver.shell.status == "STOP")
        page.deactivate()
        assert wait(lambda: not page.link.running)
    finally:
        card.shutdown()


def test_the_screen_in_a_window_of_its_own_and_a_capture_as_set_on_the_instrument(shell):
    driver = open_shell("direct")
    instrument = Instrument.from_driver(driver, uri=driver.address)
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    try:
        page = card.front_panel
        card.tabs.setCurrentWidget(page)
        page.activate()
        assert wait(lambda: page.screen_view.frames > 0)
        # *Open in a window*: the screen moves there and keeps running while another tab is shown
        window = page.open_screen_window()
        assert window is not None and page.screen_view.window() is window and not page.window_note.isHidden()
        card.tabs.setCurrentIndex(card.tabs.count() - 1)
        page.deactivate()
        frames = page.screen_view.frames
        assert wait(lambda: page.screen_view.frames > frames) and page.link.running
        # closed, it is back in the tab, and stops there while the tab is hidden
        window.close()
        assert page.screen_view.window() is not window and page.window_note.isHidden()
        assert page.screen_window is None and wait(lambda: not page.link.running)
        # *Capture with these settings*: the channels that are on at the instrument, its time base and depth
        for line in (":LA:ENABle ON", ":LA:DIGital:ENABle D2,ON", ":LA:DIGital:ENABle D5,ON", ":CHANnel1:DISPlay ON",
                     ":ACQuire:MDEPth 10k", ":TIMebase:MAIN:SCALe 1e-4"):
            driver.shell.handle(line)
        assert page.capture_button is not None and page.capture_button.isEnabled()
        page.capture_button.click()
        controller = card.controller
        session = controller.last_session
        assert session is not None and session.device_settings
        assert wait(lambda: not controller.is_capturing, 20)
        assert session.channel_numbers == [2, 5] and [c.channel_number for c in session.analog_channels] == [0]
        assert session.pre_trigger_samples + session.post_trigger_samples == 10_000
        assert session.frequency == 10_000_000  # (10k points over 10 divisions of 100 us in the simulator)
    finally:
        card.shutdown()


def test_steps_of_one_two_five():
    assert front_panel.step_125(0.2, 1) == 0.5 and front_panel.step_125(0.5, 1) == 1.0
    assert abs(front_panel.step_125(1.0, -1) - 0.5) < 1e-12 and abs(front_panel.step_125(1e-3, -1) - 5e-4) < 1e-15
    assert abs(front_panel.step_125(0.3, 1) - 0.5) < 1e-12  # (a value between steps goes to the next one)


def test_instruments_without_a_front_panel_have_no_tab(shell):
    from openscilab.driver.simulated import open_simulated

    instrument = open_simulated("pico")
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    assert card.front_panel is None
    assert instrument.facet(SettingsFacet) is None
    card.shutdown()


def test_a_closed_websocket_is_an_error_of_the_connection():
    fake = FakeWebControl()
    try:
        link = websocket.WebSocket("127.0.0.1", fake.control.getsockname()[1], timeout=2.0)
        link.close()
        try:
            link.receive(timeout=0.1)
        except websocket.WebSocketError:
            pass
        else:
            raise AssertionError("received on a closed connection")
    finally:
        fake.close()


def test_the_screen_as_h264(monkeypatch):
    """The video of port 9001: SET_ENCODER, the init message, then the stream; decoded to RGB."""
    import av
    import numpy as np
    import pytest

    try:
        encoder = av.CodecContext.create("libx264", "w")
    except Exception:  # noqa: BLE001 - PyAV without an encoder
        pytest.skip("no H.264 encoder")
    encoder.width, encoder.height, encoder.pix_fmt = 64, 48, "yuv420p"
    encoder.options = {"tune": "zerolatency"}
    units = []
    for index in range(10):
        image = np.full((48, 64, 3), index * 20, np.uint8)
        frame = av.VideoFrame.from_ndarray(image, format="rgb24").reformat(format="yuv420p")
        units += [bytes(packet) for packet in encoder.encode(frame)]
    units += [bytes(packet) for packet in encoder.encode(None)]
    fake = FakeWebControl()
    received = []

    def serve_video(client) -> None:
        received.append(json.loads(FakeWebControl._read(client)))
        init = json.dumps({"action": "init", "width": 64, "height": 48}).encode()
        client.sendall(bytes([0x81, len(init)]) + init)
        for unit in units:
            client.sendall(bytes([0x82, 127]) + struct.pack(">Q", len(unit)) + unit)
        time.sleep(2)

    video = FakeWebControl._listen()
    threading.Thread(target=fake._accept, args=(video, serve_video), daemon=True).start()
    monkeypatch.setattr(screen, "VIDEO_PORT", video.getsockname()[1])
    link = screen.DhoVideoScreen("127.0.0.1")
    frames = []
    link.start(frames.append)
    try:
        assert wait(lambda: len(frames) >= 5)
        assert frames[-1].shape == (48, 64, 3) and frames[-1].dtype == np.uint8
        assert received[0]["type"] == "SET_ENCODER"
    finally:
        link.stop()
        video.close()
        fake.close()
    assert screen.video_available()
