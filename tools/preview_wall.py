# -*- coding: utf-8 -*-
"""把指定背景图 + 固件主题，渲染成"刷进去之后看到的样子"。

固件里背景图只铺内容区 240x144（顶栏/底栏仍是 FG 纯色），文字是 FT 色压在上面，
所以预览必须走和注入完全相同的量化路径，否则看不出真实对比度。

用法：
  python tools/preview_wall.py --wall dist/wallpaper/bocchi-1536x921.png \
      --rom dist/adapted/superfw-chis-Themed.fw --dim 0.2 --tag bocchi
"""
import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import main as APP          # noqa: E402
import wallpaper as WP      # noqa: E402
import fwbuild as FB        # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

SCREENS = [("browser", "文件浏览"), ("uisettings", "界面设置"), ("loadinfo", "载入信息"),
           ("loadpatch", "补丁选项"), ("tools", "工具"), ("info", "关于")]


def font(sz):
    for name in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(name):
            try:
                return ImageFont.truetype(name, sz)
            except Exception:
                pass
    return ImageFont.load_default()


def theme_from_rom(rom, slot):
    lay = FB.detect(rom)
    out = []
    for i in range(6):
        v = struct.unpack_from("<H", bytes(rom), lay.themes + slot * 12 + i * 2)[0]
        out.append("%02X%02X%02X" % ((v & 0x1F) << 3, ((v >> 5) & 0x1F) << 3,
                                     ((v >> 10) & 0x1F) << 3))
    return out


def wall_rgb(src, dim, bg=(0, 0, 0)):
    """和注入用同一条路径：cover 适配 → 236 色量化 → 淡化 → 回到 RGB。"""
    im = WP.fit_image(src, "cover", bg)
    idx, pal, n = WP.quantize(im, dim, bg, 236, True)
    flat = []
    for v in pal:
        flat += [(v & 0x1F) * 255 // 31, ((v >> 5) & 0x1F) * 255 // 31,
                 ((v >> 10) & 0x1F) * 255 // 31]
    prev = Image.frombytes("P", (240, 144), idx)
    prev.putpalette(flat)
    return prev.convert("RGB"), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wall", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--dim", type=float, default=0.2)
    ap.add_argument("--tag", default="wall")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dist", "verify"))
    args = ap.parse_args()

    rom = open(args.rom, "rb").read()
    pal = theme_from_rom(rom, 5)
    src = Image.open(args.wall).convert("RGB")
    wall, n = wall_rgb(src, args.dim)

    r = APP.Renderer()
    r.pal_ref = pal
    r.wall = wall

    sub = args.outdir
    os.makedirs(sub, exist_ok=True)

    shots = {}
    for key, _zh in SCREENS:
        im = r.screen(key, pal, "zh", 2).convert("RGB")
        shots[key] = im
        im.save(os.path.join(sub, "screen-%s-240x160.png" % key))

    shots["browser"].resize((720, 480), Image.NEAREST).save(
        os.path.join(sub, "%s-browser-720x480.png" % args.tag))
    wall.save(os.path.join(sub, "%s-wallpaper-240x144.png" % args.tag))

    # 六屏总览
    s, pad, top, head = 2, 14, 22, 34
    cw, ch = 240 * s, 160 * s
    sheet = Image.new("RGB", (pad * 4 + cw * 3, head + pad * 3 + (ch + top) * 2), (11, 11, 9))
    d = ImageDraw.Draw(sheet)
    f = font(13)
    d.text((pad, 7), "背景图预览 · 主题取自固件第 6 槽 · 236 色 · 淡化 %.0f%%"
           % (args.dim * 100), fill=(217, 255, 87), font=f)
    for i, (key, zh) in enumerate(SCREENS):
        x = pad + (i % 3) * (cw + pad)
        y = head + pad + (i // 3) * (ch + top)
        d.text((x, y - 18), "%s %s" % (key, zh), fill=(200, 196, 184), font=f)
        sheet.paste(shots[key].resize((cw, ch), Image.NEAREST), (x, y))
    out = os.path.join(sub, "screens-6up-%s.png" % args.tag)
    sheet.save(out)

    ft = pal[2]
    print("主题   ", pal)
    print("背景   %d 色 · %s" % (n, args.tag))
    print("720x480", os.path.join(sub, "%s-browser-720x480.png" % args.tag))
    print("六屏   ", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
