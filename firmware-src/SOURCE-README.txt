SuperFW-Chis-SDTheme -- firmware source

This is the exact source used to build `SuperFW-Chis-SDTheme.fw`
(build id 79000004, built 2026-10-27, size 2,008,064 B).

Base: superfw-fix r79 -- https://github.com/moribaka/superfw-fix (GPL-3.0)
      which itself derives from SuperFW by davidgf -- https://superfw.davidgf.net
Changes in this tree: SD-card theme engine (src/sdtheme.c, res/fonts-ext.pack,
WenQuanYi 10pt bitmap font), selection bar, bar style, dialog arrows.

2026-10-26: replaced character U+2BC5..U+2BC8 (no glyph in the WenQuanYi
bitmap pack, rendered as black blobs) with U+25C0/25B6/25B2/25BC in
src/settings.c, src/ingame_menu.c and src/menu.c, plus vertical
compensation where they sit next to text/icons.

2026-10-26: the compact in-game menu (used on 32 MiB ROMs) now draws
disabled items with a solid gray (OBJ palette index 2) instead of a
checkerboard dither, which looked like ghosting on hardware. The full
in-game menu is unchanged.

2026-10-26 (v0.31): the ROM loading screen now shows the theme wallpaper.
It used to be a flat colour because the blit was guarded by
"sdt_cache >= 0x09000000" while the cache can only live at 0x081F0000.
The wallpaper is now copied once on the first progress frame (into both
framebuffers) and the cache is never read again, so the ROM data that
overwrites it halfway through the load does not matter.

2026-10-26 (v0.31): the v8 selection-bar texture path no longer rewrites
BG palette entries 240..255. Those slots were already allocated (240..244
in-game menu colours, 245..247 the three full-screen colours, 248 the
selection bar, 249 the bottom bar), so any theme with a texture got a
black loading-progress bar and wrong in-game menu colours.

2026-10-26 (v0.31): the same progress screen is now also painted when
flashing a ROM into NOR (the wallpaper is blitted into both framebuffers
before sdtheme_invalidate() marks the cache dirty). Patch-scan invalidation
is conditional now (sdtheme_invalidate_scan), so the first load of a ROM
that needs scanning also keeps its wallpaper, and reload_theme() falls back
to a readable grey when a theme's disabled-item colour matches the
background (the "missing" clock / cheat menu entries).

Build:  bash build_chis.sh   (needs arm-none-eabi-gcc; see README.md)
        tools/check_font_coverage.py reports characters used in the source
        that have no glyph in res/fonts-ext.pack.

2026-10-27 (v0.32): merged superfw-fix r79 -- Hack database grown to 1,962
lookup records (falls back to the built-in database when a hack entry misses,
and offers "run without patches" when neither matches), new SRAM/NOR support
for the standalone Pokemon SoulSilver hack and Diablo [Bangoo] v0.9.4 dual-bank
saves, plus src/webpatch.c (streams the sidecar .patch file while loading or
flashing). The in-game menu / RTC / DirectSave availability checks now use
patch_rom_size() so the extra RTC-patch image bytes are accounted for.

One local deviation from r79: `menu_language_work` in src/loader.c keeps the
__attribute__((section(".sbss"))) annotation. r79 moved it into .bss to save
EWRAM, but in this tree .bss lives in IWRAM and the 608 bytes pushed the IWRAM
image 8 bytes past __STACK_START__ (link error "Not enough free IWRAM for
stack"). Put it back in .sbss (EWRAM) and the link fits with ~600 B to spare.
