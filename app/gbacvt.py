# -*- coding: utf-8 -*-
"""图片 → GBA 格式转换器
=====================
把一个普通图片（png / jpg / bmp / gif / webp）转成 **GBA 真的能吃的数据**：

    PNG  ──裁切──▶ 240×144 / 240×160 / 自定义 ──量化──▶ 索引位图 + RGB555 调色板
                                                     │
                          ┌──────────────────────────┼──────────────────────────┐
                          ▼                          ▼                          ▼
                    索引 .bin（直接 memcpy）   调色板 .pal.bin（512B）      C 头文件（.h）

和 SuperFW 主题编辑器用的是同一套规则（`app/wallpaper.py` 的裁切与量化语义），
所以「用途 = SuperFW 背景图」出来的字节，跟直接写进固件的那张图完全一致。

用法（图形界面）:
  双击 exe，或者 `python img2gba.py`；把图片拖到 exe 上也能直接打开。

用法（命令行，适合批量）:
  python img2gba.py 图.png --use superfw -o out/
  python img2gba.py 图.png --use screen --dim 0.2 --anchor 0.5,0.4
  python img2gba.py 图.png --w 240 --h 160 --colors 256 --bpp 8 --order tile
"""
import argparse
import os
import struct
import sys

from PIL import Image, ImageDraw

VERSION = "1.0"

# ---------------------------------------------------------------- 用途预设
# 每一项都是「GBA 上真实存在的格式」，不是随便凑的：
#   superfw  内容区那层 8bpp 位图，占调色板 20..239（0..19 留给菜单自己的颜色）
#   screen   模式 3/5 的整屏位图，1 字节/像素，256 色线性
#   bgtile   背景层瓦片，仍按 8×8 分块（图块数据给 VRAM 的 BG 区）
#   sprite   4bpp 精灵/瓦片，16 色，一字节两像素（低位在左）
PRESETS = {
    "superfw": dict(label="SuperFW 背景图", w=240, h=144, colors=220, bpp=8,
                    order="linear", base=20,
                    note="写进 SuperFW 内容区的那一层：220 色，索引从 20 起（0..19 留给菜单）"),
    "screen": dict(label="GBA 整屏位图（模式 3 / 5）", w=240, h=160, colors=256, bpp=8,
                   order="linear", base=0,
                   note="直接 DMA 到 VRAM 的位图，1 字节 1 像素"),
    "bgtile": dict(label="背景层瓦片（8bpp）", w=240, h=160, colors=256, bpp=8,
                   order="tile", base=0,
                   note="按 8×8 图块重新排列，给 BG 图块区用"),
    "sprite": dict(label="精灵 / 4bpp 瓦片（16 色）", w=64, h=64, colors=16, bpp=4,
                   order="tile", base=0,
                   note="4bpp：一字节两像素，低 4 位是左边那个"),
    "custom": dict(label="自定义", w=240, h=160, colors=256, bpp=8, order="linear",
                   base=0, note="尺寸、色数、位深、排列都可以自己填"),
}
PRESET_ORDER = ["superfw", "screen", "bgtile", "sprite", "custom"]

COLOR_CHOICES = [2, 4, 8, 16, 32, 64, 128, 220, 256]


# ---------------------------------------------------------------- 颜色
def to555(rgb):
    r, g, b = (int(v) for v in rgb[:3])
    return ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)


def from555(v):
    """15 位色 → RGB。用位复制（v&31 左移 3 再补高位），和 mGBA / 真机一致。"""
    v = int(v) & 0x7FFF
    out = []
    for shift in (0, 5, 10):
        c = (v >> shift) & 0x1F
        out.append((c << 3) | (c >> 2))
    return tuple(out)


# ---------------------------------------------------------------- 裁切
def crop_window(src_size, size, anchor=(0.5, 0.5)):
    """铺满目标尺寸后，源图上保住的那块矩形（浮点）与裁切量。"""
    sw, sh = max(1, src_size[0]), max(1, src_size[1])
    tw, th = size
    ax, ay = anchor
    r = float(tw) / th
    if sw / float(sh) > r:                 # 太宽 → 裁左右
        ww, wh = sh * r, float(sh)
    else:                                  # 太高 → 裁上下
        ww, wh = float(sw), sw / r
    left = (sw - ww) * ax
    top = (sh - wh) * ay
    return {
        "win": (left, top, left + ww, top + wh),
        "cut_x": sw - ww, "cut_y": sh - wh,
        "pct_x": (sw - ww) / sw, "pct_y": (sh - wh) / sh,
        "zoom": tw / max(1e-6, ww),
    }


def fit_to(im, size, anchor=(0.5, 0.5)):
    """把任意图变成**正好** size：铺满、按 anchor 取景、不拉伸变形。"""
    im = im.convert("RGB")
    m = crop_window(im.size, size, anchor)
    l, t, r, b = m["win"]
    box = (int(round(l)), int(round(t)),
           max(int(round(l)) + 1, int(round(r))),
           max(int(round(t)) + 1, int(round(b))))
    return im.crop(box).resize(tuple(size), Image.LANCZOS)


