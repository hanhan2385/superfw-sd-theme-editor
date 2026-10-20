# -*- coding: utf-8 -*-
"""自检：没放背景图时，6 套图标会自动垫一张纯色底（只为挂钩子），注入后面板恢复原样。

6 套图标靠背景图那条钩子每帧比对 menu_theme 才会换，
所以「只要图标不要壁纸」这种情况必须能走通，而且不能把用户的「还没选图」状态改掉。

用法: python tools/test_auto_wall.py [固件]
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import tkinter as tk                     # noqa: E402
from PIL import Image                    # noqa: E402

import fwbake as B                       # noqa: E402
import fwbuild as F                      # noqa: E402
import main as APP                       # noqa: E402

FW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "in", "cn.fw")
fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def main():
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)
    app.select_slot(2)
    app.icons[0] = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    app.modified.add(0)
    app.refresh()
    check(app.wall_src is None, "一开始没选背景图")

    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, os.path.basename(FW))
    shutil.copyfile(FW, dst)
    warns = []
    APP.messagebox.askokcancel = lambda *a, **k: True
    APP.messagebox.showwarning = lambda *a, **k: warns.append(a[1] if len(a) > 1 else a)
    APP.messagebox.showerror = lambda *a, **k: warns.append(a)
    app._show_inject_result = lambda *a, **k: None
    app.fw_path = dst
    app.fw_img = bytearray(open(dst, "rb").read())
    app.all_slots.set(True)
    app.inject_firmware()

    check(not warns, "没有弹警告（%s）" % (warns or "无"))
    outs = [f for f in os.listdir(tmp) if f.endswith(".fw") and f != os.path.basename(FW)]
    check(len(outs) == 1, "写出了 1 个文件（%s）" % outs)
    if outs:
        rom = bytearray(open(os.path.join(tmp, outs[0]), "rb").read())
        meta = B.find_baked(rom)
        check(bool(meta) and meta.get("ver") == 5, "烘焙头是 v5")
        check(bool(meta and meta.get("icon_count") == 6), "6 套图标都写进去了")
        ok, why = F.validate(rom)
        check(ok, "重新签名后校验通过（%s）" % why)
    check(app.wall_src is None, "注入后面板恢复成「还没选图」")

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n结论: %s" % ("全部通过" if not fails else "有 %d 项不对" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
