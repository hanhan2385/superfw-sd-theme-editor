
VERSION_WORD := 0x00000015
VERSION_SLUG_WORD ?= 79000004
# 构建日期：关于页第 3 行会显示成「Chis SDT 2026-10-21」，
# 用来和官方 r75 发布件区分 —— 那边这一行是「Chis variant」，版本号又是同一个
# 75000001，不靠这行字在机器上根本看不出刷的是哪个。
SDT_BUILD := $(shell date +%Y-%m-%d)

PREFIX		?= arm-none-eabi-
NM := $(PREFIX)nm
PYTHON ?= python
export CROSS_COMPILE := $(PREFIX)
export CC
CC		:= $(PREFIX)gcc
CXX		:= $(PREFIX)g++
OBJDUMP		:= $(PREFIX)objdump
OBJCOPY		:= $(PREFIX)objcopy

COMPRESSION_RATIO ?= 4

GLOBAL_DEFINES = -D__GBA__

# BOARD can be "sd", "lite", "chis"
BOARD ?= sd

ifeq ($(BOARD),lite)
  GLOBAL_DEFINES += -DSUPERCARD_LITE_IO
  COMPRESS_FIRMWARE = 1
  MAXFSIZE = 496
  FWFLAVOUR = "Lite"
else ifeq ($(BOARD),sd)
  GLOBAL_DEFINES += -DSUPERCARD_FLASH_ADDRPERM
  BUNDLE_GBC_EMULATOR = 1
  COMPRESS_FIRMWARE = 1
  MAXFSIZE = 512
  FWFLAVOUR = "SD"
else ifeq ($(BOARD),chis)
  GLOBAL_DEFINES += -DSUPPORT_NORGAMES -DSUPERCHIS_IO -DFONTS_EXT
  BUNDLE_GBC_EMULATOR = 1
  BUNDLE_OTHER_EMULATORS = 1
  # Can't be over 2MiB (hardlimit)
  MAXFSIZE = 2048
  FWFLAVOUR = "Chis"
else
  $(error No valid board specified in BOARD)
endif

FWBINFILES=firmware.ewram.gba res/patches.db res/fonts.pack

ifeq ($(ENABLE_DISK_LOGGING),1)
  PAYLOADFLAGS += -DENABLE_DISK_LOGGING
endif
ifeq ($(ENABLE_EMU_LOGGING),1)
  PAYLOADFLAGS += -DENABLE_EMU_LOGGING
endif
ifeq ($(ENABLE_UART_LOGGING),1)
  PAYLOADFLAGS += -DENABLE_UART_LOGGING
endif

# SD theme diagnostics: draws a 4-line overlay at the bottom of the menu
# (probe result, cache address, theme.bin parse result, boot timings).
ifeq ($(SDTHEME_DIAG),1)
  GLOBAL_DEFINES += -DSDTHEME_DIAG
endif

# 选中条高亮的两种实现，编译时二选一（用户要比对效果）：
#   SEL_HW=1  → 63 号 OBJ + 硬件 alpha（玻璃雾感，但字被压暗）
#   不给      → 画进帧缓冲 + 8x8 Bayer 网点（字清晰，但看着像光栅）
ifeq ($(SEL_HW),1)
  GLOBAL_DEFINES += -DSEL_HW_BLEND
endif

# 给 mGBA 跑用的版本：不碰 SuperCard 硬件，扇区数据由 fatfs/diskio.c 里的
# 16KB 内存盘提供（空根目录）。只为在电脑上验证界面，**不能当正式固件刷**。
ifeq ($(EMU_BUILD),1)
  GLOBAL_DEFINES += -DEMU_BUILD
endif

ifeq ($(COMPRESS_FIRMWARE),1)
  GLOBAL_DEFINES += -DCOMPRESS_FONTS -DCOMPRESS_PATCHES -DCOMPRESS_FIRMWARE
  FWBINFILES := $(addsuffix .comp,$(FWBINFILES))
endif

