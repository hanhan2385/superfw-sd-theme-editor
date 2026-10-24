"""自检：编辑器的「6 套主题各写各自配色」这套交互。

不靠肉眼看截图：真的建窗口（隐藏）→ 真的点槽位 / 改色 / 注入 → 读回固件 6 个槽逐字节比对。

用法: python tools/test_ui_slots.py [固件]
"""
import os
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import tkinter as tk                      # noqa: E402

import fwbuild as F                       # noqa: E402
import main as APP                        # noqa: E402
import wallpaper as WP                    # noqa: E402

FW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "in", "cn.fw")

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def theme_table(rom, lay):
    return [list(struct.unpack_from("<6H", rom, lay.themes + s * 12)) for s in range(6)]


def want(pal):
    return [F.rgb2gba(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) for c in pal]


def main():
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)

    print("[1] 初始状态")
    check(app.slot == 0, "当前编辑第 1 套")
    check(len(app.slot_pals) == 6, "一共 6 套缓存")
    check(all(APP.hx(a) == APP.hx(b) for a, b in
              zip(app.slot_pals[0], APP.PRESETS[0][1])), "第 1 套初始 = 预设 White")

    print("[2] 切槽 + 改色只影响当前这套")
    app.select_slot(3)
    check(app.slot == 3 and app.pal == app.slot_pals[3], "切到第 4 套，颜色跟着换")
    app.set_hex(1, "123456")
    check(app.slot_pals[3][1] == "123456", "第 4 套的 BG 改成 123456")
    check(app.pal[1] == "123456", "当前显示的颜色同步")
    check(app.slot_pals[0][1] != "123456", "第 1 套没被带走")
    check(app.slot_pals[4][1] != "123456", "第 5 套没被带走")

    print("[3] 套预设只套当前槽")
    app.apply_preset(1)                    # Orange
    check([APP.hx(c) for c in app.slot_pals[3]] == [APP.hx(c) for c in APP.PRESETS[1][1]],
          "第 4 套变成 Orange")
    check([APP.hx(c) for c in app.slot_pals[0]] == [APP.hx(c) for c in APP.PRESETS[0][1]],
          "第 1 套仍是 White")

    print("[4] 铺到 6 套")
    app.copy_slot_to_all()
    check(all([APP.hx(c) for c in p] == [APP.hx(c) for c in app.pal] for p in app.slot_pals),
          "6 套全等于当前配色")

    print("[5] 端到端注入（6 套各不同）")
    # 每套挑一个不同预设：1 White / 2 Orange / 3 Blue / 4 Green / 5 Purple / 6 Dark
    for i in range(6):
        app.select_slot(i)
        app.apply_preset(i)
    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, os.path.basename(FW))
    shutil.copyfile(FW, dst)

    APP.messagebox.askokcancel = lambda *a, **k: True
    APP.messagebox.showwarning = lambda *a, **k: None
    APP.messagebox.showerror = lambda *a, **k: print("    注入报错:", a)
    app._show_inject_result = lambda *a, **k: None

    app.fw_path = dst
    app.fw_img = bytearray(open(dst, "rb").read())
    app.wall_src = None
    app.all_slots.set(True)
    app.inject_firmware()

    outs = [f for f in os.listdir(tmp) if "-6themes" in f]
    check(len(outs) == 1, "输出文件名带 -6themes（%s）" % (outs or "无"))
    if outs:
        out = os.path.join(tmp, outs[0])
        rom = bytearray(open(out, "rb").read())
        lay = F.detect(rom)
        got = theme_table(rom, lay)
        same_len = len(rom) == os.path.getsize(dst)
        ok, why = F.validate(rom)
        for i in range(6):
            check(got[i] == want(app.slot_pals[i]),
                  "第 %d 套写入正确 %s" % (i + 1, " ".join("%04X" % v for v in got[i])))
        check(same_len, "文件长度不变")
        check(ok, "重新签名后校验通过（%s）" % why)

    print("[6] 只写一套")
    APP.messagebox.askokcancel = lambda *a, **k: True
    app.select_slot(2)
    app.set_hex(1, "FF8000")
    app.all_slots.set(False)
    app.inject_firmware()
    outs2 = [f for f in os.listdir(tmp) if "-theme3" in f]
    check(len(outs2) == 1, "输出文件名带 -theme3（%s）" % (outs2 or "无"))
    if outs2:
        rom2 = bytearray(open(os.path.join(tmp, outs2[0]), "rb").read())
        lay2 = F.detect(rom2)
        base = bytearray(open(dst, "rb").read())
        old = theme_table(base, F.detect(base))
        now = theme_table(rom2, lay2)
        check(now[2] != old[2], "第 3 套被改了")
        check(all(now[i] == old[i] for i in (0, 1, 3, 4, 5)), "其余 5 套原样")

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n结论: %s" % ("全部通过" if not fails else "有 %d 项不对" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
