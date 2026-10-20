# -*- coding: utf-8 -*-
"""无头冒烟测试：把 App 的界面建出来，选一张背景图，跑通预览 + 注入。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "app"))

import tkinter as tk  # noqa: E402
from PIL import Image  # noqa: E402

import main as M  # noqa: E402

ROM = os.path.join(HERE, "dist", "superfw-chis-r75-胆大不怕死的来6-CN.bin")


def make_wall():
    p = os.path.join(tempfile.gettempdir(), "sfw_wall_src.png")
    im = Image.new("RGB", (480, 288))
    d = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(im)
    for y in range(288):
        d.rectangle([0, y, 480, y], fill=(20 + y // 2, 30 + y // 3, 120 - y // 4))
    d.ellipse([300, 40, 460, 200], fill=(250, 200, 60))
    d.ellipse([40, 150, 180, 270], fill=(20, 220, 180))
    im.save(p)
    return p


def main():
    root = tk.Tk()
    root.withdraw()
    app = M.App(root)
    app.pal = M.PRESETS[4][1][:]          # Purple
    for k in range(6):
        app.hexvars[k].set(app.pal[k])

    app.wall_src = Image.open(make_wall())
    app.wall_src.load()
    app._wall_key = None
    app.refresh()
    print("壁纸状态:", app.wall_state.cget("text"), "| 色数", app.wall_n,
          "| idx 长度", len(app.wall_idx))

    out = os.path.join(HERE, "dist", "app-preview-browser.png")
    app.r.screen("browser", app.pal, "zh", 2).convert("RGB").resize((720, 480), Image.NEAREST).save(out)
    print("浏览器页预览 ->", out)
    out2 = os.path.join(HERE, "dist", "app-preview-about.png")
    app.r.screen("info", app.pal, "zh", 2).convert("RGB").resize((720, 480), Image.NEAREST).save(out2)
    print("关于页预览 ->", out2)

    # 注入
    rom = bytearray(open(ROM, "rb").read())
    off, vals, digest = M.fw_patch_theme(rom, app.pal, slot=5)
    info = M.WP.apply(rom, app.wall_idx, app.wall_pal)
    out3 = os.path.join(HERE, "dist", "app-injected.bin")
    open(out3, "wb").write(rom)
    print("注入:", hex(off), info)
    print("复验:", M.fw_validate(rom))
    root.destroy()


if __name__ == "__main__":
    main()
