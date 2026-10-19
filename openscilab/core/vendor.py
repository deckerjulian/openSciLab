# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Programs and libraries of others that openSciLab uses: found, or installed into its vendor folder.

========== ================================================= ======================================
name       what                                              license
========== ================================================= ======================================
ngspice    the circuit simulator (libngspice, code models)   modified BSD
simavr     the AVR emulator (``libopenscilab_avr``)          GPL-3.0
kicad      KiCad's symbol libraries (the catalog)            CC-BY-SA-4.0 with the design exception
========== ================================================= ======================================

Where a tool is looked for, the first place that has it wins:

1. the path in the settings (``circuits.ngspice_path``),
2. ``openscilab/vendor/<platform>/`` - in a checkout ``tools/build_vendor.py`` puts it there (the folder
   is not in git),
3. ``<settings>/vendor/<platform>/`` - installed by openSciLab (always in a packaged application),
4. the system: a KiCad installation (its libngspice and symbols), Homebrew, the package manager.

openSciLab hands on none of these tools: neither the applications nor the package bring them. A tool
that is missing is fetched from its own project when it is first needed (:func:`install`): ngspice on
Windows as the DLL package of ngspice's releases, on macOS and Linux - with a C compiler - built from
ngspice's pinned sources; the AVR library built from simavr's pinned sources and the shim of the package
(``core/vendor_build.py``); KiCad's symbols from KiCad's archive of a pinned version. Every download is
checked against its SHA-256. Qt free; the user interface shows the progress.
"""

from __future__ import annotations

import ctypes.util
import glob
import hashlib
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from . import settings

log = logging.getLogger(__name__)

#: ngspice's own package of the shared library for Windows x64 (DLL, XSPICE code models), a 7-Zip archive
NGSPICE_WINDOWS_URL = "https://downloads.sourceforge.net/project/ngspice/ng-spice-rework/{version}/ngspice-{version}_dll_64.7z"
#: SHA-256 of that archive of ``vendor_build.NGSPICE_VERSION``
NGSPICE_WINDOWS_SHA256 = "5bf5eec7db989f74849cdb32f7df62693f4f149cceeebcbd222eb35c2bb821eb"
#: KiCad's symbol libraries of this version (the catalog reads KiCad 6 to 10)
KICAD_SYMBOLS_VERSION = "10.0.7"
#: SHA-256 of KiCad's archive of that version ("": not checked)
KICAD_SYMBOLS_SHA256 = "951f1392d3de6a258eea9b67fccce7d0bfd5af3e768e85c445a2240e19138c98"
KICAD_SYMBOLS_URL = "https://gitlab.com/kicad/libraries/kicad-symbols/-/archive/{version}/kicad-symbols-{version}.tar.gz"

PACKAGE_DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Progress = Callable[[str, float], None]


class VendorError(RuntimeError):
    """A tool could not be installed; the message says why, for people."""


def platform_tag() -> str:
    """``macos-arm64``, ``macos-x64``, ``linux-x86_64``, ``linux-aarch64``, ``windows-x64``."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    if sys.platform.startswith("win"):
        return "windows-arm64" if arm else "windows-x64"
    return "linux-aarch64" if arm else "linux-x86_64"


def package_directory() -> str:
    """The vendor folder of the package (a checkout: tools/build_vendor.py puts the tools there)."""
    return os.path.join(PACKAGE_DIRECTORY, "vendor", platform_tag())


def user_directory() -> str:
    """The vendor folder in the settings directory."""
    return os.path.join(settings.settings_directory(), "vendor", platform_tag())


def directories() -> list[str]:
    return [package_directory(), user_directory()]


def install_directory() -> str:
    """Where :func:`install` puts a tool: the package's folder when it can be written (not in a packaged
    application: its bundle stays as it was shipped), else the settings' one."""
    if getattr(sys, "frozen", False):
        folder = user_directory()
        os.makedirs(folder, exist_ok=True)
        return folder
    folder = package_directory()
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-test")
        with open(probe, "w") as handle:
            handle.write("")
        os.remove(probe)
        return folder
    except OSError:
        folder = user_directory()
        os.makedirs(folder, exist_ok=True)
        return folder


