#!/usr/bin/env python3
# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the vendor folder of this platform (openscilab/vendor/<platform>/).

    python tools/build_vendor.py                   # ngspice and the AVR emulator
    python tools/build_vendor.py --only simavr     # the AVR emulator only

ngspice is built from its sources on macOS and Linux (a C compiler, make); on Windows ngspice's own DLL
package is installed. The AVR emulator needs a C compiler and avr-libc's headers (core/vendor_build.py).
The folder is not in git and not in the applications: openSciLab hands on none of these tools, it
installs what is missing on the computer it runs on (core/vendor.py).
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from openscilab.core import vendor, vendor_build


def progress(text: str, share: float) -> None:
    print(f"  {share * 100:5.1f} %  {text}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=("ngspice", "simavr"), help="build only this")
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
            vendor._install_ngspice_windows(target, progress)
            print(" ", vendor.find("ngspice", system=False, settings=False))
        else:
            print(" ", vendor_build.build_ngspice(target, progress))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
