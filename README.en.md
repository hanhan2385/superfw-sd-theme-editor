# SuperFW SD Theme Editor

A theme-pack editor for the **SuperFW** GBA flashcart firmware.

Tune the colours, icons and backgrounds, save a `theme.bin`, drop it on the SD card — the new
look appears on the next boot. **You flash the firmware once; after that you only swap a file.**

*[English](README.md) · 中文*

![Editor window](docs/images/editor.png)

## Three steps

1. Download the .exe from [Releases](../../releases) and double-click it (no Python needed)
2. Tune things, then *Save as…* to produce `theme.bin`
3. Copy it to `/.superfw/` on the SD card and boot

If the file is missing, the firmware generates a default one on first boot; just overwrite it.

## What you can change

| Item | Detail |
|---|---|
| Colours | 6 sets, 6 roles each (top/bottom bar, content background, text, borders, selected row, in-game menu) |
| Icons | 6 sets, 17 icons each, replaceable cell by cell |
| Backgrounds | 6 set wallpapers, plus **a separate image for each of the 7 tabs** (falls back to the set wallpaper) |

Previews are rendered with the firmware's own bitmap font and layout, not a mock-up — font
metrics and truncation behave exactly as on hardware. 14 screens are previewable: the 7 menu
pages plus the in-game menu, ROM info, patching options, loading options, NOR write, file
manager and the loading screen.

## Where the idea comes from

Keeping themes on the SD card, separate from the firmware, comes from
[ZaindORp's EZ-FLASH OMEGA DE kernel](https://github.com/orzgithub/omega-de-kernel_ZaindORp).

The upside: no reflashing to change a theme, break it and just delete the file to fall back to
the built-in skin, and firmware updates never invalidate your themes.

## Going further

- Running from source, building the firmware, self-tests → [docs/DEVELOP.md](docs/DEVELOP.md)
- Byte layout of `theme.bin` → [docs/FORMAT.md](docs/FORMAT.md)

## Licence and credits

GPL-3.0, see [LICENSE](LICENSE). `fw/` is derived from [SuperFW](https://superfw.davidgf.net/)
by davidgf, also GPL-3.0.

- **davidgf** — author of SuperFW
- **moribaka** — [superfw-fix](https://github.com/moribaka/superfw-fix) firmware branch
  (our firmware is built on its r78 patch engine and databases)
- **ZaindORp** — the idea of SD-card themes
- **WenQuanYi** — [Bitmap Song 10pt](https://wenq.org), the CJK glyphs in the firmware
- **Fusion Pixel** — [pixel font](https://github.com/TakWolf/fusion-pixel-font) used to fill in
  missing glyphs (OFL-1.1)
- **Electr0Whale** — packaged the two fonts into the firmware's bitmap format
  ([superfw, `wqy10pt-font` branch](https://github.com/Electr0Whale/superfw))

Made by **小米粥不糊**.
