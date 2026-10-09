# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The screen of the DHO900 live, and touches on it - what its Web Control does in a browser.

The instrument's Web Control app serves (measured on a DHO924S, ``Webcontrol.apk``):

* ``ws://<host>:9001``: the screen as H.264. After ``{"type": "SET_ENCODER", "size": null,
  "rate": null}`` (or ``"720p"``/``"1080p"``, ``"High"``/``"Low"``) it answers
  ``{"action": "init", "width": 1024, "height": 600}`` and then sends the stream, an access unit
  per binary message (as fast as the screen changes, some 15-30 per second).
* ``ws://<host>:9003``: ``take_screenshot`` answers one JPEG of the screen (1024 x 600);
  ``record_screen_start`` sends one after the other (about 5 per second) until
  ``record_screen_stop``.
* ``ws://<host>:9002``: touches as JSON text, ``{"type": "onmousedown"|"onmousemove"|"onmouseup",
  "downDelta": <ms>, "clientX": <x>, "clientY": <y>}`` in screen pixels.
* (``9004`` SCPI: not used here.)

:class:`DhoVideoScreen` decodes the H.264 stream with PyAV (FFmpeg); without PyAV,
:class:`DhoScreen` shows the JPEGs. Without the network (only over USB) :class:`ScpiScreen` shows
the screen from ``:DISPlay:DATA?`` (a PNG, about two per second), without touches.

``on_frame`` gets the image either as the bytes of a JPEG or PNG, or as an RGB array
(``numpy``, height x width x 3).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from ...core.websocket import BINARY, TEXT, WebSocket, WebSocketError

log = logging.getLogger("openscilab.driver")

VIDEO_PORT = 9001
SCREEN_PORT = 9003
CONTROL_PORT = 9002
#: pixels of the instrument's screen; touches are given in them
WIDTH, HEIGHT = 1024, 600
#: seconds between two attempts to reach the screen server again
RETRY_S = 2.0

FrameHandler = Callable[[Any], None]


