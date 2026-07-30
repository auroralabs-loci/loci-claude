/* Link base 0x2000 on purpose.
 *
 * A function at address 0 is a separate, documented asmslicer limitation
 * (docs/10-limitations.md §10.1.6 — it is dropped and analysed as an unknown
 * callee, which can invert a stack verdict). Linking at 0x2000 keeps this
 * fixture measuring the staleness guard and nothing else, exactly as the
 * reported scenario's own target did.
 */
MEMORY
{
  FLASH (rx)  : ORIGIN = 0x00002000, LENGTH = 248K
  RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 32K
}

ENTRY(kernel_main)

SECTIONS
{
  .text : {
    KEEP(*(.isr_vector))
    *(.text*)
    *(.rodata*)
  } > FLASH

  .data : { *(.data*) } > RAM
  .bss  : { *(.bss*) *(COMMON) } > RAM

  _estack = ORIGIN(RAM) + LENGTH(RAM);
}
