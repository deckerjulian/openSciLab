/*
 * Copyright (C) 2026 Julian Decker
 *
 * Part of openSciLab.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * See osl_avr.h.
 */
#include <stdlib.h>
#include <string.h>

#include "osl_avr.h"
#include "sim_avr.h"
#include "sim_core.h"
#include "avr_ioport.h"
#include "avr_uart.h"
#include "avr_adc.h"

#define OSL_PORTS "ABCDEFGHJKL"
#define OSL_EVENTS 65536
#define OSL_UART_INPUT 4096

typedef struct osl_port {
	char name;
	avr_irq_t * irq;      /* the port's irqs (pins, all pins, direction) */
	struct osl_avr * owner;
} osl_port;

typedef struct osl_uart {
	char name;
	avr_irq_t * irq;
	uint8_t input[OSL_UART_INPUT];
	int head, tail;
	int ready;
	struct osl_avr * owner;
} osl_uart;

typedef struct osl_avr {
	avr_t * avr;
	osl_port ports[11];
	int port_count;
	osl_uart uarts[4];
	int uart_count;
	osl_event events[OSL_EVENTS];
	int event_head, event_tail;
	int driving; /* (version 1: set while the application drove a pin) */
} osl_avr;

static void osl_push(osl_avr * self, uint8_t kind, uint8_t port, uint8_t bit, uint8_t value)
{
	int next = (self->event_head + 1) % OSL_EVENTS;
	if (next == self->event_tail)  /* full: the oldest goes */
		self->event_tail = (self->event_tail + 1) % OSL_EVENTS;
	osl_event * event = &self->events[self->event_head];
	event->cycle = self->avr->cycle;
	event->kind = kind;
	event->port = port;
	event->bit = bit;
	event->value = value;
	self->event_head = next;
}

static void osl_port_written(struct avr_irq_t * irq, uint32_t value, void * param)
{
	/* every write of the PORT register, filtered by nothing: an output written to the level the net
	 * already had, a pull-up switched off - the pins' own irqs would swallow both */
	osl_port * port = (osl_port *)param;
	(void)irq;
	osl_push(port->owner, OSL_EVENT_PORT, (uint8_t)port->name, 0, (uint8_t)(value & 0xff));
}

static void osl_direction_changed(struct avr_irq_t * irq, uint32_t value, void * param)
{
	osl_port * port = (osl_port *)param;
	(void)irq;
	osl_push(port->owner, OSL_EVENT_DIRECTION, (uint8_t)port->name, 0, (uint8_t)(value & 0xff));
}

static void osl_uart_feed(osl_uart * uart)
{
	while (uart->ready && uart->tail != uart->head) {
		uint8_t byte = uart->input[uart->tail];
		uart->tail = (uart->tail + 1) % OSL_UART_INPUT;
		avr_raise_irq(uart->irq + UART_IRQ_INPUT, byte);
	}
}

static void osl_uart_output(struct avr_irq_t * irq, uint32_t value, void * param)
{
	osl_uart * uart = (osl_uart *)param;
	(void)irq;
	osl_push(uart->owner, OSL_EVENT_UART, (uint8_t)uart->name, 0, (uint8_t)(value & 0xff));
}

static void osl_uart_xon(struct avr_irq_t * irq, uint32_t value, void * param)
{
	osl_uart * uart = (osl_uart *)param;
	(void)irq; (void)value;
	uart->ready = 1;
	osl_uart_feed(uart);
}

static void osl_uart_xoff(struct avr_irq_t * irq, uint32_t value, void * param)
{
	osl_uart * uart = (osl_uart *)param;
	(void)irq; (void)value;
	uart->ready = 0;
}

int osl_avr_version(void)
{
	return OSL_AVR_VERSION;
}

