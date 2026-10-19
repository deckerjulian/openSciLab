"""Standard profiles and the profiles folder: the standard profiles are copied there once and
loaded without importing, so is every file put there; a profile from the folder changes in its own
file; profiles that need more channels than a device has are off, the others are fitted to it."""

from __future__ import annotations

import importlib.util
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from openscilab.core.profiles import (
    INSTALLED_FILE,
    Profile,
    ProfileStore,
    fit_profile,
    fit_session,
    install_standard_profiles,
    profile_problem,
    profiles_directory,
    read_profiles_file,
    standard_directory,
    standard_files,
    write_profiles_file,
)
from openscilab.driver.models import AnalyzerChannel, CaptureSession, EdgeKind, TriggerType
from openscilab.driver.simulated import open_simulated
from openscilab.sigrok.provider import DecoderRegistry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUSES = {"uart-9600", "uart-115200", "i2c-100k", "i2c-400k", "spi-mode0", "onewire", "can-500k", "pwm", "ws2812",
         "i2s", "parallel-8bit", "swd", "jtag", "ir-nec", "usb-low-speed", "usb-full-speed", "midi", "dmx512", "lin"}


def driver(name: str):
    return open_simulated(name, fast=True).capture.driver


def standard(name: str) -> Profile:
    return read_profiles_file(os.path.join(standard_directory(), name + ".json"))[0]


# ------------------------------------------------------------ the profiles
def test_the_standard_profiles_cover_the_buses_and_the_c64():
    names = {os.path.splitext(name)[0] for name in standard_files()}
    assert BUSES | {"c64-logicanalyzer-v2"} <= names


@pytest.mark.parametrize("name", sorted(BUSES | {"c64-logicanalyzer-v2"}))
def test_a_standard_profile_is_complete(name):
    profile = standard(name)
    registry = DecoderRegistry()
    captured = {channel.channel_number for channel in profile.capture_settings.capture_channels}
    assert profile.notes and profile.decoder_configuration
    for item in profile.decoder_configuration:
        info = registry.get(item["decoder_id"])
        assert info is not None, item["decoder_id"]
        indexes = {channel.index for channel in info.channels}
        assert {int(key) for key in item["channel_map"]} <= indexes
        assert set(item["channel_map"].values()) <= captured  # every decoder channel is captured
        assert set(item["options"]) <= {option.id for option in info.options}
        if item["parent"] is not None:
            assert item["channel_map"] == {} and 0 <= item["parent"] < len(profile.decoder_configuration)