# ---------------------------------------------------------------- 量化
def quantize(im, colors=256, dim=0.0, dither=True):
    """返回 (索引字节, RGB 调色板, 实际用到的色数)。索引从 0 起，偏移由调用方加。"""
    colors = max(2, min(256, int(colors)))
    if dim > 0:
        a = max(0.0, min(1.0, dim))
        im = Image.blend(im.convert("RGB"), Image.new("RGB", im.size, (0, 0, 0)), a)
    q = im.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT,
                                   dither=Image.FLOYDSTEINBERG if dither else Image.NONE)
    p = list(q.getpalette() or [])
    if len(p) < colors * 3:               # 纯色图调色板会短，补齐再取，否则越界
        p += [0] * (colors * 3 - len(p))
    pal = [tuple(p[i * 3:i * 3 + 3]) for i in range(colors)]
    idx = q.tobytes()
    used = len(set(idx))
    return idx, pal, used


def palette_table(pal, base=0, entries=None):
    """把 RGB 调色板铺进 256 项 RGB555 表（base 之前留空位）。"""
    base = max(0, min(255, int(base)))
    n = entries if entries else max(256, base + len(pal))
    table = [0] * n
    for i, c in enumerate(pal):
        if base + i < n:
            table[base + i] = to555(c)
    return table


# ---------------------------------------------------------------- 打包
def check_geometry(size, order):
    w, h = size
    if not (8 <= w <= 512 and 8 <= h <= 512):
        raise ValueError("宽高要在 8..512 之间（收到 %d×%d）" % (w, h))
    if order == "tile" and (w % 8 or h % 8):
        raise ValueError("瓦片排列要求宽高都是 8 的倍数（收到 %d×%d）" % (w, h))


def pack(idx, size, bpp=8, order="linear", base=0):
    """索引 → GBA 字节流。返回 (数据, 每像素占几个字节)。"""
    w, h = size
    check_geometry(size, order)
    if len(idx) != w * h:
        raise ValueError("索引数 %d 与 %d×%d 对不上" % (len(idx), w, h))
    base = max(0, min(255, int(base)))

    if bpp == 4:
        vals = [v & 0x0F for v in idx]
        if order == "linear":
            out = bytearray()
            for i in range(0, len(vals), 2):
                hi = vals[i + 1] if i + 1 < len(vals) else 0
                out.append(vals[i] | (hi << 4))       # 低 4 位 = 左边像素
            return bytes(out), 0.5
        out = bytearray()
        for ty in range(0, h, 8):
            for tx in range(0, w, 8):
                for row in range(8):
                    y = ty + row
                    for col in range(0, 8, 2):
                        a = vals[y * w + tx + col]
                        b = vals[y * w + tx + col + 1]
                        out.append(a | (b << 4))
        return bytes(out), 0.5

    vals = [min(255, base + v) for v in idx]          # 8bpp：索引带上起始槽
    if order == "linear":
        return bytes(vals), 1.0
    out = bytearray()
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for row in range(8):
                y = ty + row
                out += bytes(vals[y * w + tx:y * w + tx + 8])
    return bytes(out), 1.0


def render(idx, pal, size, base=0, bpp=8):
    """按设备真实色深（RGB555）渲染成 RGB 图，用来当预览。

    注意索引要按**存进去的值**查表：8bpp 存的是 base+idx，所以调色板也得摆在
    base 起的槽位上，否则预览会整体错位（看起来像颜色全变了）。
    """
    w, h = size
    flat = [0] * (256 * 3)
    shown = bytearray(len(idx))
    if bpp == 8:
        base = max(0, min(255, int(base)))
        for i, c in enumerate(pal):
            slot = base + i
            if slot > 255:
                break
            flat[slot * 3:slot * 3 + 3] = list(from555(to555(c)))
        for i, v in enumerate(idx):
            shown[i] = min(255, base + v)
    else:
        for i, c in enumerate(pal[:16]):
            flat[i * 3:i * 3 + 3] = list(from555(to555(c)))
        for i, v in enumerate(idx):
            shown[i] = v & 0x0F
    image = Image.frombytes("P", (w, h), bytes(shown))
    image.putpalette(flat)
    return image.convert("RGB")


# ---------------------------------------------------------------- 一次转换
def convert(src, w=240, h=144, colors=220, bpp=8, order="linear", base=0,
            dim=0.0, anchor=(0.5, 0.5), dither=True):
    """src 可以是路径，也可以是已经打开的 PIL 图。返回一份完整结果 dict。"""
    if isinstance(src, Image.Image):
        im = src.convert("RGB")
        name = "（内存图）"
    else:
        im = Image.open(src)
        im.load()
        im = im.convert("RGB")
        name = os.path.basename(src)
    size = (int(w), int(h))
    check_geometry(size, order)
    if bpp == 4:
        colors = min(16, colors)
    colors = max(2, min(256, int(colors)))
    if bpp == 8 and base + colors > 256:
        raise ValueError("索引起点 %d + %d 色超出 256 项调色板" % (base, colors))

    fitted = fit_to(im, size, anchor)
    idx, pal, used = quantize(fitted, colors, dim, dither)
    data, per_px = pack(idx, size, bpp, order, base)
    table = palette_table(pal, base if bpp == 8 else 0, 256 if bpp == 8 else colors)
    return {
        "name": name, "src_size": im.size, "size": size, "bpp": bpp,
        "order": order, "colors": colors, "used": used, "base": base if bpp == 8 else 0,
        "dim": dim, "anchor": anchor, "dither": dither,
        "data": data, "per_px": per_px, "pal": pal, "table": table,
        "crop": crop_window(im.size, size, anchor),
        "display": render(idx, pal, size, base if bpp == 8 else 0, bpp),
        "idx": idx, "fitted": fitted,
    }


