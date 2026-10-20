# -*- coding: utf-8 -*-
"""固件体检 —— 确认一个固件文件里的背景图到底是什么状态（CLI）。

实现都在 app/fwcheck.py；这里只做命令行包装。

用法：
  python tools/check_firmware.py 固件文件            # 看状态 + 解出当前背景图
  python tools/check_firmware.py 固件文件 -o 目录     # 背景图存到指定目录

刷机前跑一下「你准备刷的那个文件」，再刷完用读卡器把卡带里的文件读出来跑一遍，
两边一对比就知道是文件的问题还是刷写的问题。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("firmware")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--name", default=None, help="输出 PNG 的文件名前缀")
    args = ap.parse_args()

    import fwcheck

    rom = open(args.firmware, "rb").read()
    rep = fwcheck.check(rom, args.firmware)

    print("=" * 64)
    print("文件        : %s（%s 字节）" % (args.firmware, format(len(rom), ",")))
    for ln, bad in rep["lines"]:
        print(("  ✗ " if bad else "  · ") + ln)

    if rep["wall"] is not None:
        outdir = args.outdir or os.path.dirname(os.path.abspath(args.firmware))
        os.makedirs(outdir, exist_ok=True)
        stem = args.name or os.path.splitext(os.path.basename(args.firmware))[0]
        out = os.path.join(outdir, "%s-背景图.png" % stem)
        rep["wall"].save(out)
        print("  里面那张图: %s" % out)

    print("-" * 64)
    if rep["problems"]:
        print("发现 %d 个问题：" % len(rep["problems"]))
        for p in rep["problems"]:
            print("  ✗ " + p)
        return 1
    print("没发现结构性问题。如果实机还是没换图，对比一下：")
    print("  ① 你刷的是不是就是这个文件（看文件名和修改时间）")
    print("  ② 刷完后用读卡器把卡带里的固件读出来，对这个文件再跑一遍本脚本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