def test_the_files_are_what_the_tool_makes():
    spec = importlib.util.spec_from_file_location("make_standard_profiles",
                                                  os.path.join(ROOT, "tools", "make_standard_profiles.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    registry = DecoderRegistry()
    for entry in tool.PROFILES:
        with open(os.path.join(standard_directory(), entry[0] + ".json"), encoding="utf-8") as handle:
            assert json.load(handle) == {"Profiles": [tool.make(registry, entry).to_dict()]}, entry[0]


# ------------------------------------------------------------- the folder
def test_the_standard_profiles_are_copied_once(tmp_path):
    folder = str(tmp_path)
    copied = install_standard_profiles(folder)
    assert sorted(copied) == standard_files()
    os.remove(os.path.join(folder, "pwm.json"))  # deleted: it stays deleted
    assert install_standard_profiles(folder) == []
    assert not os.path.exists(os.path.join(folder, "pwm.json"))
    with open(os.path.join(folder, INSTALLED_FILE), "w", encoding="utf-8") as handle:
        json.dump([name for name in standard_files() if name != "jtag.json"], handle)
    os.remove(os.path.join(folder, "jtag.json"))
    assert install_standard_profiles(folder) == ["jtag.json"]  # one that came with a new version


def test_a_retired_standard_profile_is_removed_while_unchanged(tmp_path, monkeypatch):
    import hashlib

    from openscilab.core import profiles

    # the C64 profiles of 0.1.0b2 were renamed: c64-expansion-port.json is retired
    assert "c64-expansion-port.json" in profiles.RETIRED_FILES and "c64-expansion-port.json" not in standard_files()
    shipped = b'{"Profiles": []}'
    monkeypatch.setattr(profiles, "RETIRED_FILES", {"old.json": hashlib.sha256(shipped).hexdigest()})
    (tmp_path / "old.json").write_bytes(shipped)
    install_standard_profiles(str(tmp_path))
    assert not (tmp_path / "old.json").exists()
    # a copy its user changed stays
    (tmp_path / "old.json").write_bytes(shipped + b"\n")
    assert profiles.remove_retired_profiles(str(tmp_path)) == []
    assert (tmp_path / "old.json").exists()


def test_the_store_loads_the_folder_without_importing():
    folder = profiles_directory()
    session = CaptureSession(frequency=1_000_000)
    session.capture_channels = [AnalyzerChannel(channel_number=0)]
    write_profiles_file(os.path.join(folder, "lab.json"), [Profile("Lab bench", session), Profile("Lab two", session)])
    store = ProfileStore()
    sources = {os.path.basename(profile.source) for profile in store.profiles if profile.standard}
    assert sources == set(standard_files())  # the C64 file of the LogicAnalyzer V2 holds two: both boards, board B only
    assert store.get("UART 9600 baud").standard
    mine = store.user_profiles()
    assert [profile.name for profile in mine] == ["Lab bench", "Lab two"]
    assert mine[0].source == os.path.join(folder, "lab.json") and not mine[0].standard
    # a profile of the folder changes and goes in its own file, not in profiles.json
    store.remove("Lab bench")
    store.add(Profile("Saved", session))
    assert store.save()
    assert [profile.name for profile in read_profiles_file(os.path.join(folder, "lab.json"))] == ["Lab two"]
    store.remove("Lab two")
    store.remove("I²C 100 kHz (standard mode)")
    assert store.save()
    assert not os.path.exists(os.path.join(folder, "lab.json")) and not os.path.exists(os.path.join(folder, "i2c-100k.json"))
    again = ProfileStore()
    assert [profile.name for profile in again.user_profiles()] == ["Saved"]
    assert again.get("I²C 100 kHz (standard mode)") is None  # a deleted standard profile stays deleted


def test_a_file_put_into_the_folder_shows_up_in_the_menu(make_dataview):
    from PySide6.QtWidgets import QMenu

    from openscilab.ui.devices.capture import capture_controller

    window = make_dataview()
    controller = capture_controller(open_simulated("free"))
    window.attach_source(controller)
    try:
        session = CaptureSession(frequency=1_000_000)
        session.capture_channels = [AnalyzerChannel(channel_number=0)]
        write_profiles_file(os.path.join(profiles_directory(), "dropped.json"), [Profile("Dropped", session)])
        menu = QMenu()
        controller.fill_profiles_menu(menu, window)
        assert "Dropped" in [action.text() for action in menu.actions()]
    finally:
        window.close()


# ------------------------------------------------------ fitting a device
def test_the_c64_profiles_say_the_device_they_are_for():
    names = [profile.name for name in ("c64-logicanalyzer-v2", "c64-arduino-mega")
             for profile in read_profiles_file(os.path.join(standard_directory(), name + ".json"))]
    assert all("LogicAnalyzer V2" in name for name in names[:2]) and "Arduino Mega 2560" in names[2]
    # the Mega: Ben Eater's pins, a state per falling edge of Φ2 (D2), address and data as buses
    mega = standard("c64-arduino-mega").capture_settings
    named = {channel.channel_name: channel.channel_number for channel in mega.capture_channels}
    assert mega.clock_channel == 30 and mega.clock_edge == EdgeKind.FALLING  # D2
    assert named["A15 (F)"] == 0 and named["A0 (Y)"] == 9 and named["D7 (14)"] == 42 and named["D0 (21)"] == 8
    assert [bus.name for bus in mega.buses] == ["Address", "Data"]
    assert mega.buses[0].channels == [named[f"A{bit} ({pin})"] for bit, pin in enumerate("YXWVUTSRPNMLKJHF")]


def test_a_profile_needs_the_channels_it_captures():
    c64 = standard("c64-logicanalyzer-v2")
    assert "needs 39 channels" in profile_problem(c64, driver("pico"))  # 39 signals, a 24-channel device
    assert profile_problem(standard("uart-115200"), driver("uno")) == ""
    assert "needs 9 channels" in profile_problem(standard("parallel-8bit"), _small(8))
    assert profile_problem(c64, None) == ""


def _small(channels: int):
    class Small:
        channel_count = channels
        analog_channel_count = 0

    return Small()


def test_channels_the_device_has_keep_their_numbers():
    session = CaptureSession(frequency=1000)
    session.capture_channels = [AnalyzerChannel(channel_number=n) for n in (5, 2)]
    fitted, changes = fit_session(session, _Device(8))
    assert [c.channel_number for c in fitted.capture_channels] == [5, 2] and not changes


def test_rate_and_length_are_fitted_to_the_device():
    usb = standard("usb-full-speed").capture_settings
    fitted, changes = fit_session(usb, driver("uno"))
    assert fitted.frequency == 100_000 and fitted.pre_trigger_samples + fitted.post_trigger_samples <= 448
    assert fitted.pre_trigger_samples == 44  # the profile's tenth before the trigger
    assert any(change.startswith("rate") for change in changes) and any(change.startswith("length") for change in changes)
    same, nothing = fit_session(standard("uart-9600").capture_settings, driver("free"))
    assert nothing == [] and same.frequency == 1_000_000
    assert fit_session(usb, None)[0].frequency == usb.frequency


def test_a_trigger_on_a_missing_channel_becomes_none():
    session = CaptureSession(frequency=1000, trigger_type=TriggerType.EDGE, trigger_channel=20)
    session.capture_channels = [AnalyzerChannel(channel_number=0)]
    fitted, changes = fit_session(session, driver("uno"))
    assert fitted.trigger_type == TriggerType.IMMEDIATE and "no trigger" in changes


# ------------------------------------------- a profile made for another device
class _Device:
    """A device with ``count`` digital and ``analog`` analog channels and no other limits, so only
    the renumbering of channels is exercised."""

    def __init__(self, count: int, analog: int = 0) -> None:
        self.channel_count = count
        self.analog_channel_count = analog
        self.min_frequency = 1

    def max_frequency_for(self, channels, mode):
        return 10 ** 9

    def get_limits(self, channels, mode):
        class Limits:
            max_post_samples = max_pre_samples = max_total_samples = 10 ** 9
            min_pre_samples = min_post_samples = 0

        return Limits()


def test_channels_on_high_numbers_are_renumbered_onto_the_device():
    # a capture made on a 40-channel device, three signals on high channels
    session = CaptureSession(frequency=1000)
    session.capture_channels = [AnalyzerChannel(channel_number=n, channel_name=name)
                                for n, name in ((31, "CLK"), (20, "DATA"), (15, "CS"))]
    assert profile_problem(Profile("p", session), _Device(8)) == ""  # three signals fit eight channels
    fitted, changes = fit_session(session, _Device(8))
    assert [c.channel_number for c in fitted.capture_channels] == [0, 1, 2]  # in their order
    assert [c.channel_name for c in fitted.capture_channels] == ["CLK", "DATA", "CS"]  # names kept
    assert any("0-2" in change for change in changes)


def test_the_state_clock_and_buses_follow_the_renumbering():
    mega = standard("c64-arduino-mega")
    session = mega.capture_settings
    old = [c.channel_number for c in session.capture_channels]
    # 36 data channels and the clock (channel 30, not among the data) need 37 channels
    assert "needs 37 channels" in profile_problem(mega, _Device(24))
    fitted, decoders, changes = fit_profile(mega, _Device(40))
    new = [c.channel_number for c in fitted.capture_channels]
    assert new == list(range(36))  # the data channels, dense and in order
    assert fitted.clock_channel == 36  # the clock after them, still its own channel
    mapping = {old[i]: new[i] for i in range(len(new))}
    for before, after in zip(session.buses, fitted.buses):
        assert after.channels == [mapping[channel] for channel in before.channels]


def test_a_fast_trigger_pattern_is_kept_and_its_channel_follows():
    c64 = standard("c64-logicanalyzer-v2")
    session = c64.capture_settings
    assert session.trigger_type == TriggerType.FAST
    fitted, _decoders, _changes = fit_profile(c64, _Device(40))
    old = [c.channel_number for c in session.capture_channels]
    mapping = {old[i]: i for i in range(len(old))}
    assert fitted.trigger_channel == mapping[session.trigger_channel]  # the channel moved
    assert fitted.trigger_pattern == session.trigger_pattern  # the pattern is relative to it, unchanged


def test_a_pattern_window_that_stays_together_follows():
    session = CaptureSession(frequency=1000, trigger_type=TriggerType.FAST, trigger_channel=12,
                             trigger_bit_count=2, trigger_pattern=0b11)
    session.capture_channels = [AnalyzerChannel(channel_number=n) for n in (12, 13)]
    fitted, changes = fit_session(session, _Device(8))
    assert fitted.trigger_type == TriggerType.FAST and fitted.trigger_channel == 0  # 12,13 -> 0,1 (together)
    assert "no trigger" not in changes


def test_a_pattern_window_compaction_breaks_apart_is_dropped():
    session = CaptureSession(frequency=1000, trigger_type=TriggerType.FAST, trigger_channel=12,
                             trigger_bit_count=2, trigger_pattern=0b11)
    # channels 12 and 13 do not stay next to each other: 12 and 13 map to 0 and 2, not 0 and 1
    session.capture_channels = [AnalyzerChannel(channel_number=n) for n in (12, 10, 13)]
    fitted, changes = fit_session(session, _Device(8))
    assert fitted.trigger_type == TriggerType.IMMEDIATE and "no trigger" in changes


def test_decoders_follow_the_renumbering():
    session = CaptureSession(frequency=1000)
    session.capture_channels = [AnalyzerChannel(channel_number=n) for n in (30, 31)]
    profile = Profile("p", session, [{"decoder_id": "uart", "channel_map": {"0": 31}, "parent": None}])
    _fitted, decoders, _changes = fit_profile(profile, _Device(8))
    assert decoders[0]["channel_map"] == {"0": 1}  # channel 31 was the second captured -> channel 1


# --------------------------------------------------------------- the menu
def test_the_standard_profiles_menu_loads_and_disables(make_dataview):
    from PySide6.QtWidgets import QMenu

    from openscilab.core import capture_io, settings
    from openscilab.ui.devices.capture import capture_controller
    from openscilab.ui.dialogs.capture_dialog import capture_settings_file

    window = make_dataview()
    instrument = open_simulated("uno")
    controller = capture_controller(instrument)
    window.attach_source(controller)
    try:
        menu = QMenu()
        controller.fill_profiles_menu(menu, window)
        assert "Open the profiles folder" in [action.text() for action in menu.actions()]
        entries = {action.text(): action for action in controller.standard_menu.actions()}
        c64 = next(action for text, action in entries.items() if "LogicAnalyzer V2: board B (master)" in text)
        assert not c64.isEnabled() and "needs 39 channels" in c64.text()
        mega = next(action for text, action in entries.items() if "Arduino Mega" in text)
        assert not mega.isEnabled() and "needs 37 channels" in mega.text()  # 36 data + the Φ2 clock
        entries["UART 115200 baud (Arduino serial)"].trigger()
        stored = capture_io.session_from_dict(settings.get_settings(capture_settings_file(controller.driver)))
        assert stored.frequency == 100_000  # the Uno's highest rate
        assert [channel.channel_name for channel in stored.capture_channels] == ["RX", "TX"]
        assert [instance.decoder_id for instance in controller.view.provider.instances] == ["uart"]
        assert controller.view.provider.instances[0].options["baudrate"] == 115200
    finally:
        window.close()