def summary(rep):
    c = rep["crop"]
    cut = []
    if c["cut_x"] > 1:
        cut.append("左右共裁 %d px（%.0f%%）" % (round(c["cut_x"]), c["pct_x"] * 100))
    if c["cut_y"] > 1:
        cut.append("上下共裁 %d px（%.0f%%）" % (round(c["cut_y"]), c["pct_y"] * 100))
    if c["zoom"] > 1.02:
        cut.append("放大 %.1f×" % c["zoom"])
    elif c["zoom"] < 0.98:
        cut.append("缩小 %.1f×" % (1 / c["zoom"]))
    return {
        "原图": "%d×%d" % rep["src_size"],
        "成品": "%d×%d · %dbpp · %s" % (rep["size"][0], rep["size"][1], rep["bpp"],
                                       "线性" if rep["order"] == "linear" else "瓦片"),
        "取景": "，".join(cut) or "刚好铺满，不用裁",
        "调色板": "%d 色（实际用到 %d 色）%s" % (
            rep["colors"], rep["used"],
            "，索引从 %d 起" % rep["base"] if rep["base"] else ""),
        "数据": "%d 字节（%.1f KB）" % (len(rep["data"]), len(rep["data"]) / 1024.0),
    }


# ---------------------------------------------------------------- 导出
def _tag(rep):
    return "%dx%d-%dbpp" % (rep["size"][0], rep["size"][1], rep["bpp"])


def header_text(rep):
    lines = [
        "/* 图片 → GBA 格式转换器 v%s 生成" % VERSION,
        " * 来源：%s   %d×%d → %d×%d（%s）" % (
            rep["name"], rep["src_size"][0], rep["src_size"][1],
            rep["size"][0], rep["size"][1],
            "线性排列" if rep["order"] == "linear" else "8×8 瓦片排列"),
        " * 格式：%dbpp，%d 色，索引起点 %d，压暗 %d%%%s" % (
            rep["bpp"], rep["colors"], rep["base"], round(rep["dim"] * 100),
            "，已抖动" if rep["dither"] else "，未抖动"),
        " */",
        "#ifndef GFX_DATA_H",
        "#define GFX_DATA_H",
        "",
        "#define GFX_W %d" % rep["size"][0],
        "#define GFX_H %d" % rep["size"][1],
        "#define GFX_BPP %d" % rep["bpp"],
        "#define GFX_COLORS %d" % rep["colors"],
        "#define GFX_INDEX_BASE %d" % rep["base"],
        "",
    ]
    pal = rep["table"]
    lines.append("/* RGB555，共 %d 项%s */" % (
        len(pal), "（写在调色板第 %d 项起）" % rep["base"] if rep["base"] else ""))
    lines.append("const unsigned short gfx_pal[%d] = {" % len(pal))
    for i in range(0, len(pal), 8):
        lines.append("  " + ", ".join("0x%04X" % v for v in pal[i:i + 8]) + ",")
    lines.append("};")
    lines.append("")
    lines.append("/* 索引位图，%d 字节 */" % len(rep["data"]))
    lines.append("const unsigned char gfx_data[%d] = {" % len(rep["data"]))
    for i in range(0, len(rep["data"]), 16):
        lines.append("  " + ", ".join("0x%02X" % v for v in rep["data"][i:i + 16]) + ",")
    lines.append("};")
    lines.append("")
    lines.append("#endif")
    return "\n".join(lines) + "\n"


def export(rep, outdir, stem=None, png=True, bin_=True, pal=True, header=True):
    """把结果写到 outdir，返回写出的文件列表。"""
    os.makedirs(outdir, exist_ok=True)
    stem = stem or os.path.splitext(rep["name"])[0] or "gba"
    tag = _tag(rep)
    wrote = []
    if png:
        p1 = os.path.join(outdir, "%s-%s.png" % (stem, tag))
        rep["display"].save(p1)
        wrote.append(p1)
        p3 = os.path.join(outdir, "%s-%s@3x.png" % (stem, tag))
        rep["display"].resize((rep["size"][0] * 3, rep["size"][1] * 3),
                              Image.NEAREST).save(p3)
        wrote.append(p3)
    if bin_:
        b = os.path.join(outdir, "%s-%s.bin" % (stem, tag))
        with open(b, "wb") as f:
            f.write(rep["data"])
        wrote.append(b)
    if pal:
        p = os.path.join(outdir, "%s-%s.pal.bin" % (stem, tag))
        with open(p, "wb") as f:
            f.write(struct.pack("<%dH" % len(rep["table"]), *rep["table"]))
        wrote.append(p)
    if header:
        h = os.path.join(outdir, "%s-%s.h" % (stem, tag))
        with open(h, "w", encoding="utf-8", newline="\n") as f:
            f.write(header_text(rep))
        wrote.append(h)
    return wrote


