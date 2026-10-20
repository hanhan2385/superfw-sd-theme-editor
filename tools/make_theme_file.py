# -*- coding: utf-8 -*-
"""把一份「已烘焙」的 SuperFW 固件里的皮肤抽出来，打包成 SD 卡主题文件。

产物就是固件在运行时读的 /.superfw/theme.bin：

    0x0000  char[8]   "SFWTHM01"
    0x0008  u16       版本 1
    0x000A  u16       标志（bit0 = 带壁纸）
    0x000C  u32       文件长度
    0x0010  u16[36]   6 套配色（每套 6 个 RGB555，与 menu.c 的 themes[] 同序）
    0x0058  u8[6*4432] 6 套图标（4352 精灵 + 38 项调色板）
    0x6838  u8[34560] 壁纸 240x144 8bpp
    0xEF38  u16[220]  壁纸调色板（写进调色板槽 20..239）

用法：
    python tools/make_theme_file.py out/demo/cn-6themes-wall.fw
    python tools/make_theme_file.py <baked.fw> -o out/demo/theme.bin
"""

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B                      # noqa: E402

MAGIC = b"SFWTHM01"
VERSION = 1

OFF_COLORS = 0x10
OFF_ICONS = 0x58
OFF_WALL = OFF_ICONS + B.THEME_COUNT * B.ICON_STRIDE      # 0x6838
OFF_WALLPAL = OFF_WALL + 240 * 144                        # 0xEF38
FILE_SIZE = OFF_WALLPAL + 220 * 2                         # 0xF0F0


def build(rom):
    """从已烘焙固件里抽出全部皮肤数据。"""
    m = B.find_baked(rom)
    if not m:
        raise ValueError("这个固件里没有 SFWBAKE 头，先烘焙一次再说")

    # 6 套配色：找固件里的 themes[]（6x6 uint16）
    import fwbuild as F
    themes = F.Layout._find_themes(bytes(rom))
    colors = bytes(rom[themes:themes + B.THEME_COUNT * 12])

    # 6 套图标
    if not m.get("icon_count"):
        raise ValueError("这份固件只有一套共用图标，凑不出「6 套各一套」")
    icon_base = m["icon_flash"] - B.ROM_BASE
    stride = m["icon_stride"] or B.ICON_STRIDE
    if stride != B.ICON_STRIDE:
        raise ValueError("图标 stride 是 %d，跟预期 %d 不符" % (stride, B.ICON_STRIDE))
    icons = bytes(rom[icon_base:icon_base + B.THEME_COUNT * stride])
    if len(icons) != B.THEME_COUNT * stride:
        raise ValueError("图标表被截断了")

    # 壁纸：A 段（前 rows 行）+ B 段（剩下）
    rows = m["rows"]
    a = bytes(rom[m["a_rom"]:m["a_rom"] + m["a_len"]])
    b = bytes(rom[m["data_b_at"]:m["data_b_at"] + m["b_len"]])
    wall = a + b
    if len(wall) != 240 * 144:
        raise ValueError("壁纸拼起来是 %d 字节，不是 240x144" % len(wall))

    pal = bytes(rom[m["pal_at"]:m["pal_at"] + 220 * 2])
    if len(pal) != 440:
        raise ValueError("壁纸调色板长度不对")

    out = bytearray(FILE_SIZE)
    out[0:8] = MAGIC
    struct.pack_into("<HHI", out, 8, VERSION, 0x0001, FILE_SIZE)
    out[OFF_COLORS:OFF_COLORS + len(colors)] = colors
    out[OFF_ICONS:OFF_ICONS + len(icons)] = icons
    out[OFF_WALL:OFF_WALL + len(wall)] = wall
    out[OFF_WALLPAL:OFF_WALLPAL + len(pal)] = pal
    return bytes(out), {"themes": themes, "icon_base": icon_base, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fw", help="已经烘焙过（带 6 套图标）的 .fw / .gba")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--no-wall", action="store_true",
                    help="不打包壁纸（文件只有 26KB，只换配色 + 图标）")
    args = ap.parse_args()

    rom = bytearray(open(args.fw, "rb").read())
    data, info = build(rom)
    if args.no_wall:
        data = bytearray(data[:OFF_WALL])
        struct.pack_into("<H", data, 10, 0)          # flags: 没有壁纸
        struct.pack_into("<I", data, 12, len(data))
    out = args.out or os.path.join(os.path.dirname(args.fw), "theme.bin")
    open(out, "wb").write(data)

    print("已生成 %s（%d 字节）" % (out, len(data)))
    print("  配色表 @0x%X  图标表 @0x%X（6 套 x %d）%s"
          % (info["themes"], info["icon_base"], B.ICON_STRIDE,
             "" if args.no_wall else
             "  壁纸 %d 字节（前 %d 行 + 后 %d 行）"
             % (len(data) - OFF_WALL - 440, info["rows"], 144 - info["rows"])))
    print("  放到 SD 卡：/.superfw/theme.bin")


if __name__ == "__main__":
    main()
