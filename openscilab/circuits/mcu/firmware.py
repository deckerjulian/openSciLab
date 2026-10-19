# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Firmware files as the image of a flash: Intel HEX (``.hex``) and ELF (``.elf``, what avr-gcc,
the Arduino IDE and PlatformIO build). No Qt, no libraries."""

from __future__ import annotations

import os
import struct

from .. import CircuitError

#: AVR addresses at and above this are the RAM (sections that are not in the flash)
AVR_RAM = 0x800000


def read_hex(text: str) -> tuple[bytes, int]:
    """``(image, start address)`` of an Intel HEX file."""
    memory: dict[int, int] = {}
    base = 0
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise CircuitError(f"line {number} of the HEX file is no record")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError:
            raise CircuitError(f"line {number} of the HEX file is no record") from None
        if len(record) < 5 or len(record) != record[0] + 5:
            raise CircuitError(f"line {number} of the HEX file has a wrong length")
        if sum(record) & 0xFF:
            raise CircuitError(f"line {number} of the HEX file has a wrong checksum")
        count, address, kind = record[0], (record[1] << 8) | record[2], record[3]
        data = record[4:4 + count]
        if kind == 0:
            for offset, value in enumerate(data):
                memory[base + address + offset] = value
        elif kind == 1:
            break
        elif kind in (2, 4):
            if len(data) < 2:
                raise CircuitError(f"line {number} of the HEX file has no address")
            base = ((data[0] << 8) | data[1]) << (4 if kind == 2 else 16)
    if not memory:
        return b"", 0
    start, end = min(memory), max(memory) + 1
    image = bytearray(b"\xff" * (end - start))
    for address, value in memory.items():
        image[address - start] = value
    return bytes(image), start


def read_elf(data: bytes) -> tuple[bytes, int]:
    """``(image, start address)`` of the flash in an ELF file (its loadable segments below the RAM)."""
    if data[:4] != b"\x7fELF":
        raise CircuitError("this is no ELF file")
    if data[4] != 1:
        raise CircuitError("only 32-bit ELF files hold AVR firmware")
    endian = "<" if data[5] == 1 else ">"
    try:
        (machine,) = struct.unpack_from(endian + "H", data, 18)
        if machine != 83:  # (EM_AVR)
            raise CircuitError("this ELF file was not built for an AVR microcontroller")
        (phoff,) = struct.unpack_from(endian + "I", data, 28)
        phentsize, phnum = struct.unpack_from(endian + "HH", data, 42)
        headers = [struct.unpack_from(endian + "IIIIIIII", data, phoff + index * phentsize) for index in range(phnum)]
    except struct.error:
        raise CircuitError("the ELF file is cut short") from None
    memory: dict[int, bytes] = {}
    for kind, offset, _vaddr, paddr, filesz, _memsz, _flags, _align in headers:
        if kind != 1 or not filesz or paddr >= AVR_RAM:  # (PT_LOAD into the flash)
            continue
        memory[paddr] = data[offset:offset + filesz]
    if not memory:
        raise CircuitError("the ELF file holds nothing for the flash")
    start = min(memory)
    end = max(address + len(chunk) for address, chunk in memory.items())
    image = bytearray(b"\xff" * (end - start))
    for address, chunk in memory.items():
        image[address - start:address - start + len(chunk)] = chunk
    return bytes(image), start


def read_firmware(path: str) -> tuple[bytes, int]:
    """The flash image of a ``.hex`` or ``.elf`` file; raises CircuitError, OSError."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] == b"\x7fELF":
        return read_elf(data)
    if path.lower().endswith((".hex", ".ihex", ".ihx")) or data[:1] == b":":
        return read_hex(data.decode("ascii", "replace"))
    raise CircuitError(f"{os.path.basename(path)} is neither a HEX nor an ELF file")
