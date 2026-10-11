"""Remote devices when things go wrong: the connection drops and comes again, openSciLab falls silent
or never greets, the device's address cannot work, a device sends what does not fit, a sync output
fails; and pulses timed by the device, a device card without a monitor box."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from openscilab.driver.remote.instrument import RemoteGpio, make_instrument
from openscilab.driver.remote.server import RemoteServer
from openscilab_device import Device, protocol


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.fixture
def server():
    server = RemoteServer(port=0, host="127.0.0.1", token="secret").start()
    yield server
    server.stop()


def device(server, name: str = "pi", **options) -> Device:
    return Device(name, server=f"127.0.0.1:{server.port}", token="secret", **options)


def ready(server, name: str = "pi", previous=None):
    """The connection of ``name`` (another than ``previous``) once its clock is known."""
    def found():
        connection = server.device(name)
        return connection is not None and connection is not previous and connection.clock.ready
    assert wait_for(found, 8)
    return server.device(name)


class FakeOpenSciLab:
    """A server that greets a device as openSciLab does (``greet``) - or not - and then only
    listens: no pings, as a host that went away without a word."""

    def __init__(self, greet: bool = True, subscribe: tuple = ()) -> None:
        self.greet, self.subscribe = greet, subscribe
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(4)
        self.port = self.socket.getsockname()[1]
        self.accepted: list[socket.socket] = []
        #: what arrived after the handshake, per connection
        self.received: list[list[dict]] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _address = self.socket.accept()
            except OSError:
                return
            self.accepted.append(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        received: list[dict] = []
        self.received.append(received)
        if not self.greet:
            return  # (accepted; says nothing)
        try:
            client.sendall(protocol.encode({"t": "challenge", "protocol": 1, "nonce": "n", "token": False}))
            protocol.receive(client)
            client.sendall(protocol.encode({"t": "welcome", "name": "x"}))
            client.sendall(protocol.encode({"t": "subscribe", "channels": list(self.subscribe)}))
            while True:
                header, _payload = protocol.receive(client)
                received.append(header)
        except (OSError, protocol.ProtocolError):
            pass

    def close(self) -> None:
        self.socket.close()
        for client in self.accepted:
            client.close()


# ------------------------------------------------- the connection comes again
def test_listeners_and_subscriptions_stay_when_the_device_connects_again(server):
    dev = device(server)
    x = dev.input("x")
    mic = dev.input("mic", kind="analog", rate=100.0)
    dev.sync_output("SYNC", lambda level: None)
    dev.start()
    instrument = make_instrument(server, "pi")
    try:
        first = ready(server)
        remote = instrument.remote
        values, blocks, edges = [], [], []
        remote.listen("x", values.append)
        remote.listen("mic", blocks.append)
        remote.listen_sync("SYNC", edges.extend)
        assert wait_for(lambda: edges)  # (subscribed: the device drives its sync output)
        first.socket.shutdown(socket.SHUT_RDWR)  # the network dropped
        second = ready(server, previous=first)
        values.clear(), blocks.clear(), edges.clear()
        time.sleep(0.2)  # (the subscription reaches the device)
        x.send(1.5)
        mic.send_block([0.25] * 10)
        # the same listeners get the values and blocks of the new connection, which subscribed to
        # the blocks and the sync signal again
        assert wait_for(lambda: any(item.value == 1.5 for item in values) and blocks and edges)
        assert "mic" in second._subscribed and "SYNC" in second._subscribed
    finally:
        dev.stop()


# ------------------------------------------------ timeouts of the device side
def test_a_server_that_never_greets_is_given_up_and_stop_ends_the_wait():
    fake = FakeOpenSciLab(greet=False)
    dev = Device("pi", server=f"127.0.0.1:{fake.port}")
    dev.input("x")
    dev.handshake_timeout = 0.3
    try:
        dev.start()
        assert wait_for(lambda: "did not greet within" in dev.last_error, 5)
        assert wait_for(lambda: len(fake.accepted) >= 2, 5)  # (and tried again)
        dev.stop()
        dev.handshake_timeout = 60.0
        count = len(fake.accepted)  # (before the start: its connection may be accepted at once)
        dev.start()
        assert wait_for(lambda: len(fake.accepted) > count, 5)
        time.sleep(0.05)
        started = time.monotonic()
        threads = list(dev._threads)
        dev.stop()  # interrupts the handshake (the socket is known before the welcome)
        assert time.monotonic() - started < 1.0 and not any(thread.is_alive() for thread in threads)
    finally:
        dev.stop()
        fake.close()


def test_a_server_that_falls_silent_is_left_and_values_follow_on_the_next_connection():
    fake = FakeOpenSciLab(subscribe=("x",))
    dev = Device("pi", server=f"127.0.0.1:{fake.port}")
    x = dev.input("x")
    dev.silence = 0.5
    dev.start()
    try:
        assert dev.wait_connected(5)
        assert wait_for(lambda: not dev.connected, 3)  # no ping for 0.5 s: given up
        assert "silent" in dev.last_error
        x.send(7.0)  # (while it connects again: kept)
        assert wait_for(lambda: len(fake.received) >= 2 and any(
            header.get("t") == "value" and header.get("v") == 7.0 for header in fake.received[1]), 5)
    finally:
        dev.stop()
        fake.close()


def test_an_address_that_cannot_work_is_said_and_a_taken_beacon_port_is_tried_again(monkeypatch):
    with pytest.raises(ValueError, match="not a number"):
        Device("pi", server="host:abc")
    dev = Device("pi", server="127.0.0.1:1")
    dev.input("x")
    dev.server = "host:abc"  # (changed after it was made)
    dev.start()
    try:
        assert wait_for(lambda: "not a number" in dev.last_error)
        assert wait_for(lambda: not dev._threads[0].is_alive())  # trying again would not help
    finally:
        dev.stop()

    from openscilab_device import discovery

    calls = []

    def taken(timeout: float = 3.0):
        calls.append(timeout)
        raise OSError("address already in use")

    monkeypatch.setattr(discovery, "find_server", taken)
    dev = Device("pi")
    dev.input("x")
    dev.start()
    try:
        assert wait_for(lambda: "address already in use" in dev.last_error)
        assert wait_for(lambda: len(calls) >= 2) and dev._threads[0].is_alive()  # tried again
    finally:
        dev.stop()


def test_stop_waits_for_the_threads_and_an_old_thread_does_not_connect_after_a_new_start(server):
    connected = []
    server.subscribe(lambda event, connection: event == "connected" and connected.append(connection))

    def slow(host: str, port: int) -> socket.socket:
        time.sleep(0.4)  # (as the search for openSciLab or a connect that takes long)
        return socket.create_connection((host, port), timeout=5.0)

    dev = device(server, connect=slow)
    dev.input("x")
    dev.start()
    time.sleep(0.05)
    threads = list(dev._threads)
    dev.stop()
    assert not any(thread.is_alive() for thread in threads)  # waited for them
    dev.stop_timeout = 0.05  # now: the old thread is still connecting when it starts again
    dev.start()
    time.sleep(0.05)
    dev.stop()
    dev.stop_timeout = 10.0
    dev.start()
    try:
        assert dev.wait_connected(5)
        running = list(dev._threads)
        assert dev.start()._threads == running  # (started once)
        time.sleep(0.8)  # (the old thread's connect returned meanwhile)
        assert len(connected) == 1 and dev.connected
    finally:
        dev.stop()


# --------------------------------------------------- what the device gets wrong
def test_messages_that_do_not_fit_are_dropped_and_the_connection_stays(server):
    dev = device(server)
    dev.input("x")
    dev.input("mic", kind="analog", rate=100.0)
    dev.sync_output("SYNC", lambda level: None)
    dev.start()
    try:
        connection = ready(server)
        values, edges = [], []
        connection.listen("x", values.append)

        def failing(items: list) -> None:
            edges.extend(items)
            raise RuntimeError("a listener that fails")

        connection.listen_sync("SYNC", failing)
        assert wait_for(lambda: edges)
        with connection._lock:
            connection._pings[10 ** 9] = time.monotonic()  # (a ping waiting for its answer)
        dev._send({"t": "pong", "id": 10 ** 9, "t1": "soon", "t2": None})
        dev._send({"t": "block", "ch": "mic", "t0": dev.now(), "rate": 100.0, "dtype": "f4", "n": 1}, b"\x00\x01\x02")
        dev._send({"t": "block", "ch": "mic", "t0": "later", "rate": 100.0, "dtype": "f4", "n": 1}, b"\x00" * 4)
        dev._send({"t": "value", "ch": "x", "at": float("nan"), "v": 1.0})
        dev._send({"t": "value", "ch": "mic", "at": dev.now(), "v": 1.0})
        dev._send({"t": "sync", "name": "SYNC", "edges": [["a", 1], 5]})
        dev._send({"t": "value", "ch": "x", "at": dev.now(), "v": 3.0})
        assert wait_for(lambda: any(item.value == 3.0 for item in values))
        assert [item.value for item in values if item.value == 1.0] == []
        assert server.device("pi") is connection and connection.alive
    finally:
        dev.stop()


def test_a_sync_output_that_fails_does_not_end_the_schedule(server):
    dev = device(server)
    relay = dev.output("relay", kind="bool")
    switched = []
    relay.on_set(lambda value, at: switched.append(value))
    writes = []

    def write(level: int) -> None:
        writes.append(level)
        if len(writes) == 1:
            raise OSError("the pin is busy")

    dev.sync_output("SYNC", write)
    dev.start()
    try:
        connection = ready(server)
        edges = []
        connection.listen_sync("SYNC", edges.extend)
        assert wait_for(lambda: len(edges) >= 2)  # the schedule went on after the failure
        connection.set("relay", True)
        assert switched == [True]
    finally:
        dev.stop()


# ------------------------------------------------------------------- pulses
def test_a_pulse_is_timed_by_the_device_not_by_the_round_trip(server):
    from openscilab.driver.remote.simulated import Network, NetworkEmulator

    network = NetworkEmulator(("127.0.0.1", server.port), Network(latency=0.04, jitter=0.0))
    dev = Device("pi", server=f"127.0.0.1:{network.port}", token="secret")
    led = dev.output("led", kind="bool", default=False)
    edges = []
    led.on_set(lambda value, at: edges.append((value, dev.now())))
    dev.start()
    instrument = make_instrument(server, "pi")
    try:
        ready(server)
        instrument.facet(RemoteGpio).pulse("led", 0.03)
        assert wait_for(lambda: len(edges) == 2)
        (high, rise), (low, fall) = edges
        # 30 ms on the device (give or take how late its scheduler wakes); one edge sent after the
        # answer of the other came 80 ms and more later (two trips through the network)
        assert high is True and low is False and 0.02 < fall - rise < 0.045
    finally:
        dev.stop()
        network.close()


# ----------------------------------------------------------------- the card
def test_the_card_of_a_remote_device_offers_no_monitor_box(shell):
    from openscilab.driver.remote.simulated import open_simulated_remote

    instrument = open_simulated_remote("remote-sim:echo")
    try:
        shell.hub.add(instrument)
        card = shell.open_device_card(instrument)
        card.refresh()
        assert instrument.monitor is not None  # (device.monitor reads its latest values)
        assert card.monitor_box.isHidden() and card.record_button.isHidden()
        card.set_monitoring(True)  # (the command palette's "Monitor the inputs")
        card.set_monitoring(False)
        instrument.monitor.stop()  # nothing runs: nothing to stop, no error
    finally:
        instrument.close()
