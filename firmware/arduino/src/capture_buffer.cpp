// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Buffer captures: a loop paced by the counted clock reads the pins into RAM (1 to 4 bytes per
// sample, by the highest pin of the mask). While it waits for the trigger it writes a ring in
// turns of about 20 ms and returns to the main loop between them (answers, abort); the ring, the
// trigger detection and the sample clock go on across the turns, so an edge between two turns
// still triggers. Once the pre-trigger samples are in and the trigger hits, it records the rest
// without a break. Then the samples go out as runs. A sample the loop could not take in time
// while it records ends the capture with DONE status overflow.

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
static uint32_t previous;          // the levels of the last sample (edge triggers)
static hal::cycle_t next;          // when the next sample is due
static uint32_t turn_end_us;       // when the last turn returned to the main loop
static uint32_t wrap_us;           // a longer pause may have wrapped the counted clock

static const hal::cycle_t HALF = (hal::cycle_t)(((hal::cycle_t)~(hal::cycle_t)0) / 2 + 1);

static uint8_t width_of(uint32_t mask) {
    if (mask <= 0xFFu) return 1;
    if (mask <= 0xFFFFu) return 2;
    if (mask <= 0xFFFFFFu) return 3;
    return 4;
}

static inline void store(uint32_t index, uint32_t levels) {
    uint8_t *p = memory + index * width;
    p[0] = (uint8_t)levels;
    if (width > 1) p[1] = (uint8_t)(levels >> 8);
    if (width > 2) p[2] = (uint8_t)(levels >> 16);
    if (width > 3) p[3] = (uint8_t)(levels >> 24);
}

static inline uint32_t load(uint32_t index) {
    const uint8_t *p = memory + index * width;
    uint32_t levels = p[0];
    if (width > 1) levels |= (uint32_t)p[1] << 8;
    if (width > 2) levels |= (uint32_t)p[2] << 16;
    if (width > 3) levels |= (uint32_t)p[3] << 24;
    return levels;
}

uint8_t buffer_setup(uint32_t *actual) {
    Config &c = config;
    if (c.adc) return ERR_UNSUPPORTED;  // the counted loop reads pins only
    if (c.rate == 0 || c.rate > board::INFO.buffer_max_rate) return ERR_ARGUMENTS;
    uint32_t capacity = FW_CAPTURE_BYTES / width_of(c.mask);
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
    width = width_of(config.mask);
    total = config.samples;
    cursor = 0;
    late = false;
    ring_index = ring_filled = 0;
    previous = hal::read_levels();
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
    uint32_t mask = config.mask;
    uint32_t remaining = total - config.pre - 1;
    hal::quiet_begin();
    while (remaining--) {
        hal::cycle_t now = hal::cycles();
        if ((hal::cycle_t)(now - due) < HALF && (hal::cycle_t)(now - due) >= period) late = true;
        while ((hal::cycle_t)(hal::cycles() - due) >= HALF) {
        }
        due += period;
        index = index + 1 == total ? 0 : index + 1;
        store(index, hal::read_levels() & mask);
    }
    hal::quiet_end((uint32_t)((uint64_t)(total - config.pre) * 1000000u / config.rate));
}

// a sample into the ring (it holds the last pre-trigger samples)
static inline void ring_put(uint32_t levels) {
    store(ring_index, levels & config.mask);
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
        for (uint32_t i = 0; i < total; i++) store(i, previous & config.mask);
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
    uint32_t mask = config.mask;
    uint32_t pre = config.pre;
    uint32_t index = ring_index, filled = ring_filled, before = previous;
    // about 20 ms of samples; below 50 Hz the turn takes what is due and returns at once
    uint32_t budget = config.rate / 50;
    bool wait = budget > 0;
    if (!budget) budget = 1;
    while (budget--) {
        if (!wait && (hal::cycle_t)(hal::cycles() - due) >= HALF) break;
        while ((hal::cycle_t)(hal::cycles() - due) >= HALF) {
        }
        due += period;
        uint32_t levels = hal::read_levels();
        store(index, levels & mask);
        if (filled >= pre && trigger_hit(before, levels)) {
            first = (index + total - pre) % total;
            record(index, due);
            return true;
        }
        if (filled < pre) filled++;
        before = levels;
        index = index + 1 == total ? 0 : index + 1;
    }
    next = due;
    ring_index = index;
    ring_filled = filled;
    previous = before;
    turn_end_us = hal::micros();
    return false;
}

static void send_samples() {
    const uint16_t per_frame = DATA_ROOM / 6;
    app::event(EVENT_DATA);
    app::out.u8(DATA_DIGITAL);
    app::out.u32(cursor);
    for (uint16_t runs = 0; runs < per_frame && cursor < total; runs++) {
        uint32_t index = (first + cursor) % total;
        uint32_t levels = load(index);
        uint16_t count = 0;
        while (cursor < total && count < 0xFFFF && load((first + cursor) % total) == levels) {
            cursor++;
            count++;
        }
        app::out.u16(count);
        app::out.u32(levels);
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
