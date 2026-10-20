# -*- coding: utf-8 -*-
"""SD 主题文件自检：格式对不对、内容是不是从源固件里原样搬过来的。

不依赖固件仿真，纯比对：
  - 头部 magic / 版本 / 长度
  - 6 套配色 == 源固件的 themes[]
  - 6 套图标 == 源固件的图标表（每套 4352 精灵 + 76 调色板）
  - 壁纸 == 源固件 A 段 + B 段拼合
  - 壁纸调色板 == 源固件的 220 个半字

用法：python tools/verify_theme_file.py out/demo/theme.bin out/demo/cn-6themes-wall.fw
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B                      # noqa: E402
import fwbuild as F                     # noqa: E402

MAGIC = b"SFWTHM01"
OFF_COLORS = 0x10
OFF_ICONS = 0x58
OFF_WALL = OFF_ICONS + B.THEME_COUNT * B.ICON_STRIDE
OFF_WALLPAL = OFF_WALL + 240 * 144
FILE_SIZE = OFF_WALLPAL + 220 * 2


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "out", "demo", "theme.bin")
    fw = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "out", "demo",
                                                           "cn-6themes-wall.fw")
    d = open(tf, "rb").read()
    rom = bytearray(open(fw, "rb").read())
    m = B.find_baked(rom)
    bad = []

    def chk(ok, msg):
        print(("  OK   " if ok else "  FAIL ") + msg)
        if not ok:
            bad.append(msg)

    print("主题文件 %s（%d 字节）" % (tf, len(d)))
    chk(len(d) == FILE_SIZE, "长度 == %d（实际 %d）" % (FILE_SIZE, len(d)))
    chk(d[:8] == MAGIC, "magic")
    ver, flags, size = struct.unpack_from("<HHI", d, 8)
    chk(ver == 1, "版本 %d" % ver)
    chk(size == FILE_SIZE, "头部记录的长度 %d" % size)
    chk(flags == 1, "标志位 0x%X（bit0 = 有壁纸）" % flags)

    themes = F.Layout._find_themes(bytes(rom))
    n = B.THEME_COUNT * 12
    chk(d[OFF_COLORS:OFF_COLORS + n] == bytes(rom[themes:themes + n]),
        "6 套配色与固件 themes[] 一致（@0x%X）" % themes)

    base = m["icon_flash"] - B.ROM_BASE
    for i in range(B.THEME_COUNT):
        o = base + i * B.ICON_STRIDE
        src = bytes(rom[o:o + B.ICON_STRIDE])
        dst = d[OFF_ICONS + i * B.ICON_STRIDE:OFF_ICONS + (i + 1) * B.ICON_STRIDE]
        chk(src == dst, "第 %d 套图标（精灵 + 调色板）" % (i + 1))

    wall = bytes(rom[m["a_rom"]:m["a_rom"] + m["a_len"]]) + \
           bytes(rom[m["data_b_at"]:m["data_b_at"] + m["b_len"]])
    chk(d[OFF_WALL:OFF_WALL + 240 * 144] == wall, "壁纸 240x144")

    pal = bytes(rom[m["pal_at"]:m["pal_at"] + 440])
    chk(d[OFF_WALLPAL:OFF_WALLPAL + 440] == pal, "壁纸调色板 220 色")

    # 6 套图标必须彼此不同，否则「切主题换图标」没意义
    sets = [d[OFF_ICONS + i * B.ICON_STRIDE + 0:OFF_ICONS + i * B.ICON_STRIDE + B.ICON_BYTES]
            for i in range(B.THEME_COUNT)]
    chk(len(set(sets)) == B.THEME_COUNT, "6 套图标互不相同")

    print("\n%s" % ("全部通过" if not bad else "有 %d 项不通过" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
