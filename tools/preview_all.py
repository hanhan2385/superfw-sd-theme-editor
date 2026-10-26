#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次导出编辑器里所有界面的预览图，用来和实机逐页比对。

    python tools/preview_all.py                      # 用内置新主题
    python tools/preview_all.py dist/fonttest/theme.bin
    python tools/preview_all.py -o out/preview/mine -k 4

输出：
    <out>/page0..6.png   主菜单 7 页（最近/浏览/NOR/设置/界面/工具/关于）
    <out>/extra-*.png    弹窗与其它界面（游戏内菜单、载入信息、补丁选项、载入选项…）

图片是 240×160 的整数倍放大（默认 3 倍），方便直接看像素。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image                                     # noqa: E402

import sdedit as E                                        # noqa: E402

PAGES = [
    (0, "最近游戏"),
    (1, "文件浏览"),
    (2, "NOR 烧录"),
    (3, "全局设置"),
    (4, "界面设置"),
    (5, "工具"),
    (6, "关于"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("theme", nargs="?", default=None, help="theme.bin；不给就用新建的空主题")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("-k", "--scale", type=int, default=3)
    args = ap.parse_args()

    doc = E.Doc.load(args.theme) if args.theme else E.Doc()
    out = args.out or os.path.join(ROOT, "out", "preview", "v%s" % E.APP_VER)
    os.makedirs(out, exist_ok=True)
    K = max(1, args.scale)

    n = 0
    for pg, name in PAGES:
        im = E.render_preview(doc, 0, page=pg)
        path = os.path.join(out, "page%d.png" % pg)
        im.resize((im.width * K, im.height * K), Image.NEAREST).save(path)
        print("  page%d  %-8s → %s" % (pg, name, os.path.relpath(path, ROOT)))
        n += 1

    for key, label in E.EXTRA_SCREENS:
        im = E.render_preview(doc, 0, page=1, extra=key)
        path = os.path.join(out, "extra-%s.png" % key)
        im.resize((im.width * K, im.height * K), Image.NEAREST).save(path)
        print("  extra   %-8s → %s" % (label, os.path.relpath(path, ROOT)))
        n += 1

    print("共 %d 张，输出目录 %s" % (n, os.path.relpath(out, ROOT)))


if __name__ == "__main__":
    main()
