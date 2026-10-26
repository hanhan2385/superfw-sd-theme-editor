# -*- coding: utf-8 -*-
"""用 PrintWindow(PW_RENDERFULLCONTENT) 抓某个窗口的完整内容，不怕被别的窗口挡住。

shot_exe.py 用 ImageGrab 抓屏幕区域，桌面上开着微信/浏览器时会被挡；
这个走 PrintWindow，抓的是窗口自己画的内容，遮挡也没关系。

用法: python tools/shot_exe_print.py <exe> <输出png> [标题关键字]
"""
import ctypes, ctypes.wintypes as wt, sys, time
from PIL import Image

exe, out, title = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "SuperFW")
import subprocess
p = subprocess.Popen([exe])
u = ctypes.windll.user32
g = ctypes.windll.gdi32

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
            if title in b.value and u.IsWindowVisible(h):
                found.append(h)
        return True
    u.EnumWindows(cb, 0)
    if found:
        hwnd = found[0]; break
if not hwnd:
    raise SystemExit("找不到窗口")
time.sleep(1.2)
r = wt.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
w, h = r.right - r.left, r.bottom - r.top
hdc = u.GetWindowDC(hwnd)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, h)
g.SelectObject(mdc, bmp)
ok = u.PrintWindow(hwnd, mdc, 2)          # 2 = PW_RENDERFULLCONTENT
buf = ctypes.create_string_buffer(w * h * 4)
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]
bi = BITMAPINFOHEADER()
bi.biSize = ctypes.sizeof(BITMAPINFOHEADER); bi.biWidth = w; bi.biHeight = -h
bi.biPlanes = 1; bi.biBitCount = 32; bi.biCompression = 0
g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
im = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
im.save(out)
print("PrintWindow ok=%s size=%s -> %s" % (ok, (w, h), out))
u.ReleaseDC(hwnd, hdc); g.DeleteObject(bmp); g.DeleteDC(mdc)
time.sleep(0.3)
p.terminate()
