/*
 * Copyright (C) 2026 Julian Decker
 *
 * Part of openSciLab.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef __OPENSCILAB_BOARD_CHECK__
#define __OPENSCILAB_BOARD_CHECK__

//What the board around the Pico looks like, measured with the pull resistors only (nothing is
//driven): the LogicAnalyzer board of gusmanb ties GP0 and GP1 to GND through 8.2 kOhm (the
//trigger chain; the pull-up of GP1 does not lift it) and its level shifters drive every channel.
//The application warns when such a board runs an image for a bare board (whose outputs would
//drive against the shifters). Reported by command 9 (INFO:TRIGGER_CHAIN, INFO:CHANNELS_DRIVEN)
//and the self-test.

#include <stdbool.h>
#include <stdint.h>

typedef enum
{
    TRIGGER_CHAIN_UNKNOWN = 0,  //Not measured (a capture uses the pins, or the board has no chain)
    TRIGGER_CHAIN_FREE,         //GP1 follows its pull-up: a bare board (or a wire GP0-GP1)
    TRIGGER_CHAIN_PULLED_DOWN   //GP1 stays low against its pull-up: the chain of a board pulls it down

} TRIGGER_CHAIN;

typedef struct _BOARD_SIGNATURE
{
    TRIGGER_CHAIN chain;
    uint8_t channelsChecked;    //Channels measured (only pins nobody uses)
    uint8_t channelsDriven;     //Of them: driven from outside (they follow neither pull)
    uint32_t followingMask;     //Channels that follow both pulls (bit n: channel n)

} BOARD_SIGNATURE;

/// @brief Time for a pull resistor to charge the pin capacitance
#define BOARD_CHECK_SETTLE_MS 2

/// @brief Measures the board; touches only pins nobody uses and leaves them in their reset state
void board_check(BOARD_SIGNATURE* signature);

/// @brief Reads a pin with its pull-up (high: it followed) and its pull-down (low: it followed)
void board_check_read_with_pulls(uint8_t pin, bool* high, bool* low);

/// @brief Back to the reset state of the pad: no function, pull-down enabled
void board_check_restore_pin(uint8_t pin);

/// @brief The value of INFO:TRIGGER_CHAIN
const char* board_check_chain_name(TRIGGER_CHAIN chain);

#endif
