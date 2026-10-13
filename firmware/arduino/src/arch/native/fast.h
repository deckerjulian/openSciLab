// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The fast pin functions of the fake board (native tests): see fake_hal.h.

#ifndef ARCH_NATIVE_FAST_H
#define ARCH_NATIVE_FAST_H

#include <stdint.h>

namespace hal {

typedef uint32_t cycle_t;

namespace fake {
extern uint32_t input_levels;               // what drives the inputs
extern uint32_t (*input_source)();          // or a function (called per read)
extern uint32_t output_mask;                // pins that are outputs
extern uint32_t output_levels;
extern uint32_t cycle_counter;
extern uint32_t cycle_step;                 // cycles() advances by this per call
extern uint16_t dac_value;                  // the value of the first DAC
void record_write(uint32_t mask, uint32_t levels);
}  // namespace fake

// the levels of all pins (bit n: pin n)
static inline uint32_t read_levels() {
    uint32_t input = fake::input_source ? fake::input_source() : fake::input_levels;
    return (input & ~fake::output_mask) | (fake::output_levels & fake::output_mask);
}

static inline uint8_t read_port(uint8_t k) { return (uint8_t)(read_levels() >> (8 * k)); }

static inline void write_port(uint8_t k, uint8_t mask, uint8_t levels) {
    uint32_t m = (uint32_t)mask << (8 * k), l = (uint32_t)levels << (8 * k);
    fake::output_levels = (fake::output_levels & ~m) | (l & m);
    fake::record_write(m, l);
}

static inline cycle_t cycles() { return fake::cycle_counter += fake::cycle_step; }

static inline void dac_write_fast(uint16_t raw) { fake::dac_value = raw; }

}  // namespace hal

#endif
