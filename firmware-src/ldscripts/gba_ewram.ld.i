OUTPUT_FORMAT("elf32-littlearm")
OUTPUT_ARCH(arm)
ENTRY(entrypoint)
MEMORY {
    IWRAM : ORIGIN = 0x03000000, LENGTH = 32K
    EWRAM : ORIGIN = 0x027C0000, LENGTH = 251K
}
__STACK_SVC_SIZE__ = 0x40;
__STACK_IRQ_SIZE__ = 0xA0;
__STACK_USR_SIZE_MIN__ = 0x4000;
__STACK_END__ = ORIGIN(IWRAM) + LENGTH(IWRAM) - 0x20;
__STACK_SVC_END__ = __STACK_END__;
__STACK_SVC_START__ = __STACK_SVC_END__ - __STACK_SVC_SIZE__;
__STACK_IRQ_END__ = __STACK_SVC_START__;
__STACK_IRQ_START__ = __STACK_IRQ_END__ - __STACK_IRQ_SIZE__;
__STACK_USR_END__ = __STACK_IRQ_START__;
__STACK_USR_START__ = __STACK_USR_END__ - __STACK_USR_SIZE_MIN__;
__STACK_START__ = __STACK_USR_START__;
SECTIONS
{
    .gba_ewram_crt0 : ALIGN(4)
    {
        KEEP (*(.gba_ewram_crt0))
    } > EWRAM
    .text : ALIGN(4)
    {
        *(.text)
        *(.text*)
        *(.gnu.linkonce.t.*)
        *(.glue_7)
        *(.glue_7t)
        KEEP(*(.init))
        KEEP(*(.fini))
    } > EWRAM
    .rodata : ALIGN(4)
    {
        *(.ewram)
        *(.rodata)
        *(.rodata*)
        *(.gnu.linkonce.r.*)
    } > EWRAM
    .ARM.extab : ALIGN(4)
    {
        *(.ARM.extab* .gnu.linkonce.armextab.*)
    } > EWRAM
    .ARM.exidx : ALIGN(4)
    {
        PROVIDE_HIDDEN(__exidx_start = .);
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
        PROVIDE_HIDDEN(__exidx_end = .);
    } > EWRAM
    .preinit_array : ALIGN(4)
    {
        __preinit_array_start = .;
        KEEP(*(.preinit_array*))
        __preinit_array_end = .;
    } > EWRAM
    .init_array : ALIGN(4)
    {
        __init_array_start = .;
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array*))
        __init_array_end = .;
    } > EWRAM
    .fini_array : ALIGN(4)
    {
        __fini_array_start = .;
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array*))
        __fini_array_end = .;
    } > EWRAM
    .bss (NOLOAD) : ALIGN(4)
    {
        *(.bss)
        *(.bss*)
        *(.gnu.linkonce.b.*)
        *(COMMON)
        . = ALIGN(4);
    } > IWRAM
    __IWRAM_BSS_START__ = ADDR(.bss);
    __IWRAM_BSS_SIZE__ = SIZEOF(.bss);
    __IWRAM_BSS_END__ = __IWRAM_BSS_START__ + __IWRAM_BSS_SIZE__;
    .data : ALIGN(4)
    {
        __DATA_START__ = .;
        *(.data)
        *(.data*)
        *(.gnu.linkonce.d.*)
        . = ALIGN(4);
        __DATA_END__ = .;
    } > IWRAM AT > EWRAM
    __DATA_SIZE__ = __DATA_END__ - __DATA_START__;
    __DATA_LMA__ = LOADADDR(.data);
    .iwram : ALIGN(4)
    {
        __IWRAM_START__ = .;
        *(.iwram)
        *(.iwram*)
        *iwram.*(.text*)
        *iwram.*(.data*)
        . = ALIGN(4);
        __IWRAM_END__ = .;
    } > IWRAM AT > EWRAM
    __IWRAM_SIZE__ = __IWRAM_END__ - __IWRAM_START__;
    __IWRAM_LMA__ = LOADADDR(.iwram);
    ASSERT(__IWRAM_END__ <= __STACK_START__,
           "Not enough free IWRAM for stack")
    __STACK_USR_SIZE__ = __STACK_USR_END__ - __IWRAM_END__;
    .sbss (NOLOAD) : ALIGN(4)
    {
        *(.sbss)
        *(.sbss*)
        . = ALIGN(4);
    } > EWRAM
}
ASSERT(patch_flash_read_sram64k_size - (patch_flash_read_sram64k & ~1) <= 118, "Patch function too long"); ASSERT(patch_flash_read_sram128k_size - (patch_flash_read_sram128k & ~1) <= 118, "Patch function too long"); ASSERT(patch_flash_read_directsave_size - (patch_flash_read_directsave & ~1) <= 118, "Patch function too long");
ASSERT(patch_flash_erase_device_sram64k_size - (patch_flash_erase_device_sram64k & ~1) <= 90, "Patch function too long"); ASSERT(patch_flash_erase_device_sram128k_size - (patch_flash_erase_device_sram128k & ~1) <= 90, "Patch function too long"); ASSERT(patch_flash_erase_device_directsave_size - (patch_flash_erase_device_directsave & ~1) <= 90, "Patch function too long");
ASSERT(patch_flash_erase_sector_sram64k_size - (patch_flash_erase_sector_sram64k & ~1) <= 110, "Patch function too long"); ASSERT(patch_flash_erase_sector_sram128k_size - (patch_flash_erase_sector_sram128k & ~1) <= 110, "Patch function too long"); ASSERT(patch_flash_erase_sector_directsave_size - (patch_flash_erase_sector_directsave & ~1) <= 110, "Patch function too long");
ASSERT(patch_flash_write_sector_sram64k_size - (patch_flash_write_sector_sram64k & ~1) <= 100, "Patch function too long"); ASSERT(patch_flash_write_sector_sram128k_size - (patch_flash_write_sector_sram128k & ~1) <= 100, "Patch function too long"); ASSERT(patch_flash_write_sector_directsave_size - (patch_flash_write_sector_directsave & ~1) <= 100, "Patch function too long");
ASSERT(patch_flash_write_byte_sram64k_size - (patch_flash_write_byte_sram64k & ~1) <= 148, "Patch function too long"); ASSERT(patch_flash_write_byte_sram128k_size - (patch_flash_write_byte_sram128k & ~1) <= 148, "Patch function too long"); ASSERT(patch_flash_write_byte_directsave_size - (patch_flash_write_byte_directsave & ~1) <= 148, "Patch function too long");
ASSERT(patch_flash_bank_sram_size - (patch_flash_bank_sram & ~1) <= 36, "Patch function too long");
ASSERT(patch_eeprom_read_sram64k_size - (patch_eeprom_read_sram64k & ~1) <= 168, "Patch function too long"); ASSERT(patch_eeprom_read_directsave_size - (patch_eeprom_read_directsave & ~1) <= 168, "Patch function too long");
ASSERT(patch_eeprom_write_sram64k_size - (patch_eeprom_write_sram64k & ~1) <= 204, "Patch function too long"); ASSERT(patch_eeprom_write_directsave_size - (patch_eeprom_write_directsave & ~1) <= 204, "Patch function too long");
ASSERT(patch_rtc_probe_size - (patch_rtc_probe & ~1) <= 214, "Patch function too long");
ASSERT(patch_rtc_reset_size - (patch_rtc_reset & ~1) <= 132, "Patch function too long");
ASSERT(patch_rtc_gettimedate_size - (patch_rtc_gettimedate & ~1) <= 176, "Patch function too long");
ASSERT(patch_rtc_getstatus_size - (patch_rtc_getstatus & ~1) <= 204, "Patch function too long");
ASSERT(patch_rtc_settimedate_size - (patch_rtc_settimedate & ~1) <= 156, "Patch function too long");
