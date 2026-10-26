SuperFW-Chis-SDTheme -- firmware source

This is the exact source used to build `SuperFW-Chis-SDTheme.fw`
(build id 78000001, built 2026-10-26, size 1,970,176 B).

Base: superfw-fix r78 -- https://github.com/moribaka/superfw-fix (GPL-3.0)
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

Build:  bash build_chis.sh   (needs arm-none-eabi-gcc; see README.md)
        tools/check_font_coverage.py reports characters used in the source
        that have no glyph in res/fonts-ext.pack.
