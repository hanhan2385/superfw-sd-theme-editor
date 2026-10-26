# -*- coding: utf-8 -*-
"""造一份 v6 演示主题包，直接丢 SD 卡就能看效果。

    python tools/make_sdtheme_v6_demo.py [输出路径]

默认写 dist/v0.30/theme.bin。里面演示的是 v0.30 的全部能力：

  · 6 套深色配色（切主题时整套颜色 + 壁纸一起换）
  · 6 张不同的壁纸（v2 段）
  · 第 4 套的「最近游玩」页单独一张图（v4 掩码，切到别的套就是那套的默认图）
  · 第 1 套的「载入底 / 进度条 / 游戏内菜单底」三个色单独设（v5 段）
  · 自定义 Logo 31×7：黑底 + 白 SDT + 红条（v6 段，关于页和游戏内菜单都换）

图标沿用固件内置那一套（6 套一样），所以文件是 1,951,947 字节 —— v4 定长布局
加 v6 尾巴（36 字节扩展色 + 223 字节 logo）。
"""
import colorsys
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image, ImageDraw, ImageFont     # noqa: E402

import fwassets                                  # noqa: E402
import fwbake                                    # noqa: E402
import sdtheme                                   # noqa: E402
import wallpaper as WP                           # noqa: E402

# 每套 6 个色：FG 顶栏/底栏, BG 内容区, FT 文字, HI 实心按钮, HIB 高亮, SH 置灰
PALETTES = [
    ("#14243F", "#0A1020", "#E8F1FF", "#2A4E86", "#4A86D0", "#7C8AA0"),   # 蔚蓝
    ("#14301F", "#08160E", "#E6FFEE", "#22664A", "#3F9E70", "#7E9C8A"),   # 森绿
    ("#2A1B44", "#120C22", "#F2EAFF", "#5E3E9E", "#8A66D2", "#8F84A6"),   # 葡萄紫
    ("#40210F", "#1A0C06", "#FFEEDD", "#9A4A16", "#D97E2A", "#A88872"),   # 熔岩橙
    ("#3F1219", "#1A0709", "#FFE6EA", "#9E2233", "#D4455E", "#A88289"),   # 玫瑰红
    ("#0F3038", "#05161A", "#E2FAFF", "#1A6472", "#33A0B4", "#74949C"),   # 青碧
]

# 第 1 套的「整屏界面」三色：(载入底, 进度条, 游戏内菜单底)
EXCOL_SET1 = ["#05070F", "#FFD24A", "#101A30"]

LOGO_BG = (10, 12, 20)
LOGO_FG = (240, 242, 248)
LOGO_ACCENT = (218, 42, 48)


def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def make_wall(hue, label, sub, stripes=False):
    """240x144 的渐变壁纸 + 一行 ASCII 标签（内置字体没有中文字形，别写中文）。"""
    w, h = sdtheme.WALL_W, sdtheme.WALL_H
    im = Image.new("RGB", (w, h))
    px = im.load()
    hh = hue / 255.0
    for y in range(h):
        v = 0.18 + 0.62 * y / (h - 1)
        for x in range(w):
            v2 = v
            if stripes and ((x + y) // 14) % 2 == 0:
                v2 = min(1.0, v + 0.18)
            r, g, b = colorsys.hsv_to_rgb(hh, 0.58, v2)
            px[x, y] = (int(r * 255), int(g * 255), int(b * 255))
    d = ImageDraw.Draw(im)
    big = ImageFont.load_default(size=44)
    small = ImageFont.load_default(size=14)
    d.text((12, 26), label, font=big, fill=(255, 255, 255))
    d.text((14, 84), sub, font=small, fill=(255, 255, 255))
    d.rectangle([10, 20, 12 + d.textlength(label, font=big), 22], fill=(255, 255, 255))
    return im


def make_logo():
    """31x7：黑底 + 白 SDT + 红条。三个色正好是固件支持的上限。"""
    im = Image.new("RGB", (sdtheme.LOGO_W, sdtheme.LOGO_H), LOGO_BG)
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=7)
    tx = (sdtheme.LOGO_W - d.textlength("SDT", font=f)) / 2
    d.text((tx, -1), "SDT", font=f, fill=LOGO_FG)
    d.rectangle([0, sdtheme.LOGO_H - 2, 5, sdtheme.LOGO_H - 1], fill=LOGO_ACCENT)
    d.rectangle([sdtheme.LOGO_W - 6, sdtheme.LOGO_H - 2, sdtheme.LOGO_W - 1,
                 sdtheme.LOGO_H - 1], fill=LOGO_ACCENT)
    return im


