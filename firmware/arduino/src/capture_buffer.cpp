// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Buffer captures: a loop paced by the counted clock reads the pins into RAM (a byte per port of
// the mask). While it waits for the trigger it writes a ring in turns of about 20 ms and returns
// to the main loop between them (answers, abort); the ring, the trigger detection and the sample
// clock go on across the turns, so an edge between two turns still triggers. Once the
// pre-trigger samples are in and the trigger hits, it records the rest without a break. Then the
// samples go out as runs. A sample the loop could not take in time while it records ends the
// capture with DONE status overflow.

#include "app.h"
#include "board.h"
#include "capture_internal.h"
#include "generator.h"

using namespace proto;

namespace capture {

static const uint8_t ARMED = 0;    // waiting for the trigger
static const uint8_t SENDING = 1;  // sending the samples
static uint8_t stage;
static uint8_t width;              // bytes per sample
static uint32_t total;             // samples in the buffer
static uint32_t first;             // where the first sample is in the ring
static uint32_t cursor;            // samples sent
static bool late;

// the wait for the trigger, carried from turn to turn
static uint32_t ring_index;        // where the next sample goes
static uint32_t ring_filled;       // pre-trigger samples in the ring (up to pre)
static uint8_t previous[FW_PORTS]; // the levels of the last sample (edge triggers; as read_ports)
static hal::cycle_t next;          // when the next sample is due
static uint32_t turn_end_us;       // when the last turn returned to the main loop
static uint32_t wrap_us;           // a longer pause may have wrapped the counted clock

static const hal::cycle_t HALF = (hal::cycle_t)(((hal::cycle_t)~(hal::cycle_t)0) / 2 + 1);

static inline uint8_t *at(uint32_t index) { return memory + index * width; }

static bool same(const uint8_t *a, const uint8_t *b) {
    for (uint8_t i = 0; i < width; i++)
        if (a[i] != b[i]) return false;
    return true;
}

uint8_t buffer_setup(uint32_t *actual) {
    Config &c = config;
    if (c.adc) return ERR_UNSUPPORTED;  // the counted loop reads pins only
    if (c.rate == 0 || c.rate > board::INFO.buffer_max_rate) return ERR_ARGUMENTS;
    uint32_t capacity = FW_CAPTURE_BYTES / c.width;
    if (c.samples == 0 || c.pre >= c.samples) return ERR_ARGUMENTS;
    if (c.samples > capacity) return ERR_OVERFLOW;
    // the loop does not answer while it records behind the trigger: two seconds at most
    if ((c.samples - c.pre) > 2 * c.rate) return ERR_ARGUMENTS;
    c.ticks = hal::clock_ticks((float)c.rate);
    if (!c.ticks) return ERR_UNSUPPORTED;
    c.period = (uint32_t)(((float)c.ticks / (float)c.rate) + 0.5f);
    if (c.period == 0 || c.period >= (uint32_t)HALF) return ERR_ARGUMENTS;
    *actual = (uint32_t)((float)c.ticks / (float)c.period + 0.5f);
    return 0;
}

uint8_t buffer_start() {
    if (board::INFO.gpio_locked_in_buffer && gen::driving()) return ERR_BUSY;  // its timer would disturb
    uint32_t ticks;
    uint8_t error = hal::clock_begin((float)config.rate, &ticks);
    if (error) return error;
    if (ticks != config.ticks) {
        hal::clock_end();
        return ERR_BUSY;
    }
    width = config.width;
    total = config.samples;
    cursor = 0;
    late = false;
    ring_index = ring_filled = 0;
    read_ports(previous);
    next = (hal::cycle_t)(hal::cycles() + (hal::cycle_t)config.period);
    wrap_us = (uint32_t)((uint64_t)HALF * 1000000u / ticks / 2);
    turn_end_us = hal::micros();
    stage = ARMED;
    phase = RUNNING;
    return 0;
}

void buffer_stop() {
    if (stage == ARMED) hal::clock_end();
}

// records the samples behind the trigger (index: where the trigger sample went, due: when the
// next sample is)
static void record(uint32_t index, hal::cycle_t due) {
    hal::cycle_t period = (hal::cycle_t)config.period;
    uint32_t remaining = total - config.pre - 1;
    uint8_t *p = at(index);
    uint8_t *end = memory + total * width;
    const uint8_t w = width;
    hal::quiet_begin();
    while (remaining--) {
        hal::cycle_t now = hal::cycles();
        if ((hal::cycle_t)(now - due) < HALF && (hal::cycle_t)(now - due) >= period) late = true;
        while ((hal::cycle_t)(hal::cycles() - due) >= HALF) {
        }
        due += period;
        p += w;
        if (p == end) p = memory;
        for (uint8_t i = 0; i < w; i++) p[i] = (uint8_t)(hal::read_port(config.port[i]) & config.keep[i]);
    }
    hal::quiet_end((uint32_t)((uint64_t)(total - config.pre) * 1000000u / config.rate));
}

// a sample into the ring (it holds the last pre-trigger samples)
static inline void ring_put(const uint8_t *levels) {
    put_sample(at(ring_index), levels);
    if (ring_filled < config.pre) ring_filled++;
    ring_index = ring_index + 1 == total ? 0 : ring_index + 1;
}

// the samples the main loop missed hold the levels before its turn (keeps the time base)
static void catch_up() {
    uint32_t paused = hal::micros() - turn_end_us;
    hal::cycle_t now = hal::cycles();
    uint32_t missed = 0;
    if (paused >= wrap_us) {  // the counted clock may have wrapped meanwhile: count by the time
        missed = (uint32_t)((uint64_t)paused * config.rate / 1000000u);
        next = now;
    } else if ((hal::cycle_t)(now - next) < HALF) {
        missed = (uint32_t)((hal::cycle_t)(now - next) / (hal::cycle_t)config.period);
        next += (hal::cycle_t)(missed * config.period);
    }
    if (missed >= total) {  // the whole ring holds the levels
        for (uint32_t i = 0; i < total; i++) put_sample(at(i), previous);
        ring_index = (uint32_t)((ring_index + missed) % total);
        ring_filled = config.pre;
        return;
    }
    while (missed--) ring_put(previous);
}

// one turn of the wait for the trigger (about 20 ms at most); true when the capture is recorded
static bool wait_for_trigger() {
    catch_up();
    // the loop keeps its state in locals (fast on AVR) and leaves it for the next turn
    hal::cycle_t period = (hal::cycle_t)config.period;
    hal::cycle_t due = next;
    uint32_t pre = config.pre;
    uint32_t index = ring_index, filled = ring_filled;
    uint8_t before[FW_PORTS], now[FW_PORTS];
    copy_ports(before, previous);
    uint8_t *p = at(index);
    uint8_t *end = memory + total * width;
    // about 20 ms of samples; below 50 Hz the turn takes what is due and returns at once
    uint32_t budget = config.rate / 50;
    bool wait = budget > 0;
    if (!budget) budget = 1;
    while (budget--) {
        if (!wait && (hal::cycle_t)(hal::cycles() - due) >= HALF) break;
        while ((hal::cycle_t)(hal::cycles() - due) >= HALF) {
        }
        due += period;
        read_ports(now);
        put_sample(p, now);
        if (filled >= pre && trigger_hit(before, now)) {
            first = (index + total - pre) % total;
            record(index, due);
            return true;
        }
        if (filled < pre) filled++;
        copy_ports(before, now);
        index = index + 1 == total ? 0 : index + 1;
        p += width;
        if (p == end) p = memory;
    }
    next = due;
    ring_index = index;
    ring_filled = filled;
    copy_ports(previous, before);
    turn_end_us = hal::micros();
    return false;
}

static void send_samples() {
    const uint16_t per_frame = (uint16_t)(DATA_ROOM / (2 + width));
    app::event(EVENT_DATA);
    app::out.u8(DATA_DIGITAL);
    app::out.u32(cursor);
    for (uint16_t runs = 0; runs < per_frame && cursor < total; runs++) {
        const uint8_t *levels = at((first + cursor) % total);
        uint16_t count = 0;
        while (cursor < total && count < 0xFFFF && same(at((first + cursor) % total), levels)) {
            cursor++;
            count++;
        }
        app::out.u16(count);
        app::out.bytes(levels, width);
    }
    app::send();
}

void buffer_poll() {
    if (phase == RUNNING && ending) {  // aborted while it waits
        phase = DRAINING;
        send_done(0);
        return;
    }
    if (phase == RUNNING && stage == ARMED) {
        if (!wait_for_trigger()) return;
        hal::clock_end();
        stage = SENDING;
        phase = DRAINING;
        if (late) end_status = DONE_OVERFLOW;
        return;
    }
    if (cursor < total) {
        send_samples();
        return;
    }
    send_done(total);
}

}  // namespace capture
