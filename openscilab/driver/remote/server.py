# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""openSciLab's side of remote devices: a TCP server the devices connect to.

:class:`RemoteServer` accepts devices (with the token, see :func:`openscilab_device.protocol.proof`),
keeps one :class:`RemoteConnection` per device name and tells listeners when devices come and go.
A connection measures the device's clock (:class:`~.clock.ClockModel`) all the time, converts the
time stamps of what arrives into local time (``time.monotonic``, the hub's clock), sends outputs
with the time they should happen at (in the device's clock) and calls commands.

Qt free; every callback comes from a thread of the connection.
"""

from __future__ import annotations

import itertools
import logging
import math
import secrets
import socket
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from openscilab_device import protocol
from openscilab_device.discovery import beacon
from openscilab_device.timing import TIMESCALES, ArrivalClock, Latency

from .clock import ClockModel

log = logging.getLogger(__name__)

#: pings right after a device connected (the clock is known quickly), then one every PING_INTERVAL
PING_BURST = 16
PING_BURST_INTERVAL = 0.03
PING_INTERVAL = 1.0
#: a device that does not answer for this long is gone
SILENCE = 6.0
#: values kept per input before the clock is known (they wait for the first measurement)
PENDING = 10_000

_NUMPY = {"f4": "<f4", "f8": "<f8", "u1": "u1", "i2": "<i2", "i4": "<i4"}


def _finite(value: Any, name: str) -> float:
    """``value`` of a message as a number (``ValueError``: none, or not finite)."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} is {number}")
    return number


class RemoteError(Exception):
    """A remote device refused something, did not answer or is not connected."""


@dataclass
class Received:
    """A value or block of an input, with local time (seconds of ``time.monotonic``)."""

    channel: str
    kind: str
    #: local time of the value (of the first sample of a block)
    time: float
    #: the same in the device's clock
    remote_time: float
    value: Any = None
    #: samples of a block (numpy) and their rate
    samples: Optional[np.ndarray] = None
    rate: Optional[float] = None
    #: how well the device knew the time of the block (± seconds; None: not told) and how it
    #: found it (``arrival``, ``bounds``, ``latency`` - see openscilab_device.timing)
    uncertainty: Optional[float] = None
    method: str = ""
    #: the number of the first sample of a block since the device started the acquisition
    index: Optional[int] = None


class Listeners:
    """Who listens to the inputs and sync signals of one device. The server keeps them under the
    device's name (:meth:`RemoteServer.listeners`): a connection of the device takes them on, so they
    stay when it connects again after a network drop and the new connection subscribes to them."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        #: input -> listeners of its values and blocks; sync signal -> listeners of its edges
        self.values: dict[str, list[Callable[[Received], None]]] = {}
        self.syncs: dict[str, list[Callable[[list], None]]] = {}

    def add(self, table: dict, key: str, listener: Callable) -> None:
        with self.lock:
            table.setdefault(key, []).append(listener)

    def remove(self, table: dict, key: str, listener: Callable) -> None:
        with self.lock:
            if listener in table.get(key, []):
                table[key].remove(listener)

    def of(self, table: dict, key: str) -> list[Callable]:
        with self.lock:
            return list(table.get(key, []))

    def wanted(self) -> set[str]:
        """The inputs and sync signals someone listens to."""
        with self.lock:
            return ({name for name, listeners in self.values.items() if listeners}
                    | {name for name, listeners in self.syncs.items() if listeners})


class RemoteConnection:
    """One connected device."""

    def __init__(self, server: RemoteServer, sock: socket.socket, address: tuple) -> None:
        self.server = server
        self.socket = sock
        self.address = address
        self.clock = ClockModel()
        self.description: dict = {}
        self.name = ""
        self.clock_id = ""
        #: the time scale the device's clock follows ("" none) and how closely
        self.timescale = ""
        self.timescale_accuracy = 0.0
        #: the last level of every input that records a sync signal (edges across blocks), the
        #: values an analog one has shown
        self._sync_levels: dict[str, int] = {}
        self._sync_spans: dict[str, tuple[float, float]] = {}
        #: inputs stamped from their arrival: the arrivals (device clock) and latency, to place
        #: earlier samples again with everything known now (:meth:`block_time`)
        self._arrivals: dict[str, tuple[ArrivalClock, Optional[Latency], bool]] = {}
        self.connected_at = time.monotonic()
        self.closed = threading.Event()
        self._send_lock = threading.Lock()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pings: dict[int, float] = {}
        self._requests: dict[int, Future] = {}
        #: those of the device's name once it said who it is (:meth:`RemoteServer.listeners`)
        self.listeners = Listeners()
        self._close_listeners: list[Callable[[], None]] = []
        self._pending: list[tuple[dict, bytes]] = []
        self._subscribed: frozenset = frozenset()
        #: latest value of every input: channel -> Received
        self.latest: dict[str, Received] = {}
        self.last_heard = time.monotonic()

    # -------------------------------------------------------- description
    def inputs(self) -> dict[str, dict]:
        return {item["name"]: item for item in self.description.get("inputs") or []}

    def outputs(self) -> dict[str, dict]:
        return {item["name"]: item for item in self.description.get("outputs") or []}

    def commands(self) -> dict[str, dict]:
        return {item["name"]: item for item in self.description.get("commands") or []}

    def syncs(self) -> dict[str, dict]:
        return syncs_of(self.description)

    @property
    def alive(self) -> bool:
        return not self.closed.is_set()

    # ---------------------------------------------------------- handshake
    def handshake(self) -> bool:
        nonce = secrets.token_hex(16)
        token = self.server.token
        self._write({"t": "challenge", "protocol": protocol.PROTOCOL, "nonce": nonce, "token": bool(token),
                     "server": self.server.name})
        self.socket.settimeout(10.0)
        header, _payload = protocol.receive(self.socket)
        self.socket.settimeout(None)
        if header.get("t") != "hello" or header.get("protocol") != protocol.PROTOCOL:
            self._refuse(f"openSciLab speaks protocol {protocol.PROTOCOL}")
            return False
        if token and not protocol.check_proof(token, nonce, header.get("proof")):
            self._refuse("wrong token (see openSciLab: Settings → Remote devices)")
            return False
        try:
            self.description = protocol.check_description(header.get("description"))
        except protocol.ProtocolError as error:
            self._refuse(str(error))
            return False
        self.name = str(self.description["name"]).strip()
        self.clock_id = str(header.get("clock_id") or "")
        timescale = header.get("timescale")
        if timescale in TIMESCALES:
            self.timescale = str(timescale)
            self.timescale_accuracy = float(header.get("timescale_accuracy") or 0.0)
            self.use_timescale()
        self._write({"t": "welcome", "name": self.name})
        return True

    def use_timescale(self) -> bool:
        """Convert with the time scale of the device when this computer's clock follows it too
        (preference ``timing.host_clock``)."""
        from ...core.timing import host_timescale_accuracy, timescale_to_local

        host = host_timescale_accuracy() if self.timescale else None
        if host is None:
            self.clock.set_timescale("", 0.0, None)
            return False
        timescale = self.timescale
        self.clock.set_timescale(timescale, host + self.timescale_accuracy,
                                 lambda: -timescale_to_local(0.0, timescale))
        return True

    def _refuse(self, reason: str) -> None:
        log.info("remote device refused: %s", reason)
        try:
            self._write({"t": "refused", "reason": reason})
        except OSError:
            pass

    # -------------------------------------------------------------- running
    def run(self) -> None:
        """Reads until the device goes (in the connection's thread)."""
        threading.Thread(target=self._pinger, name=f"openscilab-remote-ping-{self.name}", daemon=True).start()
        self._update_subscription()
        try:
            while not self.closed.is_set():
                header, payload = protocol.receive(self.socket)
                self.last_heard = time.monotonic()
                try:
                    self._handle(header, payload)
                except Exception:  # noqa: BLE001 - one message the device got wrong, not the connection
                    log.warning("remote device %s: a %r message was dropped", self.name, header.get("t"),
                                exc_info=True)
        except (OSError, protocol.ProtocolError) as error:
            if not self.closed.is_set():
                log.info("remote device %s: %s", self.name, error)
        finally:
            self.close()

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass
        with self._lock:
            requests, self._requests = self._requests, {}
            listeners = list(self._close_listeners)
        for future in requests.values():
            if not future.done():
                future.set_exception(RemoteError(f"{self.name} disconnected"))
        for listener in listeners:
            try:
                listener()
            except Exception:  # a listener must not stop the others
                log.exception("close listener of %s", self.name)
        self.server._closed(self)

    def _write(self, header: dict, payload: bytes = b"") -> None:
        data = protocol.encode(header, payload)
        with self._send_lock:
            self.socket.sendall(data)

    def _pinger(self) -> None:
        count = 0
        while not self.closed.is_set():
            ping_id = next(self._ids)
            with self._lock:
                self._pings[ping_id] = time.monotonic()
                for old in [key for key in self._pings if key < ping_id - 50]:
                    del self._pings[old]
            try:
                self._write({"t": "ping", "id": ping_id})
            except OSError:
                self.close()
                return
            count += 1
            if time.monotonic() - self.last_heard > SILENCE:
                log.info("remote device %s does not answer", self.name)
                self.close()
                return
            self.closed.wait(PING_BURST_INTERVAL if count < PING_BURST else PING_INTERVAL)

    # ------------------------------------------------------------ messages
    def _handle(self, header: dict, payload: bytes) -> None:
        kind = header.get("t")
        if kind == "pong":
            arrived = time.monotonic()
            with self._lock:
                sent = self._pings.pop(header.get("id"), None)
            if sent is not None:
                try:
                    t1, t2 = _finite(header["t1"], "t1"), _finite(header["t2"], "t2")
                except (KeyError, TypeError, ValueError) as error:
                    log.warning("remote device %s: a pong was dropped (%s)", self.name, error)
                    return
                first = not self.clock.ready
                self.clock.add(sent, t1, t2, arrived)
                if first:
                    with self._lock:
                        pending, self._pending = self._pending, []
                    for item in pending:
                        self._deliver(*item)
        elif kind in ("value", "block"):
            if not self.clock.ready:
                with self._lock:
                    if len(self._pending) < PENDING:
                        self._pending.append((header, payload))
                return
            self._deliver(header, payload)
        elif kind in ("done", "result"):
            with self._lock:
                future = self._requests.pop(header.get("id"), None)
            if future is not None and not future.done():
                future.set_result(header)
        elif kind == "sync":
            try:
                edges = [(_finite(at, "an edge"), int(level)) for at, level in header.get("edges") or []]
            except (TypeError, ValueError) as error:
                log.warning("remote device %s: sync edges that are not [time, level] were dropped (%s)",
                            self.name, error)
                return
            self._tell_sync(str(header.get("name")), edges)
        elif kind == "bye":
            self.close()

    def _deliver(self, header: dict, payload: bytes) -> None:
        channel = str(header.get("ch"))
        spec = self.inputs().get(channel)
        if spec is None:
            return
        try:
            item = self._received(channel, spec, header, payload)
        except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
            log.warning("remote device %s: a %s of %s was dropped (%s)", self.name, header.get("t"), channel, error)
            return
        if item.samples is not None:
            if header.get("ta") is not None and item.index is not None:
                self._arrived(channel, spec, header, item)
            if spec.get("sync"):
                self._sync_block(item)
        self.latest[channel] = item
        for listener in self.listeners.of(self.listeners.values, channel):
            try:
                listener(item)
            except Exception:  # a listener must not stop the others
                log.exception("listener of %s.%s", self.name, channel)

    def _received(self, channel: str, spec: dict, header: dict, payload: bytes) -> Received:
        """A value or block as it arrived, checked (raises ``ValueError`` for what does not fit)."""
        block = spec["kind"] in protocol.BLOCK_KINDS
        if (header["t"] == "block") != block:
            raise ValueError(f"a {header['t']} for a {spec['kind']} input")
        if not block:
            remote = _finite(header.get("at", 0.0), "at")
            return Received(channel, spec["kind"], self.clock.to_local(remote), remote, value=header.get("v"))
        dtype = _NUMPY.get(str(header.get("dtype")), "<f4")
        if len(payload) % np.dtype(dtype).itemsize:
            raise ValueError(f"{len(payload)} bytes are no whole number of {header.get('dtype')} samples")
        samples = np.frombuffer(payload, dtype=dtype).copy()
        remote = _finite(header.get("t0", 0.0), "t0")
        rate = _finite(header.get("rate") or spec.get("rate") or 1.0, "rate")
        if rate <= 0:
            raise ValueError(f"the rate {rate:g} is not positive")
        uncertainty = header.get("u")
        if header.get("ta") is not None:  # (what _arrived places the samples with)
            _finite(header["ta"], "ta")
            if header.get("ts") is not None:
                _finite(header["ts"], "ts")
            if header.get("l"):
                _finite(header["l"][0], "the latency")
                _finite(header["l"][1], "the latency")
        return Received(channel, spec["kind"], self.clock.to_local(remote), remote, samples=samples, rate=rate,
                        uncertainty=float(uncertainty) if uncertainty is not None else None,
                        method=str(header.get("m") or ""),
                        index=int(header["i"]) if header.get("i") is not None else None)

    def _arrived(self, channel: str, spec: dict, header: dict, item: Received) -> None:
        with self._lock:
            current = self._arrivals.get(channel)
            clock = current[0] if current is not None else None
            if clock is None or (header.get("ts") is not None and clock.started != float(header["ts"])):
                clock = ArrivalClock(float(item.rate or 1.0))
                if header.get("ts") is not None:
                    clock.start(float(header["ts"]))
            clock.add(item.index + len(item.samples) - 1, float(header["ta"]))
            latency = Latency(float(header["l"][0]), float(header["l"][1]), "given") if header.get("l") else None
            self._arrivals[channel] = (clock, latency, spec.get("clock") == "shared")

    def block_time(self, channel: str, index: float) -> Optional[float]:
        """The device time of the sample ``index`` of the input ``channel``, placed with all arrivals
        known now (``None``: the device stamped the input itself)."""
        with self._lock:
            entry = self._arrivals.get(channel)
            if entry is None:
                return None
            clock, latency, shared = entry
            if shared:
                # one sample clock with openSciLab's instrument: evenly from the first sample
                return clock.estimate(0, latency).time + index / clock.rate
            return clock.estimate(index, latency).time

    def _sync_block(self, item: Received) -> None:
        """The edges of a block of an input that records a sync signal, in the device's clock. An
        analog one switches at the middle of its ``range`` (else of what it has shown so far) with a
        hysteresis of a quarter of that and at least five times its noise, so noise makes no edges."""
        samples = item.samples
        if samples is None or not len(samples):
            return
        previous = self._sync_levels.get(item.channel)
        if samples.dtype.kind == "f":
            spec = self.inputs().get(item.channel) or {}
            limits = spec.get("range")
            low, high = (float(limits[0]), float(limits[1])) if limits else self._sync_span(item.channel, samples)
            levels = hysteresis(samples, low, high, previous)
            if levels is None:
                return  # nothing but noise so far: no level yet
        else:
            levels = (samples != 0).astype(np.int8)
        self._sync_levels[item.channel] = int(levels[-1])
        start = previous if previous is not None else int(levels[0])
        steps = np.concatenate(([start], levels))
        index = np.nonzero(steps[1:] != steps[:-1])[0]
        if not len(index) or not self.listeners.of(self.listeners.syncs, item.channel):
            return
        rate = float(item.rate or 1.0)
        first = item.index
        edges = [(item.remote_time + float(position) / rate, int(levels[position]))
                 + ((first + int(position),) if first is not None else ()) for position in index]
        self._tell_sync(item.channel, edges)

    def _tell_sync(self, name: str, edges: list) -> None:
        for listener in self.listeners.of(self.listeners.syncs, name):
            try:
                listener(edges)
            except Exception:  # a listener must not stop the others (nor the connection)
                log.exception("sync listener of %s.%s", self.name, name)

    def _sync_span(self, channel: str, samples: np.ndarray) -> tuple[float, float]:
        low, high = self._sync_spans.get(channel, (math.inf, -math.inf))
        low, high = min(low, float(samples.min())), max(high, float(samples.max()))
        self._sync_spans[channel] = (low, high)
        return low, high

    # ----------------------------------------------------------- listening
    def listen(self, channel: str, listener: Callable[[Received], None]) -> Callable[[], None]:
        """``listener`` gets every value of the input ``channel``; returns the function that stops it.
        It goes on listening when the device connects again (:meth:`RemoteServer.listen`)."""
        if channel not in self.inputs():
            raise RemoteError(f"{self.name} has no input {channel!r}")
        return self.server.listen(self.name, channel, listener)

    def listen_sync(self, name: str, listener: Callable[[list], None]) -> Callable[[], None]:
        """``listener`` gets the edges ``[(device time, level)]`` of the sync signal ``name`` (of an input
        recording it: ``(device time, level, sample index)``, see :meth:`block_time`); the device
        drives a sync output only while someone listens."""
        if name not in self.syncs():
            raise RemoteError(f"{self.name} has no sync signal {name!r}")
        return self.server.listen_sync(self.name, name, listener)

    def on_close(self, listener: Callable[[], None]) -> None:
        with self._lock:
            self._close_listeners.append(listener)

    def _update_subscription(self) -> None:
        """Slow inputs always come (their latest value is shown); blocks and sync signals only while
        someone listens (also those who listened to the device's last connection)."""
        if self.closed.is_set():
            return
        with self._lock:
            known = set(self.inputs()) | set(self.syncs())
            wanted = {name for name, spec in self.inputs().items() if spec["kind"] not in protocol.BLOCK_KINDS}
            wanted = frozenset(wanted | (self.listeners.wanted() & known))
            if wanted == self._subscribed:
                return
            self._subscribed = wanted
            # sent under the lock: two threads updating at once must not send their sets in the
            # wrong order (the device would keep the older one)
            try:
                self._write({"t": "subscribe", "channels": sorted(wanted)})
                return
            except OSError:
                pass
        self.close()

    # ------------------------------------------------------------ commands
    def _submit(self, header: dict) -> Future:
        """Send a request; its future gets the answer (``done``, ``result``)."""
        if self.closed.is_set():
            raise RemoteError(f"{self.name} is not connected")
        request_id = next(self._ids)
        future: Future = Future()
        future.request_id = request_id  # type: ignore[attr-defined]
        with self._lock:
            self._requests[request_id] = future
        try:
            self._write({**header, "id": request_id})
        except OSError as error:
            with self._lock:
                self._requests.pop(request_id, None)
            raise RemoteError(f"{self.name}: {error}") from None
        return future

    def _answer(self, future: Future, timeout: float) -> dict:
        try:
            answer = future.result(timeout)
        except FutureTimeout:
            raise RemoteError(f"{self.name} did not answer within {timeout:g} s") from None
        finally:
            with self._lock:
                self._requests.pop(future.request_id, None)  # type: ignore[attr-defined]
        if answer.get("error"):
            raise RemoteError(f"{self.name}: {answer['error']}")
        return answer

    def _request(self, header: dict, timeout: float) -> dict:
        return self._answer(self._submit(header), timeout)

    def _set_header(self, channel: str, value: Any, at: Optional[float]) -> dict:
        if channel not in self.outputs():
            raise RemoteError(f"{self.name} has no output {channel!r}")
        return {"t": "set", "ch": channel, "v": value, "at": self.clock.to_remote(at) if at is not None else None}

    def set(self, channel: str, value: Any, at: Optional[float] = None, timeout: float = 5.0) -> float:
        """Set the output ``channel`` to ``value`` at the local time ``at`` (``None``: at once); returns
        the local time the device did it."""
        header = self._set_header(channel, value, at)
        wait = max((at - time.monotonic()) if at is not None else 0.0, 0.0)
        answer = self._request(header, timeout + wait)
        return self.clock.to_local(float(answer.get("at", 0.0)))

    def set_later(self, channel: str, value: Any, at: float) -> Future:
        """Send ``value`` for the output ``channel`` at the local time ``at`` without waiting for the
        answer: values sent one after the other are timed by the device, not by their round trips.
        :meth:`applied` waits for the answer."""
        return self._submit(self._set_header(channel, value, at))

    def applied(self, future: Future, timeout: float) -> float:
        """The local time the device applied a value of :meth:`set_later` (raises :class:`RemoteError`)."""
        return self.clock.to_local(float(self._answer(future, timeout).get("at", 0.0)))

    def call(self, name: str, args: Optional[dict] = None, timeout: float = 10.0) -> Any:
        if name not in self.commands():
            raise RemoteError(f"{self.name} has no command {name!r}")
        return self._request({"t": "call", "name": name, "args": dict(args or {})}, timeout).get("value")


def hysteresis(values: np.ndarray, low: float, high: float, previous: Optional[int] = None) -> Optional[np.ndarray]:
    """Logic levels of analog ``values`` between ``low`` and ``high``: a level changes only when a value
    passes the middle by a quarter of the span - and by five times the noise (estimated from the
    differences of neighbouring values), so a signal that has shown nothing but noise yet makes no
    edges. ``previous``: the level before the first value; without it the first clear level is taken
    (``None``: no clear level yet). Vectorized: no loop per sample."""
    values = np.asarray(values, dtype=np.float64)
    middle = (low + high) / 2
    noise = float(np.median(np.abs(np.diff(values)))) * 1.5 if len(values) > 2 else 0.0
    band = max((high - low) / 4, 5 * noise, 1e-12)
    decided = np.full(len(values), -1, dtype=np.int8)
    decided[values > middle + band] = 1
    decided[values < middle - band] = 0
    known = np.nonzero(decided >= 0)[0]
    if not len(known):
        return None if previous is None else np.full(len(values), previous, dtype=np.int8)
    first = previous if previous is not None else int(decided[known[0]])
    # every value takes the level of the last decided value before it (or the level before the block)
    position = np.maximum.accumulate(np.where(decided >= 0, np.arange(len(values)), -1))
    return np.where(position >= 0, decided[np.maximum(position, 0)], first).astype(np.int8)


def syncs_of(description: dict) -> dict[str, dict]:
    """The sync signals of a device: its sync outputs and inputs, and the inputs of blocks that
    record one (``sync: true``; ``block`` set)."""
    found = {item["name"]: item for item in description.get("sync") or []}
    for item in description.get("inputs") or []:
        if item.get("sync") and item.get("kind") in protocol.BLOCK_KINDS:
            found[item["name"]] = {"name": item["name"], "kind": "input", "block": True,
                                   "clock": item.get("clock") or "", "description": item.get("description", "")}
    return found


class RemoteServer:
    """Accepts remote devices on ``port`` (``0``: a free port, see :attr:`port`)."""

    def __init__(self, port: int = protocol.DEFAULT_PORT, token: str = "", host: str = "0.0.0.0",
                 name: str = "openSciLab", beacon: bool = False) -> None:
        self.host, self.requested_port, self.token, self.name = host, int(port), token, name
        self.beacon_enabled = beacon
        self.port = 0
        self._socket: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._devices: dict[str, RemoteConnection] = {}
        self._changed = threading.Condition(self._lock)
        self._listeners: list[Callable[[str, RemoteConnection], None]] = []
        #: who listens to which device (by its name): kept across its connections
        self._device_listeners: dict[str, Listeners] = {}

    @property
    def running(self) -> bool:
        return self._socket is not None and not self._stop.is_set()

    def start(self) -> RemoteServer:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.requested_port))
        sock.listen(16)
        self._socket = sock
        self.port = sock.getsockname()[1]
        self._stop.clear()
        threading.Thread(target=self._accept, name="openscilab-remote-server", daemon=True).start()
        if self.beacon_enabled:
            threading.Thread(target=self._beacon, name="openscilab-remote-beacon", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        for connection in self.devices():
            connection.close()

    def _accept(self) -> None:
        sock = self._socket
        while sock is not None and not self._stop.is_set():
            try:
                client, address = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client, address), daemon=True,
                             name="openscilab-remote-connection").start()

    def _serve(self, client: socket.socket, address: tuple) -> None:
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        connection = RemoteConnection(self, client, address)
        try:
            if not connection.handshake():
                client.close()
                return
        except (OSError, protocol.ProtocolError) as error:
            log.info("remote device at %s: %s", address, error)
            client.close()
            return
        connection.listeners = self.listeners(connection.name)  # (run() subscribes to what they want)
        with self._lock:
            old = self._devices.get(connection.name)
            self._devices[connection.name] = connection
            self._changed.notify_all()
        if old is not None:
            old.close()  # the device started again (or a second script took its name)
        self._tell("connected", connection)
        connection.run()

    def _closed(self, connection: RemoteConnection) -> None:
        with self._lock:
            if self._devices.get(connection.name) is connection:
                del self._devices[connection.name]
            else:
                return
        self._tell("disconnected", connection)

    def _beacon(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop.wait(2.0):
                try:
                    sock.sendto(beacon(self.name, self.port), ("<broadcast>", protocol.BEACON_PORT))
                except OSError:
                    pass

    # ------------------------------------------------------------- devices
    def devices(self) -> list[RemoteConnection]:
        with self._lock:
            return list(self._devices.values())

    def device(self, name: str) -> Optional[RemoteConnection]:
        with self._lock:
            return self._devices.get(name)

    def wait_for(self, name: str, timeout: float) -> RemoteConnection:
        deadline = time.monotonic() + timeout
        with self._lock:
            while name not in self._devices:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError(f"the remote device {name!r} did not connect within {timeout:g} s "
                                      f"(port {self.port})")
                self._changed.wait(remaining)
            return self._devices[name]

    def listeners(self, name: str) -> Listeners:
        """Who listens to the device ``name``, whether it is connected or not."""
        with self._lock:
            return self._device_listeners.setdefault(name, Listeners())

    def listen(self, name: str, channel: str, listener: Callable[[Received], None]) -> Callable[[], None]:
        """``listener`` gets every value of the input ``channel`` of the device ``name`` - of this
        connection and of every one after it (the device connects again after a network drop);
        returns the function that stops it."""
        listeners = self.listeners(name)
        return self._listen(name, listeners, listeners.values, channel, listener)

    def listen_sync(self, name: str, signal: str, listener: Callable[[list], None]) -> Callable[[], None]:
        """``listener`` gets the edges of the sync signal ``signal`` of the device ``name`` (see
        :meth:`RemoteConnection.listen_sync`), across its connections."""
        listeners = self.listeners(name)
        return self._listen(name, listeners, listeners.syncs, signal, listener)

    def _listen(self, name: str, listeners: Listeners, table: dict, key: str, listener: Callable) -> Callable[[], None]:
        listeners.add(table, key, listener)
        self._resubscribe(name)

        def stop() -> None:
            listeners.remove(table, key, listener)
            self._resubscribe(name)

        return stop

    def _resubscribe(self, name: str) -> None:
        connection = self.device(name)
        if connection is not None:
            connection._update_subscription()

    def subscribe(self, listener: Callable[[str, RemoteConnection], None]) -> Callable[[], None]:
        """``listener(event, connection)`` with ``"connected"``/``"disconnected"``."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _tell(self, event: str, connection: RemoteConnection) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, connection)
            except Exception:  # a listener must not stop the others
                log.exception("remote server listener")
