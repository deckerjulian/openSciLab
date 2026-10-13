// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// What every board derives from its pin table.

#include "board.h"

namespace board {

uint8_t analog_count() {
    uint8_t count = 0;
    for (uint8_t i = 0; i < PIN_COUNT; i++) {
        PinDef def = pin(i);
        if (def.analog != NO_CHANNEL && def.analog + 1 > count) count = (uint8_t)(def.analog + 1);
    }
    return count;
}

uint8_t analog_pin(uint8_t channel) {
    for (uint8_t i = 0; i < PIN_COUNT; i++)
        if (pin(i).analog == channel) return i;
    return NO_CHANNEL;
}

uint8_t dac_pin() {
    for (uint8_t i = 0; i < PIN_COUNT; i++)
        if (pin(i).caps & PC_DAC) return i;
    return NO_CHANNEL;
}

Pins pins_with(uint16_t caps) {
    Pins mask = Pins::none();
    for (uint8_t i = 0; i < PIN_COUNT; i++) {
        PinDef def = pin(i);
        if (exists(def) && (def.caps & caps) == caps) mask.add(i);
    }
    return mask;
}

Pins reserved_pins() {
    Pins mask = Pins::none();
    for (uint8_t i = 0; i < PIN_COUNT; i++)
        if (pin(i).reserved != RES_FREE) mask.add(i);
    return mask;
}

const char *reserved_text(uint8_t why) {
    switch (why) {
        case RES_SERIAL_RX: return FW_STR("USB serial (RX)");
        case RES_SERIAL_TX: return FW_STR("USB serial (TX)");
        case RES_USB_DM: return FW_STR("USB (D-)");
        case RES_USB_DP: return FW_STR("USB (D+)");
        default: return FW_STR("in use");
    }
}

}  // namespace board
