"""The Arduino driver against the simulated Arduino that speaks the firmware protocol
(``arduino-sim:<profile>``): description, facets, captures, watchdog, reserved pins, faults."""

from __future__ import annotations

import struct
import threading
import time

import numpy as np
import pytest

from openscilab.core import waveform as waves
from openscilab.core.instrument import AnalogInFacet, AnalogOutFacet, Instrument, InstrumentError
from openscilab.driver import discovery, ports
from openscilab.driver.arduino import driver as arduino_driver
from openscilab.driver.arduino import protocol
from openscilab.driver.arduino.link import ArduinoError, Connection, LinkError, pipe
from openscilab.driver.base import ACQUISITION_STREAM, CaptureError, DeviceConnectionError
from openscilab.driver.models import AnalogChannel, AnalyzerChannel, CaptureSession, EdgeKind, TriggerType
from openscilab.driver.simulated.arduino_shell import open_shell


@pytest.fixture
def uno():
    driver = open_shell("uno")
    instrument = Instrument.from_driver(driver, uri=driver.address)
    yield instrument
    instrument.close()


@pytest.fixture
def uno_r4():
    driver = open_shell("uno_r4")
    instrument = Instrument.from_driver(driver, uri=driver.address)
    yield instrument
    instrument.close()


def wait_for(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def capture(driver, session, seconds: float = 5.0):
    results = []
    done = threading.Event()
    assert driver.start_capture(session, lambda args: (results.append(args), done.set())) == CaptureError.NONE
    assert done.wait(seconds)
    return results[0]


def session_of(channels, rate=100_000, pre=0, post=500) -> CaptureSession:
    session = CaptureSession(frequency=rate, pre_trigger_samples=pre, post_trigger_samples=post)
    session.capture_channels = [AnalyzerChannel(channel_number=number) for number in channels]
    session.trigger_type = TriggerType.IMMEDIATE
    return session


# ------------------------------------------------------------------ the board
def test_the_board_describes_itself(uno):
    driver = uno.capture.driver
    assert driver.address == "arduino-sim:uno"
    assert driver.channel_count == 12 and driver.channel_names()[:2] == ["D2", "D3"]
    assert driver.max_frequency == 100_000 and driver.analog_channel_count == 6  # (the firmware's limits)
    assert driver.memory_depth(8, 0) == 448
    assert driver.analog_channel_names()[0] == "A0"
    assert "PATTERN_GEN=50000,6" in uno.capabilities() and "STREAM=11520" in uno.capabilities()
    assert {type(facet).__name__ for facet in uno.facets()} >= {
        "ArduinoGpio", "ArduinoMonitor", "ArduinoAnalogIn", "ArduinoGenerator"}
    pins = {pin.name: pin for pin in uno.pins()}
    assert pins["D0"].reserved == "USB serial (RX)" and pins["D2"].channel == 0
    assert pins["A0"].analog_channel == 0 and pins["D2"].logic_level == 5.0


def test_discovery_opens_the_shell():
    driver = discovery.open_device("arduino-sim:uno_r4")
    try:
        assert "DAC" in driver.capabilities()
    finally:
        driver.dispose()
    with pytest.raises(DeviceConnectionError):
        discovery.open_device("arduino-sim:no-such-board")


def test_arduino_ports_are_known_by_their_usb_ids(monkeypatch):
    class Port:
        def __init__(self, device, vid, pid, product=None):
            self.device, self.vid, self.pid, self.product, self.serial_number = device, vid, pid, product, None

    found = [Port("/dev/a", 0x2341, 0x0043, "Arduino Uno"), Port("/dev/b", 0x1A86, 0x7523),
             Port("/dev/c", 0x10C4, 0xEA60), Port("/dev/d", 0x1209, 0x3020), Port("/dev/e", 0x2341, 0x0069)]
    monkeypatch.setattr(ports, "comports", lambda: found)
    arduinos = {port.port_name: port for port in ports.detect_arduinos()}
    assert set(arduinos) == {"/dev/a", "/dev/b", "/dev/c", "/dev/e"}
    assert arduinos["/dev/a"].baud == 115200 and arduinos["/dev/c"].baud == 2_000_000
    assert arduinos["/dev/e"].baud == 2_000_000 and arduinos["/dev/a"].description == "Arduino Uno"


def test_usb_serial_chips_of_either_rate_are_tried_at_both(monkeypatch):
    # the CH340 is on Uno/Nano clones (115200) and ESP32 boards (2 Mbaud), the CH9102 on ESP32 boards
    class Port:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid, self.product, self.serial_number = device, vid, pid, None, None

    monkeypatch.setattr(ports, "comports", lambda: [Port("/dev/ch340", 0x1A86, 0x7523),
                                                    Port("/dev/ch9102", 0x1A86, 0x55D4)])
    arduinos = {port.port_name: port for port in ports.detect_arduinos()}
    assert arduinos["/dev/ch340"].bauds == (115200, 2_000_000) and arduinos["/dev/ch340"].baud == 115200
    assert arduinos["/dev/ch9102"].bauds == (2_000_000, 115200)

    opened = []

    class Link:
        def __init__(self, port, baud):
            opened.append(baud)
            self.baud = baud

    def board(link, address="", name=""):
        if link.baud != 2_000_000:
            raise DeviceConnectionError("no openSciLab Arduino firmware answers")
        return ("driver", link.baud)

    monkeypatch.setattr(arduino_driver, "SerialLink", Link)
    monkeypatch.setattr(arduino_driver, "ArduinoDriver", board)
    assert arduino_driver.open_serial("/dev/ch340") == ("driver", 2_000_000)  # an ESP32 behind a CH340
    assert opened == [115200, 2_000_000]


# ---------------------------------------------------------------------- GPIO
def test_outputs_inputs_pwm_pulse(uno):
    gpio = uno.gpio
    gpio.write("D7", 1)
    time.sleep(0.01)
    assert gpio.read_many(["D7", "D3"]) == {"D7": 1, "D3": 1}  # D7 is wired to D3
    gpio.write("D7", 0)
    time.sleep(0.01)
    assert gpio.read("D3") == 0
    assert gpio.pwm("D9", 1000.0, 0.5) == pytest.approx(1000.0)
    gpio.pulse("D7", 0.0005)
    gpio.set_mode("D4", "input_pullup")
    assert gpio.read("D4") == 1
    gpio.safe_all()


def test_reserved_pins_and_missing_capabilities(uno):
    with pytest.raises(InstrumentError, match="reserved"):
        uno.gpio.write("D0", 1)
    with pytest.raises(InstrumentError, match="pwm"):
        uno.gpio.pwm("D7", 1000, 0.5)


def test_analog_read(uno):
    uno.gpio.pwm("D9", 1000.0, 1.0)
    value = uno.facet(AnalogInFacet).read(["A0", "A1"])
    assert set(value) == {"A0", "A1"} and 0.0 <= value["A1"] <= 5.0


def test_monitor_reports_arrive(uno):
    states = []
    uno.monitor.on_state(states.append)
    uno.monitor.start(50.0, ["D7", "D3"], analog=("A0",))
    uno.gpio.write("D7", 1)
    wait_for(lambda: any(state.digital.get("D3") == 1 for state in states))
    assert "A0" in states[-1].analog
    uno.monitor.stop()


def test_the_heartbeat_keeps_outputs_and_the_watchdog_releases_them(uno):
    gpio = uno.gpio
    gpio.write("D7", 1)
    time.sleep(1.5)  # longer than the watchdog: the driver's heartbeats keep the output
    assert gpio.read("D3") == 1
    gpio.keepalive = False
    time.sleep(1.6)  # no frame for longer than the watchdog (a read would count as contact)
    assert gpio.read("D3") == 0


# ------------------------------------------------------------------- captures
def test_a_buffer_capture_with_an_edge_trigger(uno):
    driver = uno.capture.driver
    session = session_of([1, 2], rate=100_000, pre=40, post=400)
    session.trigger_type = TriggerType.EDGE
    session.trigger_channel = 1  # D3
    timer = threading.Timer(0.05, lambda: uno.gpio.write("D7", 1))
    timer.start()
    result = capture(driver, session)
    timer.join()
    assert result.success
    samples = session.capture_channels[0].samples
    assert len(samples) == 440 and samples[:30].max() == 0 and samples[-10:].min() == 1


def test_capture_pins_are_busy_while_capturing(uno):
    driver = uno.capture.driver
    session = session_of([5], rate=400, post=400)  # one second
    session.trigger_type = TriggerType.IMMEDIATE
    done = threading.Event()
    assert driver.start_capture(session, lambda args: done.set()) == CaptureError.NONE
    assert uno.gpio.pin("D7").reserved == "LA channel 5 while capturing"
    with pytest.raises(InstrumentError):
        uno.gpio.write("D7", 1)
    assert driver.start_capture(session_of([1])) == CaptureError.BUSY
    assert done.wait(5)


def test_a_stream_with_analog_until_stopped(uno):
    driver = uno.capture.driver
    session = session_of([0, 1], rate=2000, post=1_000_000)
    session.acquisition_mode = ACQUISITION_STREAM
    session.continuous = True
    session.analog_channels = [AnalogChannel(channel_number=0)]
    progress = []
    driver.add_capture_progress_handler(progress.append)
    results = []
    done = threading.Event()
    assert driver.start_capture(session, lambda args: (results.append(args), done.set())) == CaptureError.NONE
    wait_for(lambda: progress and progress[-1].sample_count > 200)
    driver.stop_capture()
    assert done.wait(5) and results[0].success
    count = len(session.capture_channels[0].samples)
    assert count > 200 and session.post_trigger_samples == count
    assert len(session.analog_channels[0].raw) == count
    assert session.analog_channels[0].scale == pytest.approx(5.0 / 1024)


def test_live_state(uno):
    driver = uno.capture.driver
    uno.gpio.pwm("D9", 1000.0, 0.5)  # the clock
    session = session_of([0, 7], post=100)
    session.acquisition_mode = ACQUISITION_STREAM
    session.clock_channel, session.clock_edge = 7, EdgeKind.RISING
    result = capture(driver, session)
    assert result.success and len(session.capture_channels[0].samples) == 100
    assert session.state_times is not None and len(session.state_times) == 100
    assert np.diff(session.state_times).mean() == pytest.approx(1000.0, rel=0.05)  # µs per edge


def test_more_samples_than_the_board_holds_are_refused(uno):
    driver = uno.capture.driver
    assert driver.start_capture(session_of([0], post=100_000)) == CaptureError.BAD_PARAMS
    assert driver.start_capture(session_of([40])) == CaptureError.BAD_PARAMS


def test_the_buffer_depth_follows_the_highest_pin(uno):
    # the firmware stores 1 to 4 bytes a sample by the highest pin of the mask: D2-D7 (pins 2-7)
    # fit one byte, D8 (pin 8, channel 6) needs two
    driver = uno.capture.driver
    assert [arduino_driver.buffer_width(mask) for mask in (0xFF, 1 << 8, 1 << 23, 1 << 31)] == [1, 2, 3, 4]
    assert driver.get_limits([0, 5]).max_post_samples == 448
    assert driver.get_limits([0, 6]).max_post_samples == 224
    assert driver.start_capture(session_of([6], post=300)) == CaptureError.BAD_PARAMS
    result = capture(driver, session_of([6], post=224))
    assert result.success and len(result.session.capture_channels[0].samples) == 224
    # the simulated board refuses it as the firmware does
    setup = struct.pack("<BIIHIIBIIBB", protocol.MODE_BUFFER, 1000, 1 << 8, 0, 300, 0, 0, 0, 0, 0xFF, 0)
    with pytest.raises(ArduinoError) as refused:
        driver.connection.request(protocol.CAPTURE_SETUP, setup)
    assert refused.value.code == protocol.ERR_OVERFLOW
    # buffer captures read no analog inputs
    session = session_of([0], post=100)
    session.analog_channels = [AnalogChannel(channel_number=0)]
    assert driver.start_capture(session) == CaptureError.UNSUPPORTED


def test_an_immediate_buffer_capture_has_no_pre_trigger_samples(uno):
    driver = uno.capture.driver
    session = session_of([0], rate=100_000, pre=40, post=400)
    result = capture(driver, session)
    assert result.success and len(session.capture_channels[0].samples) == 440
    assert session.pre_trigger_samples == 0 and session.post_trigger_samples == 440


def test_streams_stay_below_the_rate_of_the_firmware(uno_r4):
    # the R4's line carries 500 kB/s, its sampling interrupt runs at 100 kHz at most
    driver = uno_r4.capture.driver
    assert driver.info["stream_rate"] == "100000"
    assert driver.max_frequency_for([0], ACQUISITION_STREAM) == 100_000
    session = session_of([0], rate=200_000, post=1000)
    session.acquisition_mode = ACQUISITION_STREAM
    assert driver.start_capture(session) == CaptureError.BAD_PARAMS
    del driver.info["stream_rate"]  # firmware that does not say
    assert driver.max_frequency_for([0], ACQUISITION_STREAM) == arduino_driver.DEFAULT_STREAM_RATE


def _drop_data_frames(shell, which):
    """The simulated board's DATA events ``which`` (counted from 0) get lost on the line."""
    sent = shell._event
    count = [0]

    def event(code, data=b""):
        if code == protocol.EVENT_DATA:
            count[0] += 1
            if count[0] - 1 in which:
                return
        sent(code, data)

    shell._event = event
    return count


def test_a_lost_data_frame_fails_the_capture(uno):
    driver = uno.capture.driver
    uno.gpio.pwm("D9", 20_000.0, 0.5)  # a change every 2-3 samples: three DATA frames of runs
    count = _drop_data_frames(driver.shell, {1})
    result = capture(driver, session_of([7], rate=100_000, post=224))
    assert count[0] >= 3  # (the next frame starts after the gap)
    assert not result.success and "lost" in result.error
    # the last frames: only DONE says how many samples there were
    _drop_data_frames(driver.shell, set(range(1, 100)))
    result = capture(driver, session_of([7], rate=100_000, post=224))
    assert not result.success and "lost" in result.error
    driver.shell._event = type(driver.shell)._event.__get__(driver.shell)
    result = capture(driver, session_of([7], rate=100_000, post=224))
    assert result.success and len(result.session.capture_channels[0].samples) == 224


def test_a_capture_whose_abort_was_not_answered_is_aborted_again(uno, monkeypatch):
    # the board did not answer CAPTURE_ABORT in time: the driver forgot the capture, the board
    # still ran it and answered the next CAPTURE_SETUP busy
    monkeypatch.setattr(arduino_driver, "ABORT_TIMEOUT_S", 0.3)
    driver = uno.capture.driver
    shell = driver.shell
    handle = shell._handle
    dropped = []

    def deaf_to_one_abort(frame):
        if frame.command == protocol.CAPTURE_ABORT and not dropped:
            dropped.append(frame)
            return
        handle(frame)

    shell._handle = deaf_to_one_abort
    assert driver.start_capture(session_of([0], rate=100, post=200), lambda args: None) == CaptureError.NONE
    assert driver.stop_capture() and dropped and not driver.is_capturing
    result = capture(driver, session_of([1], post=400))
    assert result.success and len(result.session.capture_channels[0].samples) == 400


# ------------------------------------------------------------------ generator
def test_pattern_square_and_transmit(uno):
    generator = uno.generator
    outputs = {output.name: output for output in generator.outputs()}
    assert outputs["PATTERN"].max_rate == 50_000 and "D2" in outputs["PATTERN"].pins
    # the Uno holds 32 runs (HELLO gen_runs)
    assert outputs["PATTERN"].max_points == 32 * 0xFFFF
    with pytest.raises(InstrumentError, match="32"):
        generator.start("PATTERN", waves.pattern({"D7": [0, 1] * 50}, rate=10_000))
    generator.start("PATTERN", waves.pattern({"D7": [0, 1] * 16}, rate=10_000))
    assert generator.running("PATTERN")
    generator.stop("PATTERN")
    # the square wave goes to the pin with a timer of its own (PIN capability SQUARE), not to the
    # last PWM pin (D11: the core's fixed 490 Hz)
    assert outputs["SQUARE"].pins == ("D9",)
    assert uno.gpio.pin("D9").capabilities >= {"PWM", "SQUARE"} and "SQUARE" not in uno.gpio.pin("D11").capabilities
    generator.start("SQUARE", waves.standard("square", 1000.0, amplitude=2.5, offset=2.5))
    assert uno.capture.driver.shell.instrument.generator.running("SQUARE")
    generator.stop("SQUARE")
    assert generator.transmits("uart") and generator.transmits("i2c")
    generator.transmit("uart", b"hello", {"tx": "D4"}, baud=9600)
    assert generator.transmit("i2c", b"\x00", {"sda": "A4", "scl": "A5"}, address=0x48, read=2) == bytes.fromhex("1900")
    with pytest.raises(InstrumentError, match="0x49"):
        generator.transmit("i2c", b"\x00", {"sda": "A4", "scl": "A5"}, address=0x49)


def test_the_dac_of_the_uno_r4():
    driver = open_shell("uno_r4")
    instrument = Instrument.from_driver(driver, uri=driver.address)
    try:
        instrument.facet(AnalogOutFacet).set_voltage("A0", 1.5)
        outputs = {output.name: output for output in instrument.generator.outputs()}
        assert "DAC" in outputs
        instrument.generator.start("DAC", waves.standard("sine", 100.0, amplitude=1.0, offset=2.0))
        assert instrument.generator.running("DAC")
    finally:
        instrument.close()


def test_the_arbitrary_waveform_takes_as_many_points_as_the_dac_plays(uno_r4):
    # 100 000 DAC points a second (HELLO arb_rate): 1 kHz is a period of 100 points, not 1000
    driver = uno_r4.capture.driver
    output = uno_r4.generator.output("DAC")
    assert output.max_rate == 100_000 and output.max_points == 2048 and output.max_frequency == 5_000
    started = []
    request = driver.connection.request

    def watch(command, data=b"", timeout=3.0):
        if command == protocol.ARB_START:
            started.append(struct.unpack("<fHB", data))
        return request(command, data, timeout=timeout)

    driver.connection.request = watch
    uno_r4.generator.start("DAC", waves.standard("sine", 1000.0, amplitude=1.0, offset=2.0))
    assert uno_r4.generator.running("DAC")
    assert started[-1][:2] == (pytest.approx(100_000), 100)
    uno_r4.generator.stop("DAC")
    uno_r4.generator.start("DAC", waves.standard("sine", 10.0, amplitude=1.0, offset=2.0))
    assert started[-1][1] == 1000  # slow ones keep 1000 points


def test_slow_transmissions_wait_long_enough(uno, monkeypatch):
    # the board answers TX_UART when the bytes are out: 234 bytes at 110 baud take 21 s
    driver = uno.capture.driver
    timeouts = []

    def request(command, data=b"", timeout=3.0):
        timeouts.append((command, timeout))
        return b""

    monkeypatch.setattr(driver.connection, "request", request)
    uno.generator.transmit("uart", bytes(234), {"tx": "D4"}, baud=110)
    assert timeouts[-1][0] == protocol.TX_UART and timeouts[-1][1] > 234 * 10 / 110
    uno.generator.transmit("spi", bytes(234), {"sck": "D4", "mosi": "D5"}, frequency=100)
    assert timeouts[-1][0] == protocol.TX_SPI and timeouts[-1][1] > 234 * 8 / 100
    uno.generator.transmit("uart", b"hi", {"tx": "D4"}, baud=115200)
    assert timeouts[-1][1] < 4


# --------------------------------------------------------------------- faults
def test_a_board_that_stops_answering(uno):
    driver = uno.capture.driver
    driver.shell.device.inject("disconnect")
    with pytest.raises(InstrumentError, match="does not answer"):
        uno.gpio.read("D3")


def test_a_lost_connection_ends_a_running_capture(uno):
    driver = uno.capture.driver
    session = session_of([0], rate=100, post=200)  # two seconds (the longest the firmware records)
    results = []
    done = threading.Event()
    assert driver.start_capture(session, lambda args: (results.append(args), done.set())) == CaptureError.NONE
    driver.shell.close()
    assert done.wait(5) and not results[0].success and "lost" in results[0].error


def test_no_firmware_answers():
    host, _board = pipe()
    connection = Connection(host)
    try:
        with pytest.raises(LinkError, match="no openSciLab Arduino firmware"):
            connection.hello(attempts_until=0.6)
    finally:
        connection.close()


def test_error_frames_carry_code_and_text():
    host, board = pipe()
    connection = Connection(host)
    decoder = protocol.Decoder()

    def answer():
        while True:
            frames = decoder.feed(board.read(1.0))
            if frames:
                frame = frames[0]
                board.write(protocol.encode(protocol.ERROR, frame.sequence,
                                            bytes([frame.command, protocol.ERR_RESERVED]) + b"D0 is USB"))
                return

    threading.Thread(target=answer, daemon=True).start()
    from openscilab.driver.arduino.link import ArduinoError

    with pytest.raises(ArduinoError, match="D0 is USB") as raised:
        connection.request(protocol.WRITE, b"\x00" * 8)
    assert raised.value.code == protocol.ERR_RESERVED
    connection.close()


def test_the_device_list_offers_simulated_arduinos(monkeypatch):
    from openscilab.ui.devices.arduino import ArduinoBackend

    backend = ArduinoBackend()
    entries = {entry.value: entry for entry in backend.manual_entries()}
    assert {"uno", "uno_r4"} <= set(entries) and "pico" not in entries
    assert "firmware protocol" in entries["uno"].label
    monkeypatch.setattr(ports, "comports", lambda: [])
    assert backend.detected() == []
