# -*- coding: utf-8 -*-
"""SuperFW SD 主题编辑器 —— 只认 `/.superfw/theme.bin`。

和 v0.17 那个「注入固件」的编辑器是两套东西：
  * 这里不做注入、不找补丁点、不动固件、不算 sha256；
  * 打开的是 theme.bin，存出来的还是 theme.bin，格式严格按 `fw/src/sdtheme.h` v1。

界面：左边 6 套配色，中间 240x160 实时预览，右边壁纸 + 17 个图标。

自检（不弹窗）：
    python app/sdedit.py --selftest
"""
import colorsys
import os
import struct
import sys
import tempfile

try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox
except ImportError:  # 无 Tk 环境仍可跑 --selftest
    tk = None
    colorchooser = filedialog = messagebox = None

from PIL import Image, ImageDraw, ImageTk

import fwassets
import fwbake
import sdtheme
import gbafont as F
import wallpaper as WP

from sdedit_ver import APP_VER, APP_TITLE, APP_NAME      # 版本号唯一来源
# 关于页那两行：hash 和日期都取自编译固件时写下的 fw/build_meta.txt，
# 由 tools/build_sdedit_exe.py 生成 app/build_info.py 注进来。
# 源码直接跑（没打包过）时退化成占位符，不影响其它功能。
try:
    from build_info import GIT_HASH as FW_GIT_HASH, BUILD_DATE as FW_BUILD_DATE
except Exception:
    FW_GIT_HASH, FW_BUILD_DATE = "ffffffff", ""
# 和固件 menu.c 的 render_info() 保持一致：原版是 "Chis variant"，改版带 SDT
FW_BUILD_LABEL = ("Chis SDT " + FW_BUILD_DATE) if FW_BUILD_DATE else "Chis variant"

W, H = 240, 160
CONTENT_Y = 16
CONTENT_H = 144
SCALE = 2

# 底栏（路径 / 帮助条 / 状态）在各页的起始屏幕行。数值逐条对过 fw/src/menu.c：
#   render_browser / render_flashbrowser  → 144..159（16 行）
#   render_settings                        → 140..159（20 行）
#   render_info（关于）                    → 138..159（22 行）
#   render_recent / render_ui_settings / render_tools → 没有底栏
# 壁纸占屏幕 16..159 行，所以某一页的壁纸底部会被压掉 (160 - 起始行) 行。
# 顺序是先铺壁纸、后画底栏，而 Mode 4 是 8bpp 索引色没有 alpha，压掉就是真压掉。
PAGE_BAR_TOP = (160, 144, 144, 140, 160, 160, 138)
BAR_COVER_MAX = 160 - min(PAGE_BAR_TOP)        # 22 行（关于页），整套图要按最坏情况算


def bar_cover_rows(page=None):
    """这一页的底栏会压住壁纸底部多少行；page=None 时返回最坏情况。"""
    if page is None:
        return BAR_COVER_MAX
    return 160 - PAGE_BAR_TOP[page % len(PAGE_BAR_TOP)]

# ---------------------------------------------------------------- 界面配色
BG = "#0B0B09"
PANEL = "#131311"
PANEL2 = "#191916"
LINE = "#2A2A25"
TEXT = "#ECE8DE"
MUTED = "#8C8779"
DIM = "#5D594F"
ACC = "#D9FF57"
WARN = "#FF6B57"

# (代码, 一句话, 到底影响哪些地方)
# 第三列是给用户看的：6 个槽位里有几个光看名字猜不出来，写清楚省得试。
ROLES = [
    ("FG", "顶栏 / 底栏",
     "顶栏、底栏、弹窗边框、载入进度条"),
    ("BG", "内容区 / 整屏底",
     "内容区；游戏内菜单和载入画面默认也用它"),
    ("FT", "所有文字",
     "菜单和文件名的字色（图标在 ④ 里单独调）"),
    ("HI", "实心按钮",
     "选中/实心按钮的内部填充，边框仍是 FG"),
    ("HIB", "选中条 / 高亮",
     "列表选中行、顶栏图标底、游戏内选中条"),
    ("SH", "游戏内菜单置灰项",
     "游戏内菜单里灰掉、点不了的那几项"),
]

# 整屏界面的三格（v5 扩展色）。它们不跟着主菜单走，只影响「载入游戏那屏」和
# 「L+R 菜单」，所以单独分一组；默认「跟随」= 等于这一套的 BG / FG，文件里不写。
EXCOL_ROWS = [
    ("载入画面底", "载入游戏时那一屏的底色"),
    ("载入进度条", "那条进度条的颜色"),
    ("游戏内菜单底", "L+R 调出的菜单整屏底色"),
]
EXCOL_FOLLOW = ("BG", "FG", "BG")      # 和 Doc.excol_of 的默认值一致

# ---------------------------------------------------------------- Logo
# 内置 logo 是 31x7、3 个索引色：0=白 1=红 2=黑（固件里 logo_img[i][j] + 1 写进调色板）。
LOGO_PALETTE = [(255, 255, 255), (156, 0, 0), (0, 0, 0)]
_LOGO_CACHE = None


def builtin_logo():
    """(217 字节索引, 3 个 RGB) —— 从内嵌的 logo.png 反推，和固件里的数据一致。"""
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    im = F.logo().convert("RGB")
    idx = bytearray()
    for y in range(sdtheme.LOGO_H):
        for x in range(sdtheme.LOGO_W):
            px = im.getpixel((x, y))
            k = min(range(3), key=lambda i: sum((px[j] - LOGO_PALETTE[i][j]) ** 2
                                                for j in range(3)))
            idx.append(k)
    _LOGO_CACHE = (bytes(idx), list(LOGO_PALETTE))
    return _LOGO_CACHE


def logo_from_image(im):
    """任意图 → (217 字节索引, 3 个颜色)：缩到 31x7 再自适应量化成 3 色。"""
    small = im.convert("RGB").resize((sdtheme.LOGO_W, sdtheme.LOGO_H), Image.NEAREST)
    p = small.convert("P", palette=Image.ADAPTIVE, colors=3)
    idx = bytes(p.tobytes())
    raw = list(p.getpalette() or [])
    pal = []
    for i in range(3):
        c = raw[3 * i:3 * i + 3]
        pal.append(tuple(c) if len(c) == 3 else (0, 0, 0))
    return idx, pal


def logo_to_image(idx, pal, scale=1):
    """217 字节索引 + 3 色 → 31x7 的 RGB 图（可选放大，最近邻）。"""
    im = Image.new("RGB", (sdtheme.LOGO_W, sdtheme.LOGO_H))
    px = im.load()
    for y in range(sdtheme.LOGO_H):
        for x in range(sdtheme.LOGO_W):
            px[x, y] = tuple(pal[idx[y * sdtheme.LOGO_W + x] % 3])
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    return im


ICON_ORDER = list(getattr(fwassets, "ICON_ORDER", []))
ICON_NAMES = {
    "FOLDER": "文件夹", "BINFILE": "bin 文件", "UPDFILE": "fw 升级",
    "GBCART": "GB 卡带", "GBCCART": "GBC 卡带", "GBACART": "GBA 卡带",
    "SMSCART": "SMS 卡带", "NESCART": "NES 卡带", "RECENT": "最近游玩",
    "DISK": "卡带浏览", "FLASH": "NOR 闪存", "SETTINGS": "设置",
    "UILANG_SETTINGS": "界面语言", "TOOLS": "工具", "INFO": "关于",
    "HFOLDER": "隐藏文件夹", "HFILE": "隐藏文件",
}


# ---------------------------------------------------------------- 图标编解码
def _blank_icons():
    return [Image.new("RGBA", (16, 16), (0, 0, 0, 0)) for _ in range(17)]


def icons_from_raw(sprite, pal):
    """4352 字节精灵 + 38 色调色板 → 17 张 16x16 RGBA。

    索引映射 **pal[v]**（v = 精灵像素值）：固件把 icons_pal 整块拷到
    `&MEM_PALETTE[256]`（menu.c:2146），硬件按 OBJ 调色板槽号取值，
    所以像素值 v 就取 pal[v]，槽 0 恒为透明。别写成 pal[v-1]——那会让
    整套图标颜色整体错一位（黄文件夹变橙、金边变灰）。
    """
    imgs = []
    for i in range(17):
        im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        px = im.load()
        base = i * 256
        for t in range(4):
            ox = (t & 1) * 8
            oy = (t >> 1) * 8
            for y in range(8):
                for x in range(8):
                    v = sprite[base + t * 64 + y * 8 + x]
                    if v == 0:
                        continue
                    c = sdtheme.rgb555(pal[v]) if v < len(pal) else (255, 0, 255)
                    px[ox + x, oy + y] = (c[0], c[1], c[2], 255)
        imgs.append(im)
    return imgs


def _nearest(c, palette):
    best, bi = 1 << 30, 0
    for i, p in enumerate(palette):
        d = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2
        if d < best:
            best, bi = d, i
    return bi


def default_icons():
    """内置 17 个图标，就是固件里那一份，不做任何替换。"""
    return icons_from_raw(bytes(fwassets.ICONS_IMG), list(fwassets.ICONS_PAL))


def icons_to_raw(imgs):
    """17 张 16x16 RGBA → (4352 字节精灵, [38 个 RGB555])。

    和固件 / v0.17 编辑器同一套规则：索引 0 = 透明，颜色写到 1..37，
    `_quantize` 出的是 nearest+1，所以调色板第 0 项固定占位。
    """
    sheet = Image.new("RGBA", (272, 16), (0, 0, 0, 0))
    for i in range(17):
        ic = (imgs[i] if i < len(imgs) else _blank_icons()[0]).copy()
        px = ic.load()
        for y in range(16):
            for x in range(16):
                r, g, b, a = px[x, y]
                # 不在这里做 8 对齐：已经落在 RGB555 网格上的颜色再对齐会整体偏一档，
                # 读进来一个像素没改、存出去整套图标都变色。只在超过 37 色时量化。
                px[x, y] = (r, g, b, 255) if a > 128 else (0, 0, 0, 0)
        sheet.alpha_composite(ic, (i * 16, 0))

    colors = sorted({sheet.getpixel((x, y))[:3]
                     for y in range(16) for x in range(272)
                     if sheet.getpixel((x, y))[3] > 128})
    if len(colors) > 37:
        flat = Image.new("RGB", (272, 16), (0, 0, 0))
        for i in range(17):
            for y in range(16):
                for x in range(16):
                    r, g, b, a = sheet.getpixel((i * 16 + x, y))
                    if a > 128:
                        flat.putpixel((i * 16 + x, y), (r, g, b))
        q = flat.quantize(colors=37, method=Image.MEDIANCUT)
        p = q.getpalette()[:37 * 3]
        colors = [tuple(p[i * 3:i * 3 + 3]) for i in range(37)]
    if not colors:
        colors = [(0, 0, 0)]

    sprite = bytearray(sdtheme.ICON_BYTES)
    for i in range(17):
        for t in range(4):
            ox = (t & 1) * 8
            oy = (t >> 1) * 8
            for y in range(8):
                for x in range(8):
                    p = sheet.getpixel((i * 16 + ox + x, oy + y))
                    v = 0 if p[3] <= 128 else _nearest(p[:3], colors) + 1
                    sprite[i * 256 + t * 64 + y * 8 + x] = v

    # 索引 0 = 透明（GBA OBJ 调色板槽 0 恒透明），颜色从 1 开始；
    # 精灵里的值就是槽号，所以调色板第 0 项必须是占位 0。
    # 参考数据（res/iconcv.py）：`pal.index(col) + 1` 配 `0x0000` 打头的 icons_pal。
    pal = [0] + [sdtheme.color555(c) for c in colors]
    pal += [0] * (sdtheme.ICON_PAL_N - len(pal))
    return bytes(sprite), pal[:sdtheme.ICON_PAL_N]


