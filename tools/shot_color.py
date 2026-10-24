# -*- coding: utf-8 -*-
"""打开滑块调色框并抓图（PrintWindow，不怕被挡）。

用法: python tools/shot_color.py <输出png> [R] [G] [B]
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from shot_crop import grab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TITLE = "选择颜色"

SW = [(173, 173, 173), (255, 255, 255), (0, 0, 0), (206, 206, 206),
      (156, 156, 189), (198, 140, 140)]

RUNNER = r'''
import sys, os
sys.path.insert(0, os.path.join(%r, "app"))
import tkinter as tk
import sdedit
root = tk.Tk()
root.geometry("320x220+20+20")      # master 必须可见，否则 transient 窗口不显示
sdedit.ColorDialog(root, (%d, %d, %d), title=%r, hint=%r, swatches=%r)
root.destroy()
'''


def main():
    out = sys.argv[1]
    rgb = tuple(int(v) for v in sys.argv[2:5]) if len(sys.argv) >= 5 else (173, 173, 173)
    hint = "影响：顶栏、底栏、弹窗边框、载入进度条"
    p = subprocess.Popen([PY, "-c", RUNNER % (ROOT, rgb[0], rgb[1], rgb[2], TITLE, hint, SW)])
    u = ctypes.windll.user32
    hwnd = None
    for _ in range(80):
        time.sleep(0.25)
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, _l):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if TITLE in b.value and u.IsWindowVisible(h):
                    r = wt.RECT()
                    u.GetWindowRect(h, ctypes.byref(r))
                    if r.right - r.left > 300:
                        found.append(h)
            return True

        u.EnumWindows(cb, 0)
        if found:
            hwnd = found[0]
            break
    if not hwnd:
        p.terminate()
        raise SystemExit("找不到窗口")
    time.sleep(0.8)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    ok, size = grab(hwnd, out)
    print("PrintWindow ok=%s size=%s -> %s" % (ok, size, out))
    p.terminate()


if __name__ == "__main__":
    main()