def swatch_image(rep, cell=16, cols=16):
    """调色板色板条（方便肉眼比色）。"""
    pal = rep["pal"]
    rows = max(1, (len(pal) + cols - 1) // cols)
    im = Image.new("RGB", (cols * cell, rows * cell), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, c in enumerate(pal):
        x, y = (i % cols) * cell, (i // cols) * cell
        d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=from555(to555(c)),
                    outline=(30, 30, 30))
    return im


# ---------------------------------------------------------------- 命令行
def main_cli(argv):
    ap = argparse.ArgumentParser(description="图片 → GBA 格式（索引位图 + RGB555 调色板）")
    ap.add_argument("image", help="图片（png / jpg / bmp / gif / webp）")
    ap.add_argument("--use", default="superfw", choices=PRESET_ORDER, help="用途预设")
    ap.add_argument("--w", type=int, default=None)
    ap.add_argument("--h", type=int, default=None)
    ap.add_argument("--colors", type=int, default=None)
    ap.add_argument("--bpp", type=int, default=None, choices=[4, 8])
    ap.add_argument("--order", default=None, choices=["linear", "tile"])
    ap.add_argument("--base", type=int, default=None, help="索引起点，默认跟预设")
    ap.add_argument("--dim", type=float, default=0.0, help="压暗 0~0.8")
    ap.add_argument("--anchor", default="0.5,0.5", help="取景中心")
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("-o", "--out", default=None, help="输出目录（默认图片旁边）")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--no-h", action="store_true")
    a = ap.parse_args(argv)

    p = dict(PRESETS[a.use])
    kw = dict(w=a.w if a.w else p["w"], h=a.h if a.h else p["h"],
              colors=a.colors if a.colors else p["colors"],
              bpp=a.bpp if a.bpp else p["bpp"],
              order=a.order if a.order else p["order"],
              base=a.base if a.base is not None else p["base"],
              dim=a.dim, anchor=tuple(float(v) for v in a.anchor.split(",")),
              dither=not a.no_dither)
    try:
        rep = convert(a.image, **kw)
    except Exception as e:                                    # noqa: BLE001
        print("失败：%s" % e)
        return 2
    outdir = a.out or os.path.dirname(os.path.abspath(a.image))
    files = export(rep, outdir, png=not a.no_png, header=not a.no_h)
    for k, v in summary(rep).items():
        print("%-6s %s" % (k, v))
    print("写出：")
    for f in files:
        print("  " + f)
    return 0


# ---------------------------------------------------------------- 图形界面
def main_gui(pre_image=None, parent=None, on_apply=None, apply_hint="用作背景图"):
    """打开转换器窗口。

    parent   给了就是主程序里的子窗口（Toplevel，共用主 mainloop）；
             不给就自己开一个 Tk 窗口跑 mainloop（独立 exe 用这条）。
    on_apply 给了就多一个「apply_hint」按钮，把当前裁好的 240×144 图交回去。
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from PIL import ImageTk

    BG, PANEL, PANEL2 = "#0B0B09", "#131311", "#191916"
    LINE, TEXT, MUTED, DIM, ACC = "#2A2A25", "#ECE8DE", "#8C8779", "#5D594F", "#D9FF57"
    FONT = "Microsoft YaHei UI"

    standalone = parent is None
    root = tk.Tk() if standalone else tk.Toplevel(parent)
    root.title("图片 → GBA 格式转换器")
    root.configure(bg=BG)
    if standalone:
        W_, H_ = 1200, 812
        root.geometry("%dx%d+%d+%d" % (
            W_, H_, max(0, (root.winfo_screenwidth() - W_) // 2),
            max(0, (root.winfo_screenheight() - H_) // 3)))
        root.minsize(1080, 720)
    else:
        root.transient(parent)
        W_, H_ = 1180, 830
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - W_) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - H_) // 6)
        root.geometry("%dx%d+%d+%d" % (W_, H_, max(0, px), max(0, py)))
        root.minsize(1000, 740)
    try:
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:                                          # noqa: BLE001
        pass

    st = {"src": None, "path": None, "rep": None, "thumb": None, "busy": False}

    # ---------------- 顶栏
    head = tk.Frame(root, bg=BG, height=56)
    head.pack(fill="x", padx=18, pady=(12, 0))
    tk.Label(head, text="图片 → GBA", bg=BG, fg=TEXT,
             font=(FONT, 15, "bold")).pack(side="left")
    tk.Label(head, text="    裁到目标尺寸 → 量化成索引图 + RGB555 调色板 → 导出 .bin / .h",
             bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left")

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=18, pady=(10, 0))

    left = tk.Frame(body, bg=BG, width=404)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)
    right = tk.Frame(body, bg=BG)
    right.pack(side="left", fill="both", expand=True, padx=(18, 0))

    def section(parent, text, sub=None):
        tk.Label(parent, text=text, bg=BG, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w", pady=(0, 1))
        if sub:
            tk.Label(parent, text=sub, bg=BG, fg=DIM, font=(FONT, 9),
                     justify="left", wraplength=372).pack(anchor="w", pady=(0, 4))

    # ---------------- ① 图片
    section(left, "① 选图片", "多大都行，程序按目标尺寸自动裁切；点下面的缩略图可以改取景")

    row1 = tk.Frame(left, bg=BG)
    row1.pack(fill="x", pady=(0, 6))
    name_lbl = tk.Label(row1, text="（还没选）", bg=PANEL, fg=MUTED, anchor="w", padx=10,
                        pady=6, font=(FONT, 9))
    name_lbl.pack(side="left", fill="x", expand=True)

    thumb = tk.Canvas(left, bg=PANEL, width=392, height=240, highlightthickness=1,
                      highlightbackground=LINE, bd=0)
    thumb.pack(fill="x")
    hint = tk.Label(left, text="整张原图压暗显示，亮的那块 = 会被保留下来的部分",
                    bg=BG, fg=DIM, font=(FONT, 8), justify="left", wraplength=372)
    hint.pack(anchor="w", pady=(3, 0))

    # ---------------- 压暗 / 抖动
    opt = tk.Frame(left, bg=BG)
    opt.pack(fill="x", pady=(8, 0))
    tk.Label(opt, text="压暗", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
    dimv = tk.IntVar(value=0)
    dim_lbl = tk.Label(opt, text="0%", bg=BG, fg=MUTED, font=("Consolas", 9), width=5)
    ditherv = tk.IntVar(value=1)

    def on_dim(v=None):
        dim_lbl.configure(text="%d%%" % dimv.get())
        schedule()

    tk.Scale(opt, from_=0, to=60, orient="horizontal", variable=dimv, showvalue=False,
             command=on_dim, bg=BG, fg=MUTED, troughcolor=PANEL2, highlightthickness=0,
             bd=0, length=210, sliderrelief="flat").pack(side="left", padx=(8, 6))
    dim_lbl.pack(side="left")
    tk.Label(opt, text="压暗一点，亮色图上更好认字", bg=BG, fg=DIM,
             font=(FONT, 8)).pack(side="left", padx=(8, 0))

    opt2 = tk.Frame(left, bg=BG)
    opt2.pack(fill="x", pady=(4, 0))
    tk.Checkbutton(opt2, text="抖动（渐变更顺滑，纯色块会花）", variable=ditherv,
                   command=lambda: schedule(), bg=BG, fg=MUTED, selectcolor=PANEL2,
                   activebackground=BG, activeforeground=TEXT, bd=0, highlightthickness=0,
                   font=(FONT, 9)).pack(side="left")

    tk.Frame(left, bg=LINE, height=1).pack(fill="x", pady=(10, 10))

    # ---------------- ② 格式
    section(left, "② 输出格式", "填错也不要紧，下面会实时算出字节数，导出前一眼能看出来")

    prow = tk.Frame(left, bg=BG)
    prow.pack(fill="x", pady=(0, 6))
    tk.Label(prow, text="用途", bg=BG, fg=MUTED, font=(FONT, 9), width=6, anchor="w").pack(side="left")
    use_var = tk.StringVar(value=PRESETS["superfw"]["label"])
    use_box = tk.OptionMenu(prow, use_var, *[PRESETS[k]["label"] for k in PRESET_ORDER])
    use_box.configure(bg=PANEL2, fg=TEXT, activebackground=LINE, activeforeground=TEXT,
                      relief="flat", bd=0, highlightthickness=0, font=(FONT, 9), width=24,
                      anchor="w", padx=8, pady=4)
    use_box["menu"].configure(bg=PANEL2, fg=TEXT, activebackground=LINE, font=(FONT, 9))
    use_box.pack(side="left")

    def _label_of(key):
        return PRESETS[key]["label"]

    def _key_of(label):
        for k in PRESET_ORDER:
            if PRESETS[k]["label"] == label:
                return k
        return "custom"

    # 尺寸 / 色数 / 位深 / 排列 / 起点
    grid = tk.Frame(left, bg=BG)
    grid.pack(fill="x", pady=(2, 0))

    def field(label, r, widget):
        tk.Label(grid, text=label, bg=BG, fg=MUTED, font=(FONT, 9), width=6,
                 anchor="w").grid(row=r, column=0, sticky="w", pady=3)
        widget.grid(row=r, column=1, sticky="w")
        return widget

    wv, hv, cv, bv, ov, bav = (tk.StringVar(value="240"), tk.StringVar(value="144"),
                               tk.StringVar(value="220"), tk.IntVar(value=8),
                               tk.StringVar(value="线性"), tk.StringVar(value="20"))
    srow = tk.Frame(grid, bg=BG)
    sw_sp = tk.Spinbox(srow, from_=8, to=512, width=5, textvariable=wv, bg=PANEL2, fg=TEXT,
                       insertbackground=TEXT, relief="flat", bd=0, justify="center",
                       font=("Consolas", 10), buttonbackground=PANEL2,
                       highlightthickness=0, command=lambda: schedule(True))
    sw_sp.pack(side="left")
    tk.Label(srow, text=" × ", bg=BG, fg=DIM, font=(FONT, 10)).pack(side="left")
    sh_sp = tk.Spinbox(srow, from_=8, to=512, width=5, textvariable=hv, bg=PANEL2, fg=TEXT,
                       insertbackground=TEXT, relief="flat", bd=0, justify="center",
                       font=("Consolas", 10), buttonbackground=PANEL2,
                       highlightthickness=0, command=lambda: schedule(True))
    sh_sp.pack(side="left")
    tk.Label(srow, text="  像素", bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left")
    field("尺寸", 0, srow)

    crow = tk.Frame(grid, bg=BG)
    c_sp = tk.Spinbox(crow, values=[str(v) for v in COLOR_CHOICES], width=5, textvariable=cv,
                      bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                      justify="center", font=("Consolas", 10), buttonbackground=PANEL2,
                      highlightthickness=0, command=lambda: schedule(True))
    c_sp.pack(side="left")
    tk.Label(crow, text="  色（4bpp 最多 16 色）", bg=BG, fg=DIM,
             font=(FONT, 9)).pack(side="left")
    field("颜色", 1, crow)

    brow = tk.Frame(grid, bg=BG)
    for txt, val in (("8bpp", 8), ("4bpp", 4)):
        tk.Radiobutton(brow, text=txt, value=val, variable=bv, command=lambda: schedule(True),
                       bg=BG, fg=MUTED, selectcolor=PANEL2, activebackground=BG,
                       activeforeground=TEXT, bd=0, highlightthickness=0,
                       font=(FONT, 9)).pack(side="left", padx=(0, 12))
    field("位深", 2, brow)

    orow = tk.Frame(grid, bg=BG)
    for txt in ("线性", "瓦片"):
        tk.Radiobutton(orow, text=txt + ("（1 字节/像素）" if txt == "线性" else "（8×8 分块）"),
                       value=txt, variable=ov, command=lambda: schedule(True),
                       bg=BG, fg=MUTED, selectcolor=PANEL2, activebackground=BG,
                       activeforeground=TEXT, bd=0, highlightthickness=0,
                       font=(FONT, 9)).pack(side="left", padx=(0, 8))
    field("排列", 3, orow)

    barow = tk.Frame(grid, bg=BG)
    b_sp = tk.Spinbox(barow, from_=0, to=240, width=5, textvariable=bav, bg=PANEL2, fg=TEXT,
                      insertbackground=TEXT, relief="flat", bd=0, justify="center",
                      font=("Consolas", 10), buttonbackground=PANEL2,
                      highlightthickness=0, command=lambda: schedule(True))
    b_sp.pack(side="left")
    tk.Label(barow, text="  最低索引（SuperFW 背景固定 20）", bg=BG, fg=DIM,
             font=(FONT, 9)).pack(side="left")
    field("起点", 4, barow)

    note_lbl = tk.Label(left, text="", bg=BG, fg=DIM, font=(FONT, 8), justify="left",
                        wraplength=372)
    note_lbl.pack(anchor="w", pady=(6, 0))

    # ---------------- 右侧：预览
    section(right, "③ 成品预览", "这是设备上的真实样子（已按 15 位色深渲染），不是原图")

    pv_wrap = tk.Frame(right, bg=PANEL)
    pv_wrap.pack(fill="both", expand=True, pady=(0, 6))
    pv = tk.Canvas(pv_wrap, bg=PANEL, highlightthickness=1, highlightbackground=LINE, bd=0)
    pv.pack(fill="both", expand=True)

    info = tk.Label(right, text="", bg=BG, fg=MUTED, font=("Consolas", 9), justify="left",
                    anchor="w")
    info.pack(fill="x")

    # ---------------- 导出
    tk.Frame(right, bg=LINE, height=1).pack(fill="x", pady=(8, 8))
    ex = tk.Frame(right, bg=BG)
    ex.pack(fill="x")
    tk.Label(ex, text="④ 导出到", bg=BG, fg=TEXT,
             font=(FONT, 11, "bold")).pack(side="left")
    out_var = tk.StringVar(value="")
    out_entry = tk.Entry(ex, textvariable=out_var, bg=PANEL2, fg=MUTED, relief="flat",
                         insertbackground=TEXT, font=(FONT, 9), bd=0)
    out_entry.pack(side="left", fill="x", expand=True, padx=(10, 6), ipady=6)

    def pick_out():
        d = filedialog.askdirectory(title="导出到哪个文件夹")
        if d:
            out_var.set(d)

    tk.Button(ex, text="选文件夹…", command=pick_out, bg=PANEL2, fg=TEXT, relief="flat",
              bd=0, padx=12, pady=5, font=(FONT, 9)).pack(side="left")

    ex2 = tk.Frame(right, bg=BG)
    ex2.pack(fill="x", pady=(8, 0))
    ck = {}
    for key, txt, dflt in (("png", "效果图 PNG", 1), ("bin", "索引 .bin", 1),
                           ("pal", "调色板 .pal.bin", 1), ("h", "C 头文件 .h", 1)):
        v = tk.IntVar(value=dflt)
        ck[key] = v
        tk.Checkbutton(ex2, text=txt, variable=v, bg=BG, fg=MUTED, selectcolor=PANEL2,
                       activebackground=BG, activeforeground=TEXT, bd=0,
                       highlightthickness=0, font=(FONT, 9)).pack(side="left", padx=(0, 14))

    # ---------------- 逻辑
    def _int(var, default, lo, hi):
        try:
            v = int(str(var.get()).strip())
        except Exception:                                      # noqa: BLE001
            return default
        return max(lo, min(hi, v))

    def current_opts():
        key = _key_of(use_var.get())
        bpp = 8 if bv.get() == 8 else 4
        return dict(w=_int(wv, 240, 8, 512), h=_int(hv, 144, 8, 512),
                    colors=_int(cv, 220, 2, 256), bpp=bpp,
                    order="linear" if ov.get() == "线性" else "tile",
                    base=_int(bav, 0, 0, 240), dim=dimv.get() / 100.0,
                    anchor=st.get("anchor", (0.5, 0.5)),
                    dither=bool(ditherv.get()))

    def mark_custom():
        """手动改了参数 → 用途切到「自定义」，别再冒充某个预设。"""
        if st.get("loading"):
            return
        if _key_of(use_var.get()) != "custom":
            st["loading"] = True
            use_var.set(_label_of("custom"))
            st["loading"] = False
            note_lbl.configure(text=PRESETS["custom"]["note"])

    def draw_thumb():
        """原图缩略图：压暗整张，保留区亮着 + 绿框；点/拖它改取景。"""
        src = st["src"]
        if src is None:
            thumb.delete("all")
            return
        sw, sh = src.size
        cw, chh = 392, 240
        s = min(cw / float(sw), chh / float(sh))
        dw, dh = max(1, int(sw * s)), max(1, int(sh * s))
        small = src.resize((dw, dh), Image.LANCZOS)
        opts = current_opts()
        try:
            m = crop_window((sw, sh), (opts["w"], opts["h"]), opts["anchor"])
        except Exception:                                      # noqa: BLE001
            m = crop_window((sw, sh), (opts["w"], opts["h"]), (0.5, 0.5))
        l, t, r, b = [v * s for v in m["win"]]
        base_im = Image.new("RGB", (dw, dh), (0, 0, 0))
        shade = Image.blend(small, Image.new("RGB", (dw, dh), (0, 0, 0)), 0.62)
        base_im.paste(shade, (0, 0))
        base_im.paste(small.crop((int(l), int(t), max(int(l) + 1, int(r)),
                                  max(int(t) + 1, int(b)))).resize(
            (max(1, int(r - l)), max(1, int(b - t))), Image.LANCZOS), (int(l), int(t)))
        d = ImageDraw.Draw(base_im)
        d.rectangle([l, t, r - 1, b - 1], outline=(0x8F, 0xC8, 0x2A), width=2)
        st["thumb"] = ImageTk.PhotoImage(base_im)
        thumb.delete("all")
        thumb.create_image(0, 0, image=st["thumb"], anchor="nw")
        thumb.configure(width=dw, height=dh) if False else None
        st["thumb_box"] = (dw, dh)

    def set_anchor_from(event):
        if st["src"] is None or "thumb_box" not in st:
            return
        dw, dh = st["thumb_box"]
        sw, sh = st["src"].size
        opts = current_opts()
        m = crop_window((sw, sh), (opts["w"], opts["h"]), (0.5, 0.5))
        ww = m["win"][2] - m["win"][0]
        wh = m["win"][3] - m["win"][1]
        s = dw / float(sw)
        px, py = event.x / s, event.y / s
        ax = 0.5 if sw <= ww + 0.5 else (px - ww / 2.0) / (sw - ww)
        ay = 0.5 if sh <= wh + 0.5 else (py - wh / 2.0) / (sh - wh)
        st["anchor"] = (max(0.0, min(1.0, ax)), max(0.0, min(1.0, ay)))
        schedule()

    thumb.bind("<Button-1>", set_anchor_from)
    thumb.bind("<B1-Motion>", set_anchor_from)

    def apply_preset(key):
        p = PRESETS[key]
        st["loading"] = True
        wv.set(str(p["w"]))
        hv.set(str(p["h"]))
        cv.set(str(p["colors"]))
        bv.set(p["bpp"])
        ov.set("线性" if p["order"] == "linear" else "瓦片")
        bav.set(str(p["base"]))
        st["loading"] = False
        note_lbl.configure(text=p["note"])
        schedule()

    def on_use(_v=None):
        if st.get("loading"):
            return
        apply_preset(_key_of(use_var.get()))

    use_var.trace_add("write", lambda *_: on_use())

    def refresh(force_thumb=False):
        if st["busy"] or st["src"] is None:
            return
        st["busy"] = True
        try:
            opts = current_opts()
            try:
                rep = convert(st["src"], **opts)
            except Exception as e:                             # noqa: BLE001
                info.configure(text="参数不对：%s" % e, fg="#E06C6C")
                st["rep"] = None
                return
            st["rep"] = rep
            # 预览：按可用空间等比放大（最近邻，保证看到的就是像素本身）
            aw = max(120, pv.winfo_width())
            ah = max(120, pv.winfo_height())
            sc = max(1, min(3, int(min(aw / float(rep["size"][0]),
                                      ah / float(rep["size"][1])))))
            pic = rep["display"].resize((rep["size"][0] * sc, rep["size"][1] * sc),
                                        Image.NEAREST)
            st["prev"] = ImageTk.PhotoImage(pic)
            pv.delete("all")
            pv.create_image(aw // 2, ah // 2, image=st["prev"], anchor="center")
            pv.create_text(12, 12, anchor="nw", fill="#8C8779", font=("Consolas", 8),
                           text="%d×%d  放大 %d×  %dbpp" % (rep["size"][0], rep["size"][1],
                                                           sc, rep["bpp"]))
            s = summary(rep)
            info.configure(
                text="  ".join("%s %s" % (k, s[k]) for k in ("原图", "成品")) + "\n"
                     + "  ".join("%s %s" % (k, s[k]) for k in ("取景",)) + "\n"
                     + "  ".join("%s %s" % (k, s[k]) for k in ("调色板", "数据")),
                fg=MUTED)
            draw_thumb()
            if not out_var.get() and st["path"]:
                out_var.set(os.path.dirname(os.path.abspath(st["path"])))
        finally:
            st["busy"] = False

    _pending = {"id": None}

    def schedule(custom=False):
        if custom:
            mark_custom()
        if _pending["id"]:
            try:
                root.after_cancel(_pending["id"])
            except Exception:                                  # noqa: BLE001
                pass
        _pending["id"] = root.after(90, lambda: (refresh(), _pending.update(id=None)))

    for var in (wv, hv, cv, bav):
        var.trace_add("write", lambda *_: schedule(True))

    def load_image(p, label=None):
        if isinstance(p, Image.Image):
            im = p.convert("RGB")
            path = ""
            shown = label or "内存里的图"
        else:
            try:
                im = Image.open(p)
                im.load()
                im = im.convert("RGB")
            except Exception as e:                             # noqa: BLE001
                messagebox.showerror("打不开这张图", "%s\n\n%s" % (p, e), parent=root)
                return
            path = p
            shown = "%s   %d×%d" % (os.path.basename(p), im.width, im.height)
        st["src"], st["path"] = im, path
        st["anchor"] = (0.5, 0.5)
        name_lbl.configure(text=shown, fg=TEXT)
        if path and not out_var.get():
            out_var.set(os.path.dirname(os.path.abspath(path)))
        refresh()

    def pick_image():
        p = filedialog.askopenfilename(
            title="选一张图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("全部", "*.*")])
        if p:
            load_image(p)

    tk.Button(row1, text="选图片…", command=pick_image, bg=PANEL2, fg=TEXT, relief="flat",
              bd=0, padx=12, pady=6, font=(FONT, 9)).pack(side="left", padx=(8, 0))

    # 导出按钮
    def do_export():
        if not st.get("rep"):
            messagebox.showinfo("还没有成品", "先选一张图片", parent=root)
            return
        outdir = out_var.get().strip()
        if not outdir:
            outdir = filedialog.askdirectory(title="导出到哪个文件夹")
            if not outdir:
                return
            out_var.set(outdir)
        rep = st["rep"]
        stem = os.path.splitext(rep["name"])[0] or "gba"
        try:
            files = export(rep, outdir, stem=stem, png=bool(ck["png"].get()),
                           bin_=bool(ck["bin"].get()), pal=bool(ck["pal"].get()),
                           header=bool(ck["h"].get()))
            sw = os.path.join(outdir, "%s-%s-调色板.png" % (stem, _tag(rep)))
            swatch_image(rep).save(sw)
            files.append(sw)
        except Exception as e:                                 # noqa: BLE001
            messagebox.showerror("导出失败", str(e), parent=root)
            return
        info.configure(text="已导出 %d 个文件到 %s" % (len(files), outdir), fg="#9BD86F")
        if messagebox.askyesno("导出好了",
                               "%d 个文件已写到：\n%s\n\n要打开文件夹看看吗？" % (len(files), outdir),
                               parent=root):
            try:
                os.startfile(outdir)                           # noqa: S606
            except Exception:                                  # noqa: BLE001
                pass

    bar = tk.Frame(root, bg=BG)
    bar.pack(fill="x", padx=18, pady=12)
    tk.Button(bar, text="导出这些格式 →", command=do_export, bg=ACC, fg="#0B0B09",
              relief="flat", bd=0, font=(FONT, 11, "bold"), padx=26, pady=9).pack(side="right")
    st["open_btn"] = tk.Button(bar, text="换个图…", command=pick_image, bg=PANEL2, fg=TEXT,
                               relief="flat", bd=0, font=(FONT, 9), padx=14, pady=7)
    st["open_btn"].pack(side="right", padx=(0, 10))

    if on_apply is not None:
        def do_apply():
            rep = st.get("rep")
            if not rep:
                messagebox.showinfo("还没有成品", "先选一张图片", parent=root)
                return
            try:
                on_apply(rep["fitted"], rep)
            except Exception as e:                             # noqa: BLE001
                messagebox.showerror("没送回去", str(e), parent=root)
                return
            info.configure(text="已经送回修改器，可以直接注入固件了", fg="#9BD86F")
            messagebox.showinfo("送回去了",
                                "这张就是修改器现在用的背景图。\n"
                                "关掉这个窗口，回去点「注入到固件」就行。",
                                parent=root)

        tk.Button(bar, text=apply_hint + " ←", command=do_apply, bg=PANEL2, fg=ACC,
                  relief="flat", bd=0, font=(FONT, 10, "bold"), padx=16, pady=8).pack(
            side="right", padx=(0, 10))

    def on_resize(_e=None):
        if st.get("rep"):
            schedule()

    root.bind("<Configure>", on_resize)

    apply_preset("superfw")
    if pre_image is not None:
        load_image(pre_image)
    else:
        info.configure(text="先选一张图片。左边选图后，这里会显示它写进 GBA 之后的样子。",
                       fg=DIM)
        draw_thumb()
    if standalone:
        root.mainloop()
    return root


def main(argv):
    if not argv:
        main_gui()
        return 0
    if len(argv) == 1 and os.path.splitext(argv[0])[1].lower() in (
            ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
        main_gui(argv[0])
        return 0
    return main_cli(argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:                                          # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(3)
