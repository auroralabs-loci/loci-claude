/* Minimal Cortex-M0+ startup for the stale-artifact eval fixture.
 *
 * Not meant to run — the fixture only needs a *linked* ELF whose call edges
 * resolve, so stack-depth has a real call graph to walk. Deliberately tiny and
 * self-contained: no SDK, no libc startup (`-nostartfiles`).
 */

extern unsigned int _estack;
void kernel_main(void);

static void default_handler(void) { for (;;) { } }

/* Vector table: initial SP, reset, then a few handlers. Placed by the linker
 * script at the start of .isr_vector. */
__attribute__((section(".isr_vector"), used))
void (*const vector_table[])(void) = {
    (void (*)(void))&_estack,
    (void (*)(void))kernel_main,
    default_handler,
    default_handler,
};
