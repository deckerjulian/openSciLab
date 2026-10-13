// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// What the three capture modes share (capture.cpp, capture_stream.cpp, capture_state.cpp,
// capture_buffer.cpp).

#ifndef CAPTURE_INTERNAL_H
#define CAPTURE_INTERNAL_H

#include "board.h"
#include "board_config.h"
#include "capture.h"
#include "hal.h"
#include "protocol.h"
#include "ring.h"

namespace capture {

struct Config {
    bool valid;
    uint8_t mode;
    uint32_t rate;
    board::Pins mask;
    uint16_t adc;
    uint8_t adc_count;
    uint32_t samples;  // 0: until stopped (stream, state)
    uint32_t pre;
    uint8_t trigger;
    board::Pins trigger_mask;
    board::Pins trigger_levels;
    uint8_t clock_pin;
    uint8_t clock_edge;
    uint32_t period;   // buffer: ticks of the counted clock per sample
    uint32_t ticks;    // buffer: ticks per second of the counted clock
    // The ports the capture reads: those of the mask in ascending order (a sample is their bytes,
    // masked), then those only the trigger needs.
    uint8_t ports;
    uint8_t width;                  // the ports of the mask: bytes of a sample
    uint8_t port[FW_PORTS];         // the port of each
    uint8_t keep[FW_PORTS];         // the bits of the mask in it
    uint8_t trigger_bits[FW_PORTS]; // the bits of the trigger mask in it
    uint8_t trigger_want[FW_PORTS]; // the trigger levels of those bits
};

extern Config config;

// IDLE -> RUNNING -> (the sampling ends) DRAINING -> DONE sent -> IDLE
const uint8_t IDLE = 0;
const uint8_t RUNNING = 1;
const uint8_t DRAINING = 2;

extern volatile uint8_t phase;
extern volatile bool ending;        // set when the sampling should stop (interrupt or abort)
extern volatile uint8_t end_status; // DONE status
extern volatile uint32_t taken;     // samples (frames) taken
extern volatile uint32_t lost;      // state mode: edges lost
extern uint8_t memory[FW_CAPTURE_BYTES];

// the levels of the ports the capture reads (config.ports bytes)
FW_ISR static inline void read_ports(uint8_t *now) {
    for (uint8_t i = 0; i < config.ports; i++) now[i] = hal::read_port(config.port[i]);
}

// a sample of the levels read (config.width bytes)
FW_ISR static inline void put_sample(uint8_t *to, const uint8_t *now) {
    for (uint8_t i = 0; i < config.width; i++) to[i] = (uint8_t)(now[i] & config.keep[i]);
}

FW_ISR static inline void copy_ports(uint8_t *to, const uint8_t *from) {
    for (uint8_t i = 0; i < config.ports; i++) to[i] = from[i];
}

// the levels count as a trigger (prev: the levels before; both as read_ports)
FW_ISR static inline bool trigger_hit(const uint8_t *prev, const uint8_t *now) {
    switch (config.trigger) {
        case proto::TRIGGER_RISING:
            for (uint8_t i = 0; i < config.ports; i++)
                if ((prev[i] ^ now[i]) & now[i] & config.trigger_bits[i]) return true;
            return false;
        case proto::TRIGGER_FALLING:
            for (uint8_t i = 0; i < config.ports; i++)
                if ((prev[i] ^ now[i]) & prev[i] & config.trigger_bits[i]) return true;
            return false;
        case proto::TRIGGER_PATTERN:
            for (uint8_t i = 0; i < config.ports; i++)
                if ((now[i] & config.trigger_bits[i]) != config.trigger_want[i]) return false;
            return true;
        default: return true;
    }
}

static inline void finish(uint8_t status) {
    if (!ending) {
        end_status = status;
        ending = true;
    }
}

// sends DONE and returns to IDLE
void send_done(uint32_t samples);

uint8_t stream_start();
void stream_stop();   // the sampling stops (from the main loop)
void stream_poll();

uint8_t state_start();
void state_stop();
void state_poll();

uint8_t buffer_setup(uint32_t *actual);
uint8_t buffer_start();
void buffer_stop();
void buffer_poll();

// payload room for records behind the DATA header (event, kind, index)
const uint8_t DATA_ROOM = (uint8_t)(proto::MAX_PAYLOAD - 6);

}  // namespace capture

#endif