void * osl_avr_new(const char * mmcu, uint32_t hz, uint32_t vcc_mv)
{
	avr_t * avr = avr_make_mcu_by_name(mmcu);
	if (!avr)
		return NULL;
	osl_avr * self = calloc(1, sizeof(osl_avr));
	if (!self) {
		free(avr);
		return NULL;
	}
	self->avr = avr;
	avr_init(avr);
	avr->frequency = hz;
	avr->vcc = avr->avcc = avr->aref = vcc_mv;
	avr->log = 0;
	for (const char * name = OSL_PORTS; *name; name++) {
		avr_irq_t * irq = avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ(*name), 0);
		if (!irq)
			continue;
		osl_port * port = &self->ports[self->port_count++];
		port->name = *name;
		port->irq = irq;
		port->owner = self;
		/* simavr filters these irqs (raised only when the value changes): after a reset the registers
		 * are written with the values the irqs still hold, and nothing would be heard - every write counts */
		irq[IOPORT_IRQ_REG_PORT].flags &= ~IRQ_FLAG_FILTERED;
		irq[IOPORT_IRQ_DIRECTION_ALL].flags &= ~IRQ_FLAG_FILTERED;
		avr_irq_register_notify(irq + IOPORT_IRQ_REG_PORT, osl_port_written, port);
		avr_irq_register_notify(irq + IOPORT_IRQ_DIRECTION_ALL, osl_direction_changed, port);
	}
	for (char name = '0'; name <= '3'; name++) {
		avr_irq_t * irq = avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ(name), 0);
		if (!irq)
			continue;
		osl_uart * uart = &self->uarts[self->uart_count++];
		uart->name = name;
		uart->irq = irq;
		uart->owner = self;
		uart->ready = 1;
		avr_irq_register_notify(irq + UART_IRQ_OUTPUT, osl_uart_output, uart);
		avr_irq_register_notify(irq + UART_IRQ_OUT_XON, osl_uart_xon, uart);
		avr_irq_register_notify(irq + UART_IRQ_OUT_XOFF, osl_uart_xoff, uart);
		/* the UART takes bytes at once, not at the baud rate of the line: no stdio echo */
		uint32_t flags = 0;
		avr_ioctl(avr, AVR_IOCTL_UART_GET_FLAGS(name), &flags);
		flags &= ~AVR_UART_FLAG_STDIO;
		avr_ioctl(avr, AVR_IOCTL_UART_SET_FLAGS(name), &flags);
	}
	return self;
}

void osl_avr_free(void * handle)
{
	osl_avr * self = (osl_avr *)handle;
	if (!self)
		return;
	avr_terminate(self->avr);
	free(self->avr);
	free(self);
}

int osl_avr_load(void * handle, const uint8_t * data, uint32_t size, uint32_t address)
{
	osl_avr * self = (osl_avr *)handle;
	if (!self || address + size > self->avr->flashend + 1)
		return -1;
	avr_loadcode(self->avr, (uint8_t *)data, size, address);
	return 0;
}

void osl_avr_reset(void * handle)
{
	osl_avr * self = (osl_avr *)handle;
	avr_reset(self->avr);
	self->event_head = self->event_tail = 0;
}

uint64_t osl_avr_cycle(void * handle)
{
	return ((osl_avr *)handle)->avr->cycle;
}

uint32_t osl_avr_frequency(void * handle)
{
	return ((osl_avr *)handle)->avr->frequency;
}

int osl_avr_run_until(void * handle, uint64_t cycle)
{
	osl_avr * self = (osl_avr *)handle;
	avr_t * avr = self->avr;
	int state = avr->state;
	while (avr->cycle < cycle) {
		state = avr_run(avr);
		if (state == cpu_Done || state == cpu_Crashed)
			break;
	}
	return state;
}

int osl_avr_poll(void * handle, osl_event * events, int max)
{
	osl_avr * self = (osl_avr *)handle;
	int count = 0;
	while (count < max && self->event_tail != self->event_head) {
		events[count++] = self->events[self->event_tail];
		self->event_tail = (self->event_tail + 1) % OSL_EVENTS;
	}
	return count;
}

void osl_avr_set_pin(void * handle, char name, int bit, int level)
{
	osl_avr * self = (osl_avr *)handle;
	for (int i = 0; i < self->port_count; i++) {
		if (self->ports[i].name == name && bit >= 0 && bit < 8) {
			self->driving = 1;
			avr_raise_irq(self->ports[i].irq + bit, level ? 1 : 0);
			self->driving = 0;
			return;
		}
	}
}

void osl_avr_set_adc(void * handle, int channel, uint32_t millivolts)
{
	osl_avr * self = (osl_avr *)handle;
	avr_irq_t * irq = avr_io_getirq(self->avr, AVR_IOCTL_ADC_GETIRQ, ADC_IRQ_ADC0 + channel);
	if (irq && channel >= 0 && channel < 16)
		avr_raise_irq(irq, millivolts);
}

void osl_avr_uart_write(void * handle, char name, const uint8_t * data, int size)
{
	osl_avr * self = (osl_avr *)handle;
	for (int i = 0; i < self->uart_count; i++) {
		osl_uart * uart = &self->uarts[i];
		if (uart->name != name)
			continue;
		for (int k = 0; k < size; k++) {
			int next = (uart->head + 1) % OSL_UART_INPUT;
			if (next == uart->tail)
				break;
			uart->input[uart->head] = data[k];
			uart->head = next;
		}
		osl_uart_feed(uart);
		return;
	}
}

int osl_avr_port(void * handle, char name, uint8_t * out, uint8_t * ddr, uint8_t * pin)
{
	osl_avr * self = (osl_avr *)handle;
	avr_ioport_state_t state;
	if (avr_ioctl(self->avr, AVR_IOCTL_IOPORT_GETSTATE(name), &state) != 0)
		return -1;
	*out = state.port;
	*ddr = state.ddr;
	*pin = state.pin;
	return 0;
}
