# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Building the tools of others from their pinned sources (``core/vendor.py``).

* ``simavr``: simavr's simulator core with the AVR cores openSciLab emulates and the shim of
  ``openscilab/circuits/mcu/shim`` into ``libopenscilab_avr`` (no libelf, no GDB server); needs a C compiler and
  the register headers of avr-libc (Linux: ``apt install avr-libc``, macOS: ``brew install avr-gcc`` or
  PlatformIO's AVR toolchain, Windows: MSYS2 ``mingw-w64-x86_64-avr-libc``).
* ``ngspice``: ngspice as a shared library with its XSPICE code models (macOS, Linux; needs a C
  compiler and make).

``tools/build_vendor.py`` (the CI, a checkout) and :func:`openscilab.core.vendor.install` (when the
vendor archive cannot be had) call :func:`build`.
"""

from __future__ import annotations

import glob
import io
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable

from .vendor import PACKAGE_DIRECTORY, VendorError

log = logging.getLogger(__name__)

SIMAVR_VERSION = "1.8"
SIMAVR_URL = "https://github.com/buserror/simavr/archive/refs/tags/v{version}.tar.gz"
#: SHA-256 of that archive ("": not checked)
SIMAVR_SHA256 = "51e2682d23fb4843191ee81dadcb5488fa6194553a108bbd6e60e8b1260269d6"
#: the AVR cores openSciLab emulates (simavr's core files and their avr_kind_t)
SIMAVR_CORES = {"sim_mega328.c": "mega328", "sim_mega168.c": "mega168", "sim_mega2560.c": "mega2560",
                "sim_mega1280.c": "mega1280", "sim_mega88.c": "mega88", "sim_mega48.c": "mega48"}
#: what several cores share
SIMAVR_CORE_HELPERS = ("sim_megax8.c", "sim_megax.c")
SIMAVR_SOURCES = ("avr_acomp.c", "avr_adc.c", "avr_bitbang.c", "avr_eeprom.c", "avr_extint.c", "avr_flash.c",
                  "avr_ioport.c", "avr_lin.c", "avr_spi.c", "avr_timer.c", "avr_twi.c", "avr_uart.c", "avr_usb.c",
                  "avr_watchdog.c", "sim_avr.c", "sim_cmds.c", "sim_core.c", "sim_cycle_timers.c", "sim_interrupts.c",
                  "sim_io.c", "sim_irq.c", "sim_utils.c", "sim_vcd_file.c")
NGSPICE_VERSION = "47"
NGSPICE_URL = "https://downloads.sourceforge.net/project/ngspice/ng-spice-rework/{version}/ngspice-{version}.tar.gz"
#: SHA-256 of that archive ("": not checked)
NGSPICE_SHA256 = "894e649651f1838a14095e5a5439e7d3aa63e87ede14d283173fda4fcdef675f"
#: the C shim around simavr (part of the package: a pip install can build the emulator too)
SHIM_DIRECTORY = os.path.join(PACKAGE_DIRECTORY, "circuits", "mcu", "shim")

Progress = Callable[[str, float], None]


def _library_name(stem: str) -> str:
    if sys.platform == "darwin":
        return f"lib{stem}.dylib"
    if sys.platform.startswith("win"):
        return f"{stem}.dll"
    return f"lib{stem}.so"


def compiler() -> str | None:
    for name in (os.environ.get("CC", ""), "cc", "gcc", "clang"):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


def avr_libc_include() -> str | None:
    """The include folder of avr-libc (``avr/io.h`` in it)."""
    candidates = [os.environ.get("AVR_LIBC_INCLUDE", ""),
                  os.path.expanduser("~/.platformio/packages/toolchain-atmelavr/avr/include"),
                  "/usr/lib/avr/include", "/usr/avr/include", "/usr/local/avr/include",
                  r"C:\msys64\mingw64\avr\include", r"C:\msys64\ucrt64\avr\include"]
    candidates += sorted(glob.glob("/opt/homebrew/Cellar/avr-gcc*/*/avr/include"), reverse=True)
    candidates += sorted(glob.glob("/usr/local/Cellar/avr-gcc*/*/avr/include"), reverse=True)
    candidates += sorted(glob.glob("/opt/homebrew/opt/avr-gcc*/avr/include"), reverse=True)
    for folder in candidates:
        if folder and os.path.isfile(os.path.join(folder, "avr", "iom328p.h")):
            return folder
    return None


def _fetch(url: str, progress: Progress | None, what: str, sha256: str = "") -> bytes:
    """The source archive at ``url`` (tried again on a dropped connection), checked against ``sha256``:
    what is compiled and loaded into the process must be what was pinned."""
    from .vendor import _download

    if progress is not None:
        progress(f"Downloading {what}", 0.05)
    return _download(url, None, what, sha256=sha256)


def _extract(data: bytes, into: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = os.path.normpath(os.path.join(into, member.name))
            if not path.startswith(os.path.normpath(into) + os.sep):
                raise VendorError(f"the archive holds a path outside its folder: {member.name}")
        archive.extractall(into, filter="data") if sys.version_info >= (3, 12) else archive.extractall(into)
    entries = [os.path.join(into, name) for name in os.listdir(into)]
    folders = [entry for entry in entries if os.path.isdir(entry)]
    return folders[0] if len(folders) == 1 else into


def _run(command: list[str], cwd: str, what: str) -> None:
    log.info("%s: %s", what, " ".join(command))
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise VendorError(f"{what} failed: {(result.stderr or result.stdout)[-1500:]}")


# ---------------------------------------------------------------- simavr
def build_simavr(target: str, progress: Progress | None = None, source: str | None = None) -> str:
    """Build ``libopenscilab_avr`` into ``target/simavr``; returns the library's path."""
    cc = compiler()
    if cc is None:
        raise VendorError("no C compiler (cc, gcc, clang) to build the AVR emulator")
    include = avr_libc_include()
    if include is None:
        raise VendorError("the headers of avr-libc are missing (apt install avr-libc, brew install avr-gcc, "
                          "or PlatformIO's AVR toolchain)")
    work = tempfile.mkdtemp(prefix="openscilab-simavr-")
    try:
        if source is None:
            data = _fetch(SIMAVR_URL.format(version=SIMAVR_VERSION), progress, "simavr", SIMAVR_SHA256)
            root = _extract(data, work)
        else:
            root = source
        simavr = os.path.join(root, "simavr")
        sim, cores = os.path.join(simavr, "sim"), os.path.join(simavr, "cores")
        config = os.path.join(work, "config")
        os.makedirs(config, exist_ok=True)
        with open(os.path.join(config, "sim_core_config.h"), "w") as handle:
            handle.write("#ifndef __SIM_CORE_CONFIG_H__\n#define __SIM_CORE_CONFIG_H__\n"
                         f'#define CONFIG_SIMAVR_VERSION "{SIMAVR_VERSION}"\n')
            handle.writelines(f"#define CONFIG_{kind.upper()} 1\n" for kind in SIMAVR_CORES.values())
            handle.write("#endif\n")
        with open(os.path.join(config, "sim_core_decl.h"), "w") as handle:
            handle.write('#ifndef __SIM_CORE_DECL_H__\n#define __SIM_CORE_DECL_H__\n#include "sim_core_config.h"\n')
            handle.writelines(f"extern avr_kind_t {kind};\n" for kind in SIMAVR_CORES.values())
            handle.write("extern avr_kind_t * avr_kind[];\n#ifdef AVR_KIND_DECL\navr_kind_t * avr_kind[] = {\n")
            handle.writelines(f"\t&{kind},\n" for kind in SIMAVR_CORES.values())
            handle.write("\tNULL\n};\n#endif\n#endif\n")
        sources = [os.path.join(sim, name) for name in SIMAVR_SOURCES]
        sources += [os.path.join(cores, name) for name in SIMAVR_CORES]
        sources += [os.path.join(cores, name) for name in SIMAVR_CORE_HELPERS if os.path.isfile(os.path.join(cores, name))]
        sources += [os.path.join(SHIM_DIRECTORY, "osl_avr.c"), os.path.join(SHIM_DIRECTORY, "osl_stubs.c")]
        missing = [path for path in sources if not os.path.isfile(path)]
        if missing:
            raise VendorError(f"sources missing: {missing}")
        output_folder = os.path.join(target, "simavr")
        os.makedirs(output_folder, exist_ok=True)
        output = os.path.join(output_folder, _library_name("openscilab_avr"))
        if progress is not None:
            progress("Building the AVR emulator", 0.5)
        flags = ["-O2", "-std=gnu99", "-fPIC", "-shared", "-fvisibility=hidden", "-Wno-unused-result",
                 "-Wno-deprecated-declarations", "-D_GNU_SOURCE", f"-I{config}", f"-I{sim}", f"-I{cores}",
                 f"-I{SHIM_DIRECTORY}", f"-idirafter{include}"]
        command = [cc, *flags, *sources, "-o", output, "-lm"]
        if sys.platform == "darwin":
            command[2:2] = ["-install_name", "@rpath/" + os.path.basename(output)]
        elif not sys.platform.startswith("win"):
            command.append("-lpthread")
        else:
            command.append("-static-libgcc")
        _run(command, work, "building the AVR emulator")
        for name in ("COPYING",):
            license_file = os.path.join(root, name)
            if os.path.isfile(license_file):
                shutil.copy(license_file, os.path.join(output_folder, "COPYING-simavr"))
        if progress is not None:
            progress("The AVR emulator is built", 1.0)
        return output
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------- ngspice
def build_ngspice(target: str, progress: Progress | None = None, source: str | None = None) -> str:
    """Build libngspice with its code models into ``target/ngspice``; returns the library's path."""
    if sys.platform.startswith("win"):
        raise VendorError("ngspice is not built on Windows: its vendor archive brings it")
    cc = compiler()
    if cc is None or shutil.which("make") is None:
        raise VendorError("a C compiler and make are needed to build ngspice")
    work = tempfile.mkdtemp(prefix="openscilab-ngspice-")
    try:
        root = source or _extract(_fetch(NGSPICE_URL.format(version=NGSPICE_VERSION), progress, "ngspice", NGSPICE_SHA256), work)
        prefix = os.path.join(work, "install")
        if progress is not None:
            progress("Configuring ngspice", 0.15)
        environment = dict(os.environ, CFLAGS="-O2 -fPIC")
        result = subprocess.run(["./configure", f"--prefix={prefix}", "--with-ngshared", "--enable-xspice",
                                 "--enable-cider", "--disable-debug", "--disable-openmp", "--without-x",
                                 "--with-readline=no"], cwd=root, capture_output=True, text=True, env=environment)
        if result.returncode != 0:
            raise VendorError(f"configuring ngspice failed: {result.stderr[-1500:]}")
        if progress is not None:
            progress("Building ngspice (a few minutes)", 0.3)
        jobs = str(max(os.cpu_count() or 2, 2))
        _run(["make", "-j", jobs], root, "building ngspice")
        _run(["make", "install"], root, "installing ngspice")
        output_folder = os.path.join(target, "ngspice")
        lib = os.path.join(output_folder, "lib")
        os.makedirs(os.path.join(lib, "ngspice"), exist_ok=True)
        built = [path for path in glob.glob(os.path.join(prefix, "lib", "libngspice*"))
                 if path.endswith((".dylib", ".so")) or ".so." in path]
        if not built:
            raise VendorError("ngspice built no shared library")
        for path in built:
            destination = os.path.join(lib, os.path.basename(path))
            if os.path.islink(path):
                if os.path.lexists(destination):
                    os.remove(destination)
                os.symlink(os.readlink(path), destination)
            else:
                shutil.copy2(path, destination)
        for path in glob.glob(os.path.join(prefix, "lib", "ngspice", "*.cm")):
            if os.path.basename(path) != "table.cm":  # (GPL: not bundled)
                shutil.copy2(path, os.path.join(lib, "ngspice"))
        for name in ("COPYING",):
            if os.path.isfile(os.path.join(root, name)):
                shutil.copy(os.path.join(root, name), os.path.join(output_folder, "COPYING-ngspice"))
        if sys.platform == "darwin":
            main = next((path for path in glob.glob(os.path.join(lib, "libngspice.*.dylib"))
                         if not os.path.islink(path)), None)
            if main:
                subprocess.run(["install_name_tool", "-id", "@rpath/" + os.path.basename(main), main],
                               capture_output=True)
        if progress is not None:
            progress("ngspice is built", 1.0)
        found = sorted(glob.glob(os.path.join(lib, "libngspice.so.0")) + glob.glob(os.path.join(lib, "libngspice.0.dylib")))
        return found[0] if found else built[0]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build(name: str, target: str, progress: Progress | None = None) -> str:
    if name == "simavr":
        return build_simavr(target, progress)
    if name == "ngspice":
        return build_ngspice(target, progress)
    raise VendorError(f"{name} is not built from its sources")