ifeq ($(BUNDLE_GBC_EMULATOR),1)
  GLOBAL_DEFINES += -DBUNDLE_GBC_EMULATOR
  BIEMUFILES += emu/jagoombacolor_v0.5.gba.comp
endif

ifeq ($(BUNDLE_OTHER_EMULATORS),1)
  GLOBAL_DEFINES += -DBUNDLE_OTHER_EMULATOR
  BIEMUFILES += emu/pocketnes_20130701.gba.comp \
                emu/wasabigba_v0.2.4.gba.comp \
                emu/NGPGBA_v0.5.7.gba.comp \
                emu/pceadvance-v7.5-scptch.gba.comp \
                emu/smsadvance-v2.5-scptch.gba.comp
endif

BASEFLAGS=$(GLOBAL_DEFINES) -mcpu=arm7tdmi -mtune=arm7tdmi

# -Os：主镜像在 -O2 下 EWRAM 只剩 576 字节，装不下 SD 主题；
# 换 -Os 腾出十几 KB（菜单本来就是 -Os）。
CFLAGS=-Os -ggdb \
       $(BASEFLAGS) $(PAYLOADFLAGS) \
       -DFW_MAX_SIZE_KB=$(MAXFSIZE) -DFW_FLAVOUR="\"$(FWFLAVOUR)\"" \
       -DSC_FAST_ROM_MIRROR="use_fast_mirror()" \
       -DSD_PREERASE_BLOCKS_WRITE \
       -DSDT_BUFSZ=1024 \
       -DVERSION_WORD="$(VERSION_WORD)" \
       -DVERSION_SLUG_WORD="0x$(VERSION_SLUG_WORD)" -DSDT_BUILD_DATE="\"$(SDT_BUILD)\"" \
       -DFL_BUILD_TAG="\"$(if $(FL_TAG),$(FL_TAG),$(shell date +%H%M))\"" \
       -Wall -Isrc -I. -mthumb -flto -flto-partition=none


# -fno-jump-tables: gcc 13.3.1 下菜单镜像 53340 字节，超出 layout 1 的 53308
# 槽位 32 字节（作者用别的工具链编的）。关掉跳转表省 56 字节，正好装回去。
INGAME_CFLAGS=-Os -ggdb -fno-jump-tables \
              $(BASEFLAGS) \
              -DNO_SUPERCARD_INIT \
              -DSD_PREERASE_BLOCKS_WRITE \
              -Wall -Isrc -I. \
              -mthumb -flto

DLDI_CFLAGS=-Os -ggdb \
            $(BASEFLAGS) -DSDDRV_TIMEOUT_MULT=2 \
            -DSD_PREERASE_BLOCKS_WRITE \
            -Wall -Isrc -I. \
            -mthumb -flto -fPIC

DIRECTSAVE_CFLAGS=-Os -ggdb \
                 $(BASEFLAGS) -DSDDRV_TIMEOUT_MULT=16 \
                 -DNO_SUPERCARD_INIT \
                 -Wall -Isrc -I. \
                 -marm -flto -fPIC

FATFSFILES=fatfs/diskio.c \
           fatfs/ff.c \
           fatfs/ffsystem.c \
           fatfs/ffunicode.c

DLDIFILES=src/dldi.S \
          src/dldi_driver.c \
          src/crc.c \
          src/supercard_io.S \
          src/supercard_driver.c

DIRECTSAVEFILES=src/directsaver.S \
                src/directsave_emu.c \
                src/crc.c \
                src/supercard_driver.c

MENUFILES=src/ingame.S \
          src/ingame_menu.c \
          src/rtc_state.c \
          src/cimpl.c \
          src/fonts/font_render.c \
          src/save.c \
          src/util.c \
          src/utf_util.c \
          src/fileutil.c \
          src/crc.c \
          src/nanoprintf.c \
          src/supercard_io.S \
          src/supercard_driver.c \
          ${FATFSFILES}

