#!/bin/sh
# Builds the firmware of the example (needs avr-gcc, e.g. PlatformIO's toolchain-atmelavr).
set -e
AVR=${AVR_GCC:-$(command -v avr-gcc || echo ~/.platformio/packages/toolchain-atmelavr/bin/avr-gcc)}
cd "$(dirname "$0")"
"$AVR" -mmcu=atmega328p -DF_CPU=16000000UL -Os -o blink_pot.elf blink_pot.c
"$(dirname "$AVR")/avr-objcopy" -O ihex -R .eeprom blink_pot.elf blink_pot.hex
rm -f blink_pot.elf
