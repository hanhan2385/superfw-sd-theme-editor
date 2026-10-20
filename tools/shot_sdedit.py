# -*- coding: utf-8 -*-
"""启动 SD 主题编辑器 → 载入一份 theme.bin → 抓窗口截图。

用法： python tools/shot_sdedit.py [--theme 路径] [--out 路径] [--slot 4]
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import tkinter as tk                     # noqa: E402
from PIL import ImageGrab                # noqa: E402

import sdedit                            # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=os.path.join(
        ROOT, "dist/v0.17/SD-主题/theme.bin"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "shot-sdedit.png"))
    ap.add_argument("--slot", type=int, default=4)
    ap.add_argument("--wait", type=float, default=1.2)
    a = ap.parse_args()

    doc = sdedit.Doc.load(a.theme) if os.path.exists(a.theme) else sdedit.Doc()
    root = tk.Tk()
    app = sdedit.App(root, doc)
    app.select_slot(a.slot)
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.6)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    time.sleep(a.wait)
    im = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    im.save(a.out)
    print("saved", a.out, im.size)
    root.destroy()


if __name__ == "__main__":
    main()
