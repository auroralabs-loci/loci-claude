/* Stale-artifact eval fixture — the state the linked ELF was built from.
 *
 * `kernel_main` here contains no calls at all, so a stack-depth run against the
 * ELF linked from this file reports a single-frame worst case and PASSes any
 * sane budget. blink_post.c is the "edit" the eval then applies; the point of the
 * fixture is that measuring THIS after that edit is the reported defect.
 */

#define PORTDIRSET 0x41004408
#define PORTOUT    0x41004410
#define PORTCLR    0x41004414
#define LED_GPIO_BIT 10

volatile unsigned int *gpio;
volatile unsigned int tim;
volatile unsigned int checksum;

void kernel_main(void)
{
	gpio = (unsigned int *)PORTDIRSET;
	*gpio |= (1 << LED_GPIO_BIT);

	while (1) {
		for (tim = 0; tim < 5000; tim++)
			;

		gpio = (unsigned int *)PORTCLR;
		*gpio = (1 << LED_GPIO_BIT);

		for (tim = 0; tim < 5000; tim++)
			;

		gpio = (unsigned int *)PORTOUT;
		*gpio = (1 << LED_GPIO_BIT);
	}
}
