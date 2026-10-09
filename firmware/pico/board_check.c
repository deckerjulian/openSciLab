/*
 * Copyright (C) 2026 Julian Decker
 *
 * Part of openSciLab.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "board_settings.h"
#include "board_check.h"
#include "capture.h"
#include "gpio_ctrl.h"
#include "hardware/gpio.h"

extern const uint8_t pinMap[];

void board_check_restore_pin(uint8_t pin)
{
    gpio_deinit(pin);
    gpio_set_pulls(pin, false, true);
}

void board_check_read_with_pulls(uint8_t pin, bool* high, bool* low)
{
    gpio_init(pin);
    gpio_set_dir(pin, GPIO_IN);

    gpio_set_pulls(pin, true, false);
    busy_wait_ms(BOARD_CHECK_SETTLE_MS);
    *high = gpio_get(pin);

    gpio_set_pulls(pin, false, false);
    gpio_ctrl_pull_down(pin, BOARD_CHECK_SETTLE_MS * 1000);
    *low = !gpio_get(pin);

    board_check_restore_pin(pin);
}

//A pin the check may touch: not used by a capture, an output, the generator or an analog input,
//and not set to a mode by the host (its pulls are left alone)
static bool untouched(uint8_t pin)
{
    return !(gpio_ctrl_busy_mask() & (1u << pin)) && gpio_ctrl_mode(pin) == GPIO_MODE_IDLE;
}

void board_check(BOARD_SIGNATURE* signature)
{
    signature->chain = TRIGGER_CHAIN_UNKNOWN;
    signature->channelsChecked = 0;
    signature->channelsDriven = 0;
    signature->followingMask = 0;

    if(IsCapturing())
        return;

#ifdef SUPPORTS_COMPLEX_TRIGGER
    if(untouched(COMPLEX_TRIGGER_OUT_PIN) && untouched(COMPLEX_TRIGGER_IN_PIN))
    {
        //GP0 without pulls (it would pull a wire GP0-GP1 down), GP1 with its pull-up
        gpio_init(COMPLEX_TRIGGER_OUT_PIN);
        gpio_set_dir(COMPLEX_TRIGGER_OUT_PIN, GPIO_IN);
        gpio_disable_pulls(COMPLEX_TRIGGER_OUT_PIN);
        gpio_init(COMPLEX_TRIGGER_IN_PIN);
        gpio_set_dir(COMPLEX_TRIGGER_IN_PIN, GPIO_IN);
        gpio_set_pulls(COMPLEX_TRIGGER_IN_PIN, true, false);
        busy_wait_ms(BOARD_CHECK_SETTLE_MS);
        signature->chain = gpio_get(COMPLEX_TRIGGER_IN_PIN) ? TRIGGER_CHAIN_FREE : TRIGGER_CHAIN_PULLED_DOWN;
        board_check_restore_pin(COMPLEX_TRIGGER_OUT_PIN);
        board_check_restore_pin(COMPLEX_TRIGGER_IN_PIN);
    }
#endif

    for(uint8_t channel = 0; channel < MAX_CHANNELS; channel++)
    {
        uint8_t pin = pinMap[channel];
        if(!untouched(pin))
            continue;

        bool high, low;
        board_check_read_with_pulls(pin, &high, &low);
        signature->channelsChecked++;

        if(high && low)
            signature->followingMask |= 1u << channel;
        else if(!high && !low)
            continue; //Inverted: neither driven nor following
        else
            signature->channelsDriven++;
    }
}

const char* board_check_chain_name(TRIGGER_CHAIN chain)
{
    switch(chain)
    {
        case TRIGGER_CHAIN_FREE:
            return "FREE";
        case TRIGGER_CHAIN_PULLED_DOWN:
            return "PULLED_DOWN";
        default:
            return "UNKNOWN";
    }
}
