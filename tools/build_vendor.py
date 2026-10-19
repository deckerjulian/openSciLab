#!/usr/bin/env python3
# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the vendor folder of this platform (openscilab/vendor/<platform>/), the applications bring.

    python tools/build_vendor.py                   # ngspice and the AVR emulator
    python tools/build_vendor.py --only simavr     # the AVR emulator only
    python tools/build_vendor.py --zip dist/       # and openSciLab-vendor-<platform>.zip (the CI's artifact)
    python tools/build_vendor.py --sources dist/   # only the source archives of both (for a release)

ngspice is built from its sources on macOS and Linux (a C compiler, make); on Windows ngspice's own DLL
package is installed, with ngspice's license from its sources. The AVR emulator needs a C compiler and
avr-libc's headers (core/vendor_build.py). Each tool's license text lies beside it (COPYING-*). The
folder is not in git: the CI builds it for each platform and puts it into the applications; a source
installation builds it here or installs what is missing when it is needed (core/vendor.py).
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from openscilab.core import vendor, vendor_build


def progress(text: str, share: float) -> None:
    print(f"  {share * 100:5.1f} %  {text}", flush=True)


def source_archives() -> dict[str, tuple[str, str]]:
    """The pinned source archives of the tools in the applications: file name -> (URL, SHA-256)."""
    return {
        f"simavr-{vendor_build.SIMAVR_VERSION}.tar.gz": (
            vendor_build.SIMAVR_URL.format(version=vendor_build.SIMAVR_VERSION), vendor_build.SIMAVR_SHA256),
        f"ngspice-{vendor_build.NGSPICE_VERSION}.tar.gz": (
            vendor_build.NGSPICE_URL.format(version=vendor_build.NGSPICE_VERSION), vendor_build.NGSPICE_SHA256),
    }


def write_sources(folder: str) -> list[str]:
    """The source archives into ``folder`` (a release carries them beside the applications: GPL and LGPL
    want the source of what is handed on as binaries)."""
    os.makedirs(folder, exist_ok=True)
    written = []
    for name, (url, sha256) in source_archives().items():
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(vendor._download(url, None, name, sha256=sha256))
        written.append(path)
    return written


def ngspice_license(target: str) -> str:
    """ngspice's COPYING from its pinned sources beside the DLL package (which has none)."""
    name, (url, sha256) = next(item for item in source_archives().items() if item[0].startswith("ngspice"))
    with tarfile.open(fileobj=io.BytesIO(vendor._download(url, None, name, sha256=sha256)), mode="r:gz") as archive:
        member = next(member for member in archive.getmembers()
                      if member.isfile() and member.name.replace("\\", "/").split("/")[1:] == ["COPYING"])
        data = archive.extractfile(member).read()
    path = os.path.join(target, "ngspice", "COPYING-ngspice")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def archive(target: str, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"openSciLab-vendor-{vendor.platform_tag()}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        for name in ("ngspice", "simavr"):
            for current, _dirs, files in os.walk(os.path.join(target, name)):
                for file in sorted(files):
                    full = os.path.join(current, file)
                    handle.write(full, os.path.relpath(full, target))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=("ngspice", "simavr"), help="build only this")
    parser.add_argument("--zip", metavar="FOLDER", help="also write the archive of the vendor folder there")
    parser.add_argument("--sources", metavar="FOLDER", help="only write the source archives of the tools there")
    parser.add_argument("--simavr-source", help="a checkout of simavr instead of its download")
    arguments = parser.parse_args()
    if arguments.sources:
        for path in write_sources(arguments.sources):
            print("source:", path)
        return 0
    target = vendor.package_directory()
    os.makedirs(target, exist_ok=True)
    print(f"vendor folder: {target}")
    if arguments.only in (None, "simavr"):
        print("the AVR emulator (simavr)")
        print(" ", vendor_build.build_simavr(target, progress, arguments.simavr_source))
    if arguments.only in (None, "ngspice"):
        print("ngspice")
        if sys.platform.startswith("win"):
            vendor._install_ngspice_windows(target, progress)
            print(" ", vendor.find("ngspice", system=False, settings=False))
            print(" ", ngspice_license(target))
        else:
            print(" ", vendor_build.build_ngspice(target, progress))
    if arguments.zip:
        print("archive:", archive(target, arguments.zip))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
