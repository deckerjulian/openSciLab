/*
 * Copyright (C) 2026 Julian Decker
 *
 * Part of openSciLab.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * libopenscilab_avr has no GDB server (and needs no sockets): what simavr calls of it does nothing.
 */
#include "sim_avr.h"
#include "sim_gdb.h"

int avr_gdb_init(avr_t * avr) { (void)avr; return -1; }
void avr_deinit_gdb(avr_t * avr) { (void)avr; }
int avr_gdb_processor(avr_t * avr, int sleep) { (void)avr; (void)sleep; return 0; }
void avr_gdb_handle_watchpoints(avr_t * avr, uint16_t addr, enum avr_gdb_watch_type type)
{
	(void)avr; (void)addr; (void)type;
}
void avr_gdb_handle_break(avr_t * avr) { (void)avr; }
