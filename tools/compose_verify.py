# -*- coding: utf-8 -*-
"""把验证产物拼成两张总览图：六屏 240×160 与图标前后对比。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

OUT = os.path.join(ROOT, "dist", "verify")
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


def six_up():
    scale = 2
    cw, ch = 240 * scale, 160 * scale
    pad, top = 18, 26
    W = pad * 3 + cw * 3
    H = pad + (ch + top) * 2 + pad
    sheet = Image.new("RGB", (W, H), (11, 11, 9))
    d = ImageDraw.Draw(sheet)
    f = font(13)
    for i, (key, zh) in enumerate(SCREENS):
        p = os.path.join(OUT, "screen-%s-240x160.png" % key)
        im = Image.open(p).convert("RGB").resize((cw, ch), Image.NEAREST)
        x = pad + (i % 3) * (cw + pad)
        y = pad + (i // 3) * (ch + top)
        d.text((x, y + 4), "%s  %s  (240×160)" % (key, zh), fill=(200, 196, 184), font=f)
        sheet.paste(im, (x, y + 22))
    out = os.path.join(OUT, "screens-6up.png")
    sheet.save(out)
    return out


def icons_compare():
    old = Image.open(os.path.join(ROOT, "ref", "superfw", "res", "icons.png")).convert("RGBA")
    new = Image.open(os.path.join(ROOT, "dist", "icons", "icons.png")).convert("RGBA")
    s = 6
    gap = 18
    W = 17 * 16 * s + gap * 2
    H = 16 * s * 2 + gap * 3 + 22
    sheet = Image.new("RGB", (W, H), (19, 19, 17))
    d = ImageDraw.Draw(sheet)
    f = font(14)
    d.text((gap, 4), "原版图标（固件自带）", fill=(150, 146, 134), font=f)
    for i, (src, y0) in enumerate(((old, gap), (new, gap * 2 + 16 * s + 22))):
        big = src.resize((16 * 17 * s, 16 * s), Image.NEAREST)
        bg = Image.new("RGB", big.size, (31, 31, 27))
        bg.paste(big, (0, 0), big)
        sheet.paste(bg, (gap, y0))
    d.text((gap, gap + 16 * s + 2), "重绘版（17 个全部重画，18 色共用调色板）",
           fill=(217, 255, 87), font=f)
    out = os.path.join(OUT, "icons-before-after.png")
    sheet.save(out)
    return out


if __name__ == "__main__":
    print(six_up())
    print(icons_compare())