# ------------------------------------------------------------------ tools
def _library_names(stem: str) -> list[str]:
    if sys.platform == "darwin":
        return [f"lib{stem}.0.dylib", f"lib{stem}.dylib"]
    if sys.platform.startswith("win"):
        return [f"{stem}.dll", f"lib{stem}.dll", f"lib{stem}-0.dll"]
    return [f"lib{stem}.so.0", f"lib{stem}.so"]


def _ngspice_system() -> list[str]:
    found = []
    if sys.platform == "darwin":
        found += ["/opt/homebrew/lib/libngspice.0.dylib", "/usr/local/lib/libngspice.0.dylib",
                  "/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib"]
    elif sys.platform.startswith("win"):
        found += [r"C:\Spice64\bin_dll\ngspice.dll", r"C:\Spice64_dll\dll-vs\ngspice.dll"]
        found += sorted(glob.glob(r"C:\Program Files\KiCad\*\bin\ngspice.dll"), reverse=True)
    else:
        found += ["/usr/lib/x86_64-linux-gnu/libngspice.so.0", "/usr/lib/aarch64-linux-gnu/libngspice.so.0",
                  "/usr/lib64/libngspice.so.0", "/usr/lib/libngspice.so.0", "/usr/local/lib/libngspice.so.0"]
    name = ctypes.util.find_library("ngspice")
    if name:
        found.append(name)
    return found


def _kicad_system() -> list[str]:
    found = [os.environ[key] for key in sorted(os.environ, reverse=True)
             if key.startswith("KICAD") and key.endswith("_SYMBOL_DIR") and os.environ[key]]
    if sys.platform == "darwin":
        found.append("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")
        found += sorted(glob.glob("/Applications/KiCad*/KiCad.app/Contents/SharedSupport/symbols"), reverse=True)
    elif sys.platform.startswith("win"):
        found += sorted(glob.glob(r"C:\Program Files\KiCad\*\share\kicad\symbols"), reverse=True)
    else:
        found += ["/usr/share/kicad/symbols", "/usr/local/share/kicad/symbols",
                  os.path.expanduser("~/.local/share/kicad/symbols")]
        found += sorted(glob.glob("/usr/share/kicad-*/symbols"), reverse=True)
    return found


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    #: what the vendor folder holds of it (relative paths; the first that exists is used)
    files: tuple[str, ...]
    #: candidates on the system
    system: Callable[[], list[str]]
    license: str
    #: a folder (KiCad's symbols) rather than a library file
    folder: bool = False


TOOLS: dict[str, Tool] = {
    "ngspice": Tool("ngspice", "ngspice", tuple(os.path.join("ngspice", "lib", name) for name in _library_names("ngspice"))
                    + tuple(os.path.join("ngspice", "bin", name) for name in _library_names("ngspice")),
                    _ngspice_system, "BSD-3-Clause"),
    "simavr": Tool("simavr", "the AVR emulator (simavr)",
                   tuple(os.path.join("simavr", name) for name in _library_names("openscilab_avr")), list,
                   "GPL-3.0-or-later"),
    "kicad": Tool("kicad", "KiCad's symbol libraries", ("kicad-symbols",), _kicad_system, "CC-BY-SA-4.0", folder=True),
}

_SETTINGS = {"ngspice": "circuits.ngspice_path"}


def _usable(path: str, tool: Tool) -> bool:
    if tool.folder:
        return os.path.isdir(path) and bool(glob.glob(os.path.join(path, "*.kicad_sym"))
                                            or glob.glob(os.path.join(path, "*.kicad_symdir")))
    if os.path.isfile(path):
        return True
    if not path or os.path.isabs(path) or os.sep in path:
        return False
    # (a bare name: a library the system knows - a typo in the settings must not shadow the vendored one)
    return bool(shutil.which(path) or ctypes.util.find_library(path) or ctypes.util.find_library(path.removeprefix("lib")))


def find(name: str, system: bool = True, settings: bool = True) -> str | None:
    """Where the tool ``name`` is (a library file, or the folder of KiCad's symbols); ``None``: nowhere.
    ``system``: the system's installations count too; ``settings``: the path chosen in the settings does."""
    tool = TOOLS[name]
    key = _SETTINGS.get(name) if settings else None
    if key:
        from . import preferences

        chosen = str(preferences.get(key) or "").strip()
        if chosen and _usable(chosen, tool):
            return chosen
    for folder in directories():
        for relative in tool.files:
            path = os.path.join(folder, relative)
            if _usable(path, tool):
                return path
    if system:
        for path in tool.system():
            if path and _usable(path, tool):
                return path
    return None


