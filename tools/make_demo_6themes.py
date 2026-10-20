# -*- coding: utf-8 -*-
"""造一份「6 套主题各带一套配色 + 一套图标」的示例固件，直接刷机就能看效果。

第 N 套 = PRESETS[N-1] 的配色 + 整组图标做 (N-1)×60° 色相旋转，
所以实机在「界面设置 → 主题颜色」里左右切 1..6，屏幕颜色和图标会一起变。

用法:
  python tools/make_demo_6themes.py [固件] [--out 目录]
默认固件 in/cn.fw，输出 out/demo/<名字>-6themes-wall.fw。
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PIL import Image                      # noqa: E402

import main as APP                         # noqa: E402

HUE_STEP = 256 // 6


def hue_shift(im, delta):
    """整张图绕色环转 delta（0..255，对应 0..360°）。alpha 原样保留。"""
    if not delta:
        return im.copy()
    rgb = im.convert("RGB")
    hsv = rgb.convert("HSV")
    h, s, v = hsv.split()
    h = h.point(lambda p: (p + delta) % 256)
    out = Image.merge("HSV", (h, s, v)).convert("RGBA")
    out.putalpha(im.getchannel("A"))
    return out


def make_wall(w, h):
    """一张够花的渐变背景：分上下两段 + 斜条纹，压暗后也不糊成一团。"""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            r = 40 + x * 150 // max(1, w - 1)
            g = 30 + y * 170 // max(1, h - 1)
            b = 90 + ((x + y) * 7 % 120)
            if (x // 24 + y // 24) % 2 == 0:
                r, g, b = r + 20, g + 10, b + 30
            px[x, y] = (min(r, 255), min(g, 255), min(b, 255))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fw", nargs="?", default=os.path.join(ROOT, "in", "cn.fw"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "demo"))
    args = ap.parse_args()

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)

    print("固件: %s" % args.fw)
    slot_names = []

    # ── 6 套配色：直接用 6 个预设，一套一个 ────────────────────
    for s in range(6):
        name, cols = APP.PRESETS[s]
        app.slot_pals[s] = [hx.upper() for hx in cols]
        slot_names.append(name)

    # ── 6 套图标：整套做色相旋转，第 1 套保持原色 ──────────────
    for s in range(6):
        app.select_slot(s)
        delta = HUE_STEP * s
        for k in range(17):                      # 17 = 品牌 Logo，保持原样
            app.icons[k] = hue_shift(app.pristine_icons[k], delta)
        app.modified = set(range(17)) if delta else set()
        app.refresh()
        print("  第 %d 套 %-7s 色相 +%3d°  图标已重上色" % (
            s + 1, slot_names[s], delta * 360 // 256))

    app.select_slot(0)

    if True:
        wall = make_wall(480, 288)
        app.wall_src = wall
        app._wall_key = None
        app.refresh()
        print("  背景图 480×288（自动裁成 240×144）")

    os.makedirs(args.out, exist_ok=True)
    dst = os.path.join(args.out, os.path.basename(args.fw))
    shutil.copyfile(args.fw, dst)

    APP.messagebox.askokcancel = lambda *a, **k: True
    APP.messagebox.showwarning = lambda *a, **k: print("    警告:", a[:2])
    APP.messagebox.showerror = lambda *a, **k: print("    报错:", a[0] if a else "")
    app._show_inject_result = lambda *a, **k: None
    app.fw_path = dst
    app.fw_img = bytearray(open(dst, "rb").read())
    app.all_slots.set(True)
    app.inject_firmware()

    outs = sorted(f for f in os.listdir(args.out) if f.endswith(".fw")
                  and ("-6themes" in f or "-theme" in f))
    try:
        os.remove(dst)                     # 中间那份输入拷贝，别留在产物目录里
    except OSError:
        pass
    print("\n产物：")
    for f in outs:
        p = os.path.join(args.out, f)
        print("  %-34s %.2f MB" % (f, os.path.getsize(p) / 1048576.0))
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
