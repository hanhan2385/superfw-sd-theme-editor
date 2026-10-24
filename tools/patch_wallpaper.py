# -*- coding: utf-8 -*-
"""
给 SuperFW 固件换背景图片（命令行版）。

  python tools/patch_wallpaper.py in.bin out.bin --image wall.png
  python tools/patch_wallpaper.py in.bin out.bin --revert
  python tools/patch_wallpaper.py in.bin --info
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from PIL import Image  # noqa: E402
import wallpaper as WP  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--image", help="背景图片文件")
    ap.add_argument("--fit", default="cover", choices=["cover", "contain", "stretch"])
    ap.add_argument("--dim", type=float, default=0.0, help="0..1，向底色淡化，越大文字越清楚")
    ap.add_argument("--bg", default="000000", help="淡化底色 / contain 留边色 RRGGBB")
    ap.add_argument("--colors", type=int, default=WP.PAL_N, help="量化色数，最多 %d" % WP.PAL_N)
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--revert", action="store_true", help="还原成纯色填充")
    ap.add_argument("--info", action="store_true")
    args = ap.parse_args()

    rom = bytearray(open(args.src, "rb").read())
    print("输入 %d 字节，头部校验和 %s" % (len(rom), "有效" if WP.fw_validate(rom) else "无效"))
    print("已打过墙纸补丁: %s" % ("是" if WP.is_patched(rom) else "否"))

    if args.info:
        ok, why = WP.check_rom(rom)
        print("可打补丁:", ok, why)
        return

    if args.revert:
        if not args.dst:
            raise SystemExit("--revert 需要 out.bin")
        if WP.revert(rom):
            open(args.dst, "wb").write(rom)
            print("已还原并写出", args.dst, "校验:", "通过" if WP.fw_validate(rom) else "失败")
        else:
            print("这个固件没有墙纸补丁")
        return

    if not args.image:
        raise SystemExit("需要 --image")
    if not args.dst:
        raise SystemExit("需要 out.bin")

    bg = tuple(int(args.bg[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.open(args.image)
    im = WP.fit_image(im, args.fit, bg)
    idx, pal, n = WP.quantize(im, args.dim, bg, args.colors, not args.no_dither)
    print("位图 %dx%d，%d 色（调色板槽位 %d..%d）" % (WP.W, WP.H, n, WP.PAL_FIRST, WP.PAL_FIRST + WP.PAL_N - 1))

    info = WP.apply(rom, idx, pal)
    open(args.dst, "wb").write(rom)
    for k, v in info.items():
        print("  %-9s %s" % (k, v))
    print("写出", args.dst)


if __name__ == "__main__":
    main()
