// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Fast pin access on the AVR boards. Port k of the pin table is a port of the chip, so the levels
// of eight pins are one read:
//   Uno, Nano (ATmega328P): ports D (D0-D7), B (D8-D13), C (A0-A5; the Nano's A6/A7 sit in its
//                           unused bits 6 and 7, analog only)
//   Mega 2560:              ports A, B, C, D, E, F (A0-A7), G, H, J, K (A8-A15), L

#ifndef ARCH_AVR_FAST_H
#define ARCH_AVR_FAST_H

#include <avr/interrupt.h>
#include <avr/io.h>
#include <stdint.h>

#include "../../board_config.h"

namespace hal {

// the counted clock is Timer1
typedef uint16_t cycle_t;
static inline cycle_t cycles() { return TCNT1; }

namespace avr {
// the input register (PINx) of each port; DDRx and PORTx follow it
extern volatile uint8_t *const PORT_INPUT[FW_PORTS];
}  // namespace avr

static inline uint8_t read_port(uint8_t k) { return *avr::PORT_INPUT[k]; }

static inline void write_port(uint8_t k, uint8_t mask, uint8_t levels) {
    if (!mask) return;
    volatile uint8_t *out = avr::PORT_INPUT[k] + 2;
    // ports beyond the I/O space (Mega: H to L) have no bit instructions: interrupts stay out
    uint8_t sreg = SREG;
    cli();
    *out = (uint8_t)((*out & ~mask) | (levels & mask));
    SREG = sreg;
}

static inline void dac_write_fast(uint16_t) {}  // no DAC

}  // namespace hal

#endif
