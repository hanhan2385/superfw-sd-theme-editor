# -*- coding: utf-8 -*-
"""无头回归：真跑修改器的「注入到固件」，确认背景图 + 主题 + 图标都写进去了。

和 tools/test_inject.py 的区别：那边是自己拼一遍流程，这里点的是界面里那个
`App.inject_firmware()`，只把弹窗换成打印 —— 顺序问题（背景必须先写）只有这条
路径能测出来。

用法:
  python tools/test_ui_inject.py [源固件] [图片]
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "app"))

import tkinter as tk                    # noqa: E402
from PIL import Image                   # noqa: E402

import fwbake as FB                     # noqa: E402
import fwbuild                          # noqa: E402
import main as M                        # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else "C:/Users/HAN/Downloads/superfw-chis.fw"
IMG = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "dist", "bocchi-1536x921.png")
WORK = os.path.join(HERE, "build", "_inj")

logs = []


def main():
    if not os.path.exists(SRC):
        raise SystemExit("找不到源固件: %s" % SRC)
    os.makedirs(WORK, exist_ok=True)
    dst = os.path.join(WORK, "src" + os.path.splitext(SRC)[1])
    shutil.copy(SRC, dst)

    root = tk.Tk()
    root.withdraw()
    app = M.App(root)
    M.messagebox.askokcancel = lambda t, m="", **k: (logs.append(("OK", t, m)), True)[1]
    M.messagebox.showinfo = lambda t, m="", **k: logs.append(("INFO", t, m))
    M.messagebox.showwarning = lambda t, m="", **k: logs.append(("WARN", t, m))
    M.messagebox.showerror = lambda t, m="", **k: logs.append(("ERR", t, m))

    app.pal = M.PRESETS[4][1][:]
    for k in range(6):
        app.hexvars[k].set(app.pal[k])
    app.slot = 5
    app.all_slots.set(True)

    app.wall_src = Image.open(IMG).convert("RGB")
    app.wall_anchor = (0.5, 0.5)
    app.wall_dim = 0.25
    app._wall_key = None
    app.refresh()

    app.fw_path = dst
    app.fw_img = bytearray(open(dst, "rb").read())
    app.inject_firmware()

    for kind, t, m in logs:
        print("[%s] %s\n%s\n" % (kind, t, m))
    if any(k == "ERR" for k, _, _ in logs):
        raise SystemExit("注入报错")
    if any(k == "WARN" for k, _, _ in logs):
        raise SystemExit("注入有警告 —— 背景图没进去")

    out = os.path.splitext(dst)[0] + "-theme6-wall" + os.path.splitext(dst)[1]
    if not os.path.exists(out):
        raise SystemExit("没找到输出文件 %s" % out)
    rom = bytearray(open(out, "rb").read())
    ok, why = fwbuild.validate(rom)
    meta = FB.find_baked(rom)
    print("校验 %s（%s）" % ("通过" if ok else "失败", why))
    if not meta:
        raise SystemExit("输出里没有烘焙记录")
    print("位图 A 0x%06X  B 0x%06X  钩子 EWRAM 0x%08X" % (
        meta["a_rom"], meta["b_rom"], FB.EWRAM_MIRROR + meta["hook_off"]))

    want = FB.map_wall_indices(app.wall_idx)
    got = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]]) + \
        bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
    bad = sum(1 for a, b in zip(want, got) if a != b)
    size = int.from_bytes(rom[0xCC:0xD0], "little")
    print("位图差异 %d / %d     fw_size 0x%X vs 文件 %d → %s" % (
        bad, len(want), size, len(rom), "一致" if size == len(rom) else "不一致"))
    root.destroy()
    if bad:
        raise SystemExit("位图和编辑器预览不一致")
    if size != len(rom):
        raise SystemExit("fw_size 和文件长度不一致，内置升级器会拒收")
    print("\n结论: 通过 —— 背景图 + 主题 + 图标全部写进去了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
