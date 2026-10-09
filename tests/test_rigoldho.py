"""The Rigol DHO900 driver against the simulated oscilloscope (``rigol-sim:direct`` and
``rigol-sim:bridge``): SCPI parsing, captures directly and through the bridge app, triggers,
stopping, thresholds, the generator, the beacon and the probe tool."""

from __future__ import annotations

import socket
import threading
import time

import numpy as np
import pytest

from openscilab.core import waveform as waves
from openscilab.core.instrument import Instrument, InstrumentError
from openscilab.driver import discovery
from openscilab.driver.base import CaptureError, DeviceConnectionError
from openscilab.driver.models import AnalogChannel, AnalyzerChannel, CaptureSession, TriggerType
from openscilab.driver.rigoldho import beacon, scpi
from openscilab.driver.simulated.scpi_shell import open_shell


@pytest.fixture(scope="module")
def direct():
    driver = open_shell("direct")
    yield driver
    driver.dispose()


@pytest.fixture
def bridge():
    driver = open_shell("bridge")
    yield driver
    driver.dispose()


def make_session(channels=(0, 1, 2, 3), analog=(), pre=1000, post=9000, rate=1_000_000) -> CaptureSession:
    session = CaptureSession(frequency=rate, pre_trigger_samples=pre, post_trigger_samples=post)
    session.capture_channels = [AnalyzerChannel(channel_number=number) for number in channels]
    session.analog_channels = [AnalogChannel(channel_number=number) for number in analog]
    session.trigger_type = TriggerType.IMMEDIATE
    return session


def run(driver, session, seconds=15.0):
    results = []
    done = threading.Event()
    assert driver.start_capture(session, lambda args: (results.append(args), done.set())) == CaptureError.NONE
    assert done.wait(seconds)
    return results[0]


# ------------------------------------------------------------------ parsing
def test_blocks_preamble_meta():
    assert scpi.block(b"abc") == b"#13abc"
    assert scpi.parse_block(b"#210" + bytes(10) + b"\n") == bytes(10)
    preamble = scpi.parse_preamble("0,2,1000,1,1.0e-06,-1.0e-04,0,0.04,0,128")
    assert preamble["points"] == 1000 and preamble["xorig"] == -1e-4 and preamble["yref"] == 128
    assert scpi.parse_meta("points=10;rate=1e6;CH1.yinc=0.04") == {"points": "10", "rate": "1e6", "CH1.yinc": "0.04"}
    assert scpi.command("la_channel", n=3, value="ON") == ":LA:DIGital:ENABle D3,ON"
    assert [scpi.depth_text(value) for value in scpi.MEMORY_DEPTHS] == [
        "1k", "10k", "100k", "1M", "5M", "10M", "25M", "50M", "100M", "125M", "250M", "500M"]
    assert [scpi.parse_depth(text) for text in ("1M", "10k", "1.0000E+06", "25000000", "AUTO")] == [
        1_000_000, 10_000, 1_000_000, 25_000_000, None]


def test_a_server_that_is_no_rigol_is_refused():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with connection:
                while True:
                    data = connection.recv(1024)
                    if not data:
                        break
                    if b"*IDN?" in data:
                        connection.sendall(b"ACME,THING,1,2\n")

    threading.Thread(target=answer, daemon=True).start()
    with pytest.raises(DeviceConnectionError, match="no Rigol"):
        discovery.open_device(f"rigol:127.0.0.1:{server.getsockname()[1]}")
    server.close()