def wall_bytes(im):
    """图 → (34560 字节索引, 220 个 RGB555)。规矩和编辑器一致：
    量化输出已 +PAL_FIRST，必须过 map_wall_indices / slice_wall_palette 剥掉。"""
    fitted = im.convert("RGB")
    if fitted.size != (sdtheme.WALL_W, sdtheme.WALL_H):
        fitted = WP.fit_image(fitted, mode="cover")
    ind, pal, _used = WP.quantize(fitted, colors=fwbake.PAL_N)
    return fwbake.map_wall_indices(ind), fwbake.slice_wall_palette(pal)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "v0.30", "theme.bin")

    pals = [list(hx(c) for c in row) for row in PALETTES]
    icon_sets = [(bytes(fwassets.ICONS_IMG), list(fwassets.ICONS_PAL))] * sdtheme.THEME_COUNT

    hues = [150, 75, 205, 20, 240, 120]          # PIL HSV 的 H 是 0..255
    walls, wall_pals = [], []
    for i in range(sdtheme.THEME_COUNT):
        idx, pal = wall_bytes(make_wall(hues[i], "SET %d" % (i + 1),
                                        "SuperFW SD theme  v6 demo"))
        walls.append(idx)
        wall_pals.append(pal)
        print("  第 %d 套壁纸 OK（hue %3d）" % (i + 1, hues[i]))

    # 第 4 套的「最近游玩」页换一张斜条纹图：别的套看不到它，切到第 4 套才出现
    sp_idx, sp_pal = wall_bytes(make_wall(300, "SET 4", "RECENT PAGE ONLY", stripes=True))
    set_page_walls = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
    set_page_pals = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
    set_page_walls[3][0] = sp_idx
    set_page_pals[3][0] = sp_pal

    extra = [[None, None, None] for _ in range(sdtheme.THEME_COUNT)]
    extra[0] = [hx(c) for c in EXCOL_SET1]

    logo_idx, logo_pal = __import__("sdedit").logo_from_image(make_logo())

    raw = sdtheme.pack(pals, icon_sets, walls=walls, wall_pals=wall_pals,
                       set_page_walls=set_page_walls, set_page_pals=set_page_pals,
                       extra_cols=extra, logo=(logo_idx, logo_pal))

    r = sdtheme.parse(raw)
    q = lambda c: sdtheme.rgb555(sdtheme.color555(c))     # 写进去再读回来的样子
    assert r["version"] == sdtheme.VERSION_V6, r["version"]
    assert r["base_version"] == sdtheme.VERSION_V4, r["base_version"]
    assert r["logo"][0] == logo_idx, "logo 索引没存对"
    assert r["set_page_mask"] == (1 << sdtheme.slot_index(3, 0)), hex(r["set_page_mask"])
    assert r["extra_cols"][0] == [q(c) for c in EXCOL_SET1], r["extra_cols"][0]
    assert r["extra_cols"][1] == [q(pals[1][1]), q(pals[1][0]), q(pals[1][1])], \
        "没设扩展色的套应回落到 BG/FG"
    assert all(r["walls"][i] is not None for i in range(sdtheme.THEME_COUNT))

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(raw)
    print("\n→ %s" % out)
    print("  %d 字节 / v%d（基础布局 v%d）/ 掩码 0x%012x"
          % (len(raw), r["version"], r["base_version"], r["set_page_mask"]))
    print("  第 1 套整屏色：载入底 %s / 进度条 %s / 游戏内菜单底 %s" % tuple(EXCOL_SET1))
    print("  logo：217 字节索引 + 3 色，与内置不同（请用编辑器「关于页」预览核对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
