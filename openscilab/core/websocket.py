# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A small blocking WebSocket client (RFC 6455): the handshake, text and binary messages (also in
fragments), ping and pong, close. Enough to talk to instruments on the local network, e.g. the
screen and touch servers of the Rigol DHO900 (``driver/rigoldho/screen.py``); no TLS, no
extensions.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from typing import Optional, Union

TEXT = 0x1
BINARY = 0x2
CLOSE = 0x8
PING = 0x9
PONG = 0xA
CONTINUATION = 0x0
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
#: largest message accepted (a screen image is some 200 kB)
MAX_MESSAGE = 64 << 20


class WebSocketError(OSError):
    """The connection failed, was refused or closed."""


class WebSocket:
    """One connection: :meth:`send` from any thread, :meth:`receive` from one."""

    def __init__(self, host: str, port: int, path: str = "/", timeout: float = 5.0) -> None:
        self.host, self.port = host, port
        try:
            self._socket = socket.create_connection((host, port), timeout=timeout)
        except OSError as error:
            raise WebSocketError(f"ws://{host}:{port}{path} cannot be reached: {error}") from error
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._send_lock = threading.Lock()
        self._buffer = bytearray()
        self.closed = False
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        try:
            self._socket.sendall(request.encode("ascii"))
            head = self._read_until(b"\r\n\r\n").decode("latin-1")
        except (OSError, WebSocketError) as error:
            self.close()
            raise WebSocketError(f"ws://{host}:{port}{path}: no handshake ({error})") from error
        lines = head.split("\r\n")
        headers = {line.split(":", 1)[0].strip().lower(): line.split(":", 1)[1].strip()
                   for line in lines[1:] if ":" in line}
        accept = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
        if " 101" not in lines[0] or headers.get("sec-websocket-accept") != accept:
            self.close()
            raise WebSocketError(f"ws://{host}:{port}{path} refused the WebSocket: {lines[0]}")

    # ---------------------------------------------------------------- bytes
    def _fill(self, count: int) -> None:
        while len(self._buffer) < count:
            try:
                data = self._socket.recv(max(count - len(self._buffer), 1 << 16))
            except socket.timeout:
                raise
            except OSError as error:
                raise WebSocketError(f"the connection to {self.host}:{self.port} failed: {error}") from error
            if not data:
                self.closed = True
                raise WebSocketError(f"{self.host}:{self.port} closed the connection")
            self._buffer += data

    def _read(self, count: int) -> bytes:
        self._fill(count)
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            self._fill(len(self._buffer) + 1)
        index = self._buffer.index(marker) + len(marker)
        data = bytes(self._buffer[:index])
        del self._buffer[:index]
        return data

    # -------------------------------------------------------------- frames
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        if length < 126:
            header = struct.pack(">BB", 0x80 | opcode, 0x80 | length)
        elif length < 1 << 16:
            header = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        with self._send_lock:
            try:
                self._socket.sendall(header + mask + masked)
            except OSError as error:
                self.closed = True
                raise WebSocketError(f"the connection to {self.host}:{self.port} failed: {error}") from error

    def send(self, message: Union[str, bytes]) -> None:
        """A text message (``str``) or a binary one (``bytes``)."""
        if isinstance(message, str):
            self._send_frame(TEXT, message.encode("utf-8"))
        else:
            self._send_frame(BINARY, bytes(message))

    def receive(self, timeout: Optional[float] = None) -> tuple[int, bytes]:
        """The next message: (``TEXT`` or ``BINARY``, its bytes); answers pings on the way.
        ``socket.timeout`` when nothing came within ``timeout`` seconds (the connection stays)."""
        try:
            self._socket.settimeout(timeout)
        except OSError as error:  # (closed meanwhile, by another thread)
            self.closed = True
            raise WebSocketError(f"the connection to {self.host}:{self.port} is closed") from error
        kind, parts = None, []
        while True:
            first, second = self._read(2)
            opcode, final = first & 0x0F, bool(first & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            if length > MAX_MESSAGE:
                raise WebSocketError(f"a message of {length} bytes from {self.host}:{self.port}")
            mask = self._read(4) if second & 0x80 else None
            payload = self._read(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == PING:
                self._send_frame(PONG, payload)
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                self.closed = True
                raise WebSocketError(f"{self.host}:{self.port} closed the WebSocket")
            if opcode != CONTINUATION:
                kind = opcode
            parts.append(payload)
            if final:
                return kind if kind is not None else BINARY, b"".join(parts)

    def close(self) -> None:
        if not self.closed:
            try:
                self._send_frame(CLOSE, b"")
            except WebSocketError:
                pass
        self.closed = True
        try:
            self._socket.close()
        except OSError:
            pass
