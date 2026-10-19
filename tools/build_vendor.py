#!/usr/bin/env python3
# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the vendor folder of this platform (openscilab/vendor/<platform>/) and its archive.

    python tools/build_vendor.py                   # ngspice and the AVR emulator
    python tools/build_vendor.py --only simavr     # the AVR emulator only
    python tools/build_vendor.py --zip dist/       # and openSciLab-vendor-<platform>.zip with its .sha256

ngspice is built from its sources on macOS and Linux (a C compiler, make); on Windows it is taken
from MSYS2 (``pacman -S mingw-w64-x86_64-ngspice``) with the DLLs it needs. The AVR emulator needs a C
compiler and avr-libc's headers (core/vendor_build.py). The CI builds the archives the applications
bundle and that openSciLab installs from (core/vendor.py); KiCad's symbols are not in them (openSciLab
installs those when they are wanted).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from openscilab.core import vendor, vendor_build

MSYS2 = (r"C:\msys64\mingw64", r"C:\msys64\ucrt64")
#: DLLs of Windows itself (not copied)
SYSTEM_DLLS = re.compile(r"^(kernel32|user32|advapi32|msvcrt|ws2_32|shell32|ole32|oleaut32|gdi32|comdlg32|"
                         r"api-ms-win-.*|ucrtbase|bcrypt|ntdll|shlwapi|psapi|version|winmm|comctl32)\.dll$", re.IGNORECASE)


def progress(text: str, share: float) -> None:
    print(f"  {share * 100:5.1f} %  {text}", flush=True)


def ngspice_from_msys2(target: str) -> str:
    base = next((path for path in MSYS2 if os.path.isfile(os.path.join(path, "bin", "libngspice-0.dll"))), None)
    if base is None:
        raise SystemExit("ngspice of MSYS2 is missing: pacman -S mingw-w64-x86_64-ngspice")
    binary = os.path.join(target, "ngspice", "bin")
    models = os.path.join(target, "ngspice", "lib", "ngspice")
    os.makedirs(binary, exist_ok=True)
    os.makedirs(models, exist_ok=True)
    objdump = shutil.which("objdump") or os.path.join(base, "bin", "objdump.exe")
    wanted, done = ["libngspice-0.dll"], set()
    while wanted:
        name = wanted.pop()
        if name.lower() in done:
            continue
        done.add(name.lower())
        source = os.path.join(base, "bin", name)
        if not os.path.isfile(source):
            continue
        shutil.copy2(source, binary)
        listing = subprocess.run([objdump, "-p", source], capture_output=True, text=True).stdout
        for needed in re.findall(r"DLL Name: (\S+)", listing):
            if not SYSTEM_DLLS.match(needed):
                wanted.append(needed)
    for path in glob.glob(os.path.join(base, "lib", "ngspice", "*.cm")):
        if os.path.basename(path) != "table.cm":
            shutil.copy2(path, models)
    # (the notices of ngspice and of the MinGW runtime it links travel with the archive)
    for name in ("ngspice", "gcc-libs", "libwinpthread", "libgomp"):
        for path in glob.glob(os.path.join(base, "share", "licenses", name, "*")):
            if os.path.isfile(path):
                shutil.copy2(path, os.path.join(target, "ngspice", f"{name}-{os.path.basename(path)}"))
    return os.path.join(binary, "libngspice-0.dll")


def archive(target: str, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"openSciLab-vendor-{vendor.platform_tag()}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        for name in ("ngspice", "simavr"):
            for current, _dirs, files in os.walk(os.path.join(target, name)):
                for file in sorted(files):
                    full = os.path.join(current, file)
                    handle.write(full, os.path.relpath(full, target))
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    with open(path + ".sha256", "w") as handle:
        handle.write(f"{digest}  {os.path.basename(path)}\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=("ngspice", "simavr"), help="build only this")
    parser.add_argument("--zip", metavar="FOLDER", help="also write the archive of the vendor folder there")
    parser.add_argument("--simavr-source", help="a checkout of simavr instead of its download")
    arguments = parser.parse_args()
    target = vendor.package_directory()
    os.makedirs(target, exist_ok=True)
    print(f"vendor folder: {target}")
    if arguments.only in (None, "simavr"):
        print("the AVR emulator (simavr)")
        print(" ", vendor_build.build_simavr(target, progress, arguments.simavr_source))
    if arguments.only in (None, "ngspice"):
        print("ngspice")
        if sys.platform.startswith("win"):
            print(" ", ngspice_from_msys2(target))
        else:
            print(" ", vendor_build.build_ngspice(target, progress))
    if arguments.zip:
        print("archive:", archive(target, arguments.zip))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