INFILES=src/gba_ewram_crt0.S \
        src/main.c \
        src/log.c \
        src/cimpl.c \
        src/settings.c \
        src/rtc_state.c \
        src/rtc_custom.c \
        src/loader.c \
        src/menu_layout.c \
        src/menu_overlay_profile.c \
        src/save.c \
        src/patchengine.c \
        src/webpatch.c \
        src/irqpatch.c \
        src/patcher.c \
        src/romcompat.c \
        src/waitscan.c \
        src/hackpatch.c \
        src/patches.S \
        src/menu.c \
        src/sdtheme.c \
        src/fontload.c \
        src/recent.c \
        src/cheats.c \
        src/flash.c \
        src/sha256.c \
        src/misc.c \
        src/util.c \
        src/utf_util.c \
        src/emu.c \
        src/sfc.c \
        src/fileutil.c \
        src/asmutil.S \
        src/gbahw.c \
        src/virtfs.c \
        src/flash_mgr.c \
        src/binassets.S \
        src/crc.c \
        src/nds_loader.c \
        src/dldi_patcher.c \
        src/supercard_driver.c \
        src/supercard_io.S \
        src/heapsort.c \
        src/nanoprintf.c \
        src/fonts/font_render.c \
        ${FATFSFILES}

# 注意：Hack 补丁库由 rom_boot.S incbin 进固件，上游 r78 把它从 FWBINFILES 里删了，
# 所以改了 res/hackpatches.db 之后 make 不会自动重编 —— 手动 `touch rom_boot.S`
# 或 `make clean` 一次。**不要**在这里写 `rom_boot.o: ...` 这种规则：本工程根本
# 不用 rom_boot.o（all 里是一条 gcc 直接编 rom_boot.S 的命令），写了会变成
# 默认目标，反而用内置规则编出没带 -DFW_FLAVOUR 的目标文件。

all:	$(FWBINFILES) $(BIEMUFILES) directsave.payload ingame_trampoline.payload cheat_import.payload
	# Wrap the firmware around a ROM->EWRAM loader
	$(CC) $(CFLAGS) -o firmware.elf rom_boot.S -T ldscripts/gba_romboot.ld -nostartfiles -nostdlib -Wl,--defsym,MAX_FLASH_SIZE=$(MAXFSIZE)K
	$(OBJCOPY) --output-target=binary firmware.elf superfw.gba
	# Fix the header/checksum.
	$(PYTHON) tools/fw-fixer.py superfw.gba
	cp superfw.gba superfw.fw

ifeq ($(BOARD),chis)
FWMENUASSETS=ingamemenu.head ingamemenu.compact.head
else
FWMENUASSETS=ingamemenu.payload
endif

firmware.ewram.gba: $(INFILES) src/sfc_core_layout.h $(FWMENUASSETS) superfw.dldi.payload directsave.payload ingame_trampoline.payload ingame_nor_scatter.payload rtc_custom.payload src/rtc_custom_identities.h src/rtc_state.h src/messages_data.h src/rtc_speed.h ldscripts/gba_ewram.ld.i
	# Build the actual firmware image
	$(CC) $(CFLAGS) -o firmware.ewram.elf $(INFILES) -T ldscripts/gba_ewram.ld.i -nostartfiles -Wl,-Map=firmware.ewram.map -Wl,--print-memory-usage -fno-builtin
	$(OBJCOPY) --output-target=binary firmware.ewram.elf firmware.ewram.gba

superfw.dldi.payload:	$(DLDIFILES)
	# Build in-game menu
	$(CC) $(DLDI_CFLAGS) -o superfw.dldi.elf $(DLDIFILES) -T ldscripts/gba_dldi.ld \
			-nostartfiles -fno-builtin -Wl,-Map=superfw.dldi.map -Wl,--print-memory-usage
	$(OBJCOPY) --output-target=binary superfw.dldi.elf superfw.dldi.payload

directsave.payload:	$(DIRECTSAVEFILES)
	$(CC) $(DIRECTSAVE_CFLAGS) -o directsave.elf $(DIRECTSAVEFILES) -T ldscripts/gba_directsave.ld \
			-nostartfiles -fno-builtin -Wl,-Map=directsave.map -Wl,--print-memory-usage
	$(OBJCOPY) --output-target=binary directsave.elf directsave.payload

