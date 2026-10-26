# -*- coding: utf-8 -*-
"""从 theme.bin 里把壁纸（背景图）原样提取成 PNG。

用法:
    python tools/extract_walls.py <theme.bin> [输出目录]
不给输出目录就放在 theme.bin 旁边，目录名 `<文件名>-背景/`。

会导出：
  * 每套的整套壁纸（v1 只有一张共用，v2+ 最多 6 张）
  * 每页的专属页面图（v3+）
  * 每套每页的专属图（v4）
每张同时给一份 4 倍放大（NEAREST，纯像素放大，不插值）方便直接看。

**提取的是原始像素**：240×144、220 色索引，和你当初塞进去的那张一致
（当初如果经过裁剪/缩放，这里拿到的就是裁剪后的结果，不是原始照片）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from PIL import Image

import sdtheme

WALL_SLOT0 = 20        # 壁纸 220 色落在调色板槽 20..239（= PAL_FIRST 4 + 16）


def wall_to_rgb(idx, pal):
    """索引 + 220 色 RGB555 调色板 → RGB 图。

    走 P 模式一次性转换（逐像素 Python 循环要 3.5 万次，慢一个量级）。
    槽 0..19 / 240..255 不该出现，真出现画洋红，便于一眼看出文件有问题。
    """
    lut = [255, 0, 255] * 256
    for k, c in enumerate(pal or []):
        slot = WALL_SLOT0 + k          # 壁纸调色板占 20..239（固件里就是这些槽）
        if slot > 255:
            break
        r, g, b = sdtheme.rgb555(c)
        lut[slot * 3:slot * 3 + 3] = [r, g, b]
    im = Image.frombytes("P", (sdtheme.WALL_W, sdtheme.WALL_H), bytes(idx))
    im.putpalette(lut)
    return im.convert("RGB")


def save(im, outdir, name):
    p = os.path.join(outdir, name + ".png")
    im.save(p)
    big = os.path.join(outdir, name + "-4x.png")
    im.resize((sdtheme.WALL_W * 4, sdtheme.WALL_H * 4), Image.NEAREST).save(big)
    print("  %-40s %d×%d" % (name + ".png", im.width, im.height))
    return p


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = os.path.abspath(sys.argv[1])
    data = open(src, "rb").read()
    t = sdtheme.parse(data)

    if len(sys.argv) > 2:
        outdir = os.path.abspath(sys.argv[2])
    else:
        base, _ = os.path.splitext(os.path.basename(src))
        outdir = os.path.join(os.path.dirname(src), base + "-背景")
    os.makedirs(outdir, exist_ok=True)

    print("文件 %s（%d 字节）" % (os.path.basename(src), len(data)))
    print("版本 v%d（基础布局 v%d）· flags 0x%x" % (t["version"], t["base_version"], t["flags"]))
    print("输出到 %s\n" % outdir)

    n = 0
    print("[整套壁纸]")
    for i, w in enumerate(t["walls"]):
        if w is None:
            print("  第 %d 套：没有壁纸（内容区用 BG 纯色）" % (i + 1))
            continue
        save(wall_to_rgb(w, t["wall_pals"][i]), outdir, "第%d套-整套壁纸" % (i + 1))
        n += 1

    if any(x is not None for x in t["page_walls"]):
        print("\n[全局页面图]")
        for i, w in enumerate(t["page_walls"]):
            if w is None:
                continue
            save(wall_to_rgb(w, t["page_pals"][i]), outdir,
                 "页面-%s" % sdtheme.PAGE_NAMES[i])
            n += 1

    sp = t["set_page_walls"]
    if any(x is not None for row in sp for x in row):
        print("\n[每套每页专属图]")
        for s in range(sdtheme.THEME_COUNT):
            for p in range(sdtheme.PAGE_COUNT):
                if sp[s][p] is None:
                    continue
                save(wall_to_rgb(sp[s][p], t["set_page_pals"][s][p]), outdir,
                     "第%d套-%s页" % (s + 1, sdtheme.PAGE_NAMES[p]))
                n += 1

    if not n:
        print("\n这个文件里一张壁纸都没有（纯配色 + 图标）。")

    print("\n共 %d 张。每张另有一份 -4x 放大版。" % n)
    print("注意：这是 240×144 的原始像素，不是当初那张原图——"
          "当初裁剪/缩放掉的部分这里找不回来。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
