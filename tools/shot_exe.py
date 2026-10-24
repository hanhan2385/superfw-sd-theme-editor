# -*- coding: utf-8 -*-
"""启动打好的 exe，把它的窗口截下来 —— 确认发布出去的界面就是改过的界面。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import ImageGrab

TITLE = "SuperFW"
exe = sys.argv[1]
out = sys.argv[2]
if len(sys.argv) > 3:
    TITLE = sys.argv[3]           # 窗口标题关键字（默认 SuperFW）
extra = sys.argv[4:]              # 额外参数：可以传脚本名/图片路径

p = subprocess.Popen([exe] + extra)
user32 = ctypes.windll.user32
hwnd = None
for _ in range(60):
    time.sleep(0.3)
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        n = user32.GetWindowTextLengthW(h)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            if TITLE in buf.value and user32.IsWindowVisible(h):
                found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    if found:
        hwnd = found[0]
        break

if not hwnd:
    print("没找到窗口")
    p.terminate()
    raise SystemExit(1)

r = wt.RECT()
user32.ShowWindow(hwnd, 9)                  # SW_RESTORE
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
try:
    user32.SetForegroundWindow(hwnd)
except Exception:                            # noqa: BLE001
    pass
time.sleep(1.5)
user32.GetWindowRect(hwnd, ctypes.byref(r))
time.sleep(1.0)
print("窗口矩形:", r.left, r.top, r.right, r.bottom)
im = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
im.save(out)
print("窗口标题:", TITLE, "尺寸:", im.size, "->", out)
p.terminate()
