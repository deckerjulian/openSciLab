# Vendor folder

Programs and libraries of others that openSciLab uses, one folder per platform
(`macos-arm64`, `linux-x86_64`, `windows-x64`, ...): ngspice (`ngspice/lib/libngspice.*` with its code
models), the AVR emulator (`simavr/libopenscilab_avr.*`) and KiCad's symbol libraries
(`kicad-symbols/`), each with its license text (`COPYING-*`). The applications bring ngspice and the
emulator here (the CI builds the folder with `python tools/build_vendor.py`); in a checkout that tool
builds them here, and a source installation installs what is missing from the tools' own projects
when it is first needed (`openscilab/core/vendor.py`, docs/circuits.md). Nothing in here but this
file is kept in git.
