"""启动 mGBA 加载指定 ROM，把窗口置顶后按窗口区域截图。

不挂 GDB 调试口 —— 调试口会打断 DMA，测出来的结果不作数。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import ImageGrab

MGBA = r"C:\Users\HAN\Downloads\mGBA-0.10.5-win32\mGBA.exe"
user32 = ctypes.windll.user32

# 高分屏下必须声明 DPI 感知，否则 GetWindowRect 拿到的是逻辑坐标，
# 和 ImageGrab 的物理像素对不上，截出来是别的窗口
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:                                   # noqa: BLE001
    try:
        user32.SetProcessDPIAware()
    except Exception:                               # noqa: BLE001
        pass

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def windows_of(pid):
    out = []

    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((hwnd, buf.value))
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    return out


def rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def main():
    rom = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "out", "mgba", "shot.png")
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0

    p = subprocess.Popen([MGBA, rom])
    time.sleep(wait)
    wins = windows_of(p.pid)
    if not wins:
        print("没找到 mGBA 窗口")
        p.terminate()
        return 1
    for hwnd, title in wins:
        print("窗口 %r" % title)
    hwnd, title = max(wins, key=lambda kv: (rect(kv[0])[2] - rect(kv[0])[0]))
    # 后台进程调 SetForegroundWindow 常被系统拒绝，用 TOPMOST 保证它真的在最前面
    HWND_TOPMOST = -1
    SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
    user32.ShowWindow(hwnd, 9)              # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.2)
    l, t, r, b = rect(hwnd)
    print("区域 %d,%d %dx%d" % (l, t, r - l, b - t))
    img = ImageGrab.grab(bbox=(l, t, r, b))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print("已存 %s  %s" % (out, img.size))
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
