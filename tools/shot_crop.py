# -*- coding: utf-8 -*-
"""打开裁剪对话框并抓图（PrintWindow，不怕被挡）。

用法: python tools/shot_crop.py <图片> <输出png>
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TITLE = "调整壁纸裁剪"

RUNNER = r'''
import sys, os
sys.path.insert(0, os.path.join(%r, "app"))
import tkinter as tk
from PIL import Image
import sdedit
root = tk.Tk()
root.geometry("320x220+20+20")      # master 必须可见：transient 窗口藏在隐藏的 master 后面就不显示
im = Image.open(%r)
sdedit.CropDialog(root, im)
root.destroy()
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
    ok = u.PrintWindow(hwnd, mdc, 2)

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
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out)
    u.ReleaseDC(hwnd, hdc)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    return ok, (w, h)


def main():
    src, out = sys.argv[1], sys.argv[2]
    p = subprocess.Popen([PY, "-c", RUNNER % (ROOT, src)])
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
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ok, size = grab(hwnd, out)
    print("PrintWindow ok=%s size=%s -> %s" % (ok, size, out))
    p.terminate()


if __name__ == "__main__":
    main()