def find_all(name: str) -> list[str]:
    """Every place that has the tool, in the order of :func:`find` (KiCad's symbols: every folder)."""
    tool = TOOLS[name]
    found = []
    for folder in directories():
        for relative in tool.files:
            path = os.path.join(folder, relative)
            if _usable(path, tool) and path not in found:
                found.append(path)
    for path in tool.system():
        if path and _usable(path, tool) and path not in found:
            found.append(path)
    return found


def vendored(name: str) -> bool:
    """The tool is in a vendor folder of openSciLab (not only on the system or in the settings)."""
    return find(name, system=False, settings=False) is not None


# ----------------------------------------------------------------- install
_lock = threading.Lock()


#: how often a download is tried (the release is replaced for a moment at every push; a connection drops)
DOWNLOAD_ATTEMPTS = 3


def _download(url: str, progress: Progress | None, what: str, share: tuple[float, float] = (0.0, 1.0),
              sha256: str = "") -> bytes:
    """``url`` as bytes, tried :data:`DOWNLOAD_ATTEMPTS` times; checked against ``sha256`` when given."""
    request = urllib.request.Request(url, headers={"User-Agent": "openSciLab"})
    error: Exception | None = None
    data: bytes | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                buffer = io.BytesIO()
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    buffer.write(chunk)
                    if progress is not None and total:
                        progress(f"Downloading {what}", share[0] + (share[1] - share[0]) * buffer.tell() / total)
                data = buffer.getvalue()
            break
        except urllib.error.HTTPError as failure:
            error = failure
            if failure.code == 404:
                break  # (not there: trying again does not help)
        except (OSError, ValueError) as failure:
            error = failure
    if data is None:
        log.info("%s could not be downloaded from %s: %s", what, url, error)
        reason = f"not found ({error.code})" if isinstance(error, urllib.error.HTTPError) else "no connection"
        raise VendorError(f"{what} could not be downloaded ({reason}: {error})") from None
    if sha256 and hashlib.sha256(data).hexdigest() != sha256.lower():
        raise VendorError(f"{what} did not match its checksum: it was not used")
    return data


def _install_ngspice_windows(target: str, progress: Progress | None) -> None:
    """ngspice's DLL package (Windows x64) into ``target/ngspice``: the DLLs into ``bin``, the code models
    into ``lib/ngspice`` (where ``engines/ngspice.code_model_folders`` looks). Windows' own ``tar``
    (libarchive, Windows 10 and later) unpacks the 7-Zip archive."""
    from .vendor_build import NGSPICE_VERSION

    if platform_tag() != "windows-x64":
        raise VendorError(f"ngspice has no package for {platform_tag()}")
    tar = shutil.which("tar")
    if tar is None:
        raise VendorError("Windows' tar is missing to unpack ngspice's package")
    data = _download(NGSPICE_WINDOWS_URL.format(version=NGSPICE_VERSION), progress, "ngspice", (0.0, 0.9),
                     NGSPICE_WINDOWS_SHA256)
    os.makedirs(target, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="vendor-", dir=target)
    try:
        archive = os.path.join(staging, "ngspice.7z")
        with open(archive, "wb") as handle:
            handle.write(data)
        unpacked = os.path.join(staging, "unpacked")
        os.makedirs(unpacked)
        result = subprocess.run([tar, "-xf", archive, "-C", unpacked], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise VendorError(f"ngspice's package could not be unpacked: {(result.stderr or result.stdout)[-500:]}")
        root = os.path.join(unpacked, "Spice64_dll")
        libraries = glob.glob(os.path.join(root, "dll-vs", "*.dll"))
        if not any(os.path.basename(path).lower() == "ngspice.dll" for path in libraries):
            raise VendorError("ngspice's package holds no ngspice.dll")
        folder = os.path.join(staging, "ngspice")
        os.makedirs(os.path.join(folder, "bin"))
        os.makedirs(os.path.join(folder, "lib", "ngspice"))
        for path in libraries:
            shutil.copy2(path, os.path.join(folder, "bin"))
        for path in glob.glob(os.path.join(root, "lib", "ngspice", "*.cm")):
            shutil.copy2(path, os.path.join(folder, "lib", "ngspice"))
        _swap_in(folder, os.path.join(target, "ngspice"))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


#: what to do when a tool could not be installed
HINTS = {
    "ngspice": {"darwin": "or install it with Homebrew: brew install libngspice",
                "linux": "or install it with the package manager: sudo apt install libngspice0 (S-parameters need "
                         "ngspice 44 or newer)"},
    "simavr": {"darwin": "the emulator is built on this computer: xcode-select --install and brew install avr-gcc",
               "linux": "the emulator is built on this computer: sudo apt install build-essential avr-libc",
               "win32": "the emulator is built on this computer: MSYS2 with mingw-w64-x86_64-gcc and "
                        "mingw-w64-x86_64-avr-libc"},
}


def _hint(name: str) -> str:
    system = "win32" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "linux")
    return HINTS.get(name, {}).get(system, "")