class DhoScreen:
    """The live screen of a DHO on the network: :meth:`start` hands every image (JPEG bytes) to
    ``on_frame`` from a thread until :meth:`stop`; :meth:`touch` presses on the screen."""

    touch_supported = True

    def __init__(self, host: str) -> None:
        self.host = host
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._socket: Optional[WebSocket] = None
        self._control: Optional[WebSocket] = None
        self._control_lock = threading.Lock()
        self._pressed_at = 0.0
        #: why no image comes (``None`` while they do)
        self.problem: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, on_frame: FrameHandler) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(on_frame, self._stop), name="openscilab-dho-screen",
                                        daemon=True)
        self._thread.start()

    def _run(self, on_frame: FrameHandler, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                link = WebSocket(self.host, SCREEN_PORT, timeout=5.0)
            except WebSocketError as error:
                self.problem = str(error)
                stop.wait(RETRY_S)
                continue
            self._socket = link
            try:
                link.send("record_screen_start")
                self.problem = None
                while not stop.is_set():
                    try:
                        kind, data = link.receive(timeout=5.0)
                    except socket.timeout:
                        link.send("record_screen_start")  # (no image for a while: ask again)
                        continue
                    if kind == BINARY and len(data) > 100:  # (it also sends empty messages)
                        on_frame(data)
            except WebSocketError as error:
                if not stop.is_set():
                    self.problem = str(error)
                    log.debug("The screen of %s: %s", self.host, error)
                    stop.wait(RETRY_S)
            finally:
                self._socket = None
                if stop.is_set():
                    try:
                        link.send("record_screen_stop")
                    except WebSocketError:
                        pass
                link.close()

    def stop(self) -> None:
        self._stop.set()
        link = self._socket
        if link is not None:
            try:
                link.send("record_screen_stop")
            except WebSocketError:
                pass
            link.close()  # (ends a receive that waits)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)
        with self._control_lock:
            if self._control is not None:
                self._control.close()
                self._control = None

    def touch(self, kind: str, x: float, y: float) -> bool:
        """``kind``: ``down``, ``move`` or ``up``; ``x``, ``y`` in screen pixels. False when the
        instrument could not be reached."""
        if kind == "down":
            self._pressed_at = time.monotonic()
        message = json.dumps({"type": f"onmouse{kind}", "downDelta": int((time.monotonic() - self._pressed_at) * 1000),
                              "clientX": max(0.0, min(float(x), WIDTH - 1)),
                              "clientY": max(0.0, min(float(y), HEIGHT - 1))})
        with self._control_lock:
            for _attempt in range(2):
                try:
                    if self._control is None or self._control.closed:
                        self._control = WebSocket(self.host, CONTROL_PORT, timeout=2.0)
                    self._control.send(message)
                    return True
                except WebSocketError as error:
                    log.debug("A touch on %s failed: %s", self.host, error)
                    if self._control is not None:
                        self._control.close()
                    self._control = None
        return False


def video_available() -> bool:
    """PyAV (FFmpeg) with an H.264 decoder is installed."""
    try:
        import av

        av.codec.Codec("h264", "r")
        return True
    except Exception:  # noqa: BLE001 - not installed, or without the decoder
        return False


class DhoVideoScreen(DhoScreen):
    """The live screen as H.264 (port 9001), decoded with PyAV: smooth, as in the browser.
    ``on_frame`` gets RGB arrays."""

    def _run(self, on_frame: FrameHandler, stop: threading.Event) -> None:
        import av

        while not stop.is_set():
            try:
                link = WebSocket(self.host, VIDEO_PORT, timeout=5.0)
            except WebSocketError as error:
                self.problem = str(error)
                stop.wait(RETRY_S)
                continue
            self._socket = link
            decoder = av.CodecContext.create("h264", "r")
            try:
                link.send(json.dumps({"type": "SET_ENCODER", "size": None, "rate": None}))
                self.problem = None
                while not stop.is_set():
                    try:
                        kind, data = link.receive(timeout=5.0)
                    except socket.timeout:
                        continue  # (a still screen sends nothing)
                    if kind == TEXT or not data:
                        continue  # ({"action": "init", "width": …, "height": …})
                    try:
                        frames = [frame for packet in decoder.parse(data) for frame in decoder.decode(packet)]
                    except av.error.FFmpegError as error:  # (a damaged unit: the next key frame mends it)
                        log.debug("H.264 of %s: %s", self.host, error)
                        continue
                    if frames:  # (only the newest one of a burst is worth showing)
                        on_frame(np.ascontiguousarray(frames[-1].to_ndarray(format="rgb24")))
            except WebSocketError as error:
                if not stop.is_set():
                    self.problem = str(error)
                    log.debug("The screen video of %s: %s", self.host, error)
                    stop.wait(RETRY_S)
            finally:
                self._socket = None
                link.close()

    def stop(self) -> None:
        self._stop.set()
        link = self._socket
        if link is not None:
            link.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)
        with self._control_lock:
            if self._control is not None:
                self._control.close()
                self._control = None


class ScpiScreen:
    """The screen over SCPI (``screenshot`` of the driver: ``:DISPlay:DATA?``, a PNG), for an
    instrument reached only over USB: about two images per second, no touches."""

    touch_supported = False

    def __init__(self, screenshot: Callable[[], bytes], interval: float = 0.5) -> None:
        self._screenshot = screenshot
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.problem: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, on_frame: FrameHandler) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(on_frame, self._stop), name="openscilab-dho-screen",
                                        daemon=True)
        self._thread.start()

    def _run(self, on_frame: FrameHandler, stop: threading.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                data = self._screenshot()
                self.problem = None
                if data:
                    on_frame(data)
            except Exception as error:  # noqa: BLE001 - every way an instrument can fail to answer
                self.problem = str(error)
                log.debug("The screen over SCPI: %s", error)
            stop.wait(max(self.interval - (time.monotonic() - started), 0.05))

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(5.0)

    def touch(self, kind: str, x: float, y: float) -> bool:
        return False
