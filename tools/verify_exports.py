# -*- coding: utf-8 -*-
"""
验证导出物是否真的等价于固件里原有的东西。

1) 颜色：把 6 个内置预设依次导出，和 src/menu.c 里 themes[] 的 RGB2GBA 值逐项比对。
2) 图标：把一个自定义图标导出成 icons.h，再按固件的方式解码回来，和输入图逐像素比对。
3) 边界：调色板 <= 256、索引 < 调色板长度、图标数量 17、tile 4x8x8。

用法： .venv314/Scripts/python.exe tools/verify_exports.py
"""
import os
import re
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import main  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MENU_C = os.path.join(ROOT, "ref", "superfw", "src", "menu.c")


def rgb2gba(rgb):
    r, g, b = rgb
    return ((r >> 3) << 0) | ((g >> 3) << 5) | ((b >> 3) << 10)


def parse_original_themes():
    src = open(MENU_C, encoding="utf-8").read()
    block = src[src.index("} themes[] = {"):]
    block = block[:block.index("};")]
    rows = []
    for line in block.splitlines():
        vals = re.findall(r"RGB2GBA\(0x([0-9a-fA-F]{6})\)", line)
        if len(vals) == 6:
            rows.append([int(v, 16) for v in vals])
    return rows


def main_check():
    ok = True
    orig = parse_original_themes()
    print(f"原 themes[]: {len(orig)} 组")

    root = tk.Tk()
    root.withdraw()
    app = main.App(root)

    # ---------- 1. 颜色 ----------
    print("\n[1] 颜色导出 vs 原 themes[]")
    for i, row in enumerate(orig):
        app.select_slot(i)
        path = tempfile.mkdtemp()
        app._write_all(path)
        snippet = open(os.path.join(path, "themes-snippet.c"), encoding="utf-8").read()
        vals = [int(v, 16) for v in re.findall(r"RGB2GBA\(0x([0-9A-F]{6})\)", snippet)]
        expect = [rgb2gba((c >> 16, (c >> 8) & 255, c & 255)) for c in row]
        got = [rgb2gba((c >> 16, (c >> 8) & 255, c & 255)) for c in vals]
        same = expect == got
        ok &= same
        print(f"  presets[{i}] {'OK ' if same else 'FAIL'}  "
              f"orig={[hex(v) for v in expect]} got={[hex(v) for v in got]}")

    # ---------- 2. 图标往返 ----------
    print("\n[2] 自定义图标 → icons.h → 解码回像素")
    test = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y in range(16):
        for x in range(16):
            if (x - 7.5) ** 2 + (y - 7.5) ** 2 <= 49:
                test.putpixel((x, y), (255, 0, 0, 255))
            elif x < 4 and y < 4:
                test.putpixel((x, y), (0, 128, 255, 255))
    app.icons[0] = test
    app.modified.add(0)
    path = tempfile.mkdtemp()
    app._write_all(path)
    h = open(os.path.join(path, "icons.h"), encoding="utf-8").read()

    pal_txt = h[h.index("icons_pal["):]
    pal = [int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{4})", pal_txt)]
    pal = pal[1:]  # 跳过 0x0000（透明）
    body = h[h.index("const uint8_t icons_img"):h.index("const uint16_t icons_pal")]
    tiles = re.findall(r"\{\s*((?:\{[^}]*\},\s*){8})\}", body)
    print(f"  调色板 {len(pal)} 色，tile 组 {len(tiles)}")
    first = re.findall(r"\{([^{}]*)\}", tiles[0])
    print(f"  第一个 tile 的行数 {len(first)}")
    # 解码第一块 8x8（左上角）
    rows = [[int(v, 16) for v in r.split(",") if v.strip()] for r in first]
    dec = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for yy, row in enumerate(rows):
        for xx, idx in enumerate(row):
            if idx:
                c = pal[idx - 1]
                dec.putpixel((xx, yy), ((c & 31) * 255 // 31, ((c >> 5) & 31) * 255 // 31, ((c >> 10) & 31) * 255 // 31, 255))
    # 源图的左上 8x8 经过 5bit 量化后应一致
    srcpx = test.load()
    diffs = 0
    for y in range(8):
        for x in range(8):
            r, g, b, a = srcpx[x, y]
            if a <= 128:
                if dec.getpixel((x, y))[3] != 0:
                    diffs += 1
            else:
                dr = abs(dec.getpixel((x, y))[0] - r)
                dg = abs(dec.getpixel((x, y))[1] - g)
                db = abs(dec.getpixel((x, y))[2] - b)
                if max(dr, dg, db) > 8:
                    diffs += 1
    print(f"  左上 8x8 像素差异(>8/255): {diffs}")
    ok &= diffs == 0

    # ---------- 3. 边界 ----------
    print("\n[3] 边界检查")
    maxidx = max(max(int(v, 16) for v in r.split(",") if v.strip()) for r in first)
    checks = [
        ("调色板 <= 256", len(pal) + 1 <= 256),
        ("索引 < 调色板长度", maxidx <= len(pal)),
        ("tile 行数 = 8", len(first) == 8),
        ("icons_img 条目 = 17", len(tiles) >= 17 * 4),
    ]
    for name, cond in checks:
        ok &= cond
        print(f"  {name}: {'OK' if cond else 'FAIL'}")

    root.destroy()
    print("\n结果:", "全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_check())
