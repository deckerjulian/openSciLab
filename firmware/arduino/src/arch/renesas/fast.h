// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Fast pin access on the Uno R4 (RA4M1): the pins sit on ports 0, 1 and 3; the levels are read
// from the port input registers and gathered to the pin bits by a table made at start-up. The
// counted clock is the cycle counter of the Cortex-M4 (DWT).

#ifndef ARCH_RENESAS_FAST_H
#define ARCH_RENESAS_FAST_H

#include <Arduino.h>
#include <stdint.h>

namespace hal {

typedef uint32_t cycle_t;

namespace ra {
const uint8_t PORTS = 5;
extern R_PORT0_Type *const port[PORTS];
extern uint8_t pin_port[32];
extern uint16_t pin_mask[32];
extern uint8_t pin_count;
}  // namespace ra

static inline cycle_t cycles() { return DWT->CYCCNT; }

// the pins 8k to 8k+7 of the table, gathered from their ports
static inline uint8_t read_port(uint8_t k) {
    uint8_t levels = 0;
    for (uint8_t b = 0, i = (uint8_t)(8 * k); b < 8 && i < ra::pin_count; b++, i++)
        if (ra::port[ra::pin_port[i]]->PIDR & ra::pin_mask[i]) levels |= (uint8_t)(1u << b);
    return levels;
}

static inline void write_port(uint8_t k, uint8_t mask, uint8_t levels) {
    uint16_t set[ra::PORTS] = {0}, reset[ra::PORTS] = {0};
    for (uint8_t b = 0, i = (uint8_t)(8 * k); b < 8 && i < ra::pin_count; b++, i++) {
        uint8_t bit = (uint8_t)(1u << b);
        if (!(mask & bit)) continue;
        if (levels & bit) set[ra::pin_port[i]] |= ra::pin_mask[i];
        else reset[ra::pin_port[i]] |= ra::pin_mask[i];
    }
    for (uint8_t p = 0; p < ra::PORTS; p++) {
        if (set[p]) ra::port[p]->POSR = set[p];
        if (reset[p]) ra::port[p]->PORR = reset[p];
    }
}

static inline void dac_write_fast(uint16_t raw) { R_DAC->DADR[0] = raw; }

}  // namespace hal

#endif
