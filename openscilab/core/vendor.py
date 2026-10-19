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
2. ``openscilab/vendor/<platform>/`` - the applications bring it there; in a checkout
   ``tools/build_vendor.py`` puts it there (the folder is not in git),
3. ``<settings>/vendor/<platform>/`` - installed by openSciLab when the folder above cannot be written,
4. the system: a KiCad installation (its libngspice and symbols), Homebrew, the package manager.

A tool that is missing is installed (:func:`install`): the vendor archive of the platform from
openSciLab's releases (checked against its SHA-256), else - for ngspice and the AVR library on macOS
and Linux, with a C compiler - built from its pinned sources (``core/vendor_build.py``); KiCad's
symbols come from KiCad's archive of a pinned version. Qt free; the user interface shows the progress.
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
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from . import settings

log = logging.getLogger(__name__)

#: the releases of openSciLab whose vendor archives are installed, the first that has one: this version's,
#: then the newest build of main
def releases() -> list[str]:
    from .. import __version__

    return [f"v{__version__}", "latest-build"]


RELEASE_URL = "https://github.com/deckerjulian/openSciLab/releases/download/{release}/openSciLab-vendor-{platform}.zip"
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
    """The vendor folder of the package (the applications bring their tools there)."""
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


def _install_release(target: str, progress: Progress | None, only: str | None = None) -> None:
    """The vendor archive of this platform from openSciLab's releases, checked with its SHA-256; ``only``:
    just that tool of it is taken (the others stay as they are)."""
    problems = []
    for release in releases():
        try:
            _install_archive(RELEASE_URL.format(release=release, platform=platform_tag()), target, progress, only)
            return
        except VendorError as error:
            problems.append(str(error))
            if "checksum" in str(error) or "outside its folder" in str(error):
                raise
    raise VendorError(problems[-1] if problems else "no release has a vendor archive")


def _install_archive(url: str, target: str, progress: Progress | None, only: str | None = None) -> None:
    """Unpack the vendor archive into a staging folder beside ``target`` and move the tools in whole
    (``only``: just that one): a library that is loaded is never written over - it is renamed away."""
    expected = _download(url + ".sha256", None, "the checksum of the vendor archive").decode("ascii", "replace").split()
    data = _download(url, progress, "the vendor archive", (0.0, 0.9))
    digest = hashlib.sha256(data).hexdigest()
    if not expected or digest != expected[0].lower():
        raise VendorError("the vendor archive did not match its checksum: it was not installed")
    os.makedirs(target, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="vendor-", dir=target)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                path = os.path.normpath(os.path.join(staging, member.filename))
                if not path.startswith(os.path.normpath(staging) + os.sep) and path != os.path.normpath(staging):
                    raise VendorError(f"the vendor archive holds a path outside its folder: {member.filename}")
            archive.extractall(staging)
        for path in glob.glob(os.path.join(staging, "**", "*"), recursive=True):
            if os.path.isfile(path) and (path.endswith((".dylib", ".so", ".cm")) or ".so." in path):
                os.chmod(path, 0o755)
        wanted = [only] if only else [name for name in os.listdir(staging) if os.path.isdir(os.path.join(staging, name))]
        for name in wanted:
            source = os.path.join(staging, name)
            if not os.path.isdir(source):
                raise VendorError(f"the vendor archive holds no {name}")
            _swap_in(source, os.path.join(target, name))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
        else:
            try:
                _install_release(target, progress, only=name)
            except VendorError as error:
                problems.append(str(error))
                log.info("The vendor archive could not be installed: %s", error)
            if find(name, system=False) is None and build:
                from . import vendor_build

                try:
                    vendor_build.build(name, target, progress)
                except VendorError as error:
                    problems.append(str(error))
        found = find(name, system=False)
        if found is None:
            reasons = "; ".join(dict.fromkeys(problem.rstrip(".") for problem in problems)) or "no way to install it"
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
