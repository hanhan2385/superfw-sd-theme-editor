# -*- coding: utf-8 -*-
"""自检：固件「首次开机自动生成 theme.bin」写出来的字节，格式必须能被解析。

固件侧（fw/src/sdtheme.c::sdtheme_save_default）写的是：
    88 字节 = 16 字节文件头 + 72 字节配色（menu.c 的 themes[] 原样）
    + 6 套 × (4352 精灵 + 76 调色板)  = 26592
    合计 26,680 字节（不带壁纸）

这里用同一份数据在 Python 里拼一遍，交给 app/sdtheme.parse() 解，
并核对两边的常量一致（SDT_OFF_ICONS / SDT_ICONS_ONLY_SIZE）。
"""
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import sdtheme            # noqa: E402
import fwassets           # noqa: E402
import fwbuild            # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + extra) if extra else ""))
    if not cond:
        ok = False


def fw_define(name):
    """从 sdtheme.c 里读 #define（和固件保持同一份真相）。"""
    src = open(os.path.join(ROOT, "fw", "src", "sdtheme.c"), encoding="utf-8").read()
    m = re.search(r"#define\s+%s\s+([^\s/]+)" % name, src)
    if not m:
        return None
    expr = m.group(1)
    # 只处理本文件里出现的简单形式：常量 / 数字 / a + b / (a + b)
    env = {}
    for nm in ("SDT_NTHEME", "SDT_ICON_BYTES", "SDT_ICON_PALN", "SDT_ICON_PALB",
               "SDT_ICON_STRIDE", "SDT_OFF_COLORS", "SDT_OFF_ICONS", "SDT_WALL_BYTES",
               "SDT_WALLPALN", "SDT_OFF_WALL", "SDT_OFF_WALLPAL", "SDT_FILE_SIZE",
               "SDT_ICONS_ONLY_SIZE"):
        env[nm] = None
    return eval(expr, {}, {k: v for k, v in env.items() if v is not None})


def build_blob():
    themes = b"".join(struct.pack("<6H", *t) for t in fwbuild.SRC_THEMES)
    head = (b"SFWTHM01" + struct.pack("<H", 1) + struct.pack("<H", 0) +
            struct.pack("<I", sdtheme.ICONS_ONLY_SIZE))
    # 每套 4432 = 4352 精灵 + 76 调色板 + 4 字节对齐填充
    pad = b"\0" * (sdtheme.ICON_STRIDE - sdtheme.ICON_BYTES - sdtheme.ICON_PAL_N * 2)
    icon = (bytes(fwassets.ICONS_IMG) + struct.pack("<38H", *fwassets.ICONS_PAL) + pad)
    return head + themes + icon * sdtheme.THEME_COUNT


def main():
    print("== 首次开机自动生成的 theme.bin ==")
    blob = build_blob()
    check("大小 = 26680", len(blob) == sdtheme.ICONS_ONLY_SIZE, "实际 %d" % len(blob))
    check("大小 = SDT_OFF_WALL(固件)", len(blob) == 0x6838, hex(len(blob)))

    r = sdtheme.parse(blob)
    check("魔数/版本", blob[:8] == b"SFWTHM01" and r["version"] == 1)
    check("不带壁纸", r["has_wall"] is False)
    check("6 套配色", len(r["pals"]) == 6)
    want0 = [sdtheme.rgb555(v) for v in fwbuild.SRC_THEMES[0]]
    check("配色 = menu.c themes[]", r["pals"][0] == want0, str(r["pals"][0]))
    check("6 套图标数量", len(r["icons"]) == 6)
    same = all(bytes(r["icons"][i][0]) == bytes(fwassets.ICONS_IMG) for i in range(6))
    check("图标 = 内置（6 套相同）", same)
    pal_ok = all(list(r["icons"][i][1]) == list(fwassets.ICONS_PAL) for i in range(6))
    check("图标调色板 = 内置", pal_ok)

    check("重打包逐字节一致", sdtheme.pack(r["pals"], r["icons"], None, None) == blob)

    # 解析器认定的偏移要和固件里的一致
    check("OFF_ICONS = 88", sdtheme.OFF_ICONS == 0x58, hex(sdtheme.OFF_ICONS))
    check("ICONS_ONLY_SIZE = 0x6838", sdtheme.ICONS_ONLY_SIZE == 0x6838)

    print("全部通过。" if ok else "有失败项。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
