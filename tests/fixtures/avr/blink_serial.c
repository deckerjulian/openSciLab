/*
 * A firmware to test the emulated microcontrollers of openSciLab (tests/test_avr.py); built with
 * build.sh into blink_serial-<mcu>.hex. On an ATmega328P (Arduino Uno) and an ATmega2560 (Mega):
 *   - D13 (PB5 / PB7) toggles every millisecond,
 *   - the serial port (UART0, 9600 baud) says "hello" at the start, then every 20 ms "A0=<0..1023>",
 *   - what it receives it sends back in upper case,
 *   - D2 (PD2 / PE4) read as an input with its pull-up: "D2=0" when it is pulled low.
 */
#include <avr/io.h>
#include <util/delay.h>
#include <stdio.h>

#if defined(__AVR_ATmega2560__)
#define LED_DDR DDRB
#define LED_PORT PORTB
#define LED_BIT PB7
#define BUTTON_PORT PORTE
#define BUTTON_PIN PINE
#define BUTTON_BIT PE4
#else
#define LED_DDR DDRB
#define LED_PORT PORTB
#define LED_BIT PB5
#define BUTTON_PORT PORTD
#define BUTTON_PIN PIND
#define BUTTON_BIT PD2
#endif

static void uart_init(void)
{
	uint16_t ubrr = F_CPU / 16 / 9600 - 1;
	UBRR0H = ubrr >> 8;
	UBRR0L = ubrr & 0xff;
	UCSR0B = (1 << RXEN0) | (1 << TXEN0);
	UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
}

static void uart_put(char c)
{
	while (!(UCSR0A & (1 << UDRE0)))
		;
	UDR0 = c;
}

static void uart_print(const char * text)
{
	while (*text)
		uart_put(*text++);
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
	char line[24];
	uint8_t pressed = 0;
	LED_DDR |= (1 << LED_BIT);
	BUTTON_PORT |= (1 << BUTTON_BIT);  /* the pull-up of D2 */
	uart_init();
	uart_print("hello\n");
	for (uint16_t tick = 0;; tick++) {
		LED_PORT ^= (1 << LED_BIT);
		_delay_ms(1);
		if (UCSR0A & (1 << RXC0)) {
			char c = UDR0;
			uart_put(c >= 'a' && c <= 'z' ? c - 32 : c);
		}
		uint8_t now = !(BUTTON_PIN & (1 << BUTTON_BIT));
		if (now != pressed) {
			pressed = now;
			uart_print(pressed ? "D2=0\n" : "D2=1\n");
		}
		if (tick % 20 == 0) {
			snprintf(line, sizeof line, "A0=%u\n", adc_read(0));
			uart_print(line);
		}
	}
}