def fake_scpi(answers: dict):
    """A SCPI server on a free port that answers one line after the other: ``answers`` maps a
    header to a text, bytes (sent as they are) or a function of the argument; a header ending in
    ``@<seconds>`` waits that long first. Returns (port, close)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)

    def serve(connection):
        with connection, connection.makefile("rb") as lines:
            for raw in lines:
                header, _, argument = raw.decode().strip().partition(" ")
                for key, answer in answers.items():
                    name, _, delay = key.partition("@")
                    if name == header:
                        time.sleep(float(delay or 0))
                        answer = answer(argument) if callable(answer) else answer
                        connection.sendall(answer if isinstance(answer, bytes) else answer.encode() + b"\n")
                        break

    def accept():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(connection,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return server.getsockname()[1], server.close


def test_a_late_answer_is_dropped_and_the_answers_stay_in_step():
    port, close = fake_scpi({"*IDN?": "RIGOL TECHNOLOGIES,DHO924S,DHO9X,00.01", ":ECHO?": lambda text: text,
                             ":SLOW?@0.4": "late", ":BLOCK?@0.4": scpi.block(b"a\nb\n") + b"\n"})
    connection = scpi.ScpiConnection("127.0.0.1", port, timeout=2.0)
    try:
        assert connection.query("*IDN?").startswith("RIGOL")
        with pytest.raises(scpi.ScpiTimeout):
            connection.query(":SLOW?", timeout=0.1)
        assert connection.query(":ECHO? 1") == "1"  # not "late"
        with pytest.raises(scpi.ScpiTimeout):
            connection.query_block(":BLOCK?", timeout=0.1)
        assert connection.query(":ECHO? 2") == "2"
        # the late answer is not there when the next query looks for it either: still out of step
        with pytest.raises(scpi.ScpiTimeout):
            connection.query(":SLOW?", timeout=0.1)
        with pytest.raises(scpi.ScpiTimeout):
            connection.query(":ECHO? 3", timeout=0.1)
        assert connection.query(":ECHO? 4") == "4"
        assert connection.query(":ECHO? 5") == "5"
    finally:
        connection.close()
        close()


def test_the_models_the_driver_knows():
    from openscilab.driver.rigoldho.driver import ModelFeatures, model_features

    assert model_features("DHO924S") == ModelFeatures(analog=4, digital=16, generator=True)
    assert model_features("DHO914") == ModelFeatures(analog=4, digital=16, generator=False)
    assert model_features("DHO802") == ModelFeatures(analog=2, digital=0, generator=False)
    assert model_features("DHO814").analog == 4
    assert model_features("DS1054Z") is None and model_features("DHO1204") is None

    def open_model(model):
        port, close = fake_scpi({":BRIDge:VERSion?": "OPENSCILAB_BRIDGE,0.1.0,1",
                                 "*IDN?": f"RIGOL TECHNOLOGIES,{model},DHOX0001,00.01.02"})
        try:
            return discovery.open_device(f"rigol:127.0.0.1:{port}")
        finally:
            close()

    driver = open_model("DHO914")  # no generator
    try:
        assert "AFG" not in driver.capabilities() and "ANALOG=4" in driver.capabilities()
        assert [type(facet).__name__ for facet in driver.instrument_facets()] == ["DhoSettings", "DhoCache"]
    finally:
        driver.dispose()
    with pytest.raises(DeviceConnectionError, match="DHO804 at .* has no logic analyzer"):
        open_model("DHO804")
    with pytest.raises(DeviceConnectionError, match="DS1054Z at .* is a model openSciLab does not know"):
        open_model("DS1054Z")


# ------------------------------------------------------------------ direct
def test_the_instrument_describes_itself(direct):
    assert direct.device_version.startswith("Rigol DHO924S")
    assert not direct.uses_bridge and "PROGRESSIVE" not in direct.capabilities()
    assert {"THRESHOLD", "ANALOG=4", "AFG"} <= direct.capabilities()
    # no pattern trigger (it takes CH1–CH4 only), no external trigger input; edges on D0–D15 and CH1–CH4
    assert direct.pattern_trigger_groups() == () and not direct.has_external_trigger()
    assert direct.analog_trigger_channels() == [0, 1, 2, 3]
    assert direct.analog_channel_names() == ["CH1", "CH2", "CH3", "CH4"]
    assert direct.memory_depth(16, 4) == 10_000_000


def test_a_direct_capture_digital_and_analog(direct):
    session = make_session(analog=(0,))
    session.threshold_voltage = 1.4
    result = run(direct, session)
    assert result.success, result.error
    assert len(session.capture_channels[0].samples) == 10_000
    assert session.pre_trigger_samples == 1000 and session.post_trigger_samples == 9000
    # D0 toggles every raw sample; captured with CH1, each is repeated once (LA_PERIOD_FACTOR) to
    # line it up with the analog channels' finer time axis, so every *pair* toggles
    pairs = session.capture_channels[0].samples[:10].reshape(-1, 2)
    assert np.all(pairs[:, 0] == pairs[:, 1]) and np.all(np.diff(pairs[:, 0].astype(int)) != 0)
    volts = session.analog_channels[0].raw * session.analog_channels[0].scale + session.analog_channels[0].offset
    assert volts.max() - volts.min() > 0.5 and -5 <= volts.min() and volts.max() <= 5  # the 1 kHz sine
    assert direct.shell.thresholds == {1: 1.4, 2: 1.4}


def test_an_edge_trigger(direct):
    session = make_session(channels=(3,), pre=200, post=800)
    session.trigger_type = TriggerType.EDGE
    session.trigger_channel = 3
    result = run(direct, session)
    assert result.success
    samples = session.capture_channels[0].samples
    assert samples[199] == 0 and samples[200] == 1


def test_settings_beyond_the_instrument_are_refused(direct):
    assert direct.start_capture(make_session(post=40_000_000)) == CaptureError.BAD_PARAMS
    assert direct.start_capture(make_session(channels=(16,))) == CaptureError.BAD_PARAMS
    session = make_session()
    session.trigger_type = TriggerType.SEQUENCE
    assert direct.start_capture(session) == CaptureError.UNSUPPORTED


def test_a_capture_without_trigger_can_be_stopped(direct):
    session = make_session(channels=(0,), pre=10, post=990, rate=1000)
    session.trigger_type = TriggerType.EDGE
    session.trigger_channel = 15
    results = []
    assert direct.start_capture(session, results.append) == CaptureError.NONE
    time.sleep(0.2)
    assert direct.stop_capture()
    time.sleep(0.2)
    assert results == [] and not direct.is_capturing


@pytest.fixture
def fresh_direct():
    driver = open_shell("direct")
    yield driver
    driver.dispose()


def test_a_capture_without_trigger_is_forced(fresh_direct):
    from openscilab.driver.simulated.circuit import Constant

    # :SINGle makes the sweep SINGLE: with a trigger set up that never comes (D15 stays low), the
    # capture waits until the driver forces it
    shell = fresh_direct.shell
    shell.device.circuit.drive("D15", Constant(level=0.0))
    shell.trigger.update(mode="EDGE", source="D15", sweep="NORMAL")
    result = run(fresh_direct, make_session(channels=(0,), pre=100, post=900), seconds=5.0)
    assert result.success, result.error
    assert shell.trigger["sweep"] == "SINGLE"
    assert len(result.session.capture_channels[0].samples) == 1000


def test_a_capture_is_done_only_after_the_instrument_took_it_up(fresh_direct, monkeypatch):
    from openscilab.driver.rigoldho import driver as module

    shell = fresh_direct.shell
    assert run(fresh_direct, make_session(channels=(0,), pre=100, post=900)).success
    # the instrument is slow to take the next capture up: its status stays STOP for a while
    shell.arm_delay = 0.3
    result = run(fresh_direct, make_session(channels=(0,), pre=1000, post=9000))
    assert result.success, result.error
    assert len(result.session.capture_channels[0].samples) == 10_000  # not the 1000 of the one before
    # one it never takes up fails instead of giving the samples of the one before
    shell.arm_delay = 0.0
    monkeypatch.setattr(module, "ARM_TIMEOUT_S", 0.3)
    original = shell._take_up
    shell._take_up = lambda generation: shell.errors.append('-221,"Settings conflict"')
    try:
        result = run(fresh_direct, make_session(channels=(0,), pre=100, post=900), seconds=5.0)
    finally:
        shell._take_up = original
    assert not result.success and "did not take the capture up" in result.error and "Settings conflict" in result.error


def test_the_memory_depth_is_set_by_name_and_read_back(fresh_direct, monkeypatch):
    shell = fresh_direct.shell
    sent = []
    handle = shell.handle
    monkeypatch.setattr(shell, "handle", lambda line: (sent.append(line), handle(line))[1])
    assert run(fresh_direct, make_session(channels=(0,), pre=1000, post=99_000)).success
    assert ":ACQuire:MDEPth 100k" in sent and shell.depth == 100_000
    # a depth the instrument does not take is skipped: the next larger one holds the capture
    monkeypatch.setattr(scpi, "MEMORY_DEPTHS", (1_000, 2_000, 10_000))
    sent.clear()
    assert run(fresh_direct, make_session(channels=(0,), pre=0, post=1500)).success
    assert ":ACQuire:MDEPth 2k" in sent and shell.depth == 10_000 and not shell.errors
    # more samples than the instrument holds: refused with the reason
    result = run(fresh_direct, make_session(channels=(0,), pre=0, post=20_000))
    assert not result.success and "20000 samples are more than the 10000" in result.error


def test_firmware_that_takes_a_tenth_of_the_memory_depth(fresh_direct, monkeypatch):
    """The modified firmware NK v0.5.2 of the DHO924S: "1M" gives 100 kpts, "500M" the 25 Mpts."""
    shell = fresh_direct.shell
    shell.firmware_depths = True
    sent = []
    handle = shell.handle
    monkeypatch.setattr(shell, "handle", lambda line: (sent.append(line), handle(line))[1])
    session = make_session(channels=(0,), pre=1000, post=99_000)
    assert run(fresh_direct, session).success
    assert shell.depth == 100_000 and ":ACQuire:MDEPth 1M" in sent
    assert len(session.capture_channels[0].samples) == 100_000
    sent.clear()  # (the next capture asks only for the depth it needs)
    assert run(fresh_direct, make_session(channels=(0,), pre=1000, post=99_000)).success
    assert [line for line in sent if line.startswith(":ACQuire:MDEPth ")] == [":ACQuire:MDEPth 1M"]


def test_a_small_capture_after_a_large_one(fresh_direct):
    """The read window of the large capture lies beyond the small one (the DHO refuses STARt and
    STOP then, and its preamble counts the window): every sample comes, no error is left."""
    shell = fresh_direct.shell
    assert run(fresh_direct, make_session(channels=(), analog=(0,), pre=0, post=600_000), seconds=60).success
    session = make_session(channels=(), analog=(0,), pre=100, post=900)
    assert run(fresh_direct, session).success
    assert len(session.analog_channels[0].raw) == 1000 and session.pre_trigger_samples == 100
    assert not shell.errors


def test_logic_channels_the_instrument_does_not_give_out(fresh_direct):
    """Without the logic probe (and with the modified firmware NK v0.5.2 also with it) the DHO
    refuses D0–D15 as :WAVeform:SOURce: the capture says so; the analog channels still come."""
    fresh_direct.shell.probe = False
    result = run(fresh_direct, make_session(channels=(0,), pre=100, post=900))
    assert not result.success and "does not give out D0" in result.error
    session = make_session(channels=(), analog=(0,), pre=100, post=900)
    assert run(fresh_direct, session).success


def test_a_logic_channel_is_read_as_words(fresh_direct):
    """A logic channel comes as 16 bit words: as BYTE the instrument gives only the upper byte, always 0.
    The capture reads WORD, its data are the levels, and the analog channels still come as BYTE."""
    shell = fresh_direct.shell
    session = make_session(channels=(0, 5), analog=(0,), pre=100, post=900)
    assert run(fresh_direct, session).success
    # D0 toggles every raw sample; captured with CH1 each is repeated once (LA_PERIOD_FACTOR)
    pairs = session.capture_channels[0].samples[:10].reshape(-1, 2)
    assert np.all(pairs[:, 0] == pairs[:, 1]) and np.all(np.diff(pairs[:, 0].astype(int)) != 0)
    assert set(np.unique(session.capture_channels[1].samples)) <= {0, 1}
    assert session.analog_channels[0].raw.max() > 0
    assert shell.wave_format == "BYTE"  # (the analog channel was read last)
    shell.wave_format = "BYTE"
    shell.source = "D0"
    shell.window = (1, 100)
    assert not shell.handle(":WAVeform:DATA?")[11:].strip(b"\n").strip(b"\x00")  # BYTE: the upper byte, 0


def test_the_sample_period_of_a_logic_channel_alone_is_twice_the_preambles(fresh_direct):
    """A logic channel's preamble is computed as if it were sampled at the analog rate (measured
    on the DHO924S): captured alone (no analog channel to line it up with), the rate of its
    capture is half the preamble's, the real one. Trigger position comes from the same (otherwise
    unmodified) preamble: its pre-trigger count ends up halved too (a known imprecision of a
    logic-channel-alone capture - the requested split of pre/post is not exactly honoured, unlike
    the total, :func:`same_timing`)."""
    fresh_direct.shell.la_rate = 4_000_000.0
    session = make_session(channels=(0,), pre=100, post=900)
    result = run(fresh_direct, session)
    assert result.success, result.error
    assert session.frequency == 2_000_000
    assert session.pre_trigger_samples == 50 and len(session.capture_channels[0].samples) == 1000


def test_a_logic_channel_captured_with_an_analog_one_is_not_halved(fresh_direct):
    """Captured with an analog channel, the rate of the capture is the analog one's and the
    requested pre/post trigger split is honoured exactly (unlike alone, above): the logic
    channel's samples were repeated (LA_PERIOD_FACTOR) to line up with it, not its preamble
    corrected, which would have made the two (deliberately, below) look out of step."""
    session = make_session(channels=(0,), analog=(0,), pre=100, post=900, rate=2_000_000)
    result = run(fresh_direct, session)
    assert result.success, result.error
    assert session.frequency == 2_000_000
    assert session.pre_trigger_samples == 100 and session.post_trigger_samples == 900
    assert len(session.capture_channels[0].samples) == len(session.analog_channels[0].raw) == 1000


def test_an_edge_on_an_analog_channel(fresh_direct, monkeypatch):
    shell = fresh_direct.shell
    sent = []
    handle = shell.handle
    monkeypatch.setattr(shell, "handle", lambda line: (sent.append(line), handle(line))[1])
    session = make_session(channels=(), analog=(0,), pre=100, post=900)
    session.trigger_type = TriggerType.EDGE
    session.trigger_analog_channel, session.trigger_level, session.trigger_inverted = 1, 1.5, True
    assert run(fresh_direct, session).success
    assert {":TRIGger:EDGE:SOURce CHANnel2", ":TRIGger:EDGE:SLOPe NEGative", ":TRIGger:EDGE:LEVel 1.5",
            ":CHANnel2:DISPlay ON"} <= set(sent)  # (the trigger's channel is on, though not captured)
    assert session.trigger_description() == "Negative at 1.5 V"
    # a level outside the channel's range on the screen: the instrument would take another one
    session = make_session(channels=(), analog=(0,), pre=100, post=900)
    session.trigger_type = TriggerType.EDGE
    session.trigger_analog_channel, session.trigger_level = 0, 7.0
    result = run(fresh_direct, session)
    assert not result.success and "CH1 cannot trigger at 7 V" in result.error and "took 4 V" in result.error
    session.trigger_analog_channel = 4
    assert fresh_direct.start_capture(session) == CaptureError.BAD_PARAMS


def test_a_logic_channel_triggers_an_analog_capture(fresh_direct, monkeypatch):
    shell = fresh_direct.shell
    sent = []
    handle = shell.handle
    monkeypatch.setattr(shell, "handle", lambda line: (sent.append(line), handle(line))[1])
    session = make_session(channels=(), analog=(0,), pre=100, post=900)
    session.trigger_type, session.trigger_channel = TriggerType.EDGE, 3
    assert run(fresh_direct, session).success
    assert {":LA:ENABle ON", ":LA:DIGital:ENABle D3,ON", ":TRIGger:EDGE:SOURce D3"} <= set(sent)


def test_the_analog_trigger_is_saved_with_the_capture():
    from openscilab.core.capture_io import session_from_dict, session_to_dict

    session = make_session(channels=(0,))
    session.trigger_analog_channel, session.trigger_level = 2, -0.25
    loaded = session_from_dict(session_to_dict(session))
    assert (loaded.trigger_analog_channel, loaded.trigger_level) == (2, -0.25)
    assert session_from_dict({}).trigger_analog_channel is None  # (files from before it)


def test_channels_sampled_at_different_rates_are_refused(fresh_direct):
    fresh_direct.shell.la_rate = 2_000_000.0
    result = run(fresh_direct, make_session(channels=(0,), analog=(0,), pre=100, post=900))
    assert not result.success and "CH1 has 1e+06 Sa/s" in result.error and "D0 2e+06 Sa/s" in result.error
    assert "capture them separately" in result.error


# ------------------------------------------------------------------ bridge
def test_a_bridge_capture_arrives_progressively(bridge):
    from openscilab.driver.rigoldho import fetcher

    assert bridge.uses_bridge and "PROGRESSIVE" in bridge.capabilities()
    original = fetcher.TILE_SAMPLES
    fetcher.TILE_SAMPLES = 4096
    try:
        tiles = []
        tile_done = threading.Event()
        bridge.add_capture_tile_handler(lambda args: (tiles.append(args), args.complete and tile_done.set()))
        session = make_session(analog=(1,), pre=2000, post=18000)
        result = run(bridge, session)
        assert result.success and session.progressive is not None
        overview = session.progressive.overviews[("d", 0)]
        low, high = overview.envelope(0, 20000, 10)
        assert low.min() == 0 and high.max() == 1  # shown at once from the overview
        session.progressive.prioritize(16000, 17000)
        assert tile_done.wait(15)
        # 20000 samples asked for: the smallest memory depth that holds them is 100k points
        assert session.pre_trigger_samples + session.post_trigger_samples == 100_000
        assert session.frequency == 1_000_000 and session.pre_trigger_samples == 2000
        assert session.progressive.complete and len(tiles) == session.progressive.tile_count
        assert np.all(np.diff(session.capture_channels[0].samples[:20].astype(int)) != 0)  # D0 toggles
        volts = session.analog_channels[0].raw * session.analog_channels[0].scale + session.analog_channels[0].offset
        assert 1.0 < volts.max() - volts.min() < 10.0  # the 10 kHz square of CH2
        assert bridge.connection.query(":BRIDge:CACHe:LIST?").startswith("1,")
    finally:
        fetcher.TILE_SAMPLES = original


def test_an_interrupted_transfer_resumes(bridge):
    from openscilab.driver.rigoldho import fetcher

    original = fetcher.TILE_SAMPLES
    fetcher.TILE_SAMPLES = 1000
    try:
        session = make_session(channels=(0,), pre=0, post=20000)
        stops = []

        def stop_after_two(args):
            stops.append(args)
            if len(stops) == 2:
                bridge.stop_transfer()

        bridge.add_capture_tile_handler(stop_after_two)
        assert run(bridge, session).success
        deadline = time.monotonic() + 5
        while (len(stops) < 2 or bridge._fetcher._thread.is_alive()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not session.progressive.complete and session.progressive.interrupted
        bridge.remove_capture_tile_handler(stop_after_two)
        assert bridge.resume_transfer(session)
        deadline = time.monotonic() + 10
        while not session.progressive.complete and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session.progressive.complete
    finally:
        fetcher.TILE_SAMPLES = original


def test_the_bridge_reads_only_the_channels_of_the_capture(bridge):
    from openscilab.driver.rigoldho.fetcher import channel_list

    assert channel_list([0, 1, 2, 3, 7, 9, 10], [1]) == "D0-D3,D7,D9-D10,CH2"
    assert channel_list([], [0, 3]) == "CH1,CH4"
    # the other logic channels are off on the instrument: the bridge could not read them
    session = make_session(channels=(3, 0, 5), pre=0, post=1000)
    assert run(bridge, session).success
    assert bridge.shell.snapshots[-1].meta["channels"] == "D0,D3,D5"


def test_mixed_channels_at_different_rates_are_refused_by_the_bridge(bridge):
    bridge.shell.la_rate = 2_000_000.0
    result = run(bridge, make_session(channels=(0,), analog=(0,), pre=100, post=900))
    assert not result.success and "sampled at different rates" in result.error


def test_a_capture_stopped_while_the_bridge_reads_it_is_no_capture(bridge):
    shell = bridge.shell
    shell.snapshot_delay = 2.0
    results = []
    assert bridge.start_capture(make_session(channels=(0,), pre=0, post=1000), results.append) == CaptureError.NONE
    assert shell.snapshot_started.wait(5)
    started = time.monotonic()
    assert bridge.stop_capture()
    assert time.monotonic() - started < 1.5  # not until the bridge is done
    assert not bridge.is_capturing
    assert bridge.start_capture(make_session(channels=(0,), pre=0, post=1000)) == CaptureError.BUSY
    bridge._thread.join(10)
    assert results == []  # stopped: no capture reported
    shell.snapshot_delay = 0.0
    assert run(bridge, make_session(channels=(0,), pre=0, post=1000)).success


# ---------------------------------------------------------------- generator
def test_the_generator_drives_ch1(bridge):
    instrument = Instrument.from_driver(bridge, uri=bridge.address)
    generator = instrument.generator
    assert [output.name for output in generator.outputs()] == ["GI"]
    generator.start("GI", waves.standard("square", 5000.0, amplitude=2.0, offset=0.5, duty=0.25))
    shell = bridge.shell
    assert shell.instrument.generator.running("GI")
    played = shell.instrument.generator._running["GI"][0]
    assert (played.kind, played.frequency, played.amplitude, played.offset, played.duty) == ("square", 5000.0, 2.0, 0.5, 0.25)
    generator.start("GI", waves.standard("triangle", 2000.0, amplitude=1.0))
    played = shell.instrument.generator._running["GI"][0]
    assert (played.kind, played.frequency, played.amplitude) == ("triangle", 2000.0, 1.0)  # (RAMP, symmetry 50)
    generator.start("GI", waves.standard("ramp", 2000.0))
    assert shell.instrument.generator._running["GI"][0].kind == "ramp"  # (RAMP, symmetry 100)
    # a pulse is a square wave from the offset to offset + amplitude
    generator.start("GI", waves.standard("pulse", 1000.0, amplitude=1.0, offset=0.0, duty=0.2))
    played = shell.instrument.generator._running["GI"][0]
    assert (played.kind, played.amplitude, played.offset, played.duty) == ("square", 0.5, 0.5, 0.2)
    generator.stop("GI")
    assert not shell.instrument.generator.running("GI")
    # what the instrument cannot play is refused before anything is sent; the output stays off
    for waveform, reason in [
            (waves.standard("sine", 1000.0, modulation="sweep", sweep_start=100, sweep_stop=2000), "cannot sweep"),
            (waves.from_points([0.0, 1.0, -1.0, 0.5], frequency=200.0), "cannot play arbitrary"),
            (waves.standard("sine", 50e6), "above the 25 MHz"),
            (waves.standard("ramp", 200e3), "above the 150 kHz of ramp"),
            (waves.standard("square", 1000.0, duty=0.01), "outside the 5 to 95 %"),
            (waves.standard("sine", 1000.0, amplitude=2.0, offset=1.0), "outside the -2.5 V to 2.5 V")]:
        with pytest.raises(InstrumentError, match=reason):
            generator.start("GI", waveform)
        assert not shell.instrument.generator.running("GI")


def test_a_refused_waveform_leaves_the_output_off(bridge):
    from openscilab.driver.rigoldho import generator as module

    generator = Instrument.from_driver(bridge, uri=bridge.address).generator
    bridge.shell.errors.append('-100,"Command err"')  # (an earlier error is not the waveform's)
    generator.start("GI", waves.standard("sine", 1000.0))
    assert bridge.shell.instrument.generator.running("GI")
    generator.stop("GI")
    original = module.FUNCTIONS
    module.FUNCTIONS = dict(original, sine=("SINE_UNKNOWN", None))
    try:
        with pytest.raises(InstrumentError, match="refused the waveform"):
            generator.start("GI", waves.standard("sine", 1000.0))
    finally:
        module.FUNCTIONS = original
    assert not bridge.shell.instrument.generator.running("GI")


# ----------------------------------------------------------- beacon, probe
def test_beacons():
    item = beacon.parse("OPENSCILAB_BRIDGE 0.1.0 5560 DHO924S DHO9A123", "192.168.1.20")
    assert (item.host, item.port, item.model, item.serial) == ("192.168.1.20", 5560, "DHO924S", "DHO9A123")
    assert "DHO924S at 192.168.1.20" in item.label
    assert beacon.parse("SOMETHING ELSE", "x") is None


def test_the_probe_tool(direct, tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "tools" / "dho_la_probe.py"
    spec = importlib.util.spec_from_file_location("dho_la_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "QUERIES", ["*IDN?", ":TRIGger:STATus?", ":ACQuire:SRATe?"])
    monkeypatch.setattr(module, "SOURCES", ["D0", "CHANnel1"])
    assert run(direct, make_session(analog=(0,))).success
    port = direct.shell.port
    assert module.main(["127.0.0.1", "--port", str(port), "--points", "1000", "--speed",
                        "--speed-bytes", "20000", "--out", str(tmp_path)]) == 0
    folder = next(tmp_path.iterdir())
    assert (folder / "D0.bin").stat().st_size == 1000 and (folder / "probe.json").exists()
    assert "DHO924S" in (folder / "probe.json").read_text()


def test_discovery_and_the_device_list():
    from openscilab.ui.devices.rigoldho import RigolBackend

    labels = [entry.label for entry in RigolBackend().manual_entries()]
    assert "Rigol oscilloscope by address..." in labels and any("with bridge app" in label for label in labels)
    with pytest.raises(DeviceConnectionError):
        discovery.open_device("rigol-sim:nothing")
    driver = discovery.open_device("rigol-sim:bridge")
    try:
        assert driver.address == "rigol-sim:bridge"
    finally:
        driver.dispose()


def test_the_device_card_lists_the_bridge_cache(shell):
    driver = open_shell("bridge")
    instrument = Instrument.from_driver(driver, uri=driver.address)
    shell.hub.add(instrument)
    card = shell.open_device_card(instrument)
    tabs = [card.tabs.tabText(index) for index in range(card.tabs.count())]
    assert "Cache" in tabs
    assert run(driver, make_session(channels=(0,), pre=0, post=1000)).success
    card.cache_panel.refresh()
    assert card.cache_panel.table.rowCount() == 1 and card.cache_panel.table.item(0, 2).text() == "1,000"
    card.cache_panel.table.selectRow(0)
    card.cache_panel.delete_selected()
    assert card.cache_panel.table.rowCount() == 0
    direct_driver = open_shell("direct")
    try:
        assert Instrument.from_driver(direct_driver).facet(type(card.cache_panel.cache).__mro__[1]) is None
    finally:
        direct_driver.dispose()


def test_a_closed_connection_is_an_error_of_the_connection():
    """A thread still reading when the connection was closed (dispose during a transfer) gets a
    ScpiError, not a raw OSError that ends the thread with a traceback."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        transport = scpi.TcpTransport("127.0.0.1", server.getsockname()[1], 1.0)
        transport.close()
        with pytest.raises(scpi.ScpiError):
            transport.receive(0.1)
        with pytest.raises(scpi.ScpiError):
            transport.send(b"*IDN?\n", 0.1)
    finally:
        server.close()
