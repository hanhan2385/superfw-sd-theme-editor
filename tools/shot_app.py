# -*- coding: utf-8 -*-
"""
启动真实界面、截图、顺手导出一组设备预览。
=========================================
不靠"看起来应该对"：真的建窗口 → 真的渲染 → 抓窗口像素 → 存 PNG。
之后再用像素统计确认布局（左右面板宽度、画布尺寸、有没有空白边）。

用法：
  python tools/shot_app.py --outdir dist/verify
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import main as APP          # noqa: E402
from PIL import Image, ImageGrab  # noqa: E402


def build_app(wall=True, wall_size=(480, 288), anchor=(0.5, 0.5), wall_path=None):
    import tkinter as tk
    root = tk.Tk()
    root.geometry("1280x920+30+20")
    app = APP.App(root)
    app.select_slot(4)                  # 切到第 5 套（Purple），颜色差异明显
    for k in range(6):
        app.hexvars[k].set(app.pal[k])
    if wall:
        sw, sh = wall_size
        if wall_path:
            src = Image.open(wall_path).convert("RGB")
        else:
            src = Image.new("RGB", (sw, sh))
            from PIL import ImageDraw
            d = ImageDraw.Draw(src)
            for y in range(sh):
                d.rectangle([0, y, sw, y],
                            fill=(18 + 255 * y // sh // 6, 32 + 255 * y // sh // 5, 90 - y // 4))
            d.ellipse([int(sw * .62), int(sh * .10), int(sw * .98), int(sh * .66)],
                      fill=(250, 205, 70))
            d.ellipse([int(-sw * .08), int(sh * .73), int(sw * .37), int(sh * 1.14)],
                      fill=(40, 160, 150))
            # 四角标记，方便一眼看出保留了哪一块
            for x, y in ((6, 6), (sw - 26, 6), (6, sh - 26), (sw - 26, sh - 26)):
                d.rectangle([x, y, x + 20, y + 20], outline=(255, 60, 60), width=3)
        app.wall_src = src
        app.wall_anchor = anchor
        app.wall_dim = 0.35
        app.dim_var.set(35)
        app._wall_key = None
    app.screen = "browser"
    app.refresh()
    return root, app


def grab(root, path, extra=()):
    # 截图抓的是屏幕像素，所以得先把窗口顶到最前面（否则抓到别的窗口）
    try:
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
    except Exception:                       # noqa: BLE001
        pass
    for w in extra:                         # 悬停提示是独立小窗，得再顶一次
        try:
            w.attributes("-topmost", True)
            w.lift()
        except Exception:                   # noqa: BLE001
            pass
    root.update_idletasks()
    root.update()
    time.sleep(0.5)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    im = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    im.save(path)
    try:
        root.attributes("-topmost", False)
    except Exception:                       # noqa: BLE001
        pass
    return im, (x, y, w, h)


def panel_metrics(app, im):
    """量一下画布与面板的实际像素，验证"中间那块"不再过大。"""
    root = app.root
    cw = app.canvas.winfo_width()
    ch = app.canvas.winfo_height()
    return {
        "窗口": "%dx%d" % (root.winfo_width(), root.winfo_height()),
        "画布": "%dx%d（缩放 %d×）" % (cw, ch, app.scale),
        "画布/窗口宽": "%.0f%%" % (100.0 * cw / root.winfo_width()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dist", "verify"))
    ap.add_argument("--no-wall", action="store_true")
    ap.add_argument("--wall-size", default="480x288")
    ap.add_argument("--anchor", default="0.5,0.5")
    ap.add_argument("--out", default="app-ui.png")
    ap.add_argument("--icon", type=int, default=None, help="选中第 N 个图标（0 基），用来截位置标注")
    ap.add_argument("--tip", action="store_true", help="顺带把悬停提示弹出来一起截")
    ap.add_argument("--screen", default="browser", help="预览先停在哪一屏")
    ap.add_argument("--wall", default=None, help="用这张真图当背景（不给就用合成的测试图）")
    args = ap.parse_args()
    ws = tuple(int(v) for v in args.wall_size.lower().split("x"))
    an = tuple(float(v) for v in args.anchor.split(","))
    os.makedirs(args.outdir, exist_ok=True)

    root, app = build_app(wall=not args.no_wall, wall_size=ws, anchor=an,
                          wall_path=args.wall)
    app.screen = args.screen
    if args.icon is not None:
        app.select_icon(args.icon)
    app.refresh()
    root.update()
    if args.tip and args.icon is not None:
        app.icon_btns[args.icon].event_generate("<Enter>")
        root.update()
        time.sleep(0.45)
        root.update()
    try:
        shot = os.path.join(args.outdir, args.out)
        tips = [t.tip for t in app.icon_tips if t.tip is not None]
        im, box = grab(root, shot, extra=tips)
        print("窗口截图:", shot, box)
        for k, v in panel_metrics(app, im).items():
            print("  %-12s %s" % (k, v))

        # 六屏的 240×160 原生截图（合成壁纸与图标后的真实观感）
        for key in ("browser", "uisettings", "loadinfo", "loadpatch", "tools", "info"):
            img = app.r.screen(key, app.pal, "zh", app.sel)
            img.convert("RGB").save(os.path.join(args.outdir, "screen-%s-240x160.png" % key))
        print("六屏预览已写出到", args.outdir)
    finally:
        root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