def _swap_in(source: str, destination: str) -> None:
    """Put the folder ``source`` where ``destination`` is: the old one is renamed away first (a library
    the application has loaded keeps working under its old inode), then removed where that is possible."""
    old = None
    if os.path.exists(destination):
        old = f"{destination}.old-{os.getpid()}"
        try:
            os.replace(destination, old)
        except OSError as error:  # (Windows: a loaded DLL's folder cannot be renamed)
            raise VendorError(f"{os.path.basename(destination)} is in use: close openSciLab and install the tools "
                              f"again ({error})") from None
    os.replace(source, destination)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def _install_kicad(target: str, progress: Progress | None) -> None:
    url = KICAD_SYMBOLS_URL.format(version=KICAD_SYMBOLS_VERSION)
    data = _download(url, progress, "KiCad's symbol libraries", (0.0, 0.9), KICAD_SYMBOLS_SHA256)
    folder = os.path.join(target, "kicad-symbols")
    staging = tempfile.mkdtemp(prefix="kicad-symbols-", dir=target)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                parts = member.name.replace("\\", "/").split("/")[1:]  # (without the archive's top folder)
                if not parts or any(part in ("", ".", "..") for part in parts):
                    continue
                wanted = (len(parts) == 1 and parts[0].endswith((".kicad_sym", ".sp", "LICENSE.md"))) or \
                    (len(parts) == 2 and parts[0].endswith(".kicad_symdir") and parts[1].endswith(".kicad_sym"))
                if not wanted:
                    continue
                destination = os.path.join(staging, *parts)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with open(destination, "wb") as handle:
                    shutil.copyfileobj(source, handle)
        if not glob.glob(os.path.join(staging, "*.kicad_sym")) and not glob.glob(os.path.join(staging, "*.kicad_symdir")):
            raise VendorError("KiCad's archive held no symbol libraries")
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        os.replace(staging, folder)
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)


def install(name: str, progress: Progress | None = None, build: bool = True) -> str:
    """Install the tool ``name`` into a vendor folder (see the module); returns where it is now.

    Takes long (downloads, perhaps a build): call it in a thread. Raises :class:`VendorError`."""
    with _lock:
        existing = find(name, system=False)
        if existing:
            return existing
        target = install_directory()
        problems: list[str] = []
        if name == "kicad":
            _install_kicad(target, progress)
        elif name == "ngspice" and sys.platform.startswith("win"):
            try:
                _install_ngspice_windows(target, progress)
            except VendorError as error:
                problems.append(str(error))
        elif build:
            from . import vendor_build

            try:
                vendor_build.build(name, target, progress)
            except VendorError as error:
                problems.append(str(error))
        else:
            problems.append(f"{TOOLS[name].title} is built on this computer, not downloaded")
        found = find(name, system=False)
        if found is None:
            reasons = "; ".join(dict.fromkeys(problem.rstrip(".") for problem in problems)) or "no way to install it"
            if _hint(name):
                reasons += f" - {_hint(name)}"
            raise VendorError(f"{TOOLS[name].title[0].upper()}{TOOLS[name].title[1:]} could not be installed: {reasons}.")
        if progress is not None:
            progress(f"{TOOLS[name].title} is installed", 1.0)
        return found


def ensure(name: str, progress: Progress | None = None) -> str | None:
    """The tool, found or (when the settings allow it) installed; ``None`` when that failed."""
    found = find(name)
    if found:
        return found
    from . import preferences

    if not preferences.get("circuits.install_tools"):
        return None
    try:
        return install(name, progress)
    except VendorError as error:
        log.warning("%s", error)
        return None
