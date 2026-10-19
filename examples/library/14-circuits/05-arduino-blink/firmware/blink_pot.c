/*
 * The firmware of the example "Arduino blink" (openSciLab, Circuits): an Arduino Uno
 *   - blinks the LED on D13; the potentiometer on A0 sets how fast: 50 ms to 1 s on and as long off,
 *   - keeps the LED on while the switch on D2 is closed (D2 to ground, its pull-up on),
 *   - says on its serial port (9600 baud) how long it blinks and what the switch does.
 * Built with build.sh into blink_pot.hex (avr-gcc, e.g. PlatformIO's).
 */
#include <avr/io.h>
#include <util/delay.h>
#include <stdio.h>
#include <stdlib.h>

static void uart_init(void)
{
	uint16_t ubrr = F_CPU / 16 / 9600 - 1;
	UBRR0H = ubrr >> 8;
	UBRR0L = ubrr & 0xff;
	UCSR0B = (1 << RXEN0) | (1 << TXEN0);
	UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
}

static void uart_print(const char * text)
{
	while (*text) {
		while (!(UCSR0A & (1 << UDRE0)))
			;
		UDR0 = *text++;
	}
}

static uint16_t adc_read(uint8_t channel)
{
	ADMUX = (1 << REFS0) | (channel & 7);
	ADCSRA = (1 << ADEN) | (1 << ADSC) | (1 << ADPS2) | (1 << ADPS1) | (1 << ADPS0);
	while (ADCSRA & (1 << ADSC))
		;
	return ADC;
}

int main(void)
{
	char line[32];
	uint16_t half = 500, told = 0, elapsed = 0;
	uint8_t held = 2;
	DDRB |= (1 << PB5);    /* D13: the LED */
	PORTD |= (1 << PD2);   /* D2: the switch, with its pull-up */
	uart_init();
	uart_print("hello: the potentiometer sets the blinking, the switch holds the LED on\n");
	for (uint16_t tick = 0;; tick++) {
		_delay_ms(1);
		if (tick % 20 == 0) {
			half = 50 + (uint32_t)adc_read(0) * 950 / 1023;
			if (abs((int)half - (int)told) > 20) {
				told = half;
				snprintf(line, sizeof line, "blink: %u ms on, %u ms off\n", half, half);
				uart_print(line);
			}
		}
		uint8_t closed = !(PIND & (1 << PD2));
		if (closed != held) {
			held = closed;
			uart_print(closed ? "switch closed: the LED stays on\n" : "switch open: it blinks\n");
		}
		if (closed) {
			PORTB |= (1 << PB5);
			elapsed = 0;
		} else if (++elapsed >= half) {
			PORTB ^= (1 << PB5);
			elapsed = 0;
		}
	}
}
