# -*- coding: utf-8 -*-
"""验证「点缩略图选保留区域」：点哪里，那里就是保留区的中心。

要点：缩略图里原图是等比塞进去的（有黑边），所以坐标要先过一遍反算，
不能直接拿画布坐标当比例用。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image                     # noqa: E402
import wallpaper as WP                     # noqa: E402

import tkinter as tk                       # noqa: E402
import main as M                           # noqa: E402


class Ev(object):
    def __init__(self, x, y):
        self.x, self.y = x, y


def main():
    root = tk.Tk()
    root.geometry("1280x920+20+20")
    app = M.App(root)
    app.wall_src = Image.new("RGB", (1920, 1080))
    app.refresh()
    root.update()                           # 让画布拿到真实尺寸

    cw = app.wall_thumb.winfo_width()
    ch = app.wall_thumb.winfo_height()
    sw, sh = app.wall_src.size
    k = min(cw / float(sw), ch / float(sh))
    dw, dh = int(round(sw * k)), int(round(sh * k))
    ox, oy = (cw - dw) // 2, (ch - dh) // 2
    print("缩略图 %dx%d，原图 1920x1080 缩成 %dx%d，偏移 (%d,%d)"
          % (cw, ch, dw, dh, ox, oy))

    bad = 0
    # 在原图上挑几个点，换算成画布坐标点下去，看保留区中心是不是它
    for want in ((120, 540), (960, 540), (1800, 540),
                 (960, 200), (960, 900)):
        cx = ox + want[0] * k
        cy = oy + want[1] * k
        app.on_thumb_click(Ev(cx, cy))
        m = WP.crop_metrics(app.wall_src.size, app.wall_anchor)
        l, t, r, b = m["win"]
        ww, wh = r - l, b - t
        ccx, ccy = (l + r) / 2.0, (t + b) / 2.0
        # 期望值：尽量把点击点放到保留区中心，靠边时被边界卡住
        ex = min(max(want[0], ww / 2.0), sw - ww / 2.0)
        ey = min(max(want[1], wh / 2.0), sh - wh / 2.0)
        ok = abs(ccx - ex) <= 1.0 and abs(ccy - ey) <= 1.0
        bad += 0 if ok else 1
        print("  点原图 (%4d,%4d) → 保留区中心 (%6.1f,%6.1f)  期望 (%6.1f,%6.1f)  %s"
              % (want[0], want[1], ccx, ccy, ex, ey, "通过" if ok else "不对"))

    root.destroy()
    print("\n结论: %s" % ("通过" if bad == 0 else "%d 个点不准" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
