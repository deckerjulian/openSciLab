// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Stream captures: a timer interrupt samples the pins; the digital levels become runs
// (<H count, then a byte per port of the mask) in one ring, the analog channels (the latest conversion of each, refreshed
// in turn by the main loop) one record per sample in another. The main loop sends them as DATA
// events while the capture runs. A full ring ends the capture with DONE status overflow.

#include "app.h"
#include "board.h"
#include "capture_internal.h"

using namespace proto;

namespace capture {

static Ring runs;     // records <H count, then the sample (the wire format of DATA kind 0)
static Ring analog;   // records <H per channel (the wire format of DATA kind 1)

static volatile bool split = false;  // the main loop wants the open run now (live view)
static bool triggered;
static uint8_t previous[FW_PORTS];   // as read_ports
static uint8_t run_levels[FW_PORTS]; // a sample (config.width bytes)
static uint16_t run_count;

static uint32_t runs_sent;    // samples covered by the runs sent
static uint32_t analog_sent;  // analog samples sent
static uint32_t last_send_ms;

const uint32_t FLUSH_MS = 50;

FW_ISR static bool push_run() {
    uint8_t *slot = runs.slot();
    if (!slot) return false;
    put_u16(slot, run_count);
    for (uint8_t i = 0; i < config.width; i++) slot[2 + i] = run_levels[i];
    runs.commit();
    run_count = 0;
    return true;
}

FW_ISR static void overflow() {
    taken -= run_count;  // the open run is lost
    run_count = 0;
    finish(DONE_OVERFLOW);
}

FW_ISR static void tick() {
    if (ending) return;
    uint8_t now[FW_PORTS];
    read_ports(now);
    if (!triggered) {
        bool hit = trigger_hit(previous, now);
        copy_ports(previous, now);
        if (!hit) return;
        triggered = true;
    }
    if (config.width) {
        uint8_t levels[FW_PORTS];
        put_sample(levels, now);
        bool changed = false;
        for (uint8_t i = 0; i < config.width; i++)
            if (levels[i] != run_levels[i]) changed = true;
        if (run_count && (changed || run_count == 0xFFFF || split)) {
            split = false;
            if (!push_run()) {
                overflow();
                return;
            }
        }
        for (uint8_t i = 0; i < config.width; i++) run_levels[i] = levels[i];
        run_count++;
    }
    if (config.adc_count) {
        uint8_t *slot = analog.slot();
        if (!slot) {
            overflow();
            return;
        }
        for (uint8_t channel = 0; channel < FW_ADC_CHANNELS; channel++) {
            if (config.adc & (1u << channel)) {
                put_u16(slot, app::adc_latest[channel]);
                slot += 2;
            }
        }
        analog.commit();
    }
    taken++;
    if (config.samples && taken >= config.samples) {
        if (run_count && !push_run()) {
            overflow();
            return;
        }
        finish(DONE_COMPLETE);
    }
}

uint8_t stream_start() {
    // the memory: runs and analog records in proportion to what a sample needs at most
    uint32_t digital_bytes = config.width ? 2u + config.width : 0;
    uint32_t analog_bytes = 2u * config.adc_count;
    uint32_t split_at = FW_CAPTURE_BYTES * digital_bytes / (digital_bytes + analog_bytes);
    runs.init(memory, split_at, (uint16_t)(2 + config.width));
    analog.init(memory + split_at, FW_CAPTURE_BYTES - split_at, (uint16_t)(analog_bytes ? analog_bytes : 1));
    if (analog_bytes > DATA_ROOM) return ERR_ARGUMENTS;
    triggered = config.trigger == TRIGGER_NONE;
    read_ports(previous);
    run_count = 0;
    split = false;
    runs_sent = analog_sent = 0;
    last_send_ms = hal::millis();
    phase = RUNNING;
    float actual;
    uint8_t error = hal::timer_start(hal::TIMER_SAMPLE, (float)config.rate, tick, &actual);
    if (error) phase = IDLE;
    return error;
}

void stream_stop() {
    hal::timer_stop(hal::TIMER_SAMPLE);
    // the interrupt no longer runs: the open run can go out
    if (run_count && !push_run()) overflow();
}

static void send_runs(uint16_t count) {
    uint16_t per_frame = (uint16_t)(DATA_ROOM / runs.record());
    if (count > per_frame) count = per_frame;
    app::event(EVENT_DATA);
    app::out.u8(DATA_DIGITAL);
    app::out.u32(runs_sent);
    for (uint16_t i = 0; i < count; i++) {
        const uint8_t *record = runs.front();
        app::out.bytes(record, runs.record());
        runs_sent += get_u16(record);
        runs.pop();
    }
    app::send();
}

static void send_analog(uint16_t count) {
    uint16_t per_frame = (uint16_t)(DATA_ROOM / analog.record());
    if (count > per_frame) count = per_frame;
    app::event(EVENT_DATA);
    app::out.u8(DATA_ANALOG);
    app::out.u32(analog_sent);
    for (uint16_t i = 0; i < count; i++) {
        app::out.bytes(analog.front(), analog.record());
        analog.pop();
    }
    analog_sent += count;
    app::send();
}

void stream_poll() {
    if (phase == RUNNING && ending) {
        stream_stop();
        phase = DRAINING;
    }
    bool draining = phase == DRAINING;
    uint32_t now = hal::millis();
    bool due = draining || now - last_send_ms >= FLUSH_MS;
    uint16_t pending = config.width ? runs.count() : 0;
    if (pending && (due || pending >= DATA_ROOM / runs.record())) {
        send_runs(pending);
        last_send_ms = now;
        return;  // one frame per turn: the main loop keeps answering
    }
    uint16_t sets = config.adc_count ? analog.count() : 0;
    if (sets && (due || sets >= DATA_ROOM / analog.record())) {
        send_analog(sets);
        last_send_ms = now;
        return;
    }
    if (!draining) {
        // a level that holds: the open run goes out for the live view
        if (config.width && !pending && now - last_send_ms >= FLUSH_MS) split = true;
        return;
    }
    send_done(config.width ? runs_sent : analog_sent);
}

}  // namespace capture
