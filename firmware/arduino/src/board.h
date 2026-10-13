// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The board: its pin table and its limits. Every board (src/arch/<arch>/board_<arch>.cpp, the
// fake board of the native tests in src/arch/native/) defines PIN_TABLE, PIN_COUNT and INFO.
//
// Pins on the wire are indexes into the pin table. Masks and levels are FW_PORTS bytes: bit b of
// byte k is pin 8k+b, and byte k is what hal::read_port(k) reads. On the AVR boards byte k is a
// port of the chip, so the table has gaps (GAP: no pin at that number) where a port has no pin.

#ifndef BOARD_H
#define BOARD_H

#include "board_config.h"
#include "platform.h"

#define FIRMWARE_VERSION "0.1.0b2"

namespace board {

// pin capabilities (docs/protocols.md, "Pins")
const uint16_t PC_DIN = 1 << 0;
const uint16_t PC_DOUT = 1 << 1;
const uint16_t PC_PULLUP = 1 << 2;
const uint16_t PC_PULLDOWN = 1 << 3;
const uint16_t PC_PWM = 1 << 4;
const uint16_t PC_ADC = 1 << 5;
const uint16_t PC_DAC = 1 << 6;
const uint16_t PC_CLOCK = 1 << 7;
const uint16_t PC_CLOCK_SW = 1 << 8;
const uint16_t PC_SQUARE = 1 << 9;  // the square wave output has a timer of its own on the pin

// why a pin is reserved
const uint8_t RES_FREE = 0;
const uint8_t RES_SERIAL_RX = 1;
const uint8_t RES_SERIAL_TX = 2;
const uint8_t RES_USB_DM = 3;
const uint8_t RES_USB_DP = 4;

const uint8_t NO_CHANNEL = 0xFF;  // no analog channel

struct PinDef {
    char name[8];      // empty: no pin at this number (GAP)
    uint8_t gpio;      // the Arduino pin number (digitalWrite, analogRead, ...)
    uint16_t caps;
    uint8_t analog;    // analog channel or NO_CHANNEL
    uint8_t reserved;  // RES_FREE or why the pin is reserved
};

// a number of the pin table without a pin
#define GAP {"", 0xFF, 0, board::NO_CHANNEL, board::RES_FREE}

static inline bool exists(const PinDef &def) { return def.name[0] != 0; }

// A set of pins in the wire format of masks and levels: bit b of port[k] is pin 8k+b.
struct Pins {
    uint8_t port[FW_PORTS];

    static Pins none() {
        Pins p;
        p.clear();
        return p;
    }
    static Pins of(uint8_t pin) {
        Pins p = none();
        p.add(pin);
        return p;
    }
    // FW_PORTS bytes of a request
    static Pins from(const uint8_t *data) {
        Pins p;
        memcpy(p.port, data, FW_PORTS);
        return p;
    }
    void clear() { memset(port, 0, FW_PORTS); }
    bool has(uint8_t pin) const { return pin < 8 * FW_PORTS && (port[pin >> 3] >> (pin & 7)) & 1; }
    void add(uint8_t pin) {
        if (pin < 8 * FW_PORTS) port[pin >> 3] |= (uint8_t)(1u << (pin & 7));
    }
    void remove(uint8_t pin) {
        if (pin < 8 * FW_PORTS) port[pin >> 3] &= (uint8_t)~(1u << (pin & 7));
    }
    void add(const Pins &other) {
        for (uint8_t k = 0; k < FW_PORTS; k++) port[k] |= other.port[k];
    }
    void remove(const Pins &other) {
        for (uint8_t k = 0; k < FW_PORTS; k++) port[k] &= (uint8_t)~other.port[k];
    }
    void keep(const Pins &other) {
        for (uint8_t k = 0; k < FW_PORTS; k++) port[k] &= other.port[k];
    }
    bool any() const {
        for (uint8_t k = 0; k < FW_PORTS; k++)
            if (port[k]) return true;
        return false;
    }
    bool meets(const Pins &other) const {
        for (uint8_t k = 0; k < FW_PORTS; k++)
            if (port[k] & other.port[k]) return true;
        return false;
    }
    bool operator==(const Pins &other) const { return memcmp(port, other.port, FW_PORTS) == 0; }
    // the ports (bytes) with a pin of the set
    uint8_t ports() const {
        uint8_t n = 0;
        for (uint8_t k = 0; k < FW_PORTS; k++)
            if (port[k]) n++;
        return n;
    }
    uint8_t count() const {
        uint8_t n = 0;
        for (uint8_t k = 0; k < FW_PORTS; k++)
            for (uint8_t b = port[k]; b; b &= (uint8_t)(b - 1)) n++;
        return n;
    }
};

struct Info {
    uint32_t clock_hz;          // CPU clock
    uint32_t serial_baud;       // of the USB serial line
    uint8_t adc_bits;
    uint8_t dac_bits;           // 0: no DAC
    uint16_t vref_mv;           // ADC reference
    uint16_t logic_mv;          // logic level of the pins
    uint32_t stream_max_rate;   // stream captures, samples per second (until measured: step 2a)
    uint32_t buffer_max_rate;   // buffer captures (counted loop)
    uint32_t monitor_max_mhz;   // monitor reports, mHz
    uint32_t generator_max_rate;
    uint32_t arb_max_rate;      // DAC points per second (0: no AFG)
    uint32_t stream_bytes;      // bytes per second the line carries (CAPS STREAM=)
    bool gpio_locked_in_buffer; // a buffer capture blocks the GPIO commands (AVR)
};

extern const PinDef PIN_TABLE[] FW_FLASH;
extern const uint8_t PIN_COUNT;
extern const Info INFO;
// "uno", "nano", ... (a flash string)
const char *name();
// "USB serial (RX)", ... (a flash string)
const char *reserved_text(uint8_t why);

static inline PinDef pin(uint8_t index) {
    PinDef def;
    fw_flash_copy(&def, &PIN_TABLE[index], sizeof(def));
    return def;
}

// analog channels (the highest channel + 1)
uint8_t analog_count();
// the pin of an analog channel, or NO_CHANNEL
uint8_t analog_pin(uint8_t channel);
// the first pin with the DAC capability, or NO_CHANNEL
uint8_t dac_pin();
// pins with all of caps
Pins pins_with(uint16_t caps);
// pins that are reserved
Pins reserved_pins();

}  // namespace board

#endif
