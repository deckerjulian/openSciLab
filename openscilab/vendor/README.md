# Vendor folder

Programs and libraries of others that openSciLab uses, one folder per platform
(`macos-arm64`, `linux-x86_64`, `windows-x64`, ...): ngspice (`ngspice/lib/libngspice.*` with its code
models), the AVR emulator (`simavr/libopenscilab_avr.*`) and KiCad's symbol libraries
(`kicad-symbols/`). openSciLab hands on none of them: they are installed on the computer from their
own projects when first needed (`openscilab/core/vendor.py`, docs/circuits.md); in a checkout
`python tools/build_vendor.py` puts them here. Nothing in here but this file is kept in git, and the
applications do not contain this folder.
