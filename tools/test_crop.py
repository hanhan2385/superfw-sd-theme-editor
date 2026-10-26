# -*- coding: utf-8 -*-
"""验证「固定 240×144 + 自动裁切」：报出来的裁切框，必须就是实际用的那一块。

判定项：
  1. 输出尺寸恒为 240×144
  2. 裁切框比例恒为 5:3
  3. fit_exact 的结果 == 按 crop_metrics 报的框手工裁再缩放（逐字节相同）
  4. cut_x/cut_y 与框宽高自洽（sw - cut_x == 框宽）
  5. 取景位置：anchor=0 贴左/上，anchor=1 贴右/下
  6. 放大倍数 = 240 / 框宽
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image                     # noqa: E402
import wallpaper as WP                     # noqa: E402

CASES = [
    ((480, 288), (0.5, 0.5), "正好 5:3"),
    ((1920, 1080), (0.5, 0.5), "16:9 宽图居中"),
    ((1920, 1080), (0.0, 0.5), "16:9 看最左"),
    ((1920, 1080), (1.0, 0.5), "16:9 看最右"),
    ((900, 1200), (0.5, 0.35), "竖图偏上"),
    ((900, 1200), (0.5, 1.0), "竖图贴底"),
    ((120, 80), (0.5, 0.5), "比成品还小"),
    ((240, 144), (0.5, 0.5), "等同成品"),
    ((1, 4000), (0.5, 0.5), "极端细长"),
]


def manual(im, m):
    l, t, r, b = m["win"]
    box = (int(round(l)), int(round(t)),
           max(int(round(l)) + 1, int(round(r))),
           max(int(round(t)) + 1, int(round(b))))
    return im.crop(box).resize((WP.W, WP.H), Image.LANCZOS)


def main():
    bad = 0
    for size, anchor, note in CASES:
        src = Image.new("RGB", size)
        px = src.load()
        for y in range(size[1]):
            for x in range(size[0]):
                px[x, y] = (x * 7 % 256, y * 11 % 256, (x + y) % 256)

        out = WP.fit_exact(src, anchor)
        m = WP.crop_metrics(size, anchor)
        l, t, r, b = m["win"]

        c1 = out.size == (WP.W, WP.H)
        ratio = (r - l) / max(1e-6, (b - t))
        c2 = abs(ratio - WP.W / float(WP.H)) < 0.01
        c3 = out.tobytes() == manual(src, m).tobytes()
        c4 = (abs((size[0] - m["cut_x"]) - (r - l)) < 1.01
              and abs((size[1] - m["cut_y"]) - (b - t)) < 1.01)
        c5 = True
        if anchor[0] <= 0.001:
            c5 = c5 and abs(l) < 1.01
        if anchor[0] >= 0.999:
            c5 = c5 and abs((size[0] - r)) < 1.01
        if anchor[1] <= 0.001:
            c5 = c5 and abs(t) < 1.01
        if anchor[1] >= 0.999:
            c5 = c5 and abs((size[1] - b)) < 1.01
        c6 = abs((WP.W / (r - l)) - m["zoom"]) < 0.02

        ok = all((c1, c2, c3, c4, c5, c6))
        bad += 0 if ok else 1
        print("%-11s %-14s 裁左右 %6.0f 裁上下 %6.0f 缩放 %6.2f  %s"
              % ("%dx%d" % size, note, m["cut_x"], m["cut_y"], m["zoom"],
                 "通过" if ok else "失败 尺寸%d 比例%d 一致性%d 自洽%d 贴边%d 倍数%d"
                 % (c1, c2, c3, c4, c5, c6)))

    print("\n结论: %s" % ("全部通过 —— 报出来的裁切量就是实际裁切量"
                         if bad == 0 else "%d 个用例失败" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
