# -*- coding: utf-8 -*-
"""造一份 v2 演示 theme.bin：6 套各一张壁纸，图上写着大号套号。

刷到卡上 /.superfw/theme.bin 之后，在「界面设置 → 主题」里 1..6 挨个切，
内容区应该依次显示 红/橙/黄/绿/青/蓝 六张写着数字的图 —— 只要有一套显示的
还是别的套的图，就说明串位了。

用法：python tools/make_v2_demo.py [输出路径]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image, ImageDraw  # noqa: E402
import sdedit  # noqa: E402
import sdtheme  # noqa: E402

# 六套的底色（顺序 = 主题 1..6）
COLORS = [(200, 40, 40), (220, 130, 30), (200, 190, 40),
          (50, 160, 70), (40, 160, 200), (70, 80, 200)]


def make_wall(i):
    im = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), COLORS[i])
    d = ImageDraw.Draw(im)
    # 画个粗边框，2 倍缩放后仍一眼能看出换过
    d.rectangle([0, 0, sdtheme.WALL_W - 1, sdtheme.WALL_H - 1],
                outline=(255, 255, 255), width=2)
    txt = str(i + 1)
    # 手绘 7 段式大数字，省得依赖字体文件
    seg = {
        "1": [1, 1, 1, 0, 0, 0, 0], "2": [1, 0, 1, 1, 1, 0, 1],
        "3": [1, 0, 1, 1, 0, 1, 1], "4": [0, 1, 1, 1, 0, 1, 0],
        "5": [1, 1, 0, 1, 0, 1, 1], "6": [1, 1, 0, 1, 1, 1, 1],
    }[txt]
    x0, y0, w, h, t = 96, 40, 48, 80, 10
    bars = [
        (x0, y0, x0 + w, y0 + t),                          # 上
        (x0, y0, x0 + t, y0 + h // 2),                     # 左上
        (x0 + w - t, y0, x0 + w, y0 + h // 2),             # 右上
        (x0, y0 + h // 2 - t // 2, x0 + w, y0 + h // 2 + t // 2),   # 中
        (x0, y0 + h // 2, x0 + t, y0 + h),                 # 左下
        (x0 + w - t, y0 + h // 2, x0 + w, y0 + h),         # 右下
        (x0, y0 + h - t, x0 + w, y0 + h),                  # 下
    ]
    for on, b in zip(seg, bars):
        if on:
            d.rectangle(b, fill=(255, 255, 255))
    return im


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(ROOT, "out", "sdtheme", "theme-6wall.bin")
    doc = sdedit.Doc()
    for i in range(6):
        doc.wall_rgb[i] = make_wall(i)
        doc.wall_dirty[i] = True
    data = doc.to_bytes()
    with open(out, "wb") as fh:
        fh.write(data)

    r = sdtheme.parse(data)
    print("已生成 %s" % out)
    print("  %d 字节，v%d，有壁纸的套 %s"
          % (len(data), r["version"],
             [i + 1 for i in range(6) if r["walls"][i] is not None]))
    back = sdedit.Doc.from_bytes(data)
    for i in range(6):
        px = back.wall_rgb[i].getpixel((10, 100))
        print("  第 %d 套 角落像素 %s（源 %s）" % (i + 1, px, COLORS[i]))
        assert sum(abs(px[c] - COLORS[i][c]) for c in range(3)) <= 24, \
            "第 %d 套串位" % (i + 1)
    print("OK")


if __name__ == "__main__":
    main()
