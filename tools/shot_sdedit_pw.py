# -*- coding: utf-8 -*-
"""启动 SD 主题编辑器（载入指定 theme.bin）并用 PrintWindow 抓窗口。

比 ImageGrab 可靠：桌面上开着别的窗口也不影响，抓的是窗口自己画的内容。

用法: python tools/shot_sdedit_pw.py [--theme 路径] [--out 路径] [--slot N]
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TITLE = "SuperFW SD 主题编辑器"

RUNNER = r'''
import sys, os
sys.path.insert(0, os.path.join(%r, "app"))
import tkinter as tk, sdedit
doc = sdedit.Doc.load(%r)
root = tk.Tk()
app = sdedit.App(root, doc)
app.select_slot(%d)
root.mainloop()
'''


def grab(hwnd, out):
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)          # 2 = PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    im.save(out)
    u.ReleaseDC(hwnd, hdc)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    return ok, (w, h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=os.path.join(ROOT, "dist/v0.17/SD-主题/theme.bin"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "shot-sdedit.png"))
    ap.add_argument("--slot", type=int, default=0)
    a = ap.parse_args()

    code = RUNNER % (ROOT, a.theme, a.slot)
    p = subprocess.Popen([PY, "-c", code])
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
                    if r.right - r.left > 800 and r.bottom - r.top > 500:
                        found.append(h)
            return True

        u.EnumWindows(cb, 0)
        if found:
            hwnd = found[0]
            break
    if not hwnd:
        p.terminate()
        raise SystemExit("找不到窗口")

    time.sleep(1.0)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    ok, size = grab(hwnd, a.out)
    print("PrintWindow ok=%s size=%s -> %s" % (ok, size, a.out))
    p.terminate()


if __name__ == "__main__":
    main()
