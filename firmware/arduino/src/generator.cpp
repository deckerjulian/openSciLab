// Copyright (C) 2026 Julian Decker
//
// Part of openSciLab.
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The pattern generator (runs in RAM: <H count, then a byte per port of its pins; played by a
// timer interrupt), the square wave (a hardware timer) and the arbitrary waveform (DAC points
// played by a timer interrupt).

#include "generator.h"

#include "app.h"
#include "board.h"
#include "capture.h"
#include "hal.h"

using namespace proto;

namespace gen {

static uint8_t runs[FW_GEN_BYTES];
static uint16_t runs_loaded = 0;
static board::Pins pattern_mask;  // the pins of the runs (GEN_LOAD)
static uint8_t pattern_ports = 0;  // the ports of those pins: bytes of a run after its count
static uint8_t pattern_port[FW_PORTS];
static uint8_t pattern_bits[FW_PORTS];
#if FW_ARB_POINTS
static uint16_t points[FW_ARB_POINTS];
static uint16_t points_loaded = 0;
static uint16_t wave_points = 0;
#endif

// what plays
static const uint8_t NOTHING = 0;
static const uint8_t PATTERN = 1;
static const uint8_t WAVE = 2;
static volatile uint8_t playing = NOTHING;
static volatile bool finished = false;  // the last pass is over (the timer stops in poll)
static volatile uint16_t passes_done = 0;
static uint16_t passes_wanted = 0;
static uint16_t index_now = 0;
static uint16_t remaining = 0;
static const uint8_t *run_now = runs;

static uint8_t square_pin = 0xFF;

// the bytes of a run
static inline uint8_t run_size() { return (uint8_t)(2 + pattern_ports); }

FW_ISR static inline void play(const uint8_t *run) {
    remaining = get_u16(run);
    for (uint8_t i = 0; i < pattern_ports; i++) hal::write_port(pattern_port[i], pattern_bits[i], run[2 + i]);
}

FW_ISR static void pattern_tick() {
    if (finished) return;
    if (--remaining) return;
    run_now += run_size();
    if (++index_now == runs_loaded) {
        index_now = 0;
        run_now = runs;
        passes_done++;
        if (passes_wanted && passes_done >= passes_wanted) {
            finished = true;
            return;
        }
    }
    play(run_now);
}

#if FW_ARB_POINTS
FW_ISR static void wave_tick() {
    if (finished) return;
    if (++index_now == wave_points) {
        index_now = 0;
        passes_done++;
        if (passes_wanted && passes_done >= passes_wanted) {
            finished = true;
            return;
        }
    }
    hal::dac_write_fast(points[index_now]);
}
#endif

uint8_t load(const uint8_t *d, uint8_t n, uint16_t *loaded) {
    const uint8_t head = 2 + FW_PORTS;  // offset, pins
    if (n < head) return ERR_ARGUMENTS;
    if (playing == PATTERN) return ERR_BUSY;
    uint16_t offset = get_u16(d);
    board::Pins mask = board::Pins::from(d + 2);
    if (!mask.any()) return ERR_ARGUMENTS;
    if (offset > runs_loaded) return ERR_ARGUMENTS;  // no gaps
    if (offset && !(mask == pattern_mask)) return ERR_ARGUMENTS;  // the runs before have other pins
    uint8_t size = (uint8_t)(2 + mask.ports());
    if ((n - head) % size) return ERR_ARGUMENTS;
    uint16_t count = (uint16_t)((n - head) / size);
    if (((uint32_t)offset + count) * size > FW_GEN_BYTES) return ERR_OVERFLOW;
    for (uint16_t i = 0; i < count; i++)
        if (get_u16(d + head + size * i) == 0) return ERR_ARGUMENTS;
    if (!offset) {
        pattern_mask = mask;
        pattern_ports = 0;
        for (uint8_t k = 0; k < FW_PORTS; k++) {
            if (!mask.port[k]) continue;
            pattern_port[pattern_ports] = k;
            pattern_bits[pattern_ports++] = mask.port[k];
        }
    }
    memcpy(runs + (uint32_t)offset * size, d + head, (size_t)count * size);
    runs_loaded = (uint16_t)(offset + count);
    *loaded = runs_loaded;
    return 0;
}

static void halt() {
    if (playing != NOTHING) hal::timer_stop(hal::TIMER_OUTPUT);
    playing = NOTHING;
    finished = false;
}

// the level of a pin in the first run
static bool first_level(uint8_t pin) {
    for (uint8_t i = 0; i < pattern_ports; i++)
        if (pattern_port[i] == pin >> 3) return (runs[2 + i] >> (pin & 7)) & 1;
    return false;
}

uint8_t start(const uint8_t *d, uint8_t n, float *actual) {
    if (n != 7) return ERR_ARGUMENTS;
    float rate = get_f32(d);
    uint16_t passes = get_u16(d + 4);
    uint8_t flags = d[6];
    if (flags & 1) return ERR_UNSUPPORTED;  // no trigger input is defined for the Arduino boards
    if (!(rate > 0.0f) || rate > (float)board::INFO.generator_max_rate || !runs_loaded) return ERR_ARGUMENTS;
    if (playing != NOTHING) return ERR_BUSY;
    uint8_t error = app::check_pins(pattern_mask, board::PC_DOUT);
    if (error) return error;
    passes_wanted = passes;
    passes_done = 0;
    index_now = 0;
    run_now = runs;
    finished = false;
    remaining = get_u16(runs);
    for (uint8_t i = 0; i < board::PIN_COUNT; i++)
        if (pattern_mask.has(i)) hal::pin_output(i, first_level(i));
    app::released(pattern_mask);  // the generator owns them now
    playing = PATTERN;
    error = hal::timer_start(hal::TIMER_OUTPUT, rate, pattern_tick, actual);
    if (error) {
        // nobody would release the pins (neither SAFE nor the watchdog know them): inputs again
        playing = NOTHING;
        for (uint8_t i = 0; i < board::PIN_COUNT; i++)
            if (pattern_mask.has(i)) hal::pin_input(i, hal::PULL_NONE);
        return error;
    }
    return 0;
}

void stop() {
    if (playing == PATTERN) app::driving(pattern_mask);  // the pins keep their levels
    if (playing == WAVE) app::driving(board::Pins::of(board::dac_pin()));
    halt();
}

void status(uint8_t *running, uint16_t *passes) {
    *running = playing != NOTHING && !finished;
    *passes = passes_done;
}

static bool square_by_pwm = false;  // a pin without the square timer: PWM at 50 %

static void square_off() {
    if (square_pin == 0xFF) return;
    if (square_by_pwm) hal::pwm_stop(square_pin);
    else hal::square_stop();
    square_pin = 0xFF;
    square_by_pwm = false;
}

uint8_t square(uint8_t pin, float frequency, float *actual) {
    *actual = 0.0f;
    if (frequency == 0.0f) {
        if (square_pin != 0xFF) {
            uint8_t was = square_pin;
            square_off();
            hal::pin_output(was, false);
            app::driving(board::Pins::of(was));
        }
        return 0;
    }
    if (!(frequency > 0.0f)) return ERR_ARGUMENTS;
    if (pin >= board::PIN_COUNT) return ERR_ARGUMENTS;
    uint8_t error = (pin == square_pin) ? 0 : app::check_pins(board::Pins::of(pin), board::PC_DOUT);
    if (error) return error;
    uint16_t caps = board::pin(pin).caps;
    if (!(caps & (board::PC_SQUARE | board::PC_PWM))) return ERR_UNSUPPORTED;
    square_off();
    bool by_pwm = !(caps & board::PC_SQUARE);
    error = by_pwm ? hal::pwm_start(pin, frequency, 32768, actual) : hal::square_start(pin, frequency, actual);
    if (error) return error;
    square_pin = pin;
    square_by_pwm = by_pwm;
    app::released(board::Pins::of(pin));
    return 0;
}

uint8_t arb_load(const uint8_t *d, uint8_t n, uint16_t *loaded) {
#if FW_ARB_POINTS
    if (board::dac_pin() == board::NO_CHANNEL) return ERR_UNSUPPORTED;
    if (n < 2 || (n - 2) % 2) return ERR_ARGUMENTS;
    if (playing == WAVE) return ERR_BUSY;
    uint16_t offset = get_u16(d);
    uint16_t count = (uint16_t)((n - 2) / 2);
    if (offset > points_loaded) return ERR_ARGUMENTS;
    if ((uint32_t)offset + count > FW_ARB_POINTS) return ERR_OVERFLOW;
    uint16_t limit = (uint16_t)((1u << board::INFO.dac_bits) - 1);
    for (uint16_t i = 0; i < count; i++) {
        uint16_t value = get_u16(d + 2 + 2 * i);
        points[offset + i] = value > limit ? limit : value;
    }
    points_loaded = (uint16_t)(offset + count);
    *loaded = points_loaded;
    return 0;
#else
    (void)d;
    (void)n;
    (void)loaded;
    return ERR_UNSUPPORTED;
#endif
}

uint8_t arb_start(const uint8_t *d, uint8_t n, float *actual) {
#if FW_ARB_POINTS
    uint8_t dac = board::dac_pin();
    if (dac == board::NO_CHANNEL || !board::INFO.arb_max_rate) return ERR_UNSUPPORTED;
    if (n != 7) return ERR_ARGUMENTS;
    float rate = get_f32(d);
    uint16_t count = get_u16(d + 4);
    uint8_t passes = d[6];
    if (!(rate > 0.0f) || rate > (float)board::INFO.arb_max_rate || count == 0 || count > points_loaded)
        return ERR_ARGUMENTS;
    if (playing != NOTHING) return ERR_BUSY;
    uint8_t error = app::check_pins(board::Pins::of(dac), board::PC_DAC);
    if (error) return error;
    error = hal::dac_write(dac, points[0]);
    if (error) return error;
    wave_points = count;
    passes_wanted = passes;
    passes_done = 0;
    index_now = 0;
    finished = false;
    playing = WAVE;
    error = hal::timer_start(hal::TIMER_OUTPUT, rate, wave_tick, actual);
    if (error) {
        playing = NOTHING;
        hal::dac_stop(dac);  // its first point would stay on the pin
        return error;
    }
    return 0;
#else
    (void)d;
    (void)n;
    (void)actual;
    return ERR_UNSUPPORTED;
#endif
}

board::Pins pins() {
    board::Pins mask = board::Pins::none();
    if (playing == PATTERN) mask.add(pattern_mask);
    if (playing == WAVE) mask.add(board::dac_pin());
    if (square_pin != 0xFF) mask.add(square_pin);
    return mask;
}

bool driving() { return playing != NOTHING || square_pin != 0xFF; }

void stop_all() {
    board::Pins mask = pins();
    halt();
    square_off();
    if (board::dac_pin() != board::NO_CHANNEL && mask.has(board::dac_pin())) hal::dac_stop(board::dac_pin());
    for (uint8_t i = 0; i < board::PIN_COUNT; i++)
        if (mask.has(i)) hal::pin_input(i, hal::PULL_NONE);
}

void poll() {
    if (finished && playing != NOTHING) stop();
}

}  // namespace gen
