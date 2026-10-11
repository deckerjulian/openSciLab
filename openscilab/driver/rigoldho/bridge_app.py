# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Installing the bridge app (``firmware/bridge-android``) on a DHO900 with adb.

The DHO runs Android; its adb listens on the LAN at port 55555 (5555 is its SCPI server). The
installer connects adb there, installs the APK (``install -r``: an installed bridge is updated, its
cache and its start setting stay), starts it without a touch on the screen (``--ez start true``:
the app then also comes back after a reboot) and waits until the bridge answers on its port.

An app signed with another key (a build of another machine: the release build is signed with
the build machine's debug key) cannot be updated: :class:`SignatureMismatch`, and ``replace=True``
uninstalls it first (its snapshot cache goes with it).
"""

from __future__ import annotations

import glob
import os
import socket
import subprocess
import time
from typing import Callable, Optional

from ...core.firmware import PROJECT_DIRECTORY, find_tool
from ...core.settings import settings_directory
from . import scpi

#: the package and start activity of the bridge app
PACKAGE = "org.openscilab.bridge"
ACTIVITY = f"{PACKAGE}/.MainActivity"
#: the DHO's adb port on the LAN
ADB_PORT = 55555
#: seconds the bridge gets to answer after its start
START_TIMEOUT_S = 20.0

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class BridgeInstallError(RuntimeError):
    """The bridge app could not be installed or started (the message says why)."""


class AdbMissing(BridgeInstallError):
    """adb is not installed."""


class SignatureMismatch(BridgeInstallError):
    """An installed bridge app is signed with another key: only a new installation replaces it."""


def apk_candidates() -> list[str]:
    """Where the bridge app's APK is looked for, in this order: the release build of
    ``firmware/build_all.sh --bridge``, one put into the settings, the Gradle builds."""
    app = os.path.join(PROJECT_DIRECTORY, "firmware", "bridge-android", "app", "build", "outputs", "apk")
    return [os.path.join(PROJECT_DIRECTORY, "firmware", "apk", "openSciLab-bridge.apk"),
            os.path.join(settings_directory(), "firmware", "openSciLab-bridge.apk"),
            *sorted(glob.glob(os.path.join(app, "release", "*.apk"))),
            os.path.join(app, "debug", "app-debug.apk")]


def find_apk() -> Optional[str]:
    """The bridge app's APK that comes with the application (``None``: not built)."""
    return next((path for path in apk_candidates() if os.path.isfile(path)), None)


def bridge_version(host: str, port: int = scpi.BRIDGE_PORT, timeout: float = 2.0) -> Optional[str]:
    """The version the bridge app at ``host`` answers (``:BRIDge:VERSion?``), ``None`` if none answers."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(b":BRIDge:VERSion?\n")
            answer = b""
            while not answer.endswith(b"\n"):
                chunk = connection.recv(256)
                if not chunk:
                    break
                answer += chunk
    except OSError:
        return None
    text = answer.decode("ascii", "replace").strip()
    if not text.startswith("OPENSCILAB_BRIDGE"):
        return None
    parts = text.split(",")
    return parts[1] if len(parts) > 1 else "?"


def _default_runner(arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=180, check=False)


def install(host: str, apk: Optional[str] = None, replace: bool = False, runner: Optional[Runner] = None,
            wait: Callable[[str], Optional[str]] = bridge_version, start_timeout: float = START_TIMEOUT_S) -> str:
    """Installs and starts the bridge app on the DHO at ``host`` and returns the version it answers.
    ``replace``: uninstall an app signed with another key first. Raises :class:`BridgeInstallError`."""
    apk = apk or find_apk()
    if apk is None:
        raise BridgeInstallError("The bridge app is not built yet: run firmware/build_all.sh --bridge (or "
                                 "./gradlew assembleDebug in firmware/bridge-android).")
    adb = find_tool("adb")
    if adb is None:
        raise AdbMissing("adb is not installed. It comes with the Android platform tools (macOS: brew install "
                         "android-platform-tools); or put it into the folder 'tools' of the settings.")
    run = runner or _default_runner
    target = f"{host}:{ADB_PORT}"

    def adb_call(*arguments: str) -> str:
        try:
            result = run([*adb, *arguments])
        except (OSError, subprocess.SubprocessError) as error:
            raise BridgeInstallError(f"adb {arguments[0]} failed: {error}") from error
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            raise BridgeInstallError(f"adb {' '.join(arguments[:2])} failed: {output or result.returncode}")
        return output

    connected = adb_call("connect", target)
    if "connected" not in connected or "cannot" in connected or "failed" in connected:
        raise BridgeInstallError(f"adb could not reach the oscilloscope at {target} ({connected}). Turn on adb "
                                 "on the oscilloscope and check that it is on the network.")
    device = ("-s", target)
    if replace:
        try:
            adb_call(*device, "uninstall", PACKAGE)
        except BridgeInstallError:
            pass  # (not installed: nothing to remove)
    try:
        output = adb_call(*device, "install", "-r", apk)
    except BridgeInstallError as error:
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in str(error) or "signatures do not match" in str(error).lower():
            raise SignatureMismatch("The bridge app on the oscilloscope is signed with another key (a build of "
                                    "another computer); it can only be replaced by a new installation, which "
                                    "removes its snapshot cache.") from error
        raise
    if "Success" not in output:
        raise BridgeInstallError(f"adb install did not succeed: {output}")
    adb_call(*device, "shell", "am", "start", "-n", ACTIVITY, "--ez", "start", "true")
    deadline = time.monotonic() + start_timeout
    while True:
        version = wait(host)
        if version is not None:
            return version
        if time.monotonic() > deadline:
            raise BridgeInstallError(f"The bridge app was installed but does not answer on port "
                                     f"{scpi.BRIDGE_PORT} of {host}; open it on the oscilloscope and press Start.")
        time.sleep(1.0)
