#!/bin/sh
# Builds the test firmware of the emulated microcontrollers (needs avr-gcc, e.g. PlatformIO's).
set -e
AVR=${AVR_GCC:-$(command -v avr-gcc || echo ~/.platformio/packages/toolchain-atmelavr/bin/avr-gcc)}
BIN=$(dirname "$AVR")
cd "$(dirname "$0")"
for mcu in atmega328p atmega2560; do
  "$AVR" -mmcu=$mcu -DF_CPU=16000000UL -Os -o blink_serial-$mcu.elf blink_serial.c
  "$BIN/avr-objcopy" -O ihex -R .eeprom blink_serial-$mcu.elf blink_serial-$mcu.hex
done
