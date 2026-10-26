# -*- coding: utf-8 -*-
"""背景图一键写进固件 —— 只干这一件事的小工具。

为什么不直接用修改器：修改器要顺手改主题/图标，走的是「探测站点 → 写位图 →
改校验和」一整套；这个工具只做背景图，输入一张图 + 一个固件，输出一个能直接刷的
固件，中间不问你任何参数。

用法（命令行）:
  python bg2fw.py 图片 固件.gba [-o 输出.gba] [--dim 0.30] [--anchor 0.5,0.5]

用法（不带参数，或者把图片拖到 exe 上）:
  弹出一个小窗口，选图片、选固件，点「开始」。

规则（和修改器完全一致，共用 app/ 里的同一套代码）:
  1. 原图 → 铺满 240×144 再按 anchor 取景 → 量化成 220 色 8bpp
  2. 固件里没烘焙过：找两段空白区塞位图 + 在 EWRAM 载荷里装钩子
     固件里已经烘焙过：直接覆盖原来那两段位图（不用再找空位）
  3. 重算头部 sha256，写盘后再读回来逐字节比对
"""

import argparse
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
for p in (APP, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fwbake as FB          # noqa: E402
import fwbuild               # noqa: E402
import wallpaper as WP       # noqa: E402
from PIL import Image        # noqa: E402

OUT_SUFFIX = "-带背景"


# ------------------------------------------------------------------ 核心
def convert(img_path, fw_path, out_path=None, dim=0.0, anchor=(0.5, 0.5)):
    """把图片写进固件，返回一份报告 dict（不弹窗、不打印）。"""
    src = Image.open(img_path).convert("RGB")
    fitted = WP.fit_exact(src, anchor)                 # 保证正好 240×144
    idx0, pal, n = WP.quantize(fitted, dim, (0, 0, 0), FB.PAL_N, True)
    idx = FB.map_wall_indices(idx0)          # 别直接 SLOTS[v]：会整体错 4 格
    pal = FB.slice_wall_palette(pal)

    rom = bytearray(open(fw_path, "rb").read())
    ok, why = fwbuild.validate(rom)
    if not ok:
        raise ValueError("这个文件不是完整固件：%s" % why)
    site = fwbuild.detect(rom)
    already = FB.find_baked(rom)

    if already:
        info = FB.rebake(rom, indexed=idx, palette=pal)
        mode = "replace"
    else:
        info = FB.bake(rom, None, indexed=idx, palette=pal, site=site)
        mode = "bake"
    rom = info["rom"]
    fwbuild.reseal(rom)

    if out_path is None:
        stem = os.path.splitext(os.path.basename(fw_path))[0]
        out_path = os.path.join(os.path.dirname(os.path.abspath(fw_path)),
                                stem + OUT_SUFFIX + ".gba")

    # 写盘 → 读回来验一遍，别只信内存里那份
    with open(out_path, "wb") as f:
        f.write(bytes(rom))
    back = bytearray(open(out_path, "rb").read())
    ok2, why2 = fwbuild.validate(back)
    if not ok2:
        raise ValueError("写出来的文件校验失败：%s" % why2)
    meta = FB.find_baked(back)
    if not meta:
        raise ValueError("写出来的文件里找不到烘焙记录")
    a_len = meta["a_len"]
    if bytes(back[meta["a_rom"]:meta["a_rom"] + a_len]) != idx[:a_len]:
        raise ValueError("位图 A 写进去和预想的不一样")
    if bytes(back[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]]) != idx[a_len:]:
        raise ValueError("位图 B 写进去和预想的不一样")
    if bytes(back[meta["pal_at"]:meta["pal_at"] + FB.PAL_N * 2]) != \
            b"".join(int(v).to_bytes(2, "little") for v in pal):
        raise ValueError("调色板写进去和预想的不一样")

    return {
        "out": out_path, "build": site.build, "mode": mode, "colors": n,
        "src_size": src.size, "a_rom": meta["a_rom"], "b_rom": meta["b_rom"],
        "hook_addr": FB.EWRAM_MIRROR + meta["hook_off"],
        "free": [(a, b) for a, b in FB.find_layout(back).free],
        "size": len(back),
    }


