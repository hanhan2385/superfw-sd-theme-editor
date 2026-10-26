# -*- coding: utf-8 -*-
"""字体覆盖检查：找出源码字符串里「字体包没有字形」的字符。

2026-10-26 的教训：固件里 `⯅⯆⯇⯈`（U+2BC5..2BC8）这四个字符在文泉驿点阵里没有，
实机上渲染成黑色斑块。之前只按截图逐个修（弹窗箭头），漏掉了设置页的热键行和
游戏内菜单 —— 因为没做全量扫描。这个脚本就是那次补的扫描器，以后改字符前先跑。

用法：
    python tools/check_font_coverage.py                 # 扫固件源码（默认两条线）
    python tools/check_font_coverage.py --pack xxx.pack # 指定字体包
    python tools/check_font_coverage.py --add "★☆"      # 额外检查这些字符

退出码：0 = 全部有字形，1 = 有缺字。
"""

import argparse
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

DEFAULT_PACK = os.path.join(ROOT, "ref", "superfw-fix", "res", "fonts-ext.pack")
DEFAULT_SETS = ["ref/superfw-fix", "fw"]

# 韩文用 FLAG_COMP 合成块：pack 里只存 205 个字母，运行时组合音节，
# 所以 U+B0A0..U+D7A3 这一整段都算「有字形」，不能按单个码位判。
HANGUL = (0xAC00, 0xD7A3)

LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def load_charset(pack_path):
    import gbafont
    old = gbafont.PACK_PATH
    gbafont.PACK_PATH = pack_path
    gbafont._font = None
    try:
        have = set(gbafont._parse_pack().keys())
    finally:
        gbafont.PACK_PATH = old
        gbafont._font = None
    return have


def scan_set(root, have, extra=()):
    """返回 {字符: {文件名, ...}}，只统计字符串字面量里的字符。"""
    files = []
    for d in ("src", "res"):
        files += glob.glob(os.path.join(root, d, "**", "*.c"), recursive=True)
        files += glob.glob(os.path.join(root, d, "**", "*.h"), recursive=True)
    bad = {}
    for p in files:
        txt = io.open(p, encoding="utf-8", errors="replace").read()
        for lit in LITERAL.findall(txt):
            for ch in lit:
                cp = ord(ch)
                if cp < 0x80:
                    continue
                if HANGUL[0] <= cp <= HANGUL[1]:
                    continue
                if cp in have or ch in extra:
                    continue
                bad.setdefault(ch, set()).add(os.path.relpath(p, ROOT))
    return bad


def main():
    ap = argparse.ArgumentParser(description="检查固件字体包是否覆盖源码里用到的字符")
    ap.add_argument("--pack", default=DEFAULT_PACK)
    ap.add_argument("--add", default="", help="额外检查的字符（会很直接地告诉你缺不缺）")
    ap.add_argument("--set", action="append", default=None,
                    help="要扫的源码根目录，可重复；默认 ref/superfw-fix 与 fw")
    a = ap.parse_args()

    have = load_charset(a.pack)
    print("字体包：%s" % os.path.relpath(a.pack, ROOT))
    print("字形数：%d" % len(have))
    syms = sorted(c for c in have if 0x2000 <= c < 0x2C00)
    print("符号区（U+2000..U+2BFF）有 %d 个" % len(syms))
    print()

    rc = 0
    if a.add:
        miss = [c for c in a.add if ord(c) not in have]
        print("指定字符：%s" % " ".join(a.add))
        print("  缺：" + (" ".join(miss) if miss else "（无）"))
        rc |= 1 if miss else 0
        print()

    for root in (a.set or DEFAULT_SETS):
        full = os.path.join(ROOT, root)
        if not os.path.isdir(full):
            print("%-22s 跳过（不存在）" % root)
            continue
        bad = scan_set(full, have, a.add)
        if not bad:
            print("%-22s OK —— 字符串里的字符全都有字形" % root)
            continue
        rc = 1
        print("%-22s **缺 %d 个字符**：" % (root, len(bad)))
        for ch, fs in sorted(bad.items(), key=lambda kv: ord(kv[0])):
            print("  U+%04X  %s   ← %s" % (ord(ch), ch, ", ".join(sorted(fs))))
    return rc


if __name__ == "__main__":
    sys.exit(main())
