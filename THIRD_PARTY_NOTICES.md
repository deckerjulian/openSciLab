# Credits and third-party notices

openSciLab (formerly PiPiLogicAnalyzer) is licensed under the GNU General Public License v3 (see [LICENSE](LICENSE)).
It contains, is based on, or is distributed together with the following works. Their copyright
notices and licenses are kept; the complete license texts are part of the respective projects.

## LogicAnalyzer by Agustín Giménez Bernad (gusmanb)

<https://github.com/gusmanb/logicanalyzer> — GNU General Public License v3

openSciLab (formerly PiPiLogicAnalyzer) is a modified and extended version of this project, based
on version 6.5 (branch `version/v6_5`, commit `3fa3703`):

* the Pico firmware in `firmware/pico` is his firmware with the bug fixes and additions
  described in [firmware/README.md](firmware/README.md);
* the application is a Python/Qt port of his C#/Avalonia software (`LogicAnalyzer`,
  `SharedDriver`, `SignalDescriptionLanguage`, the sigrok decoder bridge) with the changes
  described in [docs/improvements.md](docs/improvements.md) and [CHANGELOG.md](CHANGELOG.md);
* the device protocol, the `.lac` file format, the profile format and the USB identifiers
  (VID 0x1209, PID 0x3020, manufacturer "Dr. Gusman") are his.

Thank you, Agustín, for the LogicAnalyzer hardware, firmware and software.

## DSView by DreamSourceLab (DSLogic driver)

<https://github.com/DreamSourceLab/DSView> — GNU General Public License v3 or later

The DSLogic driver in `openscilab/driver/dslogic` implements the USB protocol of the
DSLogic analyzers after the driver of DSView (`libsigrok4DSL/hardware/DSL`, `trigger.c`): device
profiles, commands, capture settings and data format. The FPGA bitstreams and firmware images of
the DSLogic boards are **not** included; they are loaded from a DSView installation or
downloaded from the DSView repository at the user's request. DSLogic and DSView are trademarks
of DreamSourceLab; openSciLab is not affiliated with DreamSourceLab.

## Protocol decoders (`decoders/`)

* Decoders of the **sigrok** project (libsigrokdecode), <https://sigrok.org>, copyright by their
  respective authors. Most are licensed under the GNU General Public License v2 or later or v3
  or later; a few are MIT (`ade77xx`, `caliper`, `enc28j60`, `nrf905`) or BSD-2/3-Clause (`cfp`,
  `mdio`). The license is stated in each file; all of them are GPL-compatible.
* Decoders added by the LogicAnalyzer 6.5 decoder set (for example `mos6502`, `mcp230xx`,
  `max72xx`), licensed as stated in each file.
* `c64bus`, part of this project, GNU General Public License v2 or later.

The decoders are shipped as source files, including in the packaged applications.

## Application dependencies (included in the packaged applications)

