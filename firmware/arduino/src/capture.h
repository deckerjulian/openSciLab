// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Captures: stream (timer interrupt, digital as runs, analog alongside), buffer (counted loop
// into RAM, pre-trigger by a ring) and state (an interrupt on the edges of a clock pin).

#ifndef CAPTURE_H
#define CAPTURE_H

#include "board.h"

namespace capture {

// CAPTURE_SETUP: <BIHIIBBB>, then the pin mask, trigger mask and trigger levels; answers the actual rate
uint8_t setup(const uint8_t *data, uint8_t n, uint32_t *actual);
// CAPTURE_START
uint8_t start();
// CAPTURE_ABORT (the event DONE follows)
void abort();
// a capture runs (or still sends its data)
bool running();
// pins of the running capture (channels, trigger, clock)
board::Pins pins();
// analog channels of the running capture
uint16_t adc_mask();
// a buffer capture runs on a board whose GPIO commands must wait (AVR)
bool gpio_locked();
// sends data and DONE; runs buffer captures
void poll();

}  // namespace capture

#endif
