/*
 * Copyright (C) 2026 Julian Decker
 *
 * Part of openSciLab.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * libopenscilab_avr: simavr behind flat functions for ctypes (openscilab/circuits/mcu/avr.py).
 * No structure of simavr crosses this boundary: a microcontroller is a handle, what it did comes back
 * as events with the cycle they happened in.
 */
#ifndef OSL_AVR_H
#define OSL_AVR_H

#include <stdint.h>

#if defined(_WIN32)
#define OSL_EXPORT __declspec(dllexport)
#else
#define OSL_EXPORT __attribute__((visibility("default")))
#endif

#define OSL_AVR_VERSION 2

/* kinds of events */
#define OSL_EVENT_PIN 0       /* (version 1: a pin's irq changed; no longer sent - inputs from outside used the same irq) */
#define OSL_EVENT_DIRECTION 1 /* the data direction register of a port changed: port, value (all bits) */
#define OSL_EVENT_UART 2      /* a byte was sent: port is the UART ('0', '1' ...), value the byte */
#define OSL_EVENT_PORT 3      /* the PORT register was written: port, value (all bits; outputs and pull-ups) */

typedef struct osl_event {
	uint64_t cycle;
	uint8_t kind;
	uint8_t port;  /* 'A' ... 'L', or the UART */
	uint8_t bit;
	uint8_t value;
} osl_event;

OSL_EXPORT int osl_avr_version(void);
/* a microcontroller ("atmega328p", "atmega2560" ...) at hz; vcc in millivolts; NULL: unknown */
OSL_EXPORT void * osl_avr_new(const char * mmcu, uint32_t hz, uint32_t vcc_mv);
OSL_EXPORT void osl_avr_free(void * handle);
/* the flash from address on (an image read from a .hex or .elf file) */
OSL_EXPORT int osl_avr_load(void * handle, const uint8_t * data, uint32_t size, uint32_t address);
OSL_EXPORT void osl_avr_reset(void * handle);
OSL_EXPORT uint64_t osl_avr_cycle(void * handle);
OSL_EXPORT uint32_t osl_avr_frequency(void * handle);
/* run until the cycle is reached; returns simavr's state (0 limbo, 1 stopped, 2 running, 3 sleeping,
 * 4 step, 5 step done, 6 done, 7 crashed) */
OSL_EXPORT int osl_avr_run_until(void * handle, uint64_t cycle);
/* up to max events since the last call; returns how many */
OSL_EXPORT int osl_avr_poll(void * handle, osl_event * events, int max);
/* drive an input pin from outside (level 0/1); only for pins the firmware made inputs - on an output
 * it would be taken for the firmware's own write and be filtered */
OSL_EXPORT void osl_avr_set_pin(void * handle, char port, int bit, int level);
/* the voltage of an analog input (millivolts) */
OSL_EXPORT void osl_avr_set_adc(void * handle, int channel, uint32_t millivolts);
/* bytes into a UART (sent to the microcontroller as its flow control lets them) */
OSL_EXPORT void osl_avr_uart_write(void * handle, char uart, const uint8_t * data, int size);
/* the registers of a port: returns 0 when the port exists */
OSL_EXPORT int osl_avr_port(void * handle, char port, uint8_t * out, uint8_t * ddr, uint8_t * pin);

#endif