| Component | License | Project |
| --- | --- | --- |
| Python | Python Software Foundation License | <https://www.python.org> |
| Qt for Python (PySide6) and Qt 6 | GNU Lesser General Public License v3 | <https://www.qt.io/qt-for-python> |
| NumPy | BSD-3-Clause (with bundled OpenBLAS/LAPACK: BSD-3-Clause, license files in `numpy.libs`) | <https://numpy.org> |
| AppImage runtime (Linux build) | MIT, with squashfuse and zstd (BSD) | <https://github.com/AppImage/AppImageKit> |
| pyserial | BSD-3-Clause | <https://github.com/pyserial/pyserial> |
| pyusb | BSD-3-Clause | <https://github.com/pyusb/pyusb> |
| PyYAML (flow, panel and waveform files) | MIT | <https://pyyaml.org> |
| libusb (via libusb-package, shared library) | GNU Lesser General Public License v2.1 | <https://libusb.info>, <https://github.com/pyocd/libusb-package> |
| PyAV (the oscilloscope's screen as H.264) | BSD-3-Clause | <https://github.com/PyAV-Org/PyAV> |
| FFmpeg (shared libraries bundled with PyAV, built with GPL components such as x264) | GNU General Public License v2 or later (GPL-compatible with this project) | <https://ffmpeg.org> |
| PyInstaller bootloader | GPL-2.0-or-later with the PyInstaller bootloader exception | <https://pyinstaller.org> |
| Inter Tight (the typeface; not distributed with openSciLab: Google Fonts' static weights and the license are downloaded at the first start into `<settings>/fonts`, `openscilab/core/typeface.py`) | SIL Open Font License 1.1 (`OFL.txt`, downloaded with the fonts); Copyright 2022 The Inter Project Authors | <https://github.com/rsms/inter-tight>, <https://fonts.google.com/specimen/Inter+Tight> |

Qt and PySide6 are included as separate shared libraries, so they can be replaced by other
versions as the LGPL requires. Their source code is available from the Qt project.

## Circuits: simulators and libraries (the vendor folder, `openscilab/vendor/<platform>/`)

openSciLab distributes none of these tools: neither the applications nor the package contain them.
They are found on the computer or installed there from their own projects when they are first needed
(`openscilab/core/vendor.py`): ngspice's DLL package from ngspice's releases on Windows, ngspice and
the AVR emulator built from their pinned sources on macOS and Linux, KiCad's symbols from KiCad's
archive. In a checkout `tools/build_vendor.py` does the same.

| Component | License | Project |
| --- | --- | --- |
| ngspice (shared library `libngspice` and its XSPICE code models; built with CIDER and the KLU sparse solver, on Windows ngspice's own DLL package with its OpenMP runtime) | Modified BSD (BSD-3-Clause); XSPICE public domain; CIDER BSD-style (UC Berkeley); KLU: GNU Lesser General Public License v2.1 | <https://ngspice.sourceforge.io> |
| simavr (simulator core and AVR cores, statically linked into `libopenscilab_avr` with openSciLab's shim `openscilab/circuits/mcu/shim`) | GNU General Public License v3 or later | <https://github.com/buserror/simavr> |
| avr-libc register headers (only read when `libopenscilab_avr` is built) | Modified BSD | <https://github.com/avrdudes/avr-libc> |
| KiCad symbol libraries (installed on request into the vendor folder or read from a KiCad installation; not in the applications) | CC-BY-SA 4.0 with an exception for the use in designs | <https://gitlab.com/kicad/libraries/kicad-symbols> |

openSciLab's own SPICE models (`openscilab/circuits/models/standard.lib`) were written for it from
published data sheet values and are licensed like openSciLab (GPL-3.0-or-later); models of makers
that a user adds keep their own terms.

## Firmware components (included in the firmware images)

| Component | License | Project |
| --- | --- | --- |
| Raspberry Pi Pico SDK | BSD-3-Clause | <https://github.com/raspberrypi/pico-sdk> |
| TinyUSB | MIT | <https://github.com/hathach/tinyusb> |
| lwIP (WiFi builds) | BSD-3-Clause | <https://savannah.nongnu.org/projects/lwip/> |
| cyw43-driver (Pico W / Pico 2 W builds) | Raspberry Pi specific license, see below | <https://github.com/georgerobotics/cyw43-driver> |

**cyw43-driver:** Copyright (C) 2019-2022 George Robotics Pty Ltd. For use with Raspberry Pi
semiconductor devices it is licensed by Raspberry Pi Ltd under the terms of `LICENSE.RP` in the
driver: use and redistribution are permitted only in conjunction with the RP2040 or other
semiconductor devices produced by Raspberry Pi Ltd, and binary redistributions must reproduce
the copyright notice, the conditions and the disclaimer. The firmware images for the Pico W and
Pico 2 W contain this driver and may only be used on these boards.

## Build tools (not distributed)

The ARM GNU Toolchain, CMake, Ninja, picotool, PyInstaller, Pillow (icons) and appimagetool are
used to build the firmware and the applications; of appimagetool only its runtime travels with
the Linux build (see the table above). Runtime parts of the ARM GNU Toolchain linked into the firmware
(newlib, libgcc) are covered by their respective licenses and exceptions permitting this use.
