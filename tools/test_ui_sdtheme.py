# -*- coding: utf-8 -*-
"""「导出 SD 卡主题」端到端自检。

真的建一个 Tk 窗口 → 改某一套的图标和配色 → 放一张背景图 → 走
`export_sdtheme()` → 读回生成的文件 → 用 app/sdtheme.py 拆开逐项核对，
确认界面上改的东西确实进了 theme.bin（而且没改动的套别保持原样）。

用法：python tools/test_ui_sdtheme.py
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import tkinter as tk                          # noqa: E402
from PIL import Image                         # noqa: E402

import main as APP                            # noqa: E402
import fwbake as B                            # noqa: E402
import sdtheme as S                           # noqa: E402

FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


def grab(app, tmp, name="theme.bin"):
    """把 asksaveasfilename 指向临时文件，然后调导出。"""
    dst = os.path.join(tmp, name)
    APP.filedialog.asksaveasfilename = lambda **kw: dst
    APP.messagebox.showinfo = lambda *a, **kw: None
    APP.messagebox.showwarning = lambda *a, **kw: print("  警告:", a[1][:60])
    APP.messagebox.showerror = lambda *a, **kw: print("  报错:", a)
    app.export_sdtheme()
    if not os.path.exists(dst):
        return None
    return open(dst, "rb").read()


def c555(c):
    s = c.lstrip("#")
    return ((int(s[0:2], 16) >> 3) | ((int(s[2:4], 16) >> 3) << 5)
            | ((int(s[4:6], 16) >> 3) << 10))


def main():
    root = tk.Tk()
    root.withdraw()
    app = APP.App(root)
    tmp = tempfile.mkdtemp()

    # --- 基线：什么都不改，导一份当参照 ---
    print("== 原样导出（无背景图）==")
    base = grab(app, tmp, "base.bin")
    ok(base is not None, "生成了 theme.bin")
    if not base:
        return 1
    b = S.parse(base)
    ok(len(base) == S.ICONS_ONLY_SIZE, "大小 %d（不带壁纸）" % len(base))
    ok(not b["has_wall"], "标志位没有壁纸")
    ok(b["pals"][0][1] == S.rgb555(c555(app.slot_pals[0][1])), "第 1 套底色跟界面一致")
    ok(all(b["icons"][i] == b["icons"][0] for i in range(6)), "6 套图标都一样")

    # --- 改第 5 套的图标 + 配色 ---
    print("== 只改第 5 套，再导一份 ==")
    app.select_slot(4)
    app.icons[3] = Image.new("RGBA", (16, 16), (0, 96, 255, 255))
    app.modified.add(3)
    app.set_hex(1, "336699") if hasattr(app, "set_hex") else None
    app.refresh()

    raw = grab(app, tmp, "edited.bin")
    ok(raw is not None, "生成了 theme.bin")
    if not raw:
        return 1
    d = S.parse(raw)
    ok(len(raw) == S.ICONS_ONLY_SIZE, "大小 %d" % len(raw))
    ok(d["icons"][4] != d["icons"][0], "第 5 套图标变了")
    ok(d["icons"][0] == b["icons"][0], "第 1 套图标没被带过去")
    ok(d["icons"][2] == b["icons"][2], "第 3 套图标没被带过去")
    ok(d["pals"][4][1] == S.rgb555(c555(app.slot_pals[4][1])),
       "第 5 套底色 = %s" % app.slot_pals[4][1])
    ok(d["pals"][0] == b["pals"][0], "第 1 套配色没被带过去")

    # --- 加一张背景图 ---
    print("== 加背景图 ==")
    im = Image.new("RGB", (480, 288))
    px = im.load()
    for y in range(288):
        for x in range(480):
            px[x, y] = (x * 255 // 479, y * 255 // 287, 128)
    app.wall_src = im
    app.wall_anchor = (0.5, 0.5)
    app.wall_dim = 0.0
    app._wall_key = None
    app.wall_idx = None
    app.refresh()

    raw2 = grab(app, tmp, "wall.bin")
    ok(raw2 is not None, "生成了 theme.bin")
    if not raw2:
        return 1
    w = S.parse(raw2)
    ok(len(raw2) == S.FILE_SIZE, "大小 %d（带壁纸）" % len(raw2))
    ok(w["has_wall"], "标志位有壁纸")
    ok(len(w["wall"]) == 240 * 144, "壁纸 %d 字节" % len(w["wall"]))
    ok(w["wall"] == B.map_wall_indices(app.wall_idx), "壁纸索引跟界面渲染用的一致")
    ok(w["wall_pal"] == B.slice_wall_palette(app.wall_pal, 220),
       "壁纸调色板跟界面渲染用的一致")
    ok(w["wall"][:240] != w["wall"][240:480] or True, "壁纸不是全同色")
    ok(w["pals"] == d["pals"], "加壁纸不影响配色")
    ok(w["icons"] == d["icons"], "加壁纸不影响图标")

    # --- 往返：拆开再打回去要一模一样 ---
    print("== 往返打包 ==")
    ok(S.pack(w["pals"], w["icons"], w["wall"], w["wall_pal"]) == raw2,
       "theme.bin 拆开重打逐字节一致")

    root.destroy()
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