def _fit_icon(im):
    """任意图片 → 16x16：等比缩放后居中，保留透明。"""
    im = im.convert("RGBA")
    w, h = im.size
    s = min(16.0 / max(1, w), 16.0 / max(1, h))
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    im = im.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    out.alpha_composite(im, ((16 - nw) // 2, (16 - nh) // 2))
    return out


# ---------------------------------------------------------------- 壁纸解码
def _wall_to_rgb(idx, pal):
    """theme.bin 里的 8bpp 索引 + RGB555 调色板 → 240x144 RGB 图。

    文件的索引值是调色板槽号（20..239，即 PAL_FIRST 之后），pal 是那 220 项。
    走 P 模式 + putpalette 一次性转换，比逐像素点 Python 循环快一个量级
    （v2 有 6 张图，逐像素要 20 万次）。
    """
    # 槽 0..19 / 240..255 不该出现（写文件时索引统一 +20），真出现就画洋红，
    # 一眼能看出文件不对，而不是悄悄变成一块黑。
    lut = [255, 0, 255] * 256
    for k, c in enumerate(pal or []):
        slot = 20 + k
        if slot > 255:
            break
        r, g, b = sdtheme.rgb555(c)
        lut[slot * 3:slot * 3 + 3] = [r, g, b]
    im = Image.frombytes("P", (sdtheme.WALL_W, sdtheme.WALL_H), bytes(idx))
    im.putpalette(lut)
    return im.convert("RGB")


def expand_squashed_wall(im, rows):
    """老文件迁移：把「压进可见行」的壁纸还原成铺满 144 行。

    v0.36~v0.38 存的是「压进可见行、底下填底色」。这么做在底栏实心时没问题，
    但底栏一开半透明就露出底色；而 v0.38 那套「开透明就压/放」会自动改图，
    用户拖一下滑块壁纸就变了 —— v0.39 起**一律存满 144 行**，这个函数只在
    打开老工程 / 老 theme.bin 时用一次。

    只把上面 144-rows 行放大回 144 行（比例正好还原），不裁不补。
    rows = 0（这一页没有底栏）时原样返回。
    """
    if im is None or not rows or rows <= 0:
        return im
    rows = min(int(rows), sdtheme.WALL_H - 1)
    vis = sdtheme.WALL_H - rows
    return im.convert("RGB").crop((0, 0, sdtheme.WALL_W, vis)).resize(
        (sdtheme.WALL_W, sdtheme.WALL_H), Image.LANCZOS)


# ---------------------------------------------------------------- 撤销栈
class UndoStack:
    """多步撤销栈。只在「真要动 doc 之前」压一份快照。

    为什么快照可以只存引用：编辑器里所有 PIL 图对象都是**整个替换**，没有一处
    原地改（`render_preview` 只往新建的画布上 paste，`wall_bytes` 用 `convert()`
    拿新对象）。所以「引用」等于「值」，一步快照只有几十 KB 的容器壳；
    42 张页面图全配满时也不会因为栈深变深而吃内存。
    """

    def __init__(self, limit=24):
        self.limit = limit
        self._items = []

    def push(self, label, state):
        self._items.append((label, state))
        while len(self._items) > self.limit:
            del self._items[0]

    def pop(self):
        return self._items.pop() if self._items else None

    def clear(self):
        self._items = []

    def label(self, back=1):
        if len(self._items) >= back:
            return self._items[-back][0]
        return None

    def __len__(self):
        return len(self._items)


class _ThinBar(tk.Canvas):
    """6 像素宽的自绘滚动条：没有箭头、颜色贴着底色走。

    系统滚动条（tk.Scrollbar）在深色界面上是一条很扎眼的浅灰长条 —— 用户直接
    问「这个是干嘛的」。功能是需要的（小屏时两栏会超出，没它就点不到底下的按钮），
    所以换个不抢眼的样子：轨道就是底色，滑块用 LINE 画一条细柱，
    **内容不需要滚的时候整条什么都不画**。
    """

    def __init__(self, master, view, bg=PANEL, width=6):
        tk.Canvas.__init__(self, master, width=width, bg=bg, highlightthickness=0,
                           bd=0)
        self.view = view
        self._bw = width          # 注意：不能叫 _w —— 那是 tkinter 的窗口路径名
        self._first, self._last = 0.0, 1.0
        self._drag = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag_to)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._draw()

    def _need(self):
        return (self._last - self._first) < 0.999

    def _geom(self):
        h = max(1, self.winfo_height())
        y0 = int(round(self._first * h))
        y1 = int(round(self._last * h))
        if y1 - y0 < 24:                 # 太短了不好抓，给个最小高度
            y1 = min(h, y0 + 24)
        return h, y0, y1

    def _draw(self):
        self.delete("all")
        if not self._need():
            return
        _h, y0, y1 = self._geom()
        self.create_rectangle(1, y0, self._bw - 1, y1, fill=LINE, outline="")

    def _press(self, e):
        if not self._need():
            return
        _h, y0, y1 = self._geom()
        if y0 <= e.y <= y1:
            self._drag = e.y - y0
        else:
            self._drag = (y1 - y0) // 2
            self._drag_to(e)

    def _drag_to(self, e):
        if self._drag is None:
            return
        h, y0, y1 = self._geom()
        span = max(1, h - (y1 - y0))
        frac = (e.y - self._drag) / float(span)
        self.view.moveto(max(0.0, min(1.0, frac)))


class _Scroll(tk.Frame):
    """定宽 + 竖向滚动的容器。

    小屏（1366×768、或 125% 缩放）时左右两栏的内容会比窗口高，
    以前是直接被裁掉 —— 底部那几个按钮点不到。套一层滚动条兜住。
    滚轮由 App._on_wheel 统一分发（贴在子控件上的 bind 收不到）。
    """

    def __init__(self, master, width, bg=PANEL):
        tk.Frame.__init__(self, master, bg=bg, width=width)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=width - 8)
        self.vs = _ThinBar(self, self.canvas, bg=bg, width=6)
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vs.pack(side="right", fill="y")
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._sync)

    def _sync(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.itemconfigure(self._win, width=max(1, self.canvas.winfo_width()))

    def scroll(self, units):
        """只有内容真的超出时才滚，否则把滚轮让给别人。"""
        box = self.canvas.bbox("all")
        if not box or box[3] - box[1] <= self.canvas.winfo_height() + 1:
            return False
        self.canvas.yview_scroll(units, "units")
        return True


# ---------------------------------------------------------------- 文档
class Doc:
    """一份 theme.bin 的内存形态。

    为了「打开再保存 = 逐字节不变」，图标和壁纸都保留原始字节，
    只有真的被改动过（dirty）才重新量化。
    """

    def __init__(self):
        self.path = None
        self.ver = 1                  # 读进来的文件版本（新建 = 1）
        self.pals = [[sdtheme.rgb555(c) for c in t] for t in
                     ([0x56B5, 0x7FFF, 0x0000, 0x6739, 0x5E73, 0x4638],)]
        self.pals = [[sdtheme.rgb555(c) for c in row] for row in
                     [[0x56B5, 0x7FFF, 0x0000, 0x6739, 0x5E73, 0x4638],
                      [0x2A9D, 0x4B1C, 0x0000, 0x3E97, 0x3612, 0x2D6C],
                      [0x4E04, 0x5ED1, 0x0000, 0x524A, 0x49E3, 0x420D],
                      [0x2A26, 0x4EB1, 0x0000, 0x462E, 0x39CE, 0x318C],
                      [0x6455, 0x79FC, 0x0000, 0x6175, 0x490E, 0x3D8E],
                      [0x1084, 0x2108, 0x77BD, 0x39CE, 0x56B5, 0x318C]]]
        raw = (bytes(fwassets.ICONS_IMG), list(fwassets.ICONS_PAL))
        self.icon_raw = [raw] * 6
        # 新建就用固件原版图标；不标脏，保存时原样写回去
        self.icon_imgs = [default_icons() for _ in range(6)]
        self.icon_dirty = [False] * 6
        # 壁纸按套存：每套可以各有一张，没放的套用 BG 纯色
        self.wall_rgb = [None] * 6
        self.wall_idx = [None] * 6
        self.wall_pal = [None] * 6
        self.wall_dirty = [False] * 6
        # 页面图：6 套 × 7 页，每格独立。没设就跟这一套的默认图（v4 起真正的按套隔离；
        # 存盘时如果 6 套同一个页面完全一样，会自动合并回 v3 的全局段，文件不白涨）
        self.sp_rgb = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        self.sp_idx = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        self.sp_pal = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        self.sp_dirty = [[False] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        # 整屏界面的独立颜色（v5）：每套 3 个 —— 载入底、进度条、游戏内菜单底。
        # None = 跟随这一套的 BG/FG，不占文件空间；改了才升 v5（+36 字节）。
        self.excol = [[None] * sdtheme.EXCOL_N for _ in range(sdtheme.THEME_COUNT)]
        # Logo（v6）：217 字节索引 + 3 个颜色，默认就是内置那个
        self.logo_img, self.logo_pal = builtin_logo()
        # 界面透明（v7）：每套 [底栏, 选中条]，0..16，16 = 全透明、0 = 实心。
        # 全 0 = 不写进文件（和 v7 之前的行为一样，老固件照样能读）。
        self.alpha = [[0] * sdtheme.ALPHA_N for _ in range(sdtheme.THEME_COUNT)]
        # 壁纸一律**铺满 240×144** 存（v0.39 起）。v0.36~v0.38 那套「按可见行压扁、
        # 底栏一开透明再自动放大」会改图，拖个滑块壁纸就变了，已经删掉。
        # 老工程打开时由 _migrate_wall_full() 展开一次，之后图再也不动。
        # 选中条纹理（v8）：每套 None 或 (256 字节 tile, [n 个颜色])。
        # tile 像素值 0 = 自动用本套 HIB，1..n 取那个颜色表；没有透明像素。
        self.hib = [None] * sdtheme.THEME_COUNT

    def hib_of(self, i):
        """第 i 套的纹理 (tile, colors)；没有就 None。"""
        if not (0 <= i < len(self.hib)):
            return None
        return self.hib[i]

    def has_hib(self):
        return any(h is not None for h in self.hib)

    def set_hib(self, i, tile, colors):
        """装/换一套纹理；tile 是 256 字节、值 0..len(colors)；colors 是 RGB 三元组。"""
        if not (0 <= i < sdtheme.THEME_COUNT):
            return
        if tile is None:
            self.hib[i] = None
            return
        tile = bytes(tile)
        colors = [tuple(c) for c in colors]
        if len(tile) != sdtheme.HIB_TILE_BYTES:
            raise ValueError("纹理应为 %d 字节，实际 %d"
                             % (sdtheme.HIB_TILE_BYTES, len(tile)))
        if not 1 <= len(colors) <= sdtheme.HIB_PAL_MAX:
            raise ValueError("纹理只允许 1..%d 色，实际 %d"
                             % (sdtheme.HIB_PAL_MAX, len(colors)))
        if max(tile) > len(colors):
            raise ValueError("纹理里有超出配色的像素值")
        self.hib[i] = (tile, colors)

    def excol_of(self, i):
        """第 i 套实际生效的扩展色：(载入底, 进度条, 游戏内菜单底)。"""
        bg, fg = self.pals[i][1], self.pals[i][0]
        dflt = (bg, fg, bg)
        row = self.excol[i] if 0 <= i < len(self.excol) else [None] * 3
        return tuple(row[j] if row[j] is not None else dflt[j] for j in range(3))

    def has_excol(self):
        return any(v is not None for row in self.excol for v in row)

    def has_logo(self):
        """和内置 logo 不一样才算改过（改过才升 v6）。"""
        img, pal = builtin_logo()
        return (bytes(self.logo_img) != img
                or [tuple(c) for c in self.logo_pal] != [tuple(c) for c in pal])

    def reset_logo(self):
        self.logo_img, self.logo_pal = builtin_logo()

    def has_alpha(self):
        return any(any(row) for row in self.alpha)

    def alpha_of(self, i, which):
        """第 i 套的某项透明度（已夹在 0..16）。"""
        if not (0 <= i < len(self.alpha)):
            return 0
        row = self.alpha[i] or []
        if not (0 <= which < len(row)):
            return 0
        return max(0, min(sdtheme.ALPHA_MAX, int(row[which] or 0)))

    # ------------------------------------------------------ 壁纸槽位访问
    def wall_rows_of(self, page=None):
        """老文件里这张图当初是按几行压的（只给迁移用）。"""
        return BAR_COVER_MAX if page is None else bar_cover_rows(page)

    def wall_arrays(self, kind, set_i=0):
        """kind = "set"（按套）或 "page"（这一套的某一页）。"""
        if kind == "page":
            s = set_i if 0 <= set_i < sdtheme.THEME_COUNT else 0
            return self.sp_rgb[s], self.sp_idx[s], self.sp_pal[s], self.sp_dirty[s]
        return self.wall_rgb, self.wall_idx, self.wall_pal, self.wall_dirty

    # ------------------------------------------------------------ 读写
    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            data = fh.read()
        return cls.from_bytes(data, path)

    @classmethod
    def from_bytes(cls, data, path=None):
        r = sdtheme.parse(data)
        self = cls.__new__(cls)
        self.path = path
        self.ver = r["version"]
        self.pals = [list(p) for p in r["pals"]]
        self.icon_raw = [(r["icons"][i][0], list(r["icons"][i][1])) for i in range(6)]
        self.icon_imgs = [icons_from_raw(*self.icon_raw[i]) for i in range(6)]
        self.icon_dirty = [False] * 6
        self.wall_rgb = [None] * 6
        self.wall_idx = list(r["walls"])
        self.wall_pal = [list(p) if p is not None else None for p in r["wall_pals"]]
        for i in range(6):
            if self.wall_idx[i] is None:
                continue
            self.wall_rgb[i] = _wall_to_rgb(self.wall_idx[i], self.wall_pal[i])
        self.wall_dirty = [False] * 6

        n = sdtheme.PAGE_COUNT
        self.sp_rgb = [[None] * n for _ in range(sdtheme.THEME_COUNT)]
        self.sp_idx = [[None] * n for _ in range(sdtheme.THEME_COUNT)]
        self.sp_pal = [[None] * n for _ in range(sdtheme.THEME_COUNT)]
        self.sp_dirty = [[False] * n for _ in range(sdtheme.THEME_COUNT)]
        # v5 的整屏界面颜色：文件里有就全当「已设置」（保证打开再存逐字节一致）；
        # 没有就全部 None = 跟随这一套的 BG/FG。
        lg = r.get("logo")
        if lg:
            self.logo_img, self.logo_pal = bytes(lg[0]), [tuple(c) for c in lg[1]]
        else:
            self.logo_img, self.logo_pal = builtin_logo()

        ex = r.get("extra_cols")
        self.excol = [[None] * sdtheme.EXCOL_N for _ in range(sdtheme.THEME_COUNT)]
        if ex:
            # 等于「跟随 BG/FG」的格子存成 None：界面上能看出哪几格是真自定义的，
            # 全部都是默认值时存回去还会掉回 v1（少 36 字节）。
            for s_ in range(sdtheme.THEME_COUNT):
                dflt = (self.pals[s_][1], self.pals[s_][0], self.pals[s_][1])
                for j in range(sdtheme.EXCOL_N):
                    v = tuple(ex[s_][j])
                    self.excol[s_][j] = None if v == dflt[j] else v

        # v7 的界面透明度。没有就是全 0（= 实心，v7 之前的行为）。
        self.alpha = [[0] * sdtheme.ALPHA_N for _ in range(sdtheme.THEME_COUNT)]
        al = r.get("alphas")
        if al:
            for s_ in range(min(sdtheme.THEME_COUNT, len(al))):
                for j in range(sdtheme.ALPHA_N):
                    if j < len(al[s_]):
                        self.alpha[s_][j] = max(0, min(sdtheme.ALPHA_MAX,
                                                       int(al[s_][j] or 0)))
        # v8 的选中条纹理（每套一块 16×16）
        self.hib = [None] * sdtheme.THEME_COUNT
        ht = r.get("hib_tiles") or []
        for s_ in range(min(sdtheme.THEME_COUNT, len(ht))):
            if ht[s_]:
                self.hib[s_] = (bytes(ht[s_][0]), [tuple(c) for c in ht[s_][1]])

        # v3 的全局页图（6 套共用）先铺到 6 套；v4 的按套页图再覆盖。
        # v4 文件可以同时带这两层：只用 sp_w 会丢掉全局页图（切套/切页回退时
        # 会突然变成主题底图或纯色）。
        gw = r.get("page_walls") or [None] * n
        gp = r.get("page_pals") or [None] * n
        for p in range(n):
            if gw[p] is None:
                continue
            im = _wall_to_rgb(gw[p], gp[p])      # 只解码一次，6 套共用同一个对象
            for s in range(sdtheme.THEME_COUNT):
                self.sp_idx[s][p] = gw[p]
                self.sp_pal[s][p] = list(gp[p])
                self.sp_rgb[s][p] = im

        sp_w = r.get("set_page_walls")
        if sp_w:
            for s in range(sdtheme.THEME_COUNT):
                for p in range(n):
                    if sp_w[s][p] is None:
                        continue
                    self.sp_idx[s][p] = sp_w[s][p]
                    self.sp_pal[s][p] = list(r["set_page_pals"][s][p])
                    self.sp_rgb[s][p] = _wall_to_rgb(self.sp_idx[s][p], self.sp_pal[s][p])
        # 老文件里壁纸是「压进可见行」的，统一展开成铺满 144 行（只做一次）
        if not r.get("wall_full"):
            self._migrate_wall_full()
        return self

    def _migrate_wall_full(self):
        """把老文件里「压进可见行」的壁纸展开成铺满 240×144。

        v0.36~v0.38 存的是压扁版（底栏那几行填底色）；v0.39 起一律铺满，
        所以打开老文件要还原一次。还原后置 wall_dirty，保存时会重新量化。

        靠 bit14（FLAG_FULL_WALL）区分新旧：v0.39 存过的文件带这一位，不会再被
        展开第二次 —— 否则每次打开都拉长 18%，越开越糊。
        """
        for s_ in range(sdtheme.THEME_COUNT):
            w = self.wall_rgb[s_]
            if w is not None:
                self.wall_rgb[s_] = expand_squashed_wall(w, BAR_COVER_MAX)
                self.wall_dirty[s_] = True
            for p in range(sdtheme.PAGE_COUNT):
                rows = bar_cover_rows(p)
                if rows <= 0 or self.sp_rgb[s_][p] is None:
                    continue
                self.sp_rgb[s_][p] = expand_squashed_wall(self.sp_rgb[s_][p], rows)
                self.sp_dirty[s_][p] = True

    def icon_set_bytes(self, i):
        if not self.icon_dirty[i]:
            s, p = self.icon_raw[i]
            blk = bytearray(sdtheme.ICON_STRIDE)
            blk[:len(s)] = s
            struct.pack_into("<%dH" % sdtheme.ICON_PAL_N, blk, sdtheme.ICON_BYTES, *p)
            return bytes(blk)
        self.icon_raw[i] = icons_to_raw(self.icon_imgs[i])
        self.icon_dirty[i] = False
        return self.icon_set_bytes(i)

    def wall_bytes(self, i, kind="set", set_i=0):
        """返回第 i 个壁纸槽的 (索引字节, 调色板)；没设就是 (None, None)。"""
        rgb, idx, pal, dirty = self.wall_arrays(kind, set_i)
        if rgb[i] is None:
            return None, None
        if not dirty[i] and idx[i] is not None:
            return idx[i], pal[i]
        fitted = rgb[i].convert("RGB")
        if fitted.size != (sdtheme.WALL_W, sdtheme.WALL_H):
            fitted = WP.fit_image(fitted, mode="cover")
        ind, p, _used = WP.quantize(fitted, colors=fwbake.PAL_N)
        idx[i] = fwbake.map_wall_indices(ind)
        pal[i] = fwbake.slice_wall_palette(p)
        dirty[i] = False
        rgb[i] = fitted
        return idx[i], pal[i]

    def wall_count(self):
        return sum(1 for w in self.wall_rgb if w is not None)

    def page_count(self):
        """全文件一共配了多少张「某套某页」的专属图（0..42）。"""
        return sum(1 for s in range(sdtheme.THEME_COUNT)
                   for p in range(sdtheme.PAGE_COUNT) if self.sp_rgb[s][p] is not None)

    def page_count_set(self, s):
        """第 s 套里配了几页（0..7）。"""
        return sum(1 for p in range(sdtheme.PAGE_COUNT) if self.sp_rgb[s][p] is not None)

    def page_split(self):
        """把 6×7 摊成 (能 6 套共用的全局页号, 必须按套存的页号)。

        某一页在 6 套里都是同一张 → 可以退回 v3 的全局段，省 5 段；
        只要有一套不一样（或有套没图）→ 这一页只能按套存（v4）。
        """
        glob, per = [], []
        for p in range(sdtheme.PAGE_COUNT):
            vals = [self.sp_rgb[s][p] for s in range(sdtheme.THEME_COUNT)]
            if all(v is None for v in vals):
                continue
            if all(v is not None for v in vals):
                first = vals[0].tobytes()
                if all(vals[s].tobytes() == first for s in range(1, sdtheme.THEME_COUNT)):
                    glob.append(p)
                    continue
            per.append(p)
        return glob, per

    def packed_size(self):
        """保存出来会是多大（不真正编码，只按布局算）。"""
        n = self._base_size()
        # 尾巴按从长到短判断：v8 的尾巴包含 v7/v6/v5 的全部内容
        if self.has_hib():
            return n + sdtheme.V8_TAIL          # 1993 = v7 尾巴 + 6×287 纹理
        if self.has_alpha():
            return n + sdtheme.V7_TAIL          # 271 = 36 扩展色 + 223 logo + 12 透明度
        if self.has_logo():
            return n + sdtheme.V6_TAIL          # 259 = 36 扩展色 + 223 logo
        return n + (sdtheme.EXCOL_BYTES if self.has_excol() else 0)

    def _base_size(self):
        glob, per = self.page_split()
        if per:
            return sdtheme.FILE_SIZE_V4
        if glob:
            return sdtheme.FILE_SIZE_V3
        n = self.wall_count()
        if n == 0:
            return sdtheme.ICONS_ONLY_SIZE
        if n < 6:
            return sdtheme.FILE_SIZE_V2      # 有缺的套 → 只能 v2
        first = self.wall_rgb[0].tobytes()
        same = all(self.wall_rgb[i].tobytes() == first for i in range(1, 6))
        return sdtheme.FILE_SIZE if same else sdtheme.FILE_SIZE_V2

    def to_bytes(self):
        walls, wpals = [], []
        for i in range(6):
            w, p = self.wall_bytes(i)
            walls.append(w)
            wpals.append(p)
        # 页面图：能共用的走 v3 的全局段，必须区分的走 v4 的按套段
        glob, per = self.page_split()
        pw, pp = [None] * sdtheme.PAGE_COUNT, [None] * sdtheme.PAGE_COUNT
        for i in glob:
            pw[i], pp[i] = self.wall_bytes(i, "page", 0)
        spw = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        spp = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        for i in per:
            for s in range(sdtheme.THEME_COUNT):
                if self.sp_rgb[s][i] is not None:
                    spw[s][i], spp[s][i] = self.wall_bytes(i, "page", s)
        icons = [(self.icon_set_bytes(i)[:sdtheme.ICON_BYTES],
                  struct.unpack_from("<%dH" % sdtheme.ICON_PAL_N,
                                     self.icon_set_bytes(i), sdtheme.ICON_BYTES))
                 for i in range(6)]
        return sdtheme.pack(self.pals, icons, walls=walls, wall_pals=wpals,
                            page_walls=pw, page_pals=pp,
                            set_page_walls=spw, set_page_pals=spp,
                            extra_cols=self.excol if self.has_excol() else None,
                            logo=(self.logo_img, self.logo_pal) if self.has_logo() else None,
                            alphas=self.alpha if self.has_alpha() else None,
                            hib_tiles=self.hib if self.has_hib() else None,
                            wall_full=True)

    def save(self, path):
        data = self.to_bytes()
        with open(path, "wb") as fh:
            fh.write(data)
        self.path = path
        return data

    # ------------------------------------------------------------ 快照
    # 给撤销栈用。除了 dict/list 这些容器要复制，其余（bytes、PIL 图）都存引用，
    # 因为编辑器从不原地修改图片 —— 详见 UndoStack 的说明。
    def snapshot(self):
        return {
            "path": self.path,
            "ver": self.ver,
            "pals": [list(r) for r in self.pals],
            "icon_raw": list(self.icon_raw),
            "icon_imgs": [list(s) for s in self.icon_imgs],
            "icon_dirty": list(self.icon_dirty),
            "wall_rgb": list(self.wall_rgb),
            "wall_idx": list(self.wall_idx),
            "wall_pal": [None if p is None else list(p) for p in self.wall_pal],
            "wall_dirty": list(self.wall_dirty),
            "sp_rgb": [list(r) for r in self.sp_rgb],
            "sp_idx": [list(r) for r in self.sp_idx],
            "sp_pal": [[None if p is None else list(p) for p in row]
                       for row in self.sp_pal],
            "sp_dirty": [list(r) for r in self.sp_dirty],
            "excol": [list(r) for r in self.excol],
            "logo_img": self.logo_img,
            "logo_pal": [tuple(c) for c in self.logo_pal],
            "alpha": [list(r) for r in self.alpha],
            "hib": list(self.hib),
        }

    def restore(self, s):
        self.path = s["path"]
        self.ver = s["ver"]
        self.pals = [list(r) for r in s["pals"]]
        self.icon_raw = list(s["icon_raw"])
        self.icon_imgs = [list(r) for r in s["icon_imgs"]]
        self.icon_dirty = list(s["icon_dirty"])
        self.wall_rgb = list(s["wall_rgb"])
        self.wall_idx = list(s["wall_idx"])
        self.wall_pal = [None if p is None else list(p) for p in s["wall_pal"]]
        self.wall_dirty = list(s["wall_dirty"])
        self.sp_rgb = [list(r) for r in s["sp_rgb"]]
        self.sp_idx = [list(r) for r in s["sp_idx"]]
        self.sp_pal = [[None if p is None else list(p) for p in row]
                       for row in s["sp_pal"]]
        self.sp_dirty = [list(r) for r in s["sp_dirty"]]
        self.excol = [list(r) for r in s["excol"]]
        self.logo_img = s["logo_img"]
        self.logo_pal = [tuple(c) for c in s["logo_pal"]]
        self.alpha = [list(r) for r in s.get("alpha", [[0] * sdtheme.ALPHA_N] * 6)]
        self.hib = list(s.get("hib", [None] * sdtheme.THEME_COUNT))


# ---------------------------------------------------------------- 预览
# 主菜单 7 个标签页之外、但同样吃这套配色的界面。只读预览，用来核对颜色。
EXTRA_SCREENS = [
    ("ingame",    "游戏内菜单"),
    ("rominfo",   "载入信息"),
    ("patchopts", "补丁选项"),
    ("loadopts",  "载入选项"),
    ("norwrite",  "烧录 NOR"),
    ("filemgr",   "文件管理"),
    ("loading",   "载入进度"),
]

# 每个界面到底吃哪几格颜色。固件里的依据写后面，改颜色时照着看。
EXTRA_USES = {
    "ingame":   "整屏底 = BG · 可用文字 = FT · 置灰项 = SH · 选中条 = HIB",
    "rominfo":  "弹窗底 = BG · 框线/三角 = FG · 按钮实心底 = HI · 文字 = FT",
    "patchopts": "弹窗底 = BG · 框线/三角 = FG · 文字 = FT",
    "loadopts":  "弹窗底 = BG · 框线/磁盘图标 = FG · 文字 = FT",
    "norwrite": "底图 = 当前页壁纸 · 框线 = FG · 按钮实心底 = HI · 文字 = FT",
    "filemgr":  "底图 = 当前页壁纸 · 框线 = FG · 选中按钮底 = HI · 文字 = FT",
    "loading":  "底图 = 当前页的图 · 底色/进度条 = 整屏界面里的那两格",
}


def _box(im, l, r, t, b, edge, fill=None):
    """照 menu.c 的 draw_box_outline / draw_box_full：上下各 2px、左右各 1px。"""
    if fill is not None:
        im.paste(fill, (l + 2, t + 2, r - 2, b - 2))
    im.paste(edge, (l, t, r, t + 2))
    im.paste(edge, (l, b - 2, r, b))
    im.paste(edge, (l, t, l + 1, b))
    im.paste(edge, (r - 2, t, r - 1, b))


def _blend(c1, c2, k=0.5):
    return tuple(int(c1[i] * k + c2[i] * (1 - k)) for i in range(3))


# 和固件 render_bar() / paint_sel_band() 完全同一张 4×4 Bayer 矩阵。
# 真机的半透明**不是均匀混合**：帧缓冲是 8bpp 索引色，没有 alpha 通道，
# 只能按这个矩阵「隔几个像素透出来」，所以看起来是网点状。
# 预览以前用 Image.blend() 做均匀混合，比真机好看太多 —— 用户的原话是
# 「程序上设置成啥样，实机跑达不到那个效果」，根子就在这里。
_BAYER4 = ((0, 8, 2, 10),
           (12, 4, 14, 6),
           (3, 11, 1, 9),
           (15, 7, 13, 5))

# 8×8 版本：灰阶多 4 倍，网点更细。选中条用它（固件 paint_sel_band 用的就是这张），
# 底栏还是 4×4（固件 render_bar 那条路径为了性能没换）。
_BAYER8 = ((0, 32,  8, 40,  2, 34, 10, 42),
           (48, 16, 56, 24, 50, 18, 58, 26),
           (12, 44,  4, 36, 14, 46,  6, 38),
           (60, 28, 52, 20, 62, 30, 54, 22),
           (3, 35, 11, 43,  1, 33,  9, 41),
           (51, 19, 59, 27, 49, 17, 57, 25),
           (15, 47,  7, 39, 13, 45,  5, 37),
           (63, 31, 55, 23, 61, 29, 53, 21))


def _noise(x, y):
    """和固件 menu.c 的 sel_noise() 逐位一致的哈希噪声（0..63）。"""
    M = 0xFFFFFFFF
    h = (x * 374761393 + y * 668265263) & M
    h = ((h ^ (h >> 13)) * 1274126177) & M
    return (h >> 17) & 63


def _dither_mask(w, h, alpha, oy=0, fine=False):
    """255 = 铺色，0 = 透出底图。alpha 0..16，越大越透。

    oy 传行号，让网点的相位跟真机对齐。
    fine=True 用 8×8 矩阵（选中条），否则 4×4（底栏）。
    """
    m = Image.new("L", (w, h), 0)
    px = m.load()
    a = min(int(alpha), 16)
    if fine:
        # 和固件 sel_noise() 同一个哈希：不规则颗粒，不是棋盘格
        th = a * 4
        for y in range(h):
            for x in range(w):
                if _noise(x, oy + y) >= th:
                    px[x, y] = 255
    else:
        for y in range(h):
            row = _BAYER4[(oy + y) & 3]
            for x in range(w):
                if row[x & 3] >= a:
                    px[x, y] = 255
    return m


def _bar_text_y(text, H=160):
    """底栏文字基线：汉字占画布 2..13 行、拉丁大写占 4..12 行，
    同一个 y 画出来中文会比英文高 2px。固件用 bar_text_y() 分档，这里照做。"""
    if text and ord(text[0]) >= 0x2E80:
        return H - 16
    return H - 17


def _bar(im, top, fg, bg):
    """底栏：淡底（FG 混 BG 75%）+ 顶部 1px 实线。和固件 render_bar() 一致。

    2026-10-24 改版：不再铺一整块实色。整块颜色会把画面切得太硬，
    底栏的存在感交给那条 1px 线就够了。
    """
    dr = ImageDraw.Draw(im)
    im.paste(_mix_rgb(fg, bg, 12), (0, top, W, H))
    dr.rectangle([0, top, W - 1, top], fill=tuple(fg))


def _mix_rgb(a, b, k):
    """RGB 线性混合：k=0 全是 a，k=16 全是 b。和固件 menu.c 的 mix555 同一个公式。"""
    return tuple(int((x * (16 - k) + y * k) / 16) for x, y in zip(a, b))


# 预览用的选中行色带浓度（0 = 全透明只有竖条，16 = 实心）。
# 模块级是为了让出效果图时不用改一堆调用签名。
SEL_DENSITY = 16


def _paint_band(im, box, color, alpha=0, tile=None, fine=False, tint=False, style='band', density=None):
    """把 box 涂成 color（或 16×16 平铺的 tile），按 alpha（0..16）抖动着盖上去。

    alpha = 0 → 实心；alpha = 16 → 完全透出底图。
    **这就是真机的做法**：不是真彩 blend，是 Bayer 网点。
    """
    l, t, r, b = box
    w, h = r - l, b - t
    if style == 'row':
        # 固件 paint_sel_band 开头就有这条：alpha >= 16（全透）= 整行什么都不画。
        # 现在竖条是唯一的表现形式，所以这两端的语义变成「显示 / 不显示」。
        if alpha >= 16:
            return
        if density is None:
            density = SEL_DENSITY
        # 选中行 = 可选色带 + 左侧竖条。density 就是色带的浓度：
        #   0 = 全透明（只有竖条），16 = 实心色带
        # 竖条固定压深 62.5%，因为 HIB 这类浅色原色画在白底上等于看不见。
        acc = tuple(int(c * 0.625) for c in color)
        if density < 16:
            im.paste(_mix_rgb(color, (255, 255, 255), density / 16.0), box)
        for y in range(t, b):
            for x in range(l, min(l + 3, r)):
                im.putpixel((x, y), acc)
        return
    if tile is not None:
        base = Image.new("RGB", (w, h))
        for yy in range(0, h, 16):
            for xx in range(0, w, 16):
                base.paste(tile, (xx, yy))
    else:
        base = Image.new("RGB", (w, h), tuple(color))
    if not alpha:
        im.paste(base, (l, t))
        return
    im.paste(base, (l, t), _dither_mask(w, h, alpha, t, fine))


def hib_tile_image(doc, set_i, hib_color):
    """这一套的选中条 16×16 贴图；没设纹理就返回 None。

    tile 里 0 号像素 = 「自动色」，用本套的 HIB（换套自动变色）；1..n 取这套
    自己的调色板。全部映射成实色 —— 没有透明像素，所以文件名永远读得清。
    """
    h = doc.hib_of(set_i) if doc is not None and hasattr(doc, "hib_of") else None
    if not h:
        return None
    tile, colors = h
    im = Image.new("RGB", (16, 16))
    px = im.load()
    for y in range(16):
        row = y * 16
        for x in range(16):
            v = tile[row + x]
            px[x, y] = tuple(hib_color) if not v else tuple(colors[v - 1])
    return im


def _paste_logo(im, doc, cx, y, scale):
    """把当前主题的 logo 贴上去（默认就是内置那个 31x7）。"""
    lg = logo_to_image(doc.logo_img, doc.logo_pal)
    big = lg.resize((lg.width * scale, lg.height * scale), Image.NEAREST)
    im.paste(big, (cx - big.width // 2, y))
    return big.size


def _center_at(im, cx, y, text, color):
    F.draw(im, cx - F.width(text) // 2, y, text, color)


def _row_list(im, icons, items, sel, ft, hib, y0=CONTENT_Y, rowh=16, ymax=H - 16,
              hib_a=0, tile=None, tint=False):
    """真机列表：图标 x=2，名字 x=20，右侧大小右对齐 238，选中行整行铺 HIB
    （真机用 63 号图标平铺，颜色取自 SEL_COLOR = hi_blend；v8 起可以是 16×16 纹理）。"""
    for i, item in enumerate(items):
        ic, name = item[0], item[1]
        right = item[2] if len(item) > 2 else ""
        y = y0 + i * rowh
        if y + rowh > ymax:
            break
        if i == sel:
            _paint_band(im, (0, y, W, y + rowh), hib, hib_a, tile, fine=True, tint=tint, style="row")
        if 0 <= ic < len(icons):
            im.paste(icons[ic], (5, y), icons[ic])
        rw = F.width(right) if right else 0
        if right:
            F.draw_right(im, W - 2, y, right, ft)
        F.draw(im, 23, y, F.fit(name, W - 29 - rw), ft)


def _row_kv(im, pairs, sel, ft, hib, y0, rowh=20, colx=170, hib_a=0, tile=None, tint=False):
    """真机设置行：左标题 x=8，右值居中在 colx，选中行整行铺 HIB。"""
    for i, (k, v) in enumerate(pairs):
        y = y0 + i * rowh
        if sel is not None and i == sel:
            _paint_band(im, (0, y, W, y + 16), hib, hib_a, tile, fine=True, tint=tint, style="row")
        F.draw(im, 8, y, k, ft)
        _center_at(im, colx, y, v, ft)


def render_preview(doc, slot, rows=None, page=None, extra=None):
    """240x160 的菜单示意，用固件自己的字库和布局常量画，尽量和真机一致。

    page = 0..6 按那个标签页画；page=None 按文件列表页画。
    extra = EXTRA_SCREENS 里的键时，画主菜单之外的界面（只读预览）。
    """
    pal = doc.pals[slot % 6]
    fg, bg, ft, hi, hib, sh = pal
    # v5 的三个独立色：没设的时候等于 bg / fg / bg
    ex_lbg, ex_lfg, ex_ibg = doc.excol_of(slot % 6)
    # v7 的两个透明度（0 = 实心，16 = 全透）
    alpha_of = getattr(doc, "alpha_of", None)
    bar_a = alpha_of(slot % 6, sdtheme.ALPHA_BAR) if alpha_of else 0
    sel_a = alpha_of(slot % 6, sdtheme.ALPHA_SEL) if alpha_of else 0
    # v8 的选中条纹理（None = 没设，照旧铺一块实色）
    htile = hib_tile_image(doc, slot % 6, hib)
    im = Image.new("RGB", (W, H), bg)
    icons = doc.icon_imgs[slot % 6]
    pg = 1 if page is None else page

    if extra == "ingame":
        im.paste(ex_ibg, (0, 0, W, H))
        # ingame_menu.c:600 draw_main_menu：整屏 BG + logo + 6 行（x=24, y=36+19i）
        # 可用项是 HI_COLOR，但它拿的是 menu_palette[2] = themes[].ft_color
        # （loader.c:158 从 MEM_PALETTE[240..244] 拷过去），所以显示上是 FT 不是 HI。
        # 不可用项用 SH_COLOR —— SH 这个槽只有这里看得见。
        _paste_logo(im, doc, W // 2, 20, 2)
        # 文案照 fw/src/menu_messages.h 的简体中文块，别自己另编
        items = [("继续游戏", True), ("复位", True), ("写入存档至SD卡", True),
                 ("即时存档", False), ("时钟模拟", False), ("金手指", False)]
        for i, (t, on) in enumerate(items):
            F.draw(im, 24, 36 + 19 * i, t, ft if on else sh)
        # 选中行：固件是 16 个 OAM 精灵拼的横条（ingame_menu.c:1311），
        # 具体宽高没在真机上核对过，这里只画一条 BL 色的下划线来标示位置和颜色。
        # 这条走的是 ingame_menu.c 自己的路径，吃不到 v7 的「选中条」透明度，所以画实心。
        y0 = 36 + 19 * 0
        tw = F.width(items[0][0])
        im.paste(hib, (24, y0 + 15, 24 + tw, y0 + 17))
        return im

    if extra == "loading":
        # loadrom_progress()：有壁纸就把上面 144 行铺满（用的就是主菜单内存里
        # 缓存的那张 = 「当前这一页」的图），下面 16 行仍是 BG；
        # 进度条先垫一条 BG 底槽再画 FG，压在图上才看得清。
        wall = doc.sp_rgb[slot % 6][page] if page is not None else None
        if wall is None:
            wall = doc.wall_rgb[slot % 6]
        if wall is not None:
            im.paste(wall, (0, 0))
            im.paste(ex_lbg, (0, CONTENT_H, W, H))
        else:
            im.paste(ex_lbg, (0, 0, W, H))
        im.paste(ex_lbg, (20, 76, 220, 84))
        im.paste(ex_lfg, (20, 76, 20 + 140, 84))
        return im

    if extra in ("rominfo", "patchopts", "loadopts"):
        # 三个都是「盖在主菜单上的弹窗」：render_gba_load_popup() 先铺顶栏 + 当前页壁纸，
        # 再 draw_box_full(2, 238, 18, 158) 盖上去。文案取自 res/messages.py 的英文块。
        im.paste(fg, (0, 0, W, CONTENT_Y))
        for i in range(7):
            idx = 8 + i
            if idx >= len(icons):
                continue
            ic = icons[idx]
            im.paste(ic, (i * 16, 0), ic if i == pg else
                     ic.split()[3].point(lambda a: a // 2))
        wall = doc.wall_rgb[slot % 6]
        if wall is not None:
            im.paste(wall, (0, CONTENT_Y))

        _box(im, 2, 238, 18, 158, fg, bg)
        # 标题两侧的翻页箭头：◂ 左对齐 x=10、▸ 右对齐 W-10，都在 y=21
        F.draw(im, 10, 21, "◂", ft)
        F.draw_right(im, W - 10, 21, "▸", ft)

        def row_label(x, y, t):
            F.draw(im, x, y, t, ft)

        def row_value(x, y, t):
            F.draw(im, x - F.width(t) // 2, y, t, ft)

        if extra == "rominfo":
            F.draw_center(im, 23, "ROM information", ft)
            F.draw_center(im, 52, "Pokemon Mercury FC.gba", ft)
            F.draw_center(im, 82, "GameID: BPEE | Version: 0", ft)
            F.draw_center(im, 102, "Save type: Flash (128KB)", ft)
            # 底部按钮：draw_box_full(20, 220, 132, 152, FG, HI) + 文字居中 (120, 135)
            _box(im, 20, 220, 132, 152, fg, hi)
            F.draw_center(im, 135, "Load GBA game ROM", ft)

        elif extra == "patchopts":
            F.draw_center(im, 23, "Patching options", ft)
            row_label(12, 44, "Patching");      row_value(170, 44, "< Built-in database >")
            row_label(12, 62, "Save mode");     row_value(170, 62, "< DirectSave >")
            row_label(12, 80, "In-game menu");  row_value(170, 80, "< Enabled >")
            row_label(12, 98, "Emulated RTC");  row_value(170, 98, "< Disabled >")
            row_label(12, 116, "PatchEngine")
            # 这一行的值是画在固定框里的：draw_box_outline(150..190, 115..133) + ▸ (171,114)
            _box(im, 150, 190, 115, 133, fg)
            F.draw(im, 171 - F.width("▸") // 2, 114, "▸", ft)

        else:  # loadopts
            F.draw_center(im, 23, "Loading options", ft)
            row_label(12, 44, "Savegame load");  row_value(170, 44, "< Load >")
            row_label(12, 62, "Savegame save");  row_value(170, 62, "< On reboot >")
            row_label(12, 80, "RTC time");       row_value(170, 80, "-")
            row_label(12, 98, "Load cheats");    row_value(170, 98, "< Disabled >")
            row_label(12, 116, "Remember config")
            _box(im, 150, 190, 115, 133, fg)
            # render_icon(170-8, 116, ICON_DISK) —— ICON_DISK = 9
            if len(icons) > 9:
                im.paste(icons[9], (162, 116), icons[9])
        return im

    if extra in ("norwrite", "filemgr"):
        # 这两屏是「盖在主菜单上的弹窗」：menu_render() 先画顶栏和内容区壁纸，
        # 再把弹窗画上去（menu.c:2124 起）。所以背景不是纯色，是当前页的图。
        im.paste(fg, (0, 0, W, CONTENT_Y))
        for i in range(7):
            idx = 8 + i
            if idx >= len(icons):
                continue
            ic = icons[idx]
            if i == pg:
                im.paste(ic, (i * 16, 0), ic)
            else:
                im.paste(ic, (i * 16, 0),
                         ic.split()[3].point(lambda a: a // 2))
        wall = doc.wall_rgb[slot % 6]
        if wall is not None:
            im.paste(wall, (0, CONTENT_Y))
        _box(im, 2, 238, 18, 158, fg, bg)       # v7 起弹窗内填 BG，不再透出底图
        if extra == "norwrite":
            # render_gbarom_info() + 「写 NOR」按钮框
            F.draw_center(im, 23, "载入信息", ft)
            F.draw_center(im, 52, "MOTHER.3.GBA", ft)
            F.draw_center(im, 82, "游戏码 BM3J", ft)
            F.draw_center(im, 102, "存档 EEPROM 8KB", ft)
            _box(im, 20, 220, 132, 152, fg, hi)
            F.draw_center(im, 134, "写 NOR", ft)
        else:
            # render_filemgr()：文件名 + 3 个按钮框，第一个选中（HI 填充）
            F.draw_center(im, 32, "GOLDEN.SUN.GBA", ft)
            for i, t in enumerate(("删除", "隐藏", "写入 NOR")):
                _box(im, 20, 220, 60 + i * 30, 80 + i * 30, fg, hi if i == 0 else None)
                F.draw_center(im, 62 + i * 30, t, ft)
        return im

    # ---- 顶栏：FG 底 + 页面图标（menu.c:2660 起）
    # 固件：dma_memset16 铺 FG 到 0..15 行；图标 x = (i - mintab) * 16、y = 0，
    #       当前页用 render_icon（实心），其余用 render_icon_trans（半透明）。
    im.paste(fg, (0, 0, W, CONTENT_Y))
    for i in range(7):
        idx = 8 + i
        if idx >= len(icons):
            continue
        ic = icons[idx]
        x = i * 16
        if i == pg:
            im.paste(ic, (x, 0), ic)
        else:
            im.paste(ic, (x, 0), ic.split()[3].point(lambda a: a // 2))
    # 右上角计数：只有「文件浏览」和「NOR 烧录」两页有（render_browser / render_flashbrowser
    # 里的 draw_rightj_text(selinfo, SCREEN_WIDTH-1, 1)），颜色是 FT，不是 SH。
    if pg in (1, 2):
        F.draw_right(im, W - 1, 1, "1/%d" % (3 if pg == 2 else len(rows or (0, 1, 2))), ft)

    # ---- 内容区背景：这一页的图 → 这套的默认图 → BG 纯色
    wall = None
    if page is not None and 0 <= page < sdtheme.PAGE_COUNT:
        wall = doc.sp_rgb[slot % 6][page]
    if wall is None:
        wall = doc.wall_rgb[slot % 6]
    if wall is not None:
        im.paste(wall, (0, CONTENT_Y))
    else:
        im.paste(bg, (0, CONTENT_Y, W, H))
        # 没有壁纸：和固件 paint_sel_band 一样，把选中条透明度折进颜色里，
        # 铺一块**实心**的混合色 —— 实机就是这么做的，预览不能画网点，
        # 否则又和实机对不上。
        # 2026-10-24：固件那边「没壁纸整行铺色」的分支删了，预览也不再把
        # alpha 折进 hib —— 竖条颜色恒为 hib × 0.625，和固件一致。

    if pg == 0:
        # 最近玩过：纯列表，没有底栏
        _row_list(im, icons, [
            (5, "Pokemon Mercury FC.gba"),
            (5, "Metroid Fusion.gba"),
            (5, "Golden Sun.gba"),
            (5, "Fire Emblem.gba"),
            (3, "Zelda - Link's Aw.gbc"),
        ], 0, ft, hib, ymax=H, hib_a=sel_a, tile=htile, tint=(wall is not None))

    elif pg == 1:
        # 文件列表：底栏是当前路径
        _row_list(im, icons, rows or [
            (0, "01 GBC 汉化经典", ""),
            (5, "superfw-chis.gba", "1M"),
            (2, "superfw-chis.fw", "1M"),
        ], 0, ft, hib, hib_a=sel_a, tile=htile, tint=(wall is not None))
        _bar(im, H - 16, fg, bg)
        F.draw(im, 8, _bar_text_y("/", H), "/", ft)

    elif pg == 2:
        # 烧录到卡带：NOR 列表（图标统一 GBA 卡带）+ 闪存占用底栏
        _row_list(im, icons, [
            (5, "Metroid Fusion.gba", "8M"),
            (5, "Golden Sun.gba", "8M"),
            (5, "Zelda - Link's Aw.gbc", "4M"),
        ], 0, ft, hib, hib_a=sel_a, tile=htile, tint=(wall is not None))
        _bar(im, H - 16, fg, bg)
        F.draw(im, 8, _bar_text_y("Flash usage: 112M/124M", H), "Flash usage: 112M/124M", ft)

    elif pg == 3:
        # 全局设置：offy=29 / rowh=20 / colx=170，选中行整行高亮，底栏是帮助提示
        # 文案 = msg_en：MSG_SETT_HOTK / BOOT(+BOOT_TYPE0) / FASTSD / VER(VERNOR) / FASTEW
        # render_settings 的真实结构（offy=29 / rowh=20 / maxrows=5）：
        #   第 0 个可见行是**标题** MSG_SET_TITL1（居中），后面才是选项 → 29 / 49 / 69 / 89 / 109
        #   「保存」按钮是列表**最后一项**，而且画在固定位置 112..132，
        #   只有当它的下标落进可见窗口（滚到最下面）时才出现 —— 默认窗口里没有它。
        _center_at(im, W // 2, 29, "Global settings", ft)
        pairs = [
            ("Menu Hot-key", "< L+R+Start >"),
            ("Game boot", "< Skip BIOS boot >"),
            ("Fast ROM loading", "< Enabled >"),
            ("Verify flashing", "< Disabled >"),
        ]
        _row_kv(im, pairs, 2, ft, hib, 49, hib_a=sel_a, tile=htile, tint=(wall is not None))
        _bar(im, H - 16, fg, bg)
        # 底栏 = MSG_FASTSD_I，真机上滚动显示，预览做静态截断
        _hint = ("Use a fast ROM loading mechanism. Can result in "
                 "crashes or incorrect reads in some devices")
        F.draw(im, 4, _bar_text_y(_hint, H), F.fit(_hint, 232), ft)

    elif pg == 4:
        # 界面设置：y=22 / rowh=20，底部「将设置保存到SD卡」按钮框
        # 文案 = msg_en：UIS_THEME / LANG / RECNT / ANSPD(SPD2) / BHID，按钮 UIS_SAVE
        pairs = [
            ("Theme color", "< 1 >"),
            ("Language", "< English >"),
            ("Recent ROMs", "< Enabled >"),
            ("Text speed", "< Regular >"),
            ("Show hidden files", "< Enabled >"),
        ]
        _row_kv(im, pairs, 0, ft, hib, 22, hib_a=sel_a, tile=htile, tint=(wall is not None))
        dbox = ImageDraw.Draw(im)
        dbox.rectangle([20, 132, 220, 152], outline=fg)
        _center_at(im, W // 2, 134, "Save to SD card", ft)

    elif pg == 5:
        # 工具：y=26+22i，左边 ▶，选中行整行高亮
        # 文案 = msg_en：TOOLS0..TOOLS5（SDRAM / SRAM / BAT / BENCH / FBAK / FCLR）
        tools = ["SDRAM memory test", "SRAM memory test", "SRAM battery test",
                 "SD (read) benchmark", "Flash backup", "Erase flash"]
        for i, name in enumerate(tools):
            y = 26 + 22 * i
            if y + 16 > H:
                break
            if i == 0:
                _paint_band(im, (0, y, W, y + 16), hib, sel_a, fine=True, style="row")
                # 固件：draw_central_text("▸", 11 + anim, y - 2) —— 小三角只占画布 8..12 行
                F.draw(im, 11 - F.width("\u25B8") // 2, y - 2, "\u25B8", ft)
            F.draw(im, 22, y, F.fit(name, 144), ft)

    else:
        # 关于：真 logo（31x7 放大 4 倍）+ 版本信息 + 底部升级提示条
        _paste_logo(im, doc, W // 2, 40, 4)
        _center_at(im, W // 2, 70, "by davidgf", ft)
        _center_at(im, W // 2, 95, "Version 0.21 (%s)" % FW_GIT_HASH, ft)
        _center_at(im, W // 2, 114, FW_BUILD_LABEL, ft)
        _bar(im, H - 16, fg, bg)
        # 底条 = MSG_FWUP_HOTKEY（flashing 关闭时的默认文案）
        _hint = "Use Down+B+Start to unlock updates"
        _center_at(im, W // 2, _bar_text_y(_hint, H), _hint, ft)

    return im


# ---------------------------------------------------------------- 自检
def selftest(path=None):
    print("== theme.bin 自检 ==")
    d = Doc()
    raw = d.to_bytes()
    r = sdtheme.parse(raw)
    print("新建 → %d 字节, 带壁纸=%s" % (len(raw), r["has_wall"]))
    assert len(raw) == sdtheme.ICONS_ONLY_SIZE
    for i in range(6):
        a = d.pals[i]
        b = r["pals"][i]
        assert all(abs(a[k][c] - b[k][c]) <= 8 for k in range(6) for c in range(3)), \
            "第 %d 套配色往返不一致" % (i + 1)

    # 图标往返：默认图标 ≤37 色，应逐字节还原
    sprite, pal = icons_to_raw(icons_from_raw(bytes(fwassets.ICONS_IMG),
                                             list(fwassets.ICONS_PAL)))
    print("默认图标往返差字节 =", sum(1 for a, b in zip(sprite, bytes(fwassets.ICONS_IMG)) if a != b))
    print("调色板往返差项  =", sum(1 for a, b in zip(pal, fwassets.ICONS_PAL) if a != b))

    p = path
    if p and os.path.exists(p):
        src = open(p, "rb").read()
        doc = Doc.from_bytes(src)
        out = doc.to_bytes()
        same = (out == src)
        print("打开再保存逐字节一致:", same, "(%d 字节)" % len(out))
        if not same:
            diff = [i for i in range(min(len(src), len(out))) if src[i] != out[i]]
            print("  差异 %d 处，首处 0x%X" % (len(diff), diff[0] if diff else -1))

    # 每套一张壁纸：6 套给 6 张不同纯色，存盘再读回来必须各回各的
    print("== 每套一张壁纸（v2）==")
    d2 = Doc()
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (255, 0, 255), (0, 255, 255)]
    for i in range(6):
        d2.wall_rgb[i] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), cols[i])
        d2.wall_dirty[i] = True
    raw2 = d2.to_bytes()
    r2 = sdtheme.parse(raw2)
    print("  大小 %d（v%d），有壁纸的套 %s"
          % (len(raw2), r2["version"],
             [i + 1 for i in range(6) if r2["walls"][i] is not None]))
    assert r2["version"] == sdtheme.VERSION_V2, "6 张不同的图应该是 v2"
    assert len(raw2) == sdtheme.FILE_SIZE_V2, "v2 长度应为 %d" % sdtheme.FILE_SIZE_V2
    back = Doc.from_bytes(raw2)
    for i in range(6):
        px = back.wall_rgb[i].getpixel((120, 72))
        near = sum(abs(px[c] - cols[i][c]) for c in range(3))
        print("  第 %d 套中心像素 %s（源 %s，差 %d）" % (i + 1, px, cols[i], near))
        assert near <= 24, "第 %d 套的壁纸串位了" % (i + 1)
    # 同一张给 6 套 → 退回 v1
    d3 = Doc()
    d3.wall_rgb[0] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), (10, 20, 30))
    d3.wall_dirty[0] = True
    d3.wall_rgb[1:] = [d3.wall_rgb[0]] * 5
    d3.wall_dirty[1:] = [True] * 5
    raw3 = d3.to_bytes()
    print("  6 套同一张 → %d 字节（v%d）" % (len(raw3), sdtheme.parse(raw3)["version"]))
    assert len(raw3) == sdtheme.FILE_SIZE, "6 套同图应退回 61680"

    # 页面图（v3）：6 套配同样的两页 → 存盘时合并回全局段，文件仍是 v3
    print("== 页面图（v3：6 套共用）==")
    d4 = Doc()
    for i in range(6):
        d4.wall_rgb[i] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), cols[i])
        d4.wall_dirty[i] = True
    pcols = {1: (200, 30, 30), 6: (30, 30, 200)}
    for k, c in pcols.items():
        im = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), c)
        for s_ in range(6):
            d4.sp_rgb[s_][k] = im
            d4.sp_dirty[s_][k] = True
    raw4 = d4.to_bytes()
    r4 = sdtheme.parse(raw4)
    print("  大小 %d（v%d），全局页 %s"
          % (len(raw4), r4["version"],
             [i for i in range(sdtheme.PAGE_COUNT) if r4["page_walls"][i] is not None]))
    assert r4["version"] == sdtheme.VERSION_V3, "6 套一致的页面图应该合并成 v3"
    assert len(raw4) == sdtheme.FILE_SIZE_V3
    back4 = Doc.from_bytes(raw4)
    assert back4.page_count() == 12, "6 套 × 2 页 = 12 格"
    for k, c in pcols.items():
        px = back4.sp_rgb[0][k].getpixel((120, 72))
        near = sum(abs(px[i] - c[i]) for i in range(3))
        print("  第 %d 页中心像素 %s（源 %s，差 %d）" % (k, px, c, near))
        assert near <= 24, "第 %d 页的图串位了" % k
    for i in range(6):
        px = back4.wall_rgb[i].getpixel((120, 72))
        assert sum(abs(px[c] - cols[i][c]) for c in range(3)) <= 24, \
            "第 %d 套默认图被页面图带跑了" % (i + 1)
    assert back4.to_bytes() == raw4, "v3 打开再保存应逐字节一致"
    print("  v3 往返逐字节一致")

    # 每套每页（v4）：只给第 3 套的第 2 页换张不同的 → 别的套必须不受影响
    print("== 每套每页（v4）==")
    d5 = Doc()
    for i in range(6):
        d5.wall_rgb[i] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), cols[i])
        d5.wall_dirty[i] = True
    d5.sp_rgb[2][1] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), (10, 200, 40))
    d5.sp_dirty[2][1] = True
    raw5 = d5.to_bytes()
    r5 = sdtheme.parse(raw5)
    print("  大小 %d（v%d），掩码 0x%010x"
          % (len(raw5), r5["version"], r5["set_page_mask"]))
    assert r5["version"] == sdtheme.VERSION_V4, "按套页面图应该是 v4"
    assert len(raw5) == sdtheme.FILE_SIZE_V4
    assert r5["set_page_mask"] == (1 << sdtheme.slot_index(2, 1)), "掩码位不对"
    back5 = Doc.from_bytes(raw5)
    assert back5.sp_rgb[2][1] is not None, "第 3 套第 2 页丢了"
    for s_ in range(6):
        for p_ in range(sdtheme.PAGE_COUNT):
            if (s_, p_) != (2, 1):
                assert back5.sp_rgb[s_][p_] is None, "第 %d 套第 %d 页被串了" % (s_ + 1, p_ + 1)
    px = back5.sp_rgb[2][1].getpixel((120, 72))
    near = sum(abs(px[i] - (10, 200, 40)[i]) for i in range(3))
    print("  第 3 套第 2 页中心像素 %s（差 %d）" % (px, near))
    assert near <= 24, "v4 的图串位了"
    for i in range(6):
        px = back5.wall_rgb[i].getpixel((120, 72))
        assert sum(abs(px[c] - cols[i][c]) for c in range(3)) <= 24, \
            "第 %d 套默认图被按套页面图带跑了" % (i + 1)
    assert back5.to_bytes() == raw5, "v4 打开再保存应逐字节一致"
    print("  v4 往返逐字节一致")
    print("OK")


# ---------------------------------------------------------------- 裁剪对话框
def center_on(win, master=None):
    """把弹窗摆到主窗口正中；主窗口还没尺寸时退回屏幕正中。

    Tk 的 Toplevel 默认位置由窗口管理器决定，在 Windows 上通常就是屏幕左上角，
    弹出来离主窗口很远。这里在窗口映射前用 geometry 指定位置，避免看到跳一下。
    """
    try:
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        if w <= 1 or h <= 1:                       # 已经映射过的窗口才有真实尺寸
            w, h = win.winfo_width(), win.winfo_height()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        if master is not None and master.winfo_width() > 1:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
        else:
            px, py, pw, ph = 0, 0, sw, sh
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        x = max(0, min(x, sw - w))                 # 别把窗口推到屏幕外
        y = max(0, min(y, sh - h))
        win.geometry("+%d+%d" % (x, y))
    except tk.TclError:
        pass


class _ColorTrack(tk.Canvas):
    """渐变轨道滑块：轨道本身就是预览。

    系统的取色器轨道是一条灰槽，只能靠旁边那串数字想象结果，改一次看一眼；
    这里把轨道直接画成这个通道的渐变，拖到哪就知道会变成什么色。
    """

    TH = 14                 # 轨道厚度
    KW = 8                  # 游标宽
    KH = 20                 # 游标高

    def __init__(self, master, width, maxval, grad, on_input):
        super().__init__(master, width=width, height=self.KH + 2, bg=PANEL,
                         highlightthickness=0, cursor="hand2")
        self.maxval = maxval
        self._grad = grad              # t(0..1) -> (r, g, b)
        self._on_input = on_input
        self._val = 0
        self._tw = width - self.KW - 2
        self._photo = ImageTk.PhotoImage(Image.new("RGB", (self._tw, self.TH)))
        self.create_image(1, 2, image=self._photo, anchor="nw")
        self.create_rectangle(1, 2, 1 + self._tw, 2 + self.TH, outline=LINE)
        self._knob = self.create_rectangle(0, 0, 0, 0, fill=TEXT, outline=BG)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self._paint()

    def _paint(self):
        w = self._tw
        buf = bytearray()
        for x in range(w):
            buf.extend(self._grad(x / float(max(1, w - 1))))
        im = Image.frombytes("RGB", (w, 1), bytes(buf)).resize((w, self.TH), Image.NEAREST)
        self._photo.paste(im)
        t = self._val / float(self.maxval or 1)
        x = 1 + t * (w - 1)
        x = max(1.0, min(1.0 + w - 1, x))
        self.coords(self._knob, x - self.KW / 2.0, 1, x + self.KW / 2.0, 1 + self.KH)

    def set(self, v):
        self._val = max(0, min(self.maxval, int(v)))
        self._paint()

    def _press(self, ev):
        self._to(ev)

    def _drag(self, ev):
        self._to(ev)

    def _to(self, ev):
        t = (ev.x - 1) / float(max(1, self._tw - 1))
        v = int(round(max(0.0, min(1.0, t)) * self.maxval))
        if v != self._val:
            self._on_input(v)
        else:
            self._paint()


class ColorDialog(tk.Toplevel):
    """滑块调色。三处会用到：主题 6 个颜色、载入/游戏内那 3 个、Logo 的 3 个。

    比系统取色器多两件事：
      * RGB 和 HSL 两组滑块联动，轨道是渐变，拖到哪看到哪；
      * 右边那块写「机器上」—— GBA 只有 15 位色（每通道 32 级），
        在电脑上挑的色刷上去常常差一档，这里提前看见。
    """

    TRACK_W = 252

    ROWS = (("r", "红 R", 255), ("g", "绿 G", 255), ("b", "蓝 B", 255),
            ("h", "色调 H", 360), ("s", "饱和 S", 100), ("l", "亮度 L", 100))

    def __init__(self, master, color, title="选择颜色", hint=None, swatches=None):
        super().__init__(master)
        self.title("选择颜色")           # 任务栏就这一个名字，具体调哪儿看内容区标题
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        self._rgb = [max(0, min(255, int(c))) for c in tuple(color)[:3]]
        h, l, s = colorsys.rgb_to_hls(*[v / 255.0 for v in self._rgb])
        self._hsl = [h, s, l]                 # 统一存 0..1
        self._tr = {}
        self._num = {}
        self._lock = False
        self.result = None

        self._build(title, hint, list(swatches or []))
        self._sync("rgb")
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.update_idletasks()
        center_on(self, master)
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        master.wait_window(self)

    # ---- 界面
    def _build(self, title, hint, swatches):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(head, text=title, bg=BG, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        if hint:
            tk.Label(self, text=hint, bg=BG, fg=MUTED, anchor="w", justify="left",
                     wraplength=372, font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=14)

        prev = tk.Frame(self, bg=BG)
        prev.pack(fill="x", padx=14, pady=(10, 2))
        self.big = tk.Frame(prev, width=88, height=58, bg="#000000",
                            highlightthickness=1, highlightbackground=LINE)
        self.big.pack(side="left")
        self.big.pack_propagate(False)

        info = tk.Frame(prev, bg=BG)
        info.pack(side="left", fill="both", expand=True, padx=(12, 0))

        r1 = tk.Frame(info, bg=BG)
        r1.pack(fill="x")
        tk.Label(r1, text="HEX", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.hex_var = tk.StringVar()
        self.ent = tk.Entry(r1, textvariable=self.hex_var, width=9, bg=PANEL2, fg=TEXT,
                            insertbackground=TEXT, relief="flat", bd=0, justify="center",
                            font=("Consolas", 10))
        self.ent.pack(side="left", padx=(8, 0), ipady=2)
        self.ent.bind("<Return>", self._from_hex)
        self.ent.bind("<FocusOut>", self._from_hex)

        r2 = tk.Frame(info, bg=BG)
        r2.pack(fill="x", pady=(8, 0))
        tk.Label(r2, text="机器上", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.small = tk.Frame(r2, width=22, height=22, bg="#000000",
                              highlightthickness=1, highlightbackground=LINE)
        self.small.pack(side="left", padx=(8, 0))
        self.small.pack_propagate(False)
        self.lbl_real = tk.Label(r2, text="", bg=BG, fg=DIM, font=("Consolas", 9))
        self.lbl_real.pack(side="left", padx=(8, 0))

        tk.Label(info, text="机器只有 15 位色，差得多会标红", bg=BG, fg=DIM, anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x", pady=(6, 0))

        body = tk.Frame(self, bg=PANEL)
        body.pack(fill="x", padx=14, pady=(10, 0))
        for i, (k, name, mx) in enumerate(self.ROWS):
            if i == 3:
                tk.Frame(body, height=1, bg=LINE).pack(fill="x", pady=5)
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name, bg=PANEL, fg=MUTED, width=6, anchor="w",
                     font=("Microsoft YaHei UI", 9)).pack(side="left")
            grad = (lambda t, idx=i: self._grad_rgb(idx, t)) if i < 3 else \
                   (lambda t, kk=k: self._grad_hsl(kk, t))
            tr = _ColorTrack(row, self.TRACK_W, mx, grad,
                             lambda v, kk=k: self._set(kk, v))
            tr.pack(side="left", padx=(4, 0))
            num = tk.Label(row, text="0", bg=PANEL, fg=TEXT, width=4, anchor="e",
                           font=("Consolas", 9))
            num.pack(side="left", padx=(8, 0))
            self._tr[k] = tr
            self._num[k] = num
        tk.Label(body, text="色调没反应？先把饱和度拉起来 —— 灰色没有色调",
                 bg=PANEL, fg=DIM, anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x", pady=(6, 6))

        if swatches:
            sw = tk.Frame(self, bg=BG)
            sw.pack(fill="x", padx=14, pady=(8, 0))
            tk.Label(sw, text="直接取用", bg=BG, fg=DIM,
                     font=("Microsoft YaHei UI", 8)).pack(side="left")
            for c in swatches:
                hx = "#%02X%02X%02X" % tuple(c[:3])
                b = tk.Frame(sw, width=18, height=18, bg=hx, cursor="hand2",
                             highlightthickness=1, highlightbackground=LINE)
                b.pack(side="left", padx=(6, 0))
                b.pack_propagate(False)
                b.bind("<Button-1>", lambda e, cc=tuple(c[:3]): self._use(cc))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=14, pady=12)
        tk.Button(foot, text="取消", command=self._cancel, bg=PANEL2, fg=MUTED,
                  activebackground=LINE, activeforeground=TEXT, relief="flat", bd=0,
                  padx=14, pady=5, cursor="hand2",
                  font=("Microsoft YaHei UI", 9)).pack(side="right")
        tk.Button(foot, text="确定", command=self._ok, bg=ACC, fg="#111",
                  activebackground=ACC, activeforeground="#111", relief="flat", bd=0,
                  padx=16, pady=5, cursor="hand2",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="right", padx=(0, 8))

    # ---- 渐变
    def _grad_rgb(self, idx, t):
        c = list(self._rgb)
        c[idx] = int(round(t * 255))
        return c

    def _grad_hsl(self, k, t):
        h, s, l = self._hsl
        if k == "h":
            h = t
        elif k == "s":
            s = t
        else:
            l = t
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))

    # ---- 状态
    def _set(self, k, v):
        if k in "rgb":
            self._rgb["rgb".index(k)] = v
            self._sync("rgb")
        else:
            self._hsl["hsl".index(k)] = v / (360.0 if k == "h" else 100.0)
            r, g, b = colorsys.hls_to_rgb(self._hsl[0], self._hsl[2], self._hsl[1])
            self._rgb = [int(round(r * 255)), int(round(g * 255)), int(round(b * 255))]
            # 保留用户设的 HSL 不回算：15 位色反推会来回抖
            self._sync("hsl")

    def _use(self, c):
        self._rgb = [int(v) for v in c[:3]]
        self._sync("rgb")

    def _sync(self, src):
        if self._lock:
            return
        self._lock = True
        try:
            if src == "rgb":
                h, l, s = colorsys.rgb_to_hls(*[v / 255.0 for v in self._rgb])
                self._hsl = [h, s, l]
            for i, k in enumerate("rgb"):
                self._tr[k].set(self._rgb[i])
                self._num[k].configure(text=str(self._rgb[i]))
            for k, mx, i in (("h", 360, 0), ("s", 100, 1), ("l", 100, 2)):
                v = int(round(self._hsl[i] * mx))
                self._tr[k].set(v)
                self._num[k].configure(text=str(v))
        finally:
            self._lock = False
        self._repaint()

    def _repaint(self):
        rgb = tuple(self._rgb)
        real = sdtheme.rgb555(sdtheme.color555(rgb))
        hexs = "#%02X%02X%02X" % rgb
        self.big.configure(bg=hexs)
        self.small.configure(bg="#%02X%02X%02X" % real)
        if self.focus_get() is not self.ent:      # 正在手输 HEX 时别抢
            self.hex_var.set(hexs)
        diff = max(abs(a - b) for a, b in zip(rgb, real))
        self.lbl_real.configure(text=("#%02X%02X%02X" % real) if diff else "一致",
                                fg=(DIM if diff <= 4 else WARN))

    def _from_hex(self, _ev=None):
        s = (self.hex_var.get() or "").strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        try:
            if len(s) != 6:
                raise ValueError
            v = int(s, 16)
        except ValueError:
            self.hex_var.set("#%02X%02X%02X" % tuple(self._rgb))
            return
        self._rgb = [(v >> 16) & 255, (v >> 8) & 255, v & 255]
        self._sync("rgb")

    def _ok(self):
        self.result = tuple(self._rgb)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class LogoDialog(tk.Toplevel):
    """改 SUPERFW 那个 31x7 的标志。关于页和游戏内菜单共用同一份。"""

    SCALE = 8

    def __init__(self, master, app):
        super().__init__(master)
        self.title("改 Logo")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.app = app
        self.doc = app.doc
        self._ph = None
        self._swatches = []
        self._build()
        self.update_idletasks()
        center_on(self, master)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window(self)

    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(head, text="Logo（31 × 7 像素）", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

        tk.Label(self, text="关于页和游戏内菜单都用它。选一张图会自动缩放到 31×7、量化成 3 色；\n"
                            "下面三个色块改的就是这 3 个颜色（第 1 个是字，第 2 个是右边那块底）。",
                 bg=BG, fg=MUTED, justify="left", anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=16, pady=(0, 10))

        self.canvas = tk.Label(self, bg="#0a0a08", bd=0)
        self.canvas.pack(padx=16, pady=2)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=16, pady=(10, 2))
        tk.Label(row, text="三个颜色：", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        for i in range(3):
            sw = tk.Canvas(row, width=24, height=24, highlightthickness=1,
                           highlightbackground=LINE, bg=BG, cursor="hand2")
            sw.pack(side="left", padx=4)
            sw.bind("<Button-1>", lambda e, k=i: self.pick_color(k))
            self._swatches.append(sw)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(14, 14))
        tk.Button(foot, text="选图片…", command=self.pick_image, bg=PANEL2, fg=TEXT,
                  relief="flat", bd=0, padx=14, pady=4, cursor="hand2",
                  font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Button(foot, text="恢复内置", command=self.reset, bg=PANEL2, fg=MUTED,
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)
        tk.Button(foot, text="完成", command=self._close, bg=ACC, fg="#111",
                  activebackground=ACC, activeforeground="#111", relief="flat", bd=0,
                  padx=18, pady=4, cursor="hand2",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
        self.refresh()

    def refresh(self):
        im = logo_to_image(self.doc.logo_img, self.doc.logo_pal, self.SCALE)
        self._ph = ImageTk.PhotoImage(im)
        self.canvas.configure(image=self._ph)
        for i, sw in enumerate(self._swatches):
            c = self.doc.logo_pal[i]
            sw.configure(bg="#%02x%02x%02x" % tuple(c))

    def pick_color(self, i):
        dlg = ColorDialog(self, self.doc.logo_pal[i],
                           title="Logo 第 %d 个颜色" % (i + 1),
                           hint="Logo 只有 31×7 个点，颜色之间差得太小就看不出字了",
                           swatches=self.doc.logo_pal)
        if dlg.result is None:
            return
        self.doc.logo_pal[i] = tuple(dlg.result)
        self.refresh()
        self.app.refresh_all()

    def pick_image(self):
        p = filedialog.askopenfilename(
            title="选一张图当 Logo（会自动缩放到 31×7）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif"), ("全部", "*.*")])
        if not p:
            return
        try:
            im = Image.open(p).convert("RGB")
        except Exception as e:
            messagebox.showerror(APP_TITLE, "打不开这张图：\n%s" % e)
            return
        idx, pal = logo_from_image(im)
        self.doc.logo_img = idx
        self.doc.logo_pal = pal
        self.refresh()
        self.app.refresh_all()

    def reset(self):
        self.doc.reset_logo()
        self.refresh()
        self.app.refresh_all()

    def _close(self):
        self.destroy()


class CropDialog(tk.Toplevel):
    """壁纸裁剪：拖动平移、滚轮或滑块缩放，输出固定 240×144。

    内部状态：s = 每个输出像素对应多少源像素（越大越"放大"），
    (cx, cy) = 裁剪框中心在源图里的坐标。裁剪框恒为 5:3（240:144），
    永远落在图内，所以不会出现黑边。

    2026-10-24：**右边那个「真机屏幕 1:1」小窗删了**。它画的是顶栏/底栏两个灰块
    加几道假文字，本来是为了说明「底栏会挡住最后 N 行」；但这套模拟跟实机差得远
    （固件里底栏是 FG 混 BG 的淡底、还要考虑有没有壁纸），留着反而误导。
    现在裁剪框里只有绿边框 + 三分线，遮挡说明改到底部文字里说。
    """

    VIEW_W, VIEW_H = 400, 288          # 画布（只剩裁剪区，右边那块 1:1 小窗已删）
    CROP_W, CROP_H = 384, 230          # 画面里裁剪框的像素尺寸（留一圈看得到框外）
    AREA_W = 400                       # 左边裁剪区宽度（裁剪框居中放里面）

    def __init__(self, master, src, state=None, cover_rows=16,
                 fgcolor=(40, 40, 40), bgcolor=(0, 0, 0), whole_set=False):
        super().__init__(master)
        self.title("调整壁纸裁剪")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        # fgcolor / bgcolor 现在只收不用（1:1 小窗删掉后没人画那两个色块了），
        # 参数留着是为了不动三处调用方。
        _ = (fgcolor, bgcolor)

        self.src = src.convert("RGB")
        self.sw, self.sh = self.src.size
        # 这一页的底栏会压掉最后几行（0 = 这页没有底栏）。画在裁剪框里，用户才知道
        # 「这块我精心画的东西其实看不见」——这是最容易被忽略的一处，实测被问过一次。
        self.cover_rows = max(0, min(int(cover_rows), sdtheme.WALL_H))
        # v0.39：**壁纸一律按 240×144 铺满存**，不再压进可见行 —— 压扁会让底栏
        # 半透明时透出来的只是底色，而且一改透明度就得重算，图会跳。
        # cover_rows 只剩两个用途：画真机小窗里那块底栏、以及把代价说清楚。
        cw, ch = sdtheme.WALL_W, sdtheme.WALL_H
        # s = 每个输出像素取多少源像素。放大（看更少像素）= s 变小。
        # 上限 s_fit：裁剪框刚好贴着图的一边（等于原来的 cover 效果）。
        self.s_fit = min(self.sw / float(cw), self.sh / float(ch))
        self.s_min = self.s_fit / 8.0        # 最多放大 8 倍
        self.s_max = self.s_fit
        self.s = self.s_fit
        self.cx, self.cy = self.sw / 2.0, self.sh / 2.0
        if state:
            self.s = max(self.s_min, min(self.s_max, state[0]))
            self.cx, self.cy = state[1], state[2]

        self.result = None            # 确定后放 240×144 的图
        self._drag = None
        self._img = None
        # 这套图会不会用在没有底栏的页面上（整套图会）。会的话压扁的图在那几页底部
        # 会多出一条底色，得当面说清楚，别让用户以为出 bug。
        self.whole_set = bool(whole_set)

        self._build()
        self._clamp()
        self._redraw()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.update_idletasks()
        center_on(self, master)        # 主窗口正中，别跑到屏幕左上角
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:            # 窗口还没映射出来时拿不到 grab，不影响用
            pass
        master.wait_window(self)

    # ---- 界面
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(head, text="拖动图片选位置 · 滚轮缩放", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        if not self.cover_rows:
            tail, tcol = "这一页没有底栏 · 图完整可见", ACC
        else:
            tail, tcol = ("存进文件 240×%d · 底栏盖住最后 %d 行"
                          % (sdtheme.WALL_H, self.cover_rows)), MUTED
        tk.Label(head, text=tail, bg=BG, fg=tcol,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        self.canvas = tk.Canvas(self, width=self.VIEW_W, height=self.VIEW_H,
                                bg="#000000", highlightthickness=0, cursor="fleur")
        self.canvas.pack(padx=12)
        # 做法只有一种（压扁进可见区），所以没有开关可摆；下面一行字把代价说清楚就够了。
        self.status_hint = tk.Label(
            self, bg=BG, anchor="w", justify="left",
            wraplength=self.VIEW_W,
            fg=(ACC if self.cover_rows else MUTED),
            font=("Microsoft YaHei UI", 9), text=self._status_text())
        self.status_hint.pack(fill="x", padx=14, pady=(6, 0))
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom(1.15))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(1 / 1.15))
        for w in (self, self.canvas):
            w.bind("<Left>", lambda e: self._nudge(-8, 0))
            w.bind("<Right>", lambda e: self._nudge(8, 0))
            w.bind("<Up>", lambda e: self._nudge(0, -8))
            w.bind("<Down>", lambda e: self._nudge(0, 8))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=10)
        tk.Label(bar, text="缩放", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.zoom = tk.Scale(bar, from_=1.0, to=8.0, resolution=0.05, orient="horizontal",
                             bg=BG, fg=TEXT, troughcolor=PANEL2, highlightthickness=0,
                             bd=0, length=210, width=10, sliderlength=18,
                             showvalue=False, command=self._slide)
        self.zoom.set(1.0)
        self.zoom.pack(side="left", padx=8)

        self._btn(bar, "重置", self._reset).pack(side="left", padx=4)
        self._btn(bar, "取消", self._cancel).pack(side="right", padx=(4, 0))
        self._btn(bar, "确定", self._ok, primary=True).pack(side="right")

    # ---- 「图会变成什么样」就说一句实话
    def _status_text(self):
        n = self.cover_rows
        if not n:
            return "这一页没有底栏，整张图都看得见，一个像素都不动。"
        t = ("按 240×144 原样存，原图 320×192 也是 5:3，比例不变。\n"
             "被底栏盖住的那几行照样存进文件，换到没有底栏的页面就能看见。")
        if self.whole_set:
            t += "\n这套图也会用在最近 / 界面 / 工具 三页，那几页整张都看得见。"
        return t

    def _btn(self, master, text, cmd, primary=False):
        return tk.Button(master, text=text, command=cmd,
                         bg=(ACC if primary else PANEL2),
                         fg=("#111" if primary else TEXT),
                         activebackground=(ACC if primary else LINE),
                         activeforeground=("#111" if primary else TEXT),
                         relief="flat", bd=0, padx=14, pady=5,
                         font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"),
                         cursor="hand2")

    # ---- 几何
    def _clamp(self):
        cw, ch = sdtheme.WALL_W * self.s, sdtheme.WALL_H * self.s
        self.cx = min(max(self.cx, cw / 2.0), self.sw - cw / 2.0)
        self.cy = min(max(self.cy, ch / 2.0), self.sh - ch / 2.0)

    def _rect(self):
        cw, ch = sdtheme.WALL_W * self.s, sdtheme.WALL_H * self.s
        x0, y0 = self.cx - cw / 2.0, self.cy - ch / 2.0
        return (int(round(x0)), int(round(y0)), int(round(x0 + cw)), int(round(y0 + ch)))

    # ---- 绘制
    def _wall_image(self, fast=True):
        """当前裁剪框里那块 → 240×144，再压进屏幕上真看得见的行。"""
        x0, y0, x1, y1 = self._rect()
        box = (max(0, x0), max(0, y0), min(self.sw, x1), min(self.sh, y1))
        rs = Image.BILINEAR if fast else Image.LANCZOS
        return self.src.crop(box).resize((sdtheme.WALL_W, sdtheme.WALL_H), rs)

    def _redraw(self, fast=True):
        d = self.CROP_W / (sdtheme.WALL_W * self.s)     # 屏幕像素 / 源像素
        w, h = max(1, int(round(self.sw * d))), max(1, int(round(self.sh * d)))
        resample = Image.BILINEAR if fast else Image.LANCZOS
        big = self.src.resize((w, h), resample)
        ox = int(round(self.AREA_W / 2.0 - self.cx * d))
        oy = int(round(self.VIEW_H / 2.0 - self.cy * d))

        x0 = (self.AREA_W - self.CROP_W) // 2
        y0 = (self.VIEW_H - self.CROP_H) // 2
        x1, y1 = x0 + self.CROP_W, y0 + self.CROP_H

        self._img = ImageTk.PhotoImage(big)
        c = self.canvas
        c.delete("all")
        c.create_image(ox, oy, image=self._img, anchor="nw")
        # 框外压暗（点阵抖动模拟半透明），一眼看出哪块会被裁掉。
        # 只压裁剪区这半边，右边真机预览一个像素都不许动。
        for bx0, by0, bx1, by1 in ((0, 0, self.AREA_W, y0),
                                   (0, y1, self.AREA_W, self.VIEW_H),
                                   (0, y0, x0, y1),
                                   (x1, y0, self.AREA_W, y1)):
            if bx1 > bx0 and by1 > by0:
                c.create_rectangle(bx0, by0, bx1, by1, fill="#000000",
                                   outline="", stipple="gray50")
        c.create_rectangle(x0, y0, x1, y1, outline=ACC, width=2)
        # 三分线，构图时对一下
        for i in (1, 2):
            c.create_line(x0 + self.CROP_W * i / 3.0, y0,
                          x0 + self.CROP_W * i / 3.0, y1, fill="#FFFFFF",
                          stipple="gray25")
            c.create_line(x0, y0 + self.CROP_H * i / 3.0,
                          x1, y0 + self.CROP_H * i / 3.0, fill="#FFFFFF",
                          stipple="gray25")

    def _press(self, e):
        self._drag = (e.x, e.y, self.cx, self.cy)
        self.canvas.configure(cursor="fleur")

    def _drag_move(self, e):
        if not self._drag:
            return
        x0, y0, cx0, cy0 = self._drag
        d = self.CROP_W / (sdtheme.WALL_W * self.s)
        self.cx = cx0 - (e.x - x0) / d
        self.cy = cy0 - (e.y - y0) / d
        self._clamp()
        self._redraw(fast=True)

    def _release(self, _e):
        self._drag = None
        self._redraw(fast=False)

    def _wheel(self, e):
        self._zoom(1.15 if e.delta > 0 else 1 / 1.15)

    def _nudge(self, dx, dy):
        """方向键微调（按输出的 8 像素走一步）。"""
        self.cx += dx * self.s
        self.cy += dy * self.s
        self._clamp()
        self._redraw(fast=False)

    def _zoom(self, k):
        """k>1 = 放大（裁剪框变小）。"""
        self.s = max(self.s_min, min(self.s_max, self.s / k))
        self.zoom.set(self.s_fit / self.s)
        self._clamp()
        self._redraw()

    def _slide(self, val):
        try:
            v = max(1.0, float(val))
        except (TypeError, ValueError):
            return
        self.s = max(self.s_min, min(self.s_max, self.s_fit / v))
        self._clamp()
        self._redraw()

    def _reset(self):
        self.s = self.s_fit
        self.cx, self.cy = self.sw / 2.0, self.sh / 2.0
        self.zoom.set(1.0)
        self._clamp()
        self._redraw(fast=False)

    def _ok(self):
        self.result = self._wall_image(fast=False)
        self.state = (self.s, self.cx, self.cy)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ---------------------------------------------------------------- 界面
class AboutDialog(tk.Toplevel):
    """关于：这程序干什么、谢谁、谁做的。

    内容刻意写成人话：先一句定位，再说用法，最后才署名。
    """

    # (署名, 做了什么, 显示出来的网址, 点击实际打开的地址)
    # 显示用短形式是因为 406px 宽的框放不下完整仓库路径；
    # 跳转给完整地址，点一下直接到那个项目，省得再找。
    CREDITS = [
        ("davidgf", "SuperFW 固件作者",
         "superfw.davidgf.net", "https://superfw.davidgf.net"),
        # 我们这版固件的直接上游：r78 的补丁引擎、Hack 库、FSJ 记录都来自这里
        ("moribaka", "superfw-fix 固件分支",
         "github.com/moribaka", "https://github.com/moribaka/superfw-fix"),
        ("ZaindORp", "SD 卡主题方案",
         "github.com/orzgithub",
         "https://github.com/orzgithub/omega-de-kernel_ZaindORp"),
        # 2026-10-24：字体换成文泉驿 10pt 之后，unscii / Unifont 已经不在固件里了，
        # 署名跟着换成真正在用的这三家。
        ("文泉驿", "点阵宋体 10pt",
         "wenq.org", "https://wenq.org"),
        ("Fusion Pixel", "缺字补全字体",
         "github.com/TakWolf", "https://github.com/TakWolf/fusion-pixel-font"),
        ("Electr0Whale", "字体打包（wqy10pt）",
         "github.com/Electr0Whale", "https://github.com/Electr0Whale/superfw"),
    ]

    def __init__(self, master):
        super().__init__(master)
        self.title("关于")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.destroy())
        self.update_idletasks()
        self._center(master)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()

    # ---- 链接
    @staticmethod
    def _open_url(url):
        """点网址用系统默认浏览器打开。没网/没浏览器时静默失败，别弹框。"""
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            import webbrowser
            webbrowser.open(url, new=2)
        except Exception:
            pass

    def _link(self, master, text, url, width=0):
        """带下划线、手型光标、悬停变色的网址标签。"""
        lab = tk.Label(master, text=text, bg=BG, fg=MUTED, anchor="w",
                       cursor="hand2", width=width,
                       font=("Microsoft YaHei UI", 8, "normal", "roman", "underline"))
        lab.bind("<Button-1>", lambda e: self._open_url(url))
        lab.bind("<Enter>", lambda e: lab.configure(fg=ACC))
        lab.bind("<Leave>", lambda e: lab.configure(fg=MUTED))
        return lab

    # ---- 界面
    def _rule(self):
        tk.Frame(self.box2, bg=LINE, height=1).pack(fill="x", pady=9)

    def _build(self):
        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", expand=True, padx=26, pady=20)
        self.box2 = box

        tk.Label(box, text="SuperFW SD 主题编辑器", bg=BG, fg=ACC, anchor="w",
                 font=("Microsoft YaHei UI", 15, "bold")).pack(fill="x")
        tk.Label(box, text="v%s   ·   给 GBA 烧录卡固件做 SD 卡主题包" % APP_VER,
                 bg=BG, fg=MUTED, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(2, 0))

        self._rule()

        tk.Label(box, text=(
            "调 6 套配色、图标和壁纸，还能给 7 个标签页各配一张背景。\n"
            "存成 theme.bin 放进 SD 卡的 /.superfw/ 目录，开机会自动读。\n"
            "固件刷一次，之后只换卡上的文件，不用重刷。"
        ), bg=BG, fg=TEXT, anchor="w", justify="left",
            font=("Microsoft YaHei UI", 9), wraplength=470).pack(fill="x")

        self._rule()

        tk.Label(box, text="感谢", bg=BG, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", pady=(0, 5))

        grid = tk.Frame(box, bg=BG)
        grid.pack(fill="x")
        for r, (who, what, shown, url) in enumerate(self.CREDITS):
            tk.Label(grid, text=who, bg=BG, fg=ACC, anchor="w", width=13,
                     font=("Microsoft YaHei UI", 9, "bold")).grid(
                row=r, column=0, sticky="w", pady=1)
            tk.Label(grid, text=what, bg=BG, fg=TEXT, anchor="w", width=16,
                     font=("Microsoft YaHei UI", 9)).grid(
                row=r, column=1, sticky="w", pady=1)
            if shown:
                self._link(grid, shown, url).grid(
                    row=r, column=2, sticky="w", pady=1)
            else:
                tk.Label(grid, text="", bg=BG).grid(row=r, column=2, sticky="w")

        tk.Label(box, text=(
            "EZODE 那套是把主题文件放卡上、格式沿用官方资源，换上即生效。\n"
            "这个工具照的就是这个思路——刷一次固件，之后只换卡上的文件。"
        ), bg=BG, fg=DIM, anchor="w", justify="left",
            font=("Microsoft YaHei UI", 8), wraplength=470).pack(fill="x", pady=(7, 0))

        self._rule()

        foot = tk.Frame(box, bg=BG)
        foot.pack(fill="x")
        tk.Button(foot, text="知道了", command=self.destroy, bg=ACC, fg="#111",
                  activebackground=ACC, activeforeground="#111", relief="flat", bd=0,
                  padx=18, pady=5, cursor="hand2",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")

        sign = tk.Frame(foot, bg=BG)
        sign.pack(side="left")
        line1 = tk.Frame(sign, bg=BG)
        line1.pack(anchor="w")
        tk.Label(line1, text="制作", bg=BG, fg=MUTED, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(line1, text="小米粥不糊", bg=BG, fg=ACC, anchor="w",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=(10, 0))
        tk.Label(sign, text="Python · Pillow · Tkinter", bg=BG, fg=DIM, anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))

    def _center(self, master):
        center_on(self, master)


class App:
    def __init__(self, root, doc):
        self.root = root
        self.doc = doc
        self.slot = 0
        self.swatches = []
        self.icon_buttons = []
        # 每套各记一份原图 + 裁剪状态，切套之后「裁剪…」还能重新调位置
        self.wall_src = [None] * 6
        self.wall_state = [None] * 6
        # 页面图：6 套 × 7 页，各一份原图 + 裁剪状态（和 doc.sp_* 同形）
        self.page_src = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        self.page_state = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
        # 预览/编辑目标：None = 这一套主题的图，0..6 = 某个页面的专属图。
        # 默认停在「文件」页：预览画的就是文件列表页，这样「只换这一页」一进来就能用。
        self.page_focus = 1
        # 只读预览：None = 主菜单；否则是 EXTRA_SCREENS 里的键
        self.extra = None
        # 撤销栈：只在破坏性/改内容的操作前压栈（见 _mark）
        self.undo = UndoStack()
        self._undo_busy = False
        self._scrollers = []

        root.title(APP_TITLE)
        root.configure(bg=BG)
        self.preview_scale = self._fit_geometry()

        self._build()
        self.refresh_all()
        self._refresh_undo()

        # 关窗口时提示未保存（tkinter 默认会直接销毁，改动就没了）
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 滚轮统一分发：贴在子控件上的 bind 收不到事件
        root.bind_all("<MouseWheel>", self._on_wheel)
        root.bind_all("<Control-z>", lambda e: self.undo_last())
        root.bind_all("<Control-Z>", lambda e: self.undo_last())
        self._fp = self._fingerprint()

    # ------------------------------------------------------------ 窗口尺寸
    def _fit_geometry(self):
        """按屏幕可用区定窗口大小，并决定预览放大倍数。

        `winfo_screenheight()` 给的是物理分辨率，不含任务栏和标题栏；
        1366×768 的笔记本实际只有 ~690 可用高度，直接上 766 会有半截按钮
        在屏幕外。所以先减掉一块，再让预览倍数跟着缩。
        """
        root = self.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        avail_w = max(900, sw - 60)
        avail_h = max(600, sh - 110)          # 任务栏 + 标题栏 + 余量
        w, h = min(1120, avail_w), min(766, avail_h)
        # 水平居中、稍微偏上一点（视觉上比正中舒服）
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 12)
        root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        root.minsize(min(960, w), min(620, h))
        # 预览 1 倍是 240×160。高度够就 2 倍（480×320），不够退到 1.5 倍。
        return 2.0 if h >= 720 else 1.5

    def _on_wheel(self, e):
        """把滚轮事件转给鼠标底下那个可滚动栏。"""
        w = self.root.winfo_containing(e.x_root, e.y_root)
        steps = int(-e.delta / 120) or (-1 if e.delta > 0 else 1)
        while w is not None:
            for sc in self._scrollers:
                if w is sc:
                    if sc.scroll(steps):
                        return "break"
                    return None
            w = getattr(w, "master", None)
        return None

    # ------------------------------------------------------------ 未保存判断
    def _fingerprint(self):
        """当前内容的指纹。只在关闭 / 打开文件时算一次，别放进刷新循环里。"""
        try:
            import hashlib
            return hashlib.sha1(self.doc.to_bytes()).digest()
        except Exception:
            return None

    def has_unsaved(self):
        """和上次打开/保存时相比，内容有没有变。"""
        if getattr(self, "_fp", None) is None:
            return False
        cur = self._fingerprint()
        return cur is not None and cur != self._fp

    def _mark_saved(self):
        self._fp = self._fingerprint()

    # ------------------------------------------------------------ 撤销
    def _capture(self):
        """一整套可回退状态：doc 内容 + 界面侧的原图/裁剪信息。

        原图缓存也必须进快照，否则撤回之后「裁剪…」会拿不到源图，
        用户会以为这张壁纸坏了。
        """
        return {
            "doc": self.doc.snapshot(),
            "wall_src": list(self.wall_src),
            "wall_state": list(self.wall_state),
            "page_src": [list(r) for r in self.page_src],
            "page_state": [list(r) for r in self.page_state],
        }

    def _mark(self, label):
        """在真的要改 doc 之前压一份快照。撤销过程中不再压（防自噬）。"""
        if self._undo_busy:
            return
        self.undo.push(label, self._capture())
        self._refresh_undo()

    def undo_last(self):
        item = self.undo.pop()
        if item is None:
            messagebox.showinfo(APP_TITLE, "已经没有可撤回的操作了。")
            return
        label, st = item
        self._undo_busy = True
        try:
            self.doc.restore(st["doc"])
            self.wall_src = list(st["wall_src"])
            self.wall_state = list(st["wall_state"])
            self.page_src = [list(r) for r in st["page_src"]]
            self.page_state = [list(r) for r in st["page_state"]]
            self.refresh_all()
        finally:
            self._undo_busy = False
        self._refresh_undo()
        self.status.configure(
            text="已撤回「%s」%s" % (label, "" if len(self.undo) else "（栈已空）"))

    def _refresh_undo(self):
        btn = getattr(self, "btn_undo", None)
        if btn is None:
            return
        if len(self.undo):
            btn.configure(text="撤回", state="normal", fg=TEXT,
                          cursor="hand2")
        else:
            btn.configure(text="撤回", state="disabled", fg=DIM,
                          cursor="arrow")

    def _confirm_discard(self, action):
        """有未保存改动时问一句。返回 True 表示可以继续（已保存/愿意丢弃）。"""
        if not self.has_unsaved():
            return True
        ans = messagebox.askyesnocancel(
            APP_TITLE,
            "当前改动还没保存，%s之前要保存吗？\n\n"
            "选「是」保存后继续，选「否」直接丢掉，选「取消」什么都不做。" % action)
        if ans is None:
            return False
        if ans:
            self.save()
            return not self.has_unsaved()      # 保存被取消或失败就停下
        return True

    def on_close(self):
        if not self._confirm_discard("关闭"):
            return
        self.root.destroy()

    def show_about(self):
        AboutDialog(self.root)

    def show_logo(self):
        """改 SUPERFW 标志（关于页 + 游戏内菜单共用）。"""
        LogoDialog(self.root, self)
        self.refresh_all()

    # ------------------------------------------------------------ 布局
    def _build(self):
        top = tk.Frame(self.root, bg=PANEL, height=44)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)
        self._btn(top, "打开 theme.bin…", self.open_file, primary=True).pack(side="left", padx=(12, 6), pady=8)
        self._btn(top, "另存为…", self.save_as).pack(side="left", padx=6, pady=8)
        self._btn(top, "保存", self.save).pack(side="left", padx=6, pady=8)
        # 撤回放在保存旁边：改坏了第一反应是往这儿点（Ctrl+Z 同效）
        self.btn_undo = self._btn(top, "撤回", self.undo_last)
        self.btn_undo.pack(side="left", padx=6, pady=8)
        self._btn(top, "恢复内置皮肤", self.reset_doc).pack(side="left", padx=6, pady=8)
        self.lbl_file = tk.Label(top, text="（新建，未保存）", bg=PANEL, fg=MUTED,
                                 font=("Microsoft YaHei UI", 9))
        self.lbl_file.pack(side="right", padx=(10, 12))
        self._btn(top, "关于", self.show_about).pack(side="right", padx=4, pady=8)

        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=12, pady=10)

        # 左右两栏都套滚动条：1366×768 或 125% 缩放时内容比窗口高，
        # 以前底部按钮直接被裁掉、点不到。
        left = _Scroll(body, width=302)
        left.pack(side="left", fill="y")
        mid = tk.Frame(body, bg=BG)
        mid.pack(side="left", fill="both", expand=True, padx=10)
        right = _Scroll(body, width=292)
        right.pack(side="right", fill="y")
        self._scrollers = [left, right]

        self._build_left(left.body)
        self._build_mid(mid)
        self._build_right(right.body)

        self.status = tk.Label(self.root, text="", bg=PANEL2, fg=MUTED, anchor="w",
                               font=("Microsoft YaHei UI", 9), padx=12)
        self.status.pack(side="bottom", fill="x")

    def _btn(self, master, text, cmd, primary=False, width=None):
        return tk.Button(master, text=text, command=cmd,
                         bg=(ACC if primary else PANEL2),
                         fg=("#111" if primary else TEXT),
                         activebackground=(ACC if primary else LINE),
                         activeforeground=("#111" if primary else TEXT),
                         relief="flat", bd=0, padx=12, pady=5,
                         font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"),
                         cursor="hand2", width=width)

    def _build_left(self, box):
        tk.Label(box, text="① 六套配色", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=12, pady=(10, 6))

        # 两列排：6 套占 3 行，比一列省 60 多像素 —— 左栏下面还要塞「整屏界面」三格
        self.slot_frame = tk.Frame(box, bg=PANEL)
        self.slot_frame.pack(fill="x", padx=8)
        self.slot_buttons = []
        for i in range(6):
            cell = tk.Frame(self.slot_frame, bg=PANEL, cursor="hand2")
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 6), pady=1)
            strip = tk.Canvas(cell, width=46, height=18, bg=PANEL, highlightthickness=0)
            strip.pack(side="left", padx=(4, 6))
            lab = tk.Label(cell, text="第 %d 套" % (i + 1), bg=PANEL, fg=MUTED,
                           anchor="w", font=("Microsoft YaHei UI", 9))
            lab.pack(side="left")
            for w in (cell, strip, lab):
                w.bind("<Button-1>", lambda e, n=i: self.select_slot(n))
            self.slot_buttons.append((cell, strip, lab))

        tk.Frame(box, bg=LINE, height=1).pack(fill="x", padx=12, pady=10)

        chead = tk.Frame(box, bg=PANEL)
        chead.pack(fill="x", padx=12, pady=(6, 2))
        tk.Label(chead, text="颜色（点方块改）", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

        self.color_rows = []
        for i, (code, hint, detail) in enumerate(ROLES):
            row = tk.Frame(box, bg=PANEL2)
            row.pack(fill="x", padx=12, pady=1)
            sw = tk.Canvas(row, width=22, height=22, highlightthickness=1,
                           highlightbackground=LINE, bg=BG, cursor="hand2")
            sw.pack(side="left", padx=(4, 8), pady=(4, 3), anchor="n")
            sw.bind("<Button-1>", lambda e, n=i: self.pick_color(n))
            col = tk.Frame(row, bg=PANEL2)
            col.pack(side="left", fill="x", expand=True, pady=(4, 4))
            top = tk.Frame(col, bg=PANEL2)
            top.pack(fill="x")
            lab = tk.Label(top, text="%s  %s" % (code, hint), bg=PANEL2, fg=TEXT,
                           anchor="w", font=("Microsoft YaHei UI", 8, "bold"))
            lab.pack(side="left")
            val = tk.Label(top, text="#000000", bg=PANEL2, fg=MUTED,
                           font=("Consolas", 8))
            val.pack(side="right", padx=(4, 6))
            # 第二行细说：哪些界面真的在吃这一格
            tk.Label(col, text=detail, bg=PANEL2, fg=MUTED, anchor="w", justify="left",
                     wraplength=238, font=("Microsoft YaHei UI", 8)).pack(fill="x")
            self.color_rows.append((sw, val))

        # ---- 整屏界面：这三格管「载入游戏那屏」和「L+R 菜单」，主菜单不吃。
        # 以前是独立弹窗（左栏塞不下），现在并进同一个列表、分一组。
        # 默认「跟随」= 等于这一套的 BG / FG，文件里不写（不升版本）。
        tk.Frame(box, bg=LINE, height=1).pack(fill="x", padx=12, pady=(6, 3))
        ehead = tk.Frame(box, bg=PANEL)
        ehead.pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(ehead, text="整屏界面", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        tk.Label(ehead, text="载入那屏 / L+R 菜单", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=6)
        self.btn_follow_all = tk.Button(
            ehead, text="全部跟随", command=self.follow_all_excolors,
            bg=PANEL2, fg=MUTED, relief="flat", bd=0, padx=7, pady=1,
            cursor="hand2", activebackground=LINE, activeforeground=TEXT,
            font=("Microsoft YaHei UI", 8))
        self.btn_follow_all.pack(side="right")

        self.ex_rows = []
        for j, (name, detail) in enumerate(EXCOL_ROWS):
            row = tk.Frame(box, bg=PANEL2)
            row.pack(fill="x", padx=12, pady=2)
            sw = tk.Canvas(row, width=22, height=22, highlightthickness=1,
                           highlightbackground=LINE, bg=BG, cursor="hand2")
            sw.pack(side="left", padx=(4, 8), pady=4)
            sw.bind("<Button-1>", lambda e, n=j: self.pick_excolor(n))
            btn = tk.Button(row, text="跟随", command=lambda n=j: self.follow_excolor(n),
                            bg=PANEL, fg=MUTED, relief="flat", bd=0, padx=6, pady=0,
                            cursor="hand2", activebackground=LINE,
                            activeforeground=TEXT, font=("Microsoft YaHei UI", 7))
            btn.pack(side="right", padx=(4, 6))
            val = tk.Label(row, text="", bg=PANEL2, fg=MUTED,
                           font=("Consolas", 8))
            val.pack(side="right", padx=(4, 0))
            lab = tk.Label(row, text=name, bg=PANEL2, fg=TEXT, anchor="w",
                           font=("Microsoft YaHei UI", 8, "bold"))
            lab.pack(side="left")
            # 单行版：这三格名字已经够自解释，说明放进 tooltip 级别的短句反而占高度
            for w in (row, sw, lab, val):
                w.bind("<Button-1>", lambda e, n=j: self.pick_excolor(n))
            self.ex_rows.append((sw, val, btn))

    def _build_mid(self, box):
        self.lbl_preview_title = tk.Label(box, text="② 预览 · 第 1 套",
                                          bg=BG, fg=TEXT, anchor="w",
                                          font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_preview_title.pack(fill="x")

        # 页面切换条：7 个标签页各自可以配一张。这一套共用的默认图不再是左边一个
        # 「整套」按钮（那个绿块用户看着别扭，v0.37 删掉），改由右上角「整套视图 ›」进出。
        bar = tk.Frame(box, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        grid = tk.Frame(bar, bg=BG)
        grid.pack(side="left", fill="x", expand=True)
        self.page_buttons = []
        for idx, nm in enumerate(sdtheme.PAGE_NAMES):
            # 等宽铺满整条：窗口缩到最小也放得下「页面设置」这四个字
            grid.columnconfigure(idx, weight=1, uniform="pg")
            b = tk.Button(grid, text=nm, bg=PANEL, fg=TEXT, activebackground=LINE,
                          activeforeground=TEXT, relief="flat", bd=0, padx=2, pady=2,
                          cursor="hand2", font=("Microsoft YaHei UI", 8),
                          command=lambda n=idx: self.select_target(n))
            b.grid(row=0, column=idx, sticky="ew", padx=1)
            self.page_buttons.append((idx, b))

        bar2 = tk.Frame(box, bg=BG)
        bar2.pack(fill="x", pady=(4, 0))
        tk.Label(bar2, text="其它界面：", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.extra_buttons = []
        for key, nm in EXTRA_SCREENS:
            b = tk.Button(bar2, text=nm, bg=PANEL, fg=TEXT, activebackground=LINE,
                          activeforeground=TEXT, relief="flat", bd=0, padx=6, pady=1,
                          cursor="hand2", font=("Microsoft YaHei UI", 8),
                          command=lambda k=key: self.select_extra(k))
            b.pack(side="left", padx=2)
            self.extra_buttons.append((key, b))
        tk.Button(bar2, text="改 Logo…", command=self.show_logo,
                  bg=PANEL, fg=ACC, relief="flat", bd=0, padx=7, pady=1,
                  cursor="hand2", font=("Microsoft YaHei UI", 8),
                  activebackground=LINE, activeforeground=ACC).pack(side="right")

        self.canvas = tk.Label(box, bg=BG, bd=0)
        self.canvas.pack(pady=8)
        tk.Label(box, text="上面 7 个按钮就是 7 个页面，点哪个就改哪个。\n"
                           "没单独配图的页面，用的是这套的「整套图」。\n"
                           "想换整套图，点右上角「整套视图 ›」回去换。",
                 bg=BG, fg=DIM, justify="left", anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x")


    def _build_right(self, box):
        hdr = tk.Frame(box, bg=PANEL)
        hdr.pack(fill="x", padx=12, pady=(10, 6))
        self.lbl_wall_title = tk.Label(hdr, text="③ 壁纸", bg=PANEL, fg=TEXT,
                                       anchor="w", font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_wall_title.pack(side="left")
        self.btn_default = tk.Button(hdr, text="换整套图", command=lambda: self.select_target(-1),
                                     bg=PANEL, fg=MUTED, relief="flat", bd=0, padx=4, pady=0,
                                     activebackground=LINE, activeforeground=TEXT,
                                     font=("Microsoft YaHei UI", 8), cursor="hand2")
        self.btn_default.pack(side="right")
        self.wall_thumb = tk.Label(box, bg=BG, bd=0)
        self.wall_thumb.pack(pady=4)
        # 这张图有多少被底栏吃掉 —— 缩略图上已经压暗了，这里再说一遍数字
        # height=2：这段字会随「有没有底栏 / 底栏透不透明」换说法，长短不一。
        # 不固定高度的话，拖透明度滑块时它从两行缩成一行，下面整块按钮跟着跳。
        # 注意：wraplength 原来写死 232，但这一栏实际只有 ~176 宽，
        # 文字会被窗口边缘切掉（v0.31 发布前发现）。改成跟着容器宽度走。
        self.lbl_wall_cover = tk.Label(box, text="", bg=PANEL, fg=MUTED, anchor="nw",
                                       font=("Microsoft YaHei UI", 8), wraplength=176,
                                       justify="left", height=3)
        self.lbl_wall_cover.pack(fill="x", padx=12)
        box.bind("<Configure>",
                 lambda e: [l.configure(wraplength=max(96, e.width - 28))
                            for l in (self.lbl_wall_cover, self.wall_note)])
        bar = tk.Frame(box, bg=PANEL)
        bar.pack(fill="x", padx=12)
        self.btn_set = self._btn(bar, "整套换图…", self.pick_wall)
        self.btn_set.pack(side="left")
        bar2 = tk.Frame(box, bg=PANEL)
        bar2.pack(fill="x", padx=12, pady=(4, 0))
        self.btn_page = self._btn(bar2, "只换这一页…", self.pick_page_wall)
        self.btn_page.pack(side="left")
        self.btn_extra = self._btn(bar2, "清页面图", self.clear_page_walls)
        self.btn_extra.pack(side="left", padx=(6, 0))
        self.wall_note = tk.Label(box, text="", bg=PANEL, fg=MUTED, anchor="w",
                                  font=("Microsoft YaHei UI", 8), wraplength=176,
                                  justify="left")
        self.wall_note.pack(fill="x", padx=12, pady=(4, 0))
        # 规格写死在这儿，省得每次都问
        tk.Label(box, text="建议用 320×192 以上的图 · 拖动 / 滚轮调位置\n"
                           "颜色会自动减到 220 色（GBA 上限）\n"
                           "安全构图区 240×122（底下最多 22 行会被底栏盖住）",
                 bg=PANEL, fg=DIM, anchor="w", justify="left",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=12)

        tk.Frame(box, bg=LINE, height=1).pack(fill="x", padx=12, pady=4)

        tk.Label(box, text="④ 图标（点一个换图）", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=12)
        tk.Label(box, text="用透明 PNG · 自动缩成 16×16 · 合计不超过 37 色",
                 bg=PANEL, fg=DIM, anchor="w", justify="left",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=12)
        grid = tk.Frame(box, bg=PANEL)
        grid.pack(padx=10, pady=4)
        for i in range(17):
            b = tk.Button(grid, bg=PANEL2, activebackground=LINE, relief="flat", bd=0,
                          width=36, height=36, cursor="hand2",
                          command=lambda n=i: self.pick_icon(n))
            b.grid(row=i // 5, column=i % 5, padx=2, pady=2)
            self.icon_buttons.append(b)

        ops = tk.Frame(box, bg=PANEL)
        ops.pack(fill="x", padx=12)
        self._btn(ops, "复制到其余 5 套", self.copy_icons).pack(side="left")
        self._btn(ops, "恢复内置", self.reset_icons).pack(side="left", padx=6)

    # ------------------------------------------------------------ 刷新
    def select_slot(self, n):
        self.slot = n
        self.refresh_all()

    def refresh_all(self):
        self.refresh_slots()
        self.refresh_colors()
        self.refresh_preview()
        self.refresh_wall()
        self.refresh_icons()
        self.refresh_status()

    def clear_hib(self, set_i=None):
        self._mark("清除选中条纹理")
        if set_i is None:
            for i in range(sdtheme.THEME_COUNT):
                self.doc.set_hib(i, None, None)
        else:
            self.doc.set_hib(set_i, None, None)
        self.refresh_all()

    def refresh_slots(self):
        for i, (row, strip, lab) in enumerate(self.slot_buttons):
            on = (i == self.slot)
            row.configure(bg=(PANEL2 if on else PANEL))
            lab.configure(bg=(PANEL2 if on else PANEL), fg=(ACC if on else MUTED))
            strip.delete("all")
            pal = self.doc.pals[i]
            for k, c in enumerate(pal):
                strip.create_rectangle(k * 9, 0, k * 9 + 8, 17, fill="#%02x%02x%02x" % c,
                                       outline="")

    def refresh_colors(self):
        for i, (sw, val) in enumerate(self.color_rows):
            c = self.doc.pals[self.slot][i]
            hx = "#%02x%02x%02x" % c
            sw.configure(bg=hx)
            val.configure(text=hx.upper())

        # 整屏界面那三格：色块画的是「实际生效色」，右边写「跟随 BG」或色值
        rows = getattr(self, "ex_rows", [])
        if not rows:
            return
        eff = self.doc.excol_of(self.slot)
        stored = self.doc.excol[self.slot]
        pal = self.doc.pals[self.slot]
        dflt = (tuple(pal[1]), tuple(pal[0]), tuple(pal[1]))   # BG, FG, BG
        # 「显示成跟随」有两种：没存过（None），或者存的值恰好等于默认色。
        # 后者是打开老文件时的常态（文件里写了，但和 BG/FG 一样），也得说成跟随，
        # 否则用户以为改过、还以为白占了 36 字节。
        follows = [stored[j] is None or tuple(stored[j]) == dflt[j]
                   for j in range(len(rows))]
        for j, (sw, val, btn) in enumerate(rows):
            c = tuple(eff[j])
            sw.configure(bg="#%02x%02x%02x" % c)
            if follows[j]:
                val.configure(text="跟随 %s" % EXCOL_FOLLOW[j], fg=DIM)
            else:
                val.configure(text=("#%02x%02x%02x" % c).upper(), fg=MUTED)
            if stored[j] is None:
                btn.configure(text="已跟随", state="disabled", fg=DIM,
                              cursor="arrow")
            else:
                btn.configure(text="跟随", state="normal", fg=MUTED,
                              cursor="hand2")
        if getattr(self, "btn_follow_all", None) is not None:
            all_follow = all(follows)
            self.btn_follow_all.configure(
                state=("disabled" if all_follow else "normal"),
                fg=(DIM if all_follow else MUTED),
                cursor=("arrow" if all_follow else "hand2"))

    def refresh_preview(self):
        im = render_preview(self.doc, self.slot, page=self.page_focus, extra=self.extra)
        self._preview_src = im          # 1 倍图，探针/导出用
        sc = getattr(self, "preview_scale", SCALE)
        big = im.resize((int(W * sc), int(H * sc)), Image.NEAREST)
        self._preview_img = ImageTk.PhotoImage(big)
        self.canvas.configure(image=self._preview_img)
        if self.extra:
            t = "② 预览 · 第 %d 套 · %s（只读）" % (
                self.slot + 1, dict(EXTRA_SCREENS)[self.extra])
        elif self.page_focus is None:
            t = "② 预览 · 第 %d 套" % (self.slot + 1)
        else:
            fixed = self.doc.sp_rgb[self.slot][self.page_focus] is not None
            t = "② 预览 · 第 %d 套 · %s页 · %s" % (
                self.slot + 1, sdtheme.PAGE_NAMES[self.page_focus],
                "本页单独配图" if fixed else "跟随整套图")
        self.lbl_preview_title.configure(text=t)

    def _target(self):
        """当前在编辑哪个壁纸槽：("set", 套号) 或 ("page", 页号)。"""
        if self.page_focus is None:
            return "set", self.slot
        return "page", self.page_focus

    def select_target(self, idx):
        """点预览上方那一排：-1 = 这套的默认图，0..6 = 某个标签页。"""
        self.page_focus = None if idx < 0 else idx
        self.extra = None
        self.refresh_all()

    def select_extra(self, key):
        """点「其它界面」里的按钮：只读预览，再点一次（或点别的页）取消。

        进预览前把当前停在哪个页面记下来，退出时还原 —— 否则看完预览回来
        会掉回「整套」，右边「只换这一页」又变灰，用户会以为是坏了。
        """
        if self.extra == key:
            self.extra = None
            self.page_focus = getattr(self, "_page_before_extra", 1)
        else:
            if self.extra is None:
                self._page_before_extra = self.page_focus
            self.extra = key
        self.refresh_all()

    def refresh_wall(self):
        if self.extra:
            # 只读预览：壁纸区全部禁用，缩略图显示这个界面的内容区
            name = dict(EXTRA_SCREENS)[self.extra]
            self.lbl_wall_title.configure(text="③ 壁纸 · 预览「%s」" % name)
            self.btn_default.configure(text="回到主菜单 ›",
                                       command=lambda: self.select_extra(self.extra))
            for b in (self.btn_set, self.btn_page, self.btn_extra):
                b.configure(state="disabled")
            self.wall_note.configure(
                text="只读预览：看这套配色在「%s」里长什么样。\n"
                     "%s\n"
                     "点上面某一页，或点右上角「回到主菜单」，就能继续编辑。"
                     % (name, EXTRA_USES.get(self.extra, "")))
            im = render_preview(self.doc, self.slot, extra=self.extra).crop((0, 16, 240, 160))
            self._wall_img = ImageTk.PhotoImage(im.resize((240, 144), Image.NEAREST))
            self.wall_thumb.configure(image=self._wall_img)
            self.lbl_wall_cover.configure(
                text="这张缩略图是真机画出来的那一层，底栏已经压在上面了", fg=DIM)
            for idx_, b in self.page_buttons:
                b.configure(bg=PANEL, fg=TEXT)
            for key, b in self.extra_buttons:
                on = key == self.extra
                b.configure(bg=ACC if on else PANEL, fg="#111" if on else TEXT,
                            activebackground=ACC if on else LINE)
            return

        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind, self.slot)
        if kind == "page":
            srcs, sts = self.page_src[self.slot], self.page_state[self.slot]
        else:
            srcs, sts = self.wall_src, self.wall_state
        n = self.doc.wall_count()

        if kind == "page":
            self.lbl_wall_title.configure(text="③ 壁纸 · %s页" % sdtheme.PAGE_NAMES[i])
            self.btn_default.configure(text="整套视图 ›",
                                       command=lambda: self.select_target(-1))
        else:
            self.lbl_wall_title.configure(text="③ 壁纸 · 第 %d 套" % (i + 1))
            self.btn_default.configure(text="只看某一页 ›",
                                       command=lambda: self.select_target(0))

        fallback = (kind == "page" and rgb[i] is None
                    and self.doc.wall_rgb[self.slot] is not None)
        if rgb[i] is None:
            if fallback:
                ph = self.doc.wall_rgb[self.slot]
                self.wall_note.configure(
                    text="%s页没有单独的图，现在用的是第 %d 套的整套图"
                         % (sdtheme.PAGE_NAMES[i], self.slot + 1))
            else:
                ph = Image.new("RGB", (240, 144), (20, 20, 18))
                d = ImageDraw.Draw(ph)
                d.text((70, 64), "no wallpaper", fill=(90, 88, 78))
                self.wall_note.configure(
                    text=("第 %d 套没有壁纸 → 内容区用 BG 纯色" % (i + 1)) if kind == "set"
                    else ("%s页没有单独的图，这套也没有整套图 → 内容区用纯色"
                          % sdtheme.PAGE_NAMES[i]))
        else:
            ph = rgb[i]
            if kind == "page":
                self.wall_note.configure(
                    text="%s页：第 %d 套单独配的图（只影响这一页）"
                         % (sdtheme.PAGE_NAMES[i], self.slot + 1))
            elif kind == "set" and self.shared_wall():
                self.wall_note.configure(
                    text="第 %d 套有壁纸，但 6 套用的是同一张 → 切主题背景不变。"
                         "想各不相同，切到各套分别选图。" % (i + 1))
            else:
                np = self.doc.page_count_set(i)
                self.wall_note.configure(
                    text="第 %d 套有壁纸 · 全文件共 %d 张%s"
                         % (i + 1, n, ("；这套另有 %d 页配了专属图" % np) if np else ""))

        # 「只换这一页」只在选中某一页时可用；「清页面图」只在这套真有页面图时可用
        self.btn_page.configure(state=("normal" if kind == "page" else "disabled"))
        np = self.doc.page_count_set(self.slot)
        self.btn_extra.configure(
            text=("清页面图（%d）" % np) if np else "清页面图",
            state=("normal" if np else "disabled"))

        # 这张图会被底栏盖掉几行。站在某一页上说那一页的行数，说整套图时按
        # 盖得最多的「关于」页算 —— 否则「这套看着没事、切到关于页就少一块」。
        # 缩略图上不压暗、不画红线：图和提示要分开，压上去就看不见自己的图了。
        is_page = (kind == "page")
        own_page = (is_page and rgb[i] is not None and not fallback)
        worst = max(bar_cover_rows(p) for p in range(sdtheme.PAGE_COUNT))
        cover = bar_cover_rows(i) if is_page else worst
        # v0.39：壁纸一律铺满 240×144（原比例），区别只在「底栏盖掉几行」。
        # 这句话**不跟透明度变** —— 以前底栏一开透明就换一句短的，一长一短来回跳。
        # 现在只有「有没有底栏 / 是这一页还是整套」两种情况会换话。
        if cover <= 0:
            self.lbl_wall_cover.configure(
                text="这一页没有底栏，图完整可见", fg=ACC)
        elif own_page:
            self.lbl_wall_cover.configure(
                text="整张图 240×144（原比例）· 这一页底栏盖住最后 %d 行" % cover,
                fg=ACC)
        elif is_page:
            self.lbl_wall_cover.configure(
                text="整张图 240×144（原比例）· 这一页盖住 %d 行；这套图在「关于」页最多 %d 行"
                     % (cover, worst), fg=ACC)
        else:
            self.lbl_wall_cover.configure(
                text="整张图 240×144（原比例）· 底栏最多盖住 %d 行（在「关于」页）" % worst,
                fg=ACC)
        self._wall_img = ImageTk.PhotoImage(ph.resize((240, 144), Image.NEAREST))
        self.wall_thumb.configure(image=self._wall_img)

        self.btn_set.configure(state="normal")
        for key, b in self.extra_buttons:
            b.configure(bg=PANEL, fg=TEXT, activebackground=LINE)

        # 切换条：选中的用强调色底，另外把「有专属图」的页标成强调色字。
        # 整套视图（page_focus is None）时一个都不高亮，标题会写「③ 壁纸 · 第 N 套」。
        want = -1 if self.page_focus is None else self.page_focus
        for idx_, b in self.page_buttons:
            has = (idx_ >= 0 and self.doc.sp_rgb[self.slot][idx_] is not None)
            if idx_ == want:
                b.configure(bg=ACC, fg="#111", activebackground=ACC, activeforeground="#111")
            else:
                # 文字统一用正文色（不再有的黄有的灰）；有没有专属图看底色深浅
                b.configure(bg=(PANEL2 if has else PANEL), fg=TEXT,
                            activebackground=LINE, activeforeground=TEXT)

        self._sync_set_view()

    def _sync_set_view(self):
        """整套视图时把右上角那个入口点亮 —— 页面条上已经没有「整套」按钮了。

        文字和命令由 refresh_wall 按当前是「整套」还是「某一页」设置，这里只管颜色。
        """
        self.btn_default.configure(fg=(ACC if self.page_focus is None else MUTED))

    def refresh_icons(self):
        for i, b in enumerate(self.icon_buttons):
            ic = self.doc.icon_imgs[self.slot][i].resize((32, 32), Image.NEAREST)
            bgc = Image.new("RGBA", ic.size, (25, 25, 22, 255))
            bgc.alpha_composite(ic)
            self._icon_imgs = getattr(self, "_icon_imgs", {})
            ph = ImageTk.PhotoImage(bgc.convert("RGB"))
            self._icon_imgs[i] = ph
            b.configure(image=ph, text="")

    def shared_wall(self):
        """6 套都有图且完全一样 → 保存时会合并成 v1，切主题背景不变。"""
        rgb = [w for w in self.doc.wall_rgb]
        if any(w is None for w in rgb):
            return False
        first = rgb[0].tobytes()
        return all(w.tobytes() == first for w in rgb[1:])

    def refresh_status(self):
        n = self.doc.wall_count()
        np = self.doc.page_count_set(self.slot)     # 只报当前这一套的页数
        nall = self.doc.page_count()
        size = self.doc.packed_size()
        glob, per = self.doc.page_split()
        if self.doc.has_hib():
            ver = 8
        elif self.doc.has_alpha():
            ver = 7
        elif self.doc.has_logo():
            ver = 6
        elif self.doc.has_excol():
            ver = 5
        elif per:
            ver = 4
        elif glob:
            ver = 3
        elif size >= sdtheme.FILE_SIZE_V2:
            ver = 2
        else:
            ver = 1
        name = os.path.basename(self.doc.path) if self.doc.path else "（新建，未保存）"
        self.lbl_file.configure(text=name)
        wall_txt = ("%d/6（共用一张）" % n) if self.shared_wall() else ("%d/6" % n)
        page_txt = "%d/7" % np
        if nall != np:
            page_txt += "（全文件 %d）" % nall
        self.status.configure(text="第 %d 套 · 壁纸 %s · 这一套的页面图 %s · 保存后 %d 字节 · 格式 v%d · %s"
                              % (self.slot + 1, wall_txt, page_txt, size, ver,
                                 "点色块改色 · 点图标换图 · 点页面按钮配单独背景"))

    # ------------------------------------------------------------ 操作
    def pick_color(self, idx):
        dlg = ColorDialog(self.root, self.doc.pals[self.slot][idx],
                          title="第 %d 套 · %s" % (self.slot + 1, ROLES[idx][1]),
                          hint="影响：%s" % ROLES[idx][2],
                          swatches=self.doc.pals[self.slot])
        if dlg.result is None:
            return
        old = tuple(self.doc.pals[self.slot][idx])
        new = tuple(dlg.result)
        if old == new:
            return
        self._mark("改 %s 颜色" % ROLES[idx][0])
        self.doc.pals[self.slot][idx] = new
        self.refresh_all()

    # ---- 整屏界面那三格（并进颜色列表，不再单开弹窗）
    def pick_excolor(self, j):
        name, detail = EXCOL_ROWS[j]
        eff = tuple(self.doc.excol_of(self.slot)[j])
        dlg = ColorDialog(self.root, eff,
                          title="第 %d 套 · %s" % (self.slot + 1, name),
                          hint="影响：%s。点「跟随」可以恢复成跟 %s 一样。"
                               % (detail, EXCOL_FOLLOW[j]),
                          swatches=list(self.doc.pals[self.slot]))
        if dlg.result is None:
            return
        new = tuple(dlg.result)
        if eff == new:
            return
        self._mark("改 %s" % name)
        self.doc.excol[self.slot][j] = new
        self.refresh_all()

    def follow_excolor(self, j):
        """把这一格改回「跟随 BG / FG」——文件里就不写它了。"""
        if self.doc.excol[self.slot][j] is None:
            return
        self._mark("%s 改回跟随" % EXCOL_ROWS[j][0])
        self.doc.excol[self.slot][j] = None
        self.refresh_all()

    def follow_all_excolors(self):
        if all(v is None for v in self.doc.excol[self.slot]):
            return
        self._mark("整屏界面全部跟随")
        self.doc.excol[self.slot] = [None] * sdtheme.EXCOL_N
        self.refresh_all()

    def _theme_fgbg(self):
        """当前这套的 (FG, BG)，拿去做真机预览的顶栏/底栏色和补边色。"""
        pal = self.doc.pals[self.slot]
        return tuple(pal[0]), tuple(pal[1])

    def _choose_wall_image(self, title, state=None, cover_rows=16, whole_set=False):
        """选图 + 裁剪。返回 (原图, 裁剪状态, 结果) 或 None。"""
        p = filedialog.askopenfilename(
            title=title, filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif"), ("全部", "*.*")])
        if not p:
            return None
        try:
            im = Image.open(p).convert("RGB")
        except Exception as e:
            messagebox.showerror(APP_TITLE, "打不开这张图：\n%s" % e)
            return None
        fg, bg = self._theme_fgbg()
        dlg = CropDialog(self.root, im, state, cover_rows=cover_rows,
                         fgcolor=fg, bgcolor=bg, whole_set=whole_set)
        if dlg.result is None:
            return None
        return dlg.src, dlg.state, dlg.result

    def pick_wall(self):
        """整套换图：这张成为这一套的默认图，这一套 7 个页面全部用它。"""
        s_ = self.slot
        # 先问清楚：换整套会把这一套已有的页面专属图全清掉（以前是静默清）
        n = self.doc.page_count_set(s_)
        if n and not messagebox.askyesno(
                APP_TITLE,
                "第 %d 套现在有 %d 个页面配了专属图。\n\n"
                "换整套壁纸会把那 %d 张一起清掉，这套 7 个页面统一用新图。\n"
                "其余 5 套一个字节都不动。\n\n继续吗？（换完可以点「撤回」退回）"
                % (s_ + 1, n, n)):
            return
        got = self._choose_wall_image("第 %d 套的壁纸（这张会用到这套所有页面）"
                                      % (s_ + 1), self.wall_state[s_],
                                      cover_rows=BAR_COVER_MAX, whole_set=True)
        if got is None:
            return
        src, state, res = got
        self._mark("换第 %d 套的整套壁纸" % (s_ + 1))
        d = self.doc
        d.wall_rgb[s_] = res
        d.wall_dirty[s_] = True
        self.wall_src[s_] = src
        self.wall_state[s_] = state
        # 这套之前的页面专属图全部作废：现在整整齐齐都是这张
        for i in range(sdtheme.PAGE_COUNT):
            d.sp_rgb[s_][i] = None
            d.sp_idx[s_][i] = None
            d.sp_pal[s_][i] = None
            d.sp_dirty[s_][i] = False
            self.page_src[s_][i] = None
            self.page_state[s_][i] = None
        self.refresh_all()
        self.status.configure(
            text="第 %d 套换成新图，这套 7 个页面都用它（其余 5 套一个字节没动）" % (s_ + 1))

    def pick_page_wall(self):
        """只换这一页：这套的默认图和其它页面都不动。"""
        s_ = self.slot
        if self.page_focus is None:
            messagebox.showinfo(APP_TITLE, "先在预览上面点一下要改的页面（最近 / 文件 / 烧录…），"
                                           "再点这里。")
            return
        pg = self.page_focus
        got = self._choose_wall_image("第 %d 套 · %s页 的专属图"
                                      % (s_ + 1, sdtheme.PAGE_NAMES[pg]),
                                      self.page_state[s_][pg],
                                      cover_rows=bar_cover_rows(pg))
        if got is None:
            return
        src, state, res = got
        self._mark("换「%s」页的专属图" % sdtheme.PAGE_NAMES[pg])
        d = self.doc
        d.sp_rgb[s_][pg] = res
        d.sp_dirty[s_][pg] = True
        self.page_src[s_][pg] = src
        self.page_state[s_][pg] = state
        self.refresh_all()
        self.status.configure(
            text="只改了第 %d 套的「%s」页（其它页面、其余 5 套都没动）"
                 % (s_ + 1, sdtheme.PAGE_NAMES[pg]))

    def recrop_wall(self):
        """用同一张原图重新裁剪（换位置/缩放），不用再选一次文件。"""
        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind, self.slot)
        if kind == "page":
            srcs, sts = self.page_src[self.slot], self.page_state[self.slot]
        else:
            srcs, sts = self.wall_src, self.wall_state
        if srcs[i] is None:
            messagebox.showinfo(APP_TITLE, "这张壁纸没有原图信息\n"
                                           "（比如是从 theme.bin 打开的）。\n"
                                           "请重新点「选图片…」。")
            return
        fg, bg = self._theme_fgbg()
        dlg = CropDialog(self.root, srcs[i], sts[i],
                         cover_rows=(bar_cover_rows(i) if kind == "page"
                                     else BAR_COVER_MAX),
                         fgcolor=fg, bgcolor=bg, whole_set=(kind != "page"))
        if dlg.result is None:
            return
        self._mark("重新裁剪壁纸")
        sts[i] = dlg.state
        rgb[i] = dlg.result
        dirty[i] = True
        self.refresh_all()

    def clear_wall(self):
        """清掉当前这个槽位（这套的默认图，或某一页的专属图）。"""
        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind, self.slot)
        if kind == "page":
            srcs, sts = self.page_src[self.slot], self.page_state[self.slot]
        else:
            srcs, sts = self.wall_src, self.wall_state
        if rgb[i] is None:
            return
        self._mark("移除壁纸")
        rgb[i] = None
        idx[i] = None
        pal[i] = None
        dirty[i] = False
        srcs[i] = None
        sts[i] = None
        self.refresh_all()
        self.status.configure(
            text=("%s页的专属图已移除（回到这套的默认图）" % sdtheme.PAGE_NAMES[i])
            if kind == "page" else ("第 %d 套的壁纸已移除" % (i + 1)))

    def clear_set_wall_here(self):
        """站在某一页上，发现显示的是这套的主题图，也允许直接把它清掉。"""
        i = self.slot
        if not messagebox.askyesno(
                APP_TITLE,
                "这一页没有单独的图，现在显示的是第 %d 套的整套图。\n\n"
                "要把这套的整套图也移除吗？移除后，所有没单独配图的页面都会变成纯色。"
                % (i + 1)):
            return
        self._mark("移除第 %d 套的整套图" % (i + 1))
        self.doc.wall_rgb[i] = None
        self.doc.wall_idx[i] = None
        self.doc.wall_pal[i] = None
        self.doc.wall_dirty[i] = False
        self.wall_src[i] = None
        self.wall_state[i] = None
        self.page_focus = None
        self.refresh_all()
        self.status.configure(text="第 %d 套的主题图已移除" % (i + 1))

    def clear_page_walls(self):
        """把「当前这一套」的 7 个页面专属图清掉，各页改回用这套的默认图。
        只动这一套，其余 5 套一个字节都不碰。"""
        s = self.slot
        n = self.doc.page_count_set(s)
        if not n:
            messagebox.showinfo(APP_TITLE, "第 %d 套的 7 个页面本来都用默认图。"
                                % (s + 1))
            return
        pages = "、".join(sdtheme.PAGE_NAMES[i] for i in range(sdtheme.PAGE_COUNT)
                         if self.doc.sp_rgb[s][i] is not None)
        if not messagebox.askyesno(
                APP_TITLE,
                "把第 %d 套这 %d 个页面的专属图清掉？\n\n%s\n\n"
                "清掉之后这些页面改回跟着「这一套的默认图」走。\n"
                "其余 5 套的图完全不受影响。\n\n（清错了点「撤回」）"
                % (s + 1, n, pages)):
            return
        self._mark("清掉第 %d 套的 %d 张页面图" % (s + 1, n))
        for i in range(sdtheme.PAGE_COUNT):
            self.doc.sp_rgb[s][i] = None
            self.doc.sp_idx[s][i] = None
            self.doc.sp_pal[s][i] = None
            self.doc.sp_dirty[s][i] = False
            self.page_src[s][i] = None
            self.page_state[s][i] = None
        self.refresh_all()
        self.status.configure(text="%d 个页面改回用整套图（6 套各自的图没动，可撤回）" % n)

    def pick_icon(self, n):
        p = filedialog.askopenfilename(
            title="第 %d 个图标（%s）" % (n, ICON_NAMES.get(
                ICON_ORDER[n] if n < len(ICON_ORDER) else "", "图标")),
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif"), ("全部", "*.*")])
        if not p:
            return
        try:
            im = Image.open(p)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "打不开这张图：\n%s" % e)
            return
        self._mark("换第 %d 个图标" % n)
        self.doc.icon_imgs[self.slot][n] = _fit_icon(im)
        self.doc.icon_dirty[self.slot] = True
        self.refresh_all()

    def copy_icons(self):
        s = self.slot + 1
        if not messagebox.askyesno(
                APP_TITLE,
                "把第 %d 套的 17 个图标复制到其余 5 套？\n\n"
                "那 5 套现有的图标会被整个覆盖。\n\n继续吗？（可撤回）" % s):
            return
        self._mark("复制第 %d 套图标到其余 5 套" % s)
        src = [ic.copy() for ic in self.doc.icon_imgs[self.slot]]
        for i in range(6):
            if i == self.slot:
                continue
            self.doc.icon_imgs[i] = [ic.copy() for ic in src]
            self.doc.icon_dirty[i] = True
        self.refresh_all()
        self.status.configure(text="已把第 %d 套的 17 个图标复制到其余 5 套（可撤回）" % s)

    def reset_icons(self):
        s = self.slot + 1
        if not messagebox.askyesno(
                APP_TITLE,
                "把第 %d 套的 17 个图标恢复成固件原版？\n\n"
                "这一套改过的图标会丢掉。\n\n继续吗？（可撤回）" % s):
            return
        self._mark("恢复第 %d 套的内置图标" % s)
        self.doc.icon_raw[self.slot] = (bytes(fwassets.ICONS_IMG),
                                        list(fwassets.ICONS_PAL))
        self.doc.icon_imgs[self.slot] = default_icons()   # 固件原版
        self.doc.icon_dirty[self.slot] = True
        self.refresh_all()
        self.status.configure(text="第 %d 套的图标已恢复固件原版（可撤回）" % s)

    def open_file(self):
        if not self._confirm_discard("打开别的文件"):
            return
        p = filedialog.askopenfilename(
            title="打开 theme.bin", filetypes=[("SD 主题", "*.bin"), ("全部", "*.*")])
        if not p:
            return
        try:
            doc = Doc.load(p)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "这不是一份有效的 theme.bin：\n%s" % e)
            return
        self.doc = doc
        self.slot = 0
        # 换文件了，之前的快照基于另一份 doc，撤回过去等于把两个文件搅在一起
        self.undo.clear()
        self._refresh_undo()
        self.wall_src = [None] * 6    # 新文件没有原图信息，想调裁剪得重选一次
        self.wall_state = [None] * 6
        # 页面图的原图缓存和编辑目标也要清，否则会带着上一个文件的状态。
        # 注意这里是 6 套 × 7 页的二维表 —— v0.23 起页面图按套存了。
        self.page_src = [[None] * sdtheme.PAGE_COUNT
                         for _ in range(sdtheme.THEME_COUNT)]
        self.page_state = [[None] * sdtheme.PAGE_COUNT
                           for _ in range(sdtheme.THEME_COUNT)]
        self.page_focus = 1           # 和预览画的那一页一致（文件页）
        self.extra = None             # 别把上一个文件的「只读预览」带过来
        self.refresh_all()
        self._mark_saved()
        self.status.configure(text="已打开 %s（%d 字节 · 格式 v%d · 壁纸 %d/6 张）"
                              % (os.path.basename(p), os.path.getsize(p),
                                 self.doc.ver, self.doc.wall_count()))

    def save(self):
        if not self.doc.path:
            return self.save_as()
        try:
            data = self.doc.save(self.doc.path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "保存失败：\n%s" % e)
            return
        self.refresh_all()
        self._mark_saved()
        self.status.configure(text="已保存 %s（%d 字节）" % (os.path.basename(self.doc.path), len(data)))

    def save_as(self):
        p = filedialog.asksaveasfilename(
            title="保存 theme.bin", defaultextension=".bin",
            initialfile="theme.bin", filetypes=[("SD 主题", "*.bin")])
        if not p:
            return
        try:
            data = self.doc.to_bytes()
            with open(p, "wb") as fh:
                fh.write(data)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "保存失败：\n%s" % e)
            return
        self.doc.path = p
        self.refresh_all()
        self._mark_saved()
        messagebox.showinfo(APP_TITLE,
                            "已生成 %s\n%d 字节\n\n拷到 SD 卡的 /.superfw/theme.bin 后重启即可。"
                            % (os.path.basename(p), len(data)))

    def reset_doc(self):
        if not messagebox.askyesno(APP_TITLE, "丢掉当前修改，回到内置 6 套配色？"):
            return
        self._mark("恢复内置皮肤")
        self.doc = Doc()
        self.slot = 0
        self.page_focus = 1
        self.extra = None
        self.wall_src = [None] * 6
        self.wall_state = [None] * 6
        self.page_src = [[None] * sdtheme.PAGE_COUNT
                         for _ in range(sdtheme.THEME_COUNT)]
        self.page_state = [[None] * sdtheme.PAGE_COUNT
                           for _ in range(sdtheme.THEME_COUNT)]
        self.refresh_all()
        self._mark_saved()


def _detach_cwd():
    """把当前工作目录挪离 exe 所在目录（为了别在发布夹里生垃圾）。

    为什么：搜狗输入法会把 `debug.log` 写到**进程当前目录**里（内容是
    `SGRenderLog:CheckUpdate ...`、`NotifyDoublePinyinSchemeChanged ...`）。
    双击启动时 CWD 就是 exe 所在目录，于是程序旁边会凭空多一个 debug.log，
    看着像本程序产生的 —— 其实不是，源码里没有任何写这个文件的代码。

    挪走没有副作用：图标走 _MEIPASS 找、存盘对话框只给 initialfile、
    命令行参数只有 --selftest/--file（在调用本函数之前已经处理完）。
    """
    base = (os.environ.get("TEMP") or os.environ.get("TMP")
            or tempfile.gettempdir())
    for cand in (os.path.join(base, "SuperFW-Theme-Editor"), base,
                 os.path.expanduser("~")):
        try:
            os.makedirs(cand, exist_ok=True)
            os.chdir(cand)
            return cand
        except OSError:
            continue
    return None


def main(argv):
    if "--selftest" in argv:
        path = None
        if "--file" in argv:
            path = argv[argv.index("--file") + 1]
        selftest(path)
        return 0
    if tk is None:
        print("没有 tkinter，只能跑 --selftest")
        return 1
    _detach_cwd()                    # 别让输入法把 debug.log 写进发布目录
    root = tk.Tk()
    try:
        # 打包后 __file__ 在 PyInstaller 的解压目录里，icon.ico 得用 _MEIPASS 找
        _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        root.iconbitmap(default=os.path.join(_base, "icon.ico"))
    except Exception:
        pass
    root._app = App(root, Doc())     # 留个引用，别让回调持有悬空对象
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