ingamemenu.payload:	$(MENUFILES) src/ingame.h src/menu_messages.h src/rtc_speed.h ldscripts/gba_ingame.ld
	# Build in-game menu
	$(CC) $(INGAME_CFLAGS) -o ingamemenu.elf $(MENUFILES) -T ldscripts/gba_ingame.ld \
			-nostartfiles -fno-builtin -Wl,-Map=firmware.ingame.map -Wl,--print-memory-usage
	$(OBJCOPY) --output-target=binary ingamemenu.elf ingamemenu.payload

ingamemenu.compact.raw: src/menu_messages_compact.h src/menu_format.c $(MENUFILES) src/ingame.h src/menu_compact_render.h src/menu_messages.h src/rtc_speed.h ldscripts/gba_ingame.ld
	$(CC) $(INGAME_CFLAGS) -DCOMPACT_INGAME_MENU -o ingamemenu.compact.elf $(subst src/nanoprintf.c,src/menu_format.c,$(MENUFILES)) -Wl,--defsym,IGM_EWRAM_LIMIT=35K -Wl,--defsym,IGM_SPILL_LIMIT=33K -T ldscripts/gba_ingame.ld \
		-nostartfiles -fno-builtin -Wl,-Map=firmware.ingame.compact.map -Wl,--print-memory-usage
	$(OBJCOPY) --output-target=binary ingamemenu.compact.elf ingamemenu.compact.raw

ingamemenu.head ingamemenu.body ingamemenu.size.S ingamemenu.compact.head ingamemenu.compact.body ingamemenu.compact.size.S ingamemenu.compact.payload ingame_nor_scatter.payload &: ingamemenu.payload ingamemenu.compact.raw tools/build_menu_assets.py
	$(PYTHON) tools/build_menu_assets.py

cheat_import.payload cheat_runtime.payload &: $(wildcard src/cheat_formats/*) src/cheats.h tools/build_cheat_engine.py
	$(PYTHON) tools/build_cheat_engine.py

ingame_trampoline.payload:	src/ingame_trampoline.S
	$(CC) $(BASEFLAGS) -nostartfiles -T ldscripts/gba_ingametramp.ld -o ingame_trampoline.elf src/ingame_trampoline.S
	$(OBJCOPY) --output-target=binary ingame_trampoline.elf ingame_trampoline.payload

src/messages_data.h:	res/messages.py
	$(PYTHON) res/messages.py h main > src/messages_data.h

src/menu_messages_compact.h: src/menu_messages.h tools/build_compact_menu_text.py
	$(PYTHON) tools/build_compact_menu_text.py
	$(PYTHON) tools/build_menu_font_index.py

src/menu_messages.h:	res/messages.py
	$(PYTHON) res/messages.py h menu > src/menu_messages.h

firmware.ewram.gba.comp:	firmware.ewram.gba ./upkr.elf
	./upkr.elf -l $(COMPRESSION_RATIO) $< $@

%.gba.comp:	%.gba.bin ./upkr.elf
	./upkr.elf -l $(COMPRESSION_RATIO) $< $@

rtc_custom.payload: src/rtc_custom.S src/patches.S tools/build_rtc_custom_payload.py
	$(PYTHON) tools/build_rtc_custom_payload.py

%.db.comp:	%.db ./upkr.elf
	./upkr.elf -l $(COMPRESSION_RATIO) $< $@

%.pack.comp:	%.pack apultra.elf
	./apultra.elf $< $@

ldscripts/gba_ewram.ld.i: ldscripts/gba_ewram.ld ldscripts/validate.inc

%.ld.i:	%.ld
	$(CC) -E -P -x c $< -o $@

apultra.elf:	tools/apultra.cc
	g++ -std=c++20 -O3 $< -o $@

upkr.elf:	tools/upkr.cc
	g++ -o $@ $< -O3 -ffast-math

clean:
	rm -f ldscripts/*.i *.gba *.fw *.elf *.payload *.map res/*.comp emu/*.comp *.comp src/menu_messages.h src/messages_data.h

