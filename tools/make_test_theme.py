#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成一份「能设置的都设上」的测试主题，用来验证固件对每一项的响应。

    python tools/make_test_theme.py dist/fonttest/主题包-测试/theme.bin

六套配色各不相同、壁纸策略也不同（有整套图 / 无图 / 逐页图），
另外把 logo、扩展色、透明度、v8 纹理都写进去 —— 每项都对应实机上的一个可见位置，
说明写在生成的 读我.txt 里。
"""
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import sdedit as E                       # noqa: E402
import sdtheme as S                      # noqa: E402

W, H = S.WALL_W, S.WALL_H


def stripes(top, bot, step=8):
    """斜纹：深一行浅一行，看得出「这是图不是纯色」。"""
    im = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(im)
    for x in range(-H, W, step * 2):
        d.line([(x, H), (x + H, 0)], fill=bot, width=step)
    return im


def checker(a, b, cell=16):
    im = Image.new("RGB", (W, H), a)
    d = ImageDraw.Draw(im)
    for y in range(0, H, cell):
        for x in range(0, W, cell):
            if ((x // cell) + (y // cell)) % 2:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=b)
    return im


def hgrad(c0, c1):
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for x in range(W):
        k = x / float(W - 1)
        c = tuple(int(a + (b - a) * k) for a, b in zip(c0, c1))
        d.line([(x, 0), (x, H - 1)], fill=c)
    return im


def page_img(color, n):
    """逐页图：纯色 + n 条很淡的斜纹。

    一开始是在左上角画 n 条白杠标页码，结果预览里那几根白条正好压在文件名上，
    看着像渲染 bug。改成整张图的淡斜纹：既能数出第几页，又不会遮住文字。"""
    im = Image.new("RGB", (W, H), color)
    d = ImageDraw.Draw(im)
    light = tuple(min(255, c + 46) for c in color)
    step = max(9, W // (n + 1))
    for i in range(n):
        x = step // 2 + i * step
        d.line([(x, H), (x + H, 0)], fill=light, width=6)
    return im


def bars_logo():
    """31×7 的 logo：竖条图案，3 个索引色（0 白 / 1 品红 / 2 黑）。"""
    img = bytearray(31 * 7)
    for y in range(7):
        for x in range(31):
            img[y * 31 + x] = 1 if (x // 4) % 2 else 2
    pal = [(255, 255, 255), (255, 0, 160), (0, 0, 0)]
    return bytes(img), pal


def hib_tile():
    """16×16 斜纹：索引 1/2 两色（0 = 自动色）。"""
    t = bytearray(256)
    for y in range(16):
        for x in range(16):
            t[y * 16 + x] = 1 if ((x + y) // 3) % 2 else 2
    return bytes(t), [(255, 255, 0), (0, 0, 0)]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "dist", "fonttest", "主题包-测试", "theme.bin")

    doc = E.Doc()

    # ---- 六套配色（FG 顶栏/底栏/边框, BG 内容底, FT 文字, HI 按钮/高亮, HIB 选中条, SH 阴影）
    pals = [
        ((0, 173, 173), (255, 255, 255), (0, 0, 0), (206, 206, 206), (156, 156, 189), (198, 140, 140)),
        ((239, 165, 82), (252, 246, 236), (40, 30, 20), (214, 200, 178), (150, 120, 80), (180, 150, 110)),
        ((38, 80, 140), (28, 32, 40), (240, 240, 240), (70, 90, 120), (90, 130, 190), (60, 70, 90)),
        ((60, 160, 80), (232, 244, 232), (20, 40, 20), (190, 220, 190), (120, 170, 120), (150, 180, 150)),
        ((200, 40, 140), (255, 250, 252), (30, 10, 25), (230, 190, 215), (170, 90, 140), (200, 150, 180)),
        ((120, 120, 120), (24, 24, 24), (235, 235, 235), (70, 70, 70), (150, 150, 150), (90, 90, 90)),
    ]
    for i, p in enumerate(pals):
        doc.pals[i] = [tuple(c) for c in p]   # Doc 里存的是 RGB 三元组，打包时再转 555

    # ---- 整套图：1 有斜纹、2 无（纯色）、3 棋盘、4 渐变、5 无（改用逐页图）、6 无
    doc.wall_rgb[0] = stripes((0, 173, 173), (0, 130, 130))
    doc.wall_rgb[2] = checker((30, 30, 30), (200, 200, 200))
    doc.wall_rgb[3] = hgrad((0, 180, 200), (250, 220, 60))

    # ---- 逐页图：给第 5 套的 7 页各一张（纯色 + 页码条纹）
    page_colors = [(220, 60, 60), (230, 150, 40), (220, 210, 60), (70, 180, 80),
                   (60, 150, 220), (120, 90, 200), (180, 80, 160)]
    for pg in range(S.PAGE_COUNT):
        doc.sp_rgb[4][pg] = page_img(page_colors[pg], pg + 1)

    # ---- 扩展色（v5）：载入底 / 载入字 / 游戏内菜单底
    for i in range(6):
        doc.excol[i] = [(255, 0, 200), (0, 255, 220), (0, 90, 40)]

    # ---- logo（v6）：自定义竖条图案
    doc.logo_img, doc.logo_pal = bars_logo()

    # ---- 透明度（v7）：全部写 0。2026-10-24 起固件不再按它开 OBJ 混合
    # （开了会把图标一起弄成半透明），选中条永远画竖条，这一项已经没有可见效果。
    doc.alpha = [[0, 0] for _ in range(6)]

    # ---- v8 纹理：给第 1 套塞一块斜纹（新固件已不渲染，正好用来验证它确实被忽略）
    tile, cols = hib_tile()
    doc.set_hib(0, tile, cols)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    data = doc.save(out)
    print("写出 %s（%d 字节，v%s）" % (out, len(data), data[8]))
    return out


if __name__ == "__main__":
    main()
