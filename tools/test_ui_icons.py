"""自检：编辑器的「6 套主题各带一套图标」端到端流程。

真的建窗口（隐藏）→ 真的改某几套的图标 → 真的注入 → 读回固件逐块比对，
再用 unicorn 跑一遍钩子，确认切主题时 VRAM 里换成了对应那套的图标。

用法: python tools/test_ui_icons.py [固件]
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import tkinter as tk                      # noqa: E402
from PIL import Image                     # noqa: E402

import fwbake as B                        # noqa: E402
import fwbuild as F                       # noqa: E402
import main as APP                        # noqa: E402

FW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "in", "cn.fw")

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def paint_icon(app, color):
    ic = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y in range(16):
        for x in range(16):
            if 2 <= x < 14 and 2 <= y < 14:
                ic.putpixel((x, y), color + (255,))
    app.icons[0] = ic
    app.modified.add(0)
    app.refresh()


def main():
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)

    print("[1] 6 套图标初始互相独立")
    check(len(app.slot_icons) == 6, "6 套图标缓存")
    check(len(app.slot_icons[0]) == 17, "每套 17 个图标")
    app.select_slot(1)
    paint_icon(app, (255, 0, 0))
    reloaded = app.slot_icons[1][0].getpixel((4, 4))
    check(reloaded[0] > 200 and reloaded[1] < 60, "第 2 套的图标改成红块")
    app.select_slot(0)
    check(app.slot_icons[0][0].getpixel((4, 4)) != reloaded, "第 1 套没被带走")

    print("[2] 铺到 6 套")
    app.copy_icons_to_all()
    check(all(app.slot_icons[s][0].getpixel((4, 4)) == app.slot_icons[0][0].getpixel((4, 4))
              for s in range(6)), "6 套图标一致")

    print("[3] 端到端注入：壁纸 + 6 套各不同的一套图标")
    # 先恢复成各自不同：第 1 套原样、第 3 套红、第 5 套绿
    app.select_slot(0)
    app.icons[0] = app.originals[0].copy()
    app.modified.clear()
    app.refresh()
    app.select_slot(2)
    paint_icon(app, (255, 0, 0))
    app.select_slot(4)
    paint_icon(app, (0, 200, 60))
    app.select_slot(0)

    wall = Image.new("RGB", (320, 200))
    px = wall.load()
    for y in range(200):
        for x in range(320):
            px[x, y] = (x * 255 // 319, y * 255 // 199, (x * 3 + y * 5) % 256)
    app.wall_src = wall
    app._wall_key = None
    app.refresh()

    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, os.path.basename(FW))
    shutil.copyfile(FW, dst)

    APP.messagebox.askokcancel = lambda *a, **k: True
    APP.messagebox.showwarning = lambda *a, **k: print("    警告:", a[:2])
    APP.messagebox.showerror = lambda *a, **k: print("    报错:", a)
    app._show_inject_result = lambda *a, **k: None

    app.fw_path = dst
    app.fw_img = bytearray(open(dst, "rb").read())
    app.all_slots.set(True)
    app.inject_firmware()

    outs = [f for f in os.listdir(tmp) if "-6themes-wall" in f]
    check(len(outs) == 1, "输出文件名带 -6themes-wall（%s）" % (outs or "无"))
    if not outs:
        root.destroy()
        shutil.rmtree(tmp, ignore_errors=True)
        print("\n结论: 有 %d 项不对" % len(fails))
        return 1

    out = os.path.join(tmp, outs[0])
    rom = bytearray(open(out, "rb").read())
    meta = B.find_baked(rom)
    check(bool(meta) and meta["ver"] == 5, "烘焙头是 v5（%s）" % (meta and meta["ver"]))
    check(bool(meta and meta["icon_flash"]), "烘焙头记了 6 套图标表")
    ok, why = F.validate(rom)
    check(ok, "重新签名后校验通过（%s）" % why)

    if meta and meta["icon_flash"]:
        base = meta["icon_flash"] - B.ROM_BASE
        stride = meta["icon_stride"]

        def blk(n):
            o = base + n * stride
            return bytes(rom[o:o + B.ICON_BYTES]), bytes(
                rom[o + B.ICON_BYTES:o + B.ICON_BYTES + B.ICON_PAL_BYTES])

        s1, p1 = blk(0)
        s3, p3 = blk(2)
        s5, p5 = blk(4)
        check(s1 != s3, "第 1 套和第 3 套的图标块不同")
        check(s3 != s5, "第 3 套和第 5 套的图标块不同")
        check(p3 != p1, "第 3 套的图标调色板也换了")
        check(blk(1)[0] == s1, "第 2 套（没改）与第 1 套一致")

        import test_theme_icons as T
        print("[4] unicorn 跑钩子：切 6 套看图标是否跟着换")
        sys.argv = ["test_theme_icons.py", out]
        r = T.main()
        check(r == 0, "钩子自检通过")

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n结论: %s" % ("全部通过" if not fails else "有 %d 项不对" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
