# Vendor folder

Programs and libraries of others that openSciLab uses, one folder per platform
(`macos-arm64`, `linux-x86_64`, `windows-x64`, ...): ngspice (`ngspice/lib/libngspice.*` with its code
models), the AVR emulator (`simavr/libopenscilab_avr.*`) and KiCad's symbol libraries
(`kicad-symbols/`). The applications bring them; in a checkout `python tools/build_vendor.py` builds
them here, and openSciLab installs what is missing on its own (`openscilab/core/vendor.py`,
docs/circuits.md). Nothing in here but this file is kept in git.