def describe(rep):
    lines = [
        "构建：%s" % rep["build"],
        "方式：%s" % ("固件里原来就有背景图，这次直接换掉" if rep["mode"] == "replace"
                     else "第一次烘焙（找空位 + 装钩子）"),
        "原图：%d×%d → 240×144，用了 %d 色" % (rep["src_size"][0], rep["src_size"][1],
                                           rep["colors"]),
        "位图：0x%06X（上半）+ 0x%06X（下半）" % (rep["a_rom"], rep["b_rom"]),
        "钩子：EWRAM 0x%08X（CPU 拷贝版，每次换图都会重写）" % rep["hook_addr"],
        "校验：sha256 已重算，复验通过",
        "输出：%s（%d 字节）" % (rep["out"], rep["size"]),
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 命令行
def main_cli(argv):
    ap = argparse.ArgumentParser(description="把一张图写进 SuperFW 固件")
    ap.add_argument("image", help="图片（png/jpg/bmp 都行，大小不限）")
    ap.add_argument("firmware", help="卡带原厂 .fw / .gba")
    ap.add_argument("-o", "--out", default=None, help="输出固件（默认在输入旁边）")
    ap.add_argument("--dim", type=float, default=0.0, help="压暗 0~0.7，默认 0")
    ap.add_argument("--anchor", default="0.5,0.5", help="取景中心，默认 0.5,0.5")
    a = ap.parse_args(argv)
    an = tuple(float(v) for v in a.anchor.split(","))
    try:
        rep = convert(a.image, a.firmware, a.out, a.dim, an)
    except Exception as e:                                   # noqa: BLE001
        print("失败：%s" % e)
        return 2
    print(describe(rep))
    return 0


# ------------------------------------------------------------------ 小窗口
def main_gui(pre_image=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox

    BG, PANEL, TEXT, DIM, ACC = "#16160F", "#1D1D19", "#EDEDE3", "#9A9A8C", "#C8EE45"

    root = tk.Tk()
    root.title("SuperFW 背景图一键写入")
    root.configure(bg=BG)
    W_, H_ = 620, 720
    root.geometry("%dx%d+%d+%d" % (W_, H_, max(0, (root.winfo_screenwidth() - W_) // 2),
                                   max(0, (root.winfo_screenheight() - H_) // 3)))

    st = {"image": pre_image, "fw": None}

    tk.Label(root, text="① 选图片", bg=BG, fg=TEXT,
             font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
    tk.Label(root, text="多大都行，程序会自动裁成 240×144（GBA 的内容区尺寸）",
             bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16)

    row1 = tk.Frame(root, bg=BG)
    row1.pack(fill="x", padx=16, pady=(6, 0))
    img_lbl = tk.Label(row1, text="（还没选）", bg=PANEL, fg=TEXT, anchor="w", padx=10, pady=6,
                       font=("Microsoft YaHei UI", 9))
    img_lbl.pack(side="left", fill="x", expand=True)

    # 预览固定占一块位置（没图时高度为 0），这样它永远待在 ① 下面
    box = tk.Frame(root, bg=BG)
    box.pack(fill="x", padx=16, pady=(8, 0))
    prev = tk.Label(box, bg=PANEL)
    show = tk.Label(box, text="↑ 这就是写进固件里的样子（放大 1.5 倍显示）",
                    bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9))

    # 压暗滑块放在最前面建，预览要用它的值算「真正写进去的样子」
    dimv = tk.IntVar(value=25)

    def device_image(p):
        """按固件的真实规则渲染：裁成 240×144 → 压暗 → 量化 220 色。"""
        im = Image.open(p).convert("RGB")
        fitted = WP.fit_exact(im, (0.5, 0.5))
        idx, pal, _n = WP.quantize(fitted, dimv.get() / 100.0, (0, 0, 0), FB.PAL_N, True)
        flat = []
        for v in pal:
            flat += [(v & 0x1F) * 255 // 31, ((v >> 5) & 0x1F) * 255 // 31,
                     ((v >> 10) & 0x1F) * 255 // 31]
        pic = Image.frombytes("P", (240, 144), idx)
        pic.putpalette(flat)
        return im, pic.convert("RGB")

    def set_preview(p):
        im, pic = device_image(p)
        from PIL import ImageTk
        ph = ImageTk.PhotoImage(pic.resize((360, 216), Image.NEAREST))
        prev.configure(image=ph, width=360, height=216)
        prev.image = ph
        if not prev.winfo_ismapped():
            prev.pack()
            show.pack(anchor="w", pady=(2, 0))
        return im

    def pick_image():
        p = filedialog.askopenfilename(
            title="选一张背景图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("全部", "*.*")])
        if not p:
            return
        try:
            im = set_preview(p)
        except Exception as e:                               # noqa: BLE001
            messagebox.showerror("打不开这张图", str(e), parent=root)
            return
        st["image"] = p
        img_lbl.configure(text="%s   %d×%d" % (os.path.basename(p), im.width, im.height))

    tk.Button(row1, text="选图片…", command=pick_image, bg="#2A2A24", fg=TEXT, relief="flat",
              padx=12, pady=6, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))

    tk.Label(root, text="② 选固件", bg=BG, fg=TEXT,
             font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
    tk.Label(root, text="用卡带自带的 .fw / .gba（已经改过主题的文件也能用，"
                        "里面已经有背景就换成新的）",
             bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16)

    row2 = tk.Frame(root, bg=BG)
    row2.pack(fill="x", padx=16, pady=(6, 0))
    fw_lbl = tk.Label(row2, text="（还没选）", bg=PANEL, fg=TEXT, anchor="w", padx=10, pady=6,
                      font=("Microsoft YaHei UI", 9))
    fw_lbl.pack(side="left", fill="x", expand=True)

    def pick_fw():
        p = filedialog.askopenfilename(
            title="选卡带固件",
            filetypes=[("SuperFW 固件", "*.fw *.gba *.bin"), ("全部", "*.*")])
        if not p:
            return
        try:
            rom = bytearray(open(p, "rb").read())
            ok, why = fwbuild.validate(rom)
            if not ok:
                raise ValueError(why)
            site = fwbuild.detect(rom)
            baked = FB.find_baked(rom)
        except Exception as e:                               # noqa: BLE001
            messagebox.showerror("这个文件用不了", "%s\n\n%s" % (p, e), parent=root)
            return
        st["fw"] = p
        fw_lbl.configure(text="%s   %s%s" % (os.path.basename(p), site.build,
                                             "（已有背景，会替换）" if baked else ""))
        if not baked:
            fit, need, regs = FB.room_for(rom)
            tip.configure(
                text=("固件里能用的空位：%s 字节（%s 需要 %s）"
                      % (" + ".join(format(b - a, ",") for a, b in regs) or "没有",
                         "够放，" if fit else "不够放，",
                         format(need, ","))),
                fg="#C8EE45" if fit else "#FF9B4A")
        else:
            tip.configure(text="里面已经有一张背景图，这次直接换掉，不需要空位", fg="#C8EE45")

    tk.Button(row2, text="选固件…", command=pick_fw, bg="#2A2A24", fg=TEXT, relief="flat",
              padx=12, pady=6, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))
    tip = tk.Label(root, text="", bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9))
    tip.pack(anchor="w", padx=16, pady=(4, 0))

    tk.Label(root, text="③ 开始", bg=BG, fg=TEXT,
             font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 2))

    dimrow = tk.Frame(root, bg=BG)
    dimrow.pack(fill="x", padx=16)
    tk.Label(dimrow, text="压暗", bg=BG, fg=DIM,
             font=("Microsoft YaHei UI", 9)).pack(side="left")

    def on_dim(_v=None):
        if st["image"]:
            set_preview(st["image"])

    tk.Scale(dimrow, from_=0, to=60, orient="horizontal", variable=dimv, showvalue=True,
             command=on_dim, bg=BG, fg=DIM, troughcolor=PANEL, highlightthickness=0, bd=0,
             length=200, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=6)
    tk.Label(dimrow, text="%（压暗一点，菜单文字更好认）", bg=BG, fg=DIM,
             font=("Microsoft YaHei UI", 9)).pack(side="left")

    log = tk.Text(root, height=5, bg=PANEL, fg=TEXT, bd=0, relief="flat", wrap="word",
                  font=("Consolas", 9), padx=10, pady=8)
    log.pack(fill="both", expand=True, padx=16, pady=(10, 0))
    log.insert("1.0", "选好图片和固件，点下面的按钮。\n"
                      "程序会先把图裁成 240×144，再写进固件的空位里，最后重算校验和。")
    log.configure(state="disabled")

    def say(s):
        log.configure(state="normal")
        log.insert("end", "\n" + s)
        log.see("end")
        log.configure(state="disabled")

    def go():
        if not st["image"]:
            messagebox.showinfo("还差一步", "先选一张图片", parent=root)
            return
        if not st["fw"]:
            messagebox.showinfo("还差一步", "先选固件文件", parent=root)
            return
        btn.configure(state="disabled", text="正在写…")
        root.update()
        try:
            rep = convert(st["image"], st["fw"], None, dimv.get() / 100.0, (0.5, 0.5))
        except Exception as e:                               # noqa: BLE001
            say("失败：%s" % e)
            messagebox.showerror("没写进去", str(e), parent=root)
            btn.configure(state="normal", text="写进固件")
            return
        for line in describe(rep).splitlines():
            say(line)
        btn.configure(state="normal", text="写进固件")
        if messagebox.askyesno("写好了", "%s\n\n要打开文件夹看看吗？" % rep["out"], parent=root):
            try:
                os.startfile(os.path.dirname(rep["out"]))     # noqa: S606
            except Exception:                                 # noqa: BLE001
                pass

    btn = tk.Button(root, text="写进固件", command=go, bg=ACC, fg="#16160F", relief="flat",
                    font=("Microsoft YaHei UI", 11, "bold"), pady=8)
    btn.pack(fill="x", padx=16, pady=12)

    if pre_image:
        try:
            im = set_preview(pre_image)
            st["image"] = pre_image
            img_lbl.configure(text="%s   %d×%d" % (os.path.basename(pre_image),
                                                  im.width, im.height))
        except Exception:                                     # noqa: BLE001
            pass

    root.mainloop()
    return 0


def main(argv):
    if not argv:
        return main_gui()
    if len(argv) == 1 and os.path.splitext(argv[0])[1].lower() in (
            ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
        return main_gui(argv[0])
    return main_cli(argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:                                        # noqa: BLE001
        traceback.print_exc()
        sys.exit(3)
