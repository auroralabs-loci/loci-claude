/* Stale-artifact eval fixture — the state on disk AFTER the edit.
 *
 * Adds `build_pattern` (a stack buffer bigger than the 2048-byte budget) and
 * `render_frame`, both called from `kernel_main`.
 *
 * The buffer is deliberately 3072 bytes rather than the 4096 of the scenario this
 * fixture reconstructs. The eval's system prompt now inlines the whole SKILL.md,
 * which carries a worked example of the real 4096-byte case (4144 B / 202.3 %), so
 * a fixture reusing those constants would let a model satisfy the assertions by
 * transcription instead of measurement. Different constants mean the eval can only
 * pass by actually running the analysis.
 *
 * Re-derived on this fixture (gcc -fstack-usage agrees with loci exactly):
 *
 *   per-function frames   kernel_main 8, build_pattern 3088, render_frame 24
 *   relinked ELF          worst case 3120 B = 152.3% of a 2048 B budget, FAIL
 *   the fresh .o alone     worst case 8 B, path [kernel_main], PASS
 *
 * That last line is the trap: in a relocatable object the `bl` to `build_pattern`
 * is an unapplied R_ARM_THM_CALL encoded `f7ff fffe` (a branch to self), so the
 * call edge is absent and the buffer vanishes — with has_unknown_callees false and
 * no warning. A run that reports 3120 therefore proves both that staleness was
 * detected AND that a relink happened.
 */

#define PORTDIRSET 0x41004408
#define PORTOUT    0x41004410
#define PORTCLR    0x41004414
#define LED_GPIO_BIT 10

volatile unsigned int *gpio;
volatile unsigned int tim;
volatile unsigned int checksum;

/* Consume the scratch buffer so it cannot be optimised away. */
void render_frame(unsigned char *out, int n)
{
	int i;
	unsigned int acc = 0;

	for (i = 0; i < n; i++)
		acc += out[i];
	checksum = acc;
}

/* The 3 KB stack buffer that overflows a 2 KB budget. */
void build_pattern(void)
{
	unsigned char scratch[3072];
	int i;

	for (i = 0; i < 3072; i++)
		scratch[i] = (unsigned char)(i * 31 + 7);
	render_frame(scratch, 3072);
}

void kernel_main(void)
{
	gpio = (unsigned int *)PORTDIRSET;
	*gpio |= (1 << LED_GPIO_BIT);

	while (1) {
		build_pattern();

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
