# -*- coding: utf-8 -*-
"""SuperFW SD 主题编辑器 —— 只认 `/.superfw/theme.bin`。

和 v0.17 那个「注入固件」的编辑器是两套东西：
  * 这里不做注入、不找补丁点、不动固件、不算 sha256；
  * 打开的是 theme.bin，存出来的还是 theme.bin，格式严格按 `fw/src/sdtheme.h` v1。

界面：左边 6 套配色，中间 240x160 实时预览，右边壁纸 + 17 个图标。

自检（不弹窗）：
    python app/sdedit.py --selftest
"""
import os
import struct
import sys

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

APP_TITLE = "SuperFW SD 主题编辑器 v0.21"
APP_VER = "0.21"

W, H = 240, 160
CONTENT_Y = 16
CONTENT_H = 144
SCALE = 2

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

ROLES = [
    ("FG", "顶栏 / 底栏"),
    ("BG", "内容区底色"),
    ("FT", "文字和图标"),
    ("HI", "按钮 / 边框"),
    ("HIB", "选中项底色"),
    ("SH", "游戏内菜单"),
]

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
        # 页面图（v3）：7 个标签页各可以覆盖一张，没设就跟当前套的默认图
        self.page_rgb = [None] * sdtheme.PAGE_COUNT
        self.page_idx = [None] * sdtheme.PAGE_COUNT
        self.page_pal = [None] * sdtheme.PAGE_COUNT
        self.page_dirty = [False] * sdtheme.PAGE_COUNT

    # ------------------------------------------------------ 壁纸槽位访问
    def wall_arrays(self, kind):
        """kind = "set"（按套）或 "page"（按页面）。"""
        if kind == "page":
            return self.page_rgb, self.page_idx, self.page_pal, self.page_dirty
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
        self.page_rgb = [None] * n
        self.page_idx = list(r.get("page_walls") or [None] * n)
        self.page_pal = [list(p) if p is not None else None
                         for p in (r.get("page_pals") or [None] * n)]
        for i in range(n):
            if self.page_idx[i] is None:
                continue
            self.page_rgb[i] = _wall_to_rgb(self.page_idx[i], self.page_pal[i])
        self.page_dirty = [False] * n
        return self

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

    def wall_bytes(self, i, kind="set"):
        """返回第 i 个壁纸槽的 (索引字节, 调色板)；没设就是 (None, None)。"""
        rgb, idx, pal, dirty = self.wall_arrays(kind)
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
        return sum(1 for w in self.page_rgb if w is not None)

    def packed_size(self):
        """保存出来会是多大（不真正编码，只按布局算）。"""
        if self.page_count():
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
        pw, pp = [], []
        for i in range(sdtheme.PAGE_COUNT):
            w, p = self.wall_bytes(i, "page")
            pw.append(w)
            pp.append(p)
        icons = [(self.icon_set_bytes(i)[:sdtheme.ICON_BYTES],
                  struct.unpack_from("<%dH" % sdtheme.ICON_PAL_N,
                                     self.icon_set_bytes(i), sdtheme.ICON_BYTES))
                 for i in range(6)]
        return sdtheme.pack(self.pals, icons, walls=walls, wall_pals=wpals,
                            page_walls=pw, page_pals=pp)

    def save(self, path):
        data = self.to_bytes()
        with open(path, "wb") as fh:
            fh.write(data)
        self.path = path
        return data


# ---------------------------------------------------------------- 预览
def _blend(c1, c2, k=0.5):
    return tuple(int(c1[i] * k + c2[i] * (1 - k)) for i in range(3))


def _center_at(im, cx, y, text, color):
    F.draw(im, cx - F.width(text) // 2, y, text, color)


def _row_list(im, icons, items, sel, ft, hib, y0=CONTENT_Y, rowh=16, ymax=H - 16):
    """真机列表：图标 x=2，名字 x=20，右侧大小右对齐 238，选中行整行铺 HIB
    （真机用 63 号图标平铺，颜色取自 SEL_COLOR = hi_blend）。"""
    for i, item in enumerate(items):
        ic, name = item[0], item[1]
        right = item[2] if len(item) > 2 else ""
        y = y0 + i * rowh
        if y + rowh > ymax:
            break
        if i == sel:
            im.paste(hib, (0, y, W, y + rowh))
        if 0 <= ic < len(icons):
            im.paste(icons[ic], (2, y), icons[ic])
        rw = F.width(right) if right else 0
        if right:
            F.draw_right(im, W - 2, y, right, ft)
        F.draw(im, 20, y, F.fit(name, W - 26 - rw), ft)


def _row_kv(im, pairs, sel, ft, hib, y0, rowh=20, colx=170):
    """真机设置行：左标题 x=8，右值居中在 colx，选中行整行铺 HIB。"""
    for i, (k, v) in enumerate(pairs):
        y = y0 + i * rowh
        if sel is not None and i == sel:
            im.paste(hib, (0, y, W, y + 16))
        F.draw(im, 8, y, k, ft)
        _center_at(im, colx, y, v, ft)


def render_preview(doc, slot, rows=None, page=None):
    """240x160 的菜单示意，用固件自己的字库和布局常量画，尽量和真机一致。

    page = 0..6 按那个标签页画；page=None 按文件列表页画。
    """
    pal = doc.pals[slot % 6]
    fg, bg, ft, hi, hib, sh = pal
    im = Image.new("RGB", (W, H), bg)
    icons = doc.icon_imgs[slot % 6]
    pg = 1 if page is None else page

    # ---- 顶栏：FG 底 + 7 个页面图标，当前页实心、其余 50% 透明
    im.paste(fg, (0, 0, W, CONTENT_Y))
    for i in range(7):
        idx = 8 + i
        if idx >= len(icons):
            continue
        ic = icons[idx]
        if i == pg:
            im.paste(ic, (6 + i * 18, 0), ic)
        else:
            im.paste(ic, (6 + i * 18, 0), ic.split()[3].point(lambda a: a // 2))
    if pg in (0, 1, 2):
        F.draw_right(im, W - 1, 1, "1/12", sh)

    # ---- 内容区背景：这一页的图 → 这套的默认图 → BG 纯色
    wall = None
    if page is not None and 0 <= page < len(doc.page_rgb):
        wall = doc.page_rgb[page]
    if wall is None:
        wall = doc.wall_rgb[slot % 6]
    if wall is not None:
        im.paste(wall, (0, CONTENT_Y))
    else:
        im.paste(bg, (0, CONTENT_Y, W, H))

    if pg == 0:
        # 最近玩过：纯列表，没有底栏
        _row_list(im, icons, [
            (5, "Pokemon Mercury FC.gba"),
            (5, "Metroid Fusion.gba"),
            (5, "Golden Sun.gba"),
            (5, "Fire Emblem.gba"),
            (3, "Zelda - Link's Aw.gbc"),
        ], 0, ft, hib, ymax=H)

    elif pg == 1:
        # 文件列表：底栏是当前路径
        _row_list(im, icons, rows or [
            (0, "01 GBC 汉化经典", ""),
            (5, "superfw-chis.gba", "1M"),
            (2, "superfw-chis.fw", "1M"),
        ], 0, ft, hib)
        im.paste(fg, (0, H - 16, W, H))
        F.draw(im, 8, H - 16, "/", ft)

    elif pg == 2:
        # 烧录到卡带：NOR 列表（图标统一 GBA 卡带）+ 闪存占用底栏
        _row_list(im, icons, [
            (5, "Metroid Fusion.gba", "8M"),
            (5, "Golden Sun.gba", "8M"),
            (5, "Zelda - Link's Aw.gbc", "4M"),
        ], 0, ft, hib)
        im.paste(fg, (0, H - 16, W, H))
        F.draw(im, 8, H - 16, "Flash usage: 112M/124M", ft)

    elif pg == 3:
        # 全局设置：offy=29 / rowh=20 / colx=170，选中行整行高亮，底栏是帮助提示
        # 文案 = msg_en：MSG_SETT_HOTK / BOOT(+BOOT_TYPE0) / FASTSD / VER(VERNOR) / FASTEW
        pairs = [
            ("Menu Hot-key", "< L+R+Start >"),
            ("Game boot", "< Skip BIOS boot >"),
            ("Fast ROM loading", "< Enabled >"),
            ("Verify flashing", "< Disabled >"),
            ("EWRAM overclock", "< Disabled >"),
        ]
        _row_kv(im, pairs, 2, ft, hib, 29)
        # 真机用 ⯅/⯆（MSG 里的字形很小），这里直接画 7x5 的小三角，别压到第一行
        arr = ImageDraw.Draw(im)
        arr.polygon([(120, 18), (116, 24), (124, 24)], fill=ft)
        arr.polygon([(116, 126), (124, 126), (120, 132)], fill=ft)
        im.paste(fg, (0, 140, W, H))
        # 底栏 = MSG_FASTSD_I，真机上滚动显示，预览做静态截断
        F.draw(im, 4, 142, F.fit("Use a fast ROM loading mechanism. Can result in "
                                 "crashes or incorrect reads in some devices", 232), ft)

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
        _row_kv(im, pairs, 0, ft, hib, 22)
        dbox = ImageDraw.Draw(im)
        dbox.rectangle([20, 132, 220, 152], outline=hi)
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
                im.paste(hib, (0, y, W, y + 16))
                F.draw(im, 11, y, "\u25B6", ft)
            F.draw(im, 22, y, F.fit(name, 144), ft)

    else:
        # 关于：真 logo（31x7 放大 4 倍）+ 版本信息 + 底部升级提示条
        F.draw_logo(im, W // 2, 28, 4)
        _center_at(im, W // 2, 70, "by davidgf", ft)
        _center_at(im, W // 2, 95, "Version 0.21 (ffffffff)", ft)
        _center_at(im, W // 2, 114, "Chis variant", ft)
        im.paste(fg, (0, 138, W, H))
        # 底条 = MSG_FWUP_HOTKEY（flashing 关闭时的默认文案）
        _center_at(im, W // 2, 141, "Use Down+B+Start to unlock updates", ft)

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

    # 页面图（v3）：只给第 2 页和第 7 页单独配图，其余页应当回落
    print("== 页面图（v3）==")
    d4 = Doc()
    for i in range(6):
        d4.wall_rgb[i] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), cols[i])
        d4.wall_dirty[i] = True
    pcols = {1: (200, 30, 30), 6: (30, 30, 200)}
    for k, c in pcols.items():
        d4.page_rgb[k] = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), c)
        d4.page_dirty[k] = True
    raw4 = d4.to_bytes()
    r4 = sdtheme.parse(raw4)
    print("  大小 %d（v%d），单独页面 %s，默认套 %d 张"
          % (len(raw4), r4["version"],
             [i for i in range(sdtheme.PAGE_COUNT) if r4["page_walls"][i] is not None],
             sum(1 for w in r4["walls"] if w)))
    assert r4["version"] == sdtheme.VERSION_V3, "有页面图应该是 v3"
    assert len(raw4) == sdtheme.FILE_SIZE_V3
    back4 = Doc.from_bytes(raw4)
    assert back4.page_count() == 2, "只设了 2 页"
    for k, c in pcols.items():
        px = back4.page_rgb[k].getpixel((120, 72))
        near = sum(abs(px[i] - c[i]) for i in range(3))
        print("  第 %d 页中心像素 %s（源 %s，差 %d）" % (k, px, c, near))
        assert near <= 24, "第 %d 页的图串位了" % k
    for i in range(6):
        px = back4.wall_rgb[i].getpixel((120, 72))
        assert sum(abs(px[c] - cols[i][c]) for c in range(3)) <= 24, \
            "第 %d 套默认图被页面图带跑了" % (i + 1)
    assert back4.to_bytes() == raw4, "v3 打开再保存应逐字节一致"
    print("  v3 往返逐字节一致")
    print("OK")


# ---------------------------------------------------------------- 裁剪对话框
class CropDialog(tk.Toplevel):
    """壁纸裁剪：拖动平移、滚轮或滑块缩放，输出固定 240×144。

    内部状态：s = 每个输出像素对应多少源像素（越大越"放大"），
    (cx, cy) = 裁剪框中心在源图里的坐标。裁剪框恒为 5:3（240:144），
    永远落在图内，所以不会出现黑边。
    """

    VIEW_W, VIEW_H = 480, 288          # 画布
    CROP_W, CROP_H = 384, 230          # 画面里裁剪框的像素尺寸（留一圈看得到框外）

    def __init__(self, master, src, state=None):
        super().__init__(master)
        self.title("调整壁纸裁剪")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        self.src = src.convert("RGB")
        self.sw, self.sh = self.src.size
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

        self._build()
        self._clamp()
        self._redraw()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.update_idletasks()
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
        tk.Label(head, text="输出 240×144", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        self.canvas = tk.Canvas(self, width=self.VIEW_W, height=self.VIEW_H,
                                bg="#000000", highlightthickness=0, cursor="fleur")
        self.canvas.pack(padx=12)
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
    def _redraw(self, fast=True):
        d = self.CROP_W / (sdtheme.WALL_W * self.s)     # 屏幕像素 / 源像素
        w, h = max(1, int(round(self.sw * d))), max(1, int(round(self.sh * d)))
        resample = Image.BILINEAR if fast else Image.LANCZOS
        big = self.src.resize((w, h), resample)
        ox = int(round(self.VIEW_W / 2.0 - self.cx * d))
        oy = int(round(self.VIEW_H / 2.0 - self.cy * d))

        x0 = (self.VIEW_W - self.CROP_W) // 2
        y0 = (self.VIEW_H - self.CROP_H) // 2
        x1, y1 = x0 + self.CROP_W, y0 + self.CROP_H

        self._img = ImageTk.PhotoImage(big)
        c = self.canvas
        c.delete("all")
        c.create_image(ox, oy, image=self._img, anchor="nw")
        # 框外压暗（点阵抖动模拟半透明），一眼看出哪块会被裁掉
        for bx0, by0, bx1, by1 in ((0, 0, self.VIEW_W, y0),
                                   (0, y1, self.VIEW_W, self.VIEW_H),
                                   (0, y0, x0, y1),
                                   (x1, y0, self.VIEW_W, y1)):
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

    # ---- 交互
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
        x0, y0, x1, y1 = self._rect()
        box = (max(0, x0), max(0, y0), min(self.sw, x1), min(self.sh, y1))
        im = self.src.crop(box).resize((sdtheme.WALL_W, sdtheme.WALL_H), Image.LANCZOS)
        self.result = im
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
        ("ZaindORp", "SD 卡主题方案",
         "github.com/orzgithub",
         "https://github.com/orzgithub/omega-de-kernel_ZaindORp"),
        ("Viznut", "unscii 点阵字库",
         "viznut.fi/unscii", "https://viznut.fi/unscii"),
        ("GNU Unifont", "文字字形补全", "", ""),
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
            tk.Label(grid, text=who, bg=BG, fg=ACC, anchor="w", width=12,
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
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
            w, h = self.winfo_width(), self.winfo_height()
            self.geometry("+%d+%d" % (px + max(0, (pw - w) // 2),
                                      py + max(0, (ph - h) // 2)))
        except tk.TclError:
            pass


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
        # 页面图（v3）：7 个标签页各一份原图 + 裁剪状态
        self.page_src = [None] * sdtheme.PAGE_COUNT
        self.page_state = [None] * sdtheme.PAGE_COUNT
        # 预览/编辑目标：None = 这一套主题的图（默认），0..6 = 某个页面的专属图
        self.page_focus = None

        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.geometry("1120x766")
        root.minsize(1040, 720)

        self._build()
        self.refresh_all()

        # 关窗口时提示未保存（tkinter 默认会直接销毁，改动就没了）
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._fp = self._fingerprint()

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

    # ------------------------------------------------------------ 布局
    def _build(self):
        top = tk.Frame(self.root, bg=PANEL, height=44)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)
        self._btn(top, "打开 theme.bin…", self.open_file, primary=True).pack(side="left", padx=(12, 6), pady=8)
        self._btn(top, "另存为…", self.save_as).pack(side="left", padx=6, pady=8)
        self._btn(top, "保存", self.save).pack(side="left", padx=6, pady=8)
        self._btn(top, "恢复内置皮肤", self.reset_doc).pack(side="left", padx=6, pady=8)
        self.lbl_file = tk.Label(top, text="（新建，未保存）", bg=PANEL, fg=MUTED,
                                 font=("Microsoft YaHei UI", 9))
        self.lbl_file.pack(side="right", padx=(10, 12))
        self._btn(top, "关于", self.show_about).pack(side="right", padx=4, pady=8)

        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=12, pady=10)

        left = tk.Frame(body, bg=PANEL, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        mid = tk.Frame(body, bg=BG)
        mid.pack(side="left", fill="both", expand=True, padx=12)
        right = tk.Frame(body, bg=PANEL, width=290)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        self._build_left(left)
        self._build_mid(mid)
        self._build_right(right)

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

        self.slot_frame = tk.Frame(box, bg=PANEL)
        self.slot_frame.pack(fill="x", padx=8)
        self.slot_buttons = []
        for i in range(6):
            row = tk.Frame(self.slot_frame, bg=PANEL, cursor="hand2")
            row.pack(fill="x", pady=1)
            strip = tk.Canvas(row, width=54, height=18, bg=PANEL, highlightthickness=0)
            strip.pack(side="left", padx=(4, 8))
            lab = tk.Label(row, text="第 %d 套" % (i + 1), bg=PANEL, fg=MUTED,
                           anchor="w", font=("Microsoft YaHei UI", 9))
            lab.pack(side="left")
            for w in (row, strip, lab):
                w.bind("<Button-1>", lambda e, n=i: self.select_slot(n))
            self.slot_buttons.append((row, strip, lab))

        tk.Frame(box, bg=LINE, height=1).pack(fill="x", padx=12, pady=10)

        tk.Label(box, text="颜色（点方块改）", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=12, pady=(6, 2))

        self.color_rows = []
        for i, (code, hint) in enumerate(ROLES):
            row = tk.Frame(box, bg=PANEL2)
            row.pack(fill="x", padx=12, pady=2)
            sw = tk.Canvas(row, width=22, height=22, highlightthickness=1,
                           highlightbackground=LINE, bg=BG, cursor="hand2")
            sw.pack(side="left", pady=4, padx=(4, 8))
            sw.bind("<Button-1>", lambda e, n=i: self.pick_color(n))
            lab = tk.Label(row, text="%s  %s" % (code, hint), bg=PANEL2, fg=TEXT,
                           anchor="w", font=("Microsoft YaHei UI", 8))
            lab.pack(side="left")
            val = tk.Label(row, text="#000000", bg=PANEL2, fg=MUTED,
                           font=("Consolas", 8))
            val.pack(side="right", padx=6)
            self.color_rows.append((sw, val))

        self.flag = tk.Label(box, text="", bg=PANEL, fg=MUTED, anchor="w", justify="left",
                             wraplength=270, font=("Microsoft YaHei UI", 8))
        self.flag.pack(fill="x", padx=12, pady=(10, 6))

    def _build_mid(self, box):
        self.lbl_preview_title = tk.Label(box, text="② 预览 · 第 1 套（默认图）",
                                          bg=BG, fg=TEXT, anchor="w",
                                          font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_preview_title.pack(fill="x")

        # 页面切换条：点哪个，上面预览和右边壁纸区就切到哪个，可以来回比
        bar = tk.Frame(box, bg=BG)
        bar.pack(fill="x", pady=(6, 0))
        self.page_buttons = []
        targets = [(p, nm) for p, nm in enumerate(sdtheme.PAGE_NAMES)]
        for col, (idx, nm) in enumerate(targets):
            # 等宽铺满整条：窗口缩到最小也放得下「页面设置」这四个字
            bar.columnconfigure(col, weight=1, uniform="pg")
            b = tk.Button(bar, text=nm, bg=PANEL, fg=TEXT, activebackground=LINE,
                          activeforeground=TEXT, relief="flat", bd=0, padx=2, pady=2,
                          cursor="hand2", font=("Microsoft YaHei UI", 8),
                          command=lambda n=idx: self.select_target(n))
            b.grid(row=0, column=col, sticky="ew", padx=1)
            self.page_buttons.append((idx, b))

        self.canvas = tk.Label(box, bg=BG, bd=0)
        self.canvas.pack(pady=8)
        tk.Label(box, text="左边切第 1..6 套，右边编的是「这一套的主题图」；点上面某一页可以只改那一页。\n"
                           "顶栏底栏用 FG，内容区用 BG / 壁纸，文字用 FT，选中行底色用 HIB。",
                 bg=BG, fg=DIM, justify="left", anchor="w",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x")

    def _build_right(self, box):
        hdr = tk.Frame(box, bg=PANEL)
        hdr.pack(fill="x", padx=12, pady=(10, 6))
        self.lbl_wall_title = tk.Label(hdr, text="③ 壁纸", bg=PANEL, fg=TEXT,
                                       anchor="w", font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_wall_title.pack(side="left")
        self.btn_default = tk.Button(hdr, text="改默认图", command=lambda: self.select_target(-1),
                                     bg=PANEL, fg=MUTED, relief="flat", bd=0, padx=4, pady=0,
                                     activebackground=LINE, activeforeground=TEXT,
                                     font=("Microsoft YaHei UI", 8), cursor="hand2")
        self.btn_default.pack(side="right")
        self.wall_thumb = tk.Label(box, bg=BG, bd=0)
        self.wall_thumb.pack(pady=4)
        bar = tk.Frame(box, bg=PANEL)
        bar.pack(fill="x", padx=12)
        self._btn(bar, "选图片…", self.pick_wall).pack(side="left")
        self.btn_crop = self._btn(bar, "裁剪…", self.recrop_wall)
        self.btn_crop.pack(side="left", padx=6)
        self.btn_crop.configure(state="disabled")
        self.btn_del = self._btn(bar, "移除", self.clear_wall)
        self.btn_del.pack(side="left")
        bar2 = tk.Frame(box, bg=PANEL)
        bar2.pack(fill="x", padx=12, pady=(4, 0))
        self.btn_extra = self._btn(bar2, "这套的图给全部 6 套", self.copy_wall)
        self.btn_extra.pack(side="left")
        self.wall_note = tk.Label(box, text="", bg=PANEL, fg=MUTED, anchor="w",
                                  font=("Microsoft YaHei UI", 8), wraplength=266,
                                  justify="left")
        self.wall_note.pack(fill="x", padx=12, pady=(4, 0))
        # 规格写死在这儿，省得每次都问
        tk.Label(box, text="原图 ≥320×192 · 选图后可拖动 / 滚轮调位置\n"
                           "颜色自动减到 220 色（GBA 上限）",
                 bg=PANEL, fg=DIM, anchor="w", justify="left",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=12)

        tk.Frame(box, bg=LINE, height=1).pack(fill="x", padx=12, pady=4)

        tk.Label(box, text="④ 图标（点一个换图）", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=12)
        tk.Label(box, text="透明 PNG · 自动 16×16 · 合计 ≤37 色",
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

    def refresh_preview(self):
        im = render_preview(self.doc, self.slot, page=self.page_focus)
        self._preview_src = im          # 1 倍图，探针/导出用
        im = im.resize((W * SCALE, (H) * SCALE), Image.NEAREST)
        self._preview_img = ImageTk.PhotoImage(im)
        self.canvas.configure(image=self._preview_img)
        if self.page_focus is None:
            t = "② 预览 · 第 %d 套 · 这套的主题图" % (self.slot + 1)
        else:
            fixed = self.doc.page_rgb[self.page_focus] is not None
            t = "② 预览 · 第 %d 套 · %s页 · %s" % (
                self.slot + 1, sdtheme.PAGE_NAMES[self.page_focus],
                "本页固定" if fixed else "跟随主题")
        self.lbl_preview_title.configure(text=t)

    def _target(self):
        """当前在编辑哪个壁纸槽：("set", 套号) 或 ("page", 页号)。"""
        if self.page_focus is None:
            return "set", self.slot
        return "page", self.page_focus

    def select_target(self, idx):
        """点预览上方那一排：-1 = 这套的默认图，0..6 = 某个标签页。"""
        self.page_focus = None if idx < 0 else idx
        self.refresh_all()

    def refresh_wall(self):
        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind)
        srcs = self.page_src if kind == "page" else self.wall_src
        n = self.doc.wall_count()

        if kind == "page":
            self.lbl_wall_title.configure(text="③ 壁纸 · %s页" % sdtheme.PAGE_NAMES[i])
            self.btn_default.configure(text="改这一套的图",
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
                    text="%s页没有单独的图，现在用的是第 %d 套的主题图"
                         % (sdtheme.PAGE_NAMES[i], self.slot + 1))
            else:
                ph = Image.new("RGB", (240, 144), (20, 20, 18))
                d = ImageDraw.Draw(ph)
                d.text((70, 64), "no wallpaper", fill=(90, 88, 78))
                self.wall_note.configure(
                    text=("第 %d 套没有壁纸 → 内容区用 BG 纯色" % (i + 1)) if kind == "set"
                    else ("%s页没有图，这套也没主题图 → 内容区纯色"
                          % sdtheme.PAGE_NAMES[i]))
        else:
            ph = rgb[i]
            if kind == "page":
                self.wall_note.configure(text="%s页有自己的图（不随主题套变）"
                                         % sdtheme.PAGE_NAMES[i])
            elif kind == "set" and self.shared_wall():
                self.wall_note.configure(
                    text="第 %d 套有壁纸，但 6 套用的是同一张 → 切主题背景不变。"
                         "想各不相同，切到各套分别选图。" % (i + 1))
            else:
                self.wall_note.configure(text="第 %d 套有壁纸 · 全文件共 %d 张" % (i + 1, n))

        self.btn_crop.configure(state=("normal" if srcs[i] is not None else "disabled"))
        if fallback:
            self.btn_del.configure(text="移除主题图", state="normal",
                                   command=self.clear_set_wall_here)
        else:
            self.btn_del.configure(text="移除",
                                   state=("normal" if rgb[i] is not None else "disabled"),
                                   command=self.clear_wall)

        if kind == "page":
            self.btn_extra.configure(text="用这套的默认图", command=self.use_set_for_page,
                                     state=("normal" if self.doc.wall_rgb[self.slot] is not None
                                            else "disabled"))
        else:
            self.btn_extra.configure(text="这套的图给全部 6 套", command=self.copy_wall,
                                     state="normal")

        self._wall_img = ImageTk.PhotoImage(ph.resize((240, 144), Image.NEAREST))
        self.wall_thumb.configure(image=self._wall_img)

        # 切换条：选中的用强调色底，另外把「有专属图」的页标成强调色字
        want = -1 if self.page_focus is None else self.page_focus
        for idx_, b in self.page_buttons:
            has = (idx_ >= 0 and self.doc.page_rgb[idx_] is not None)
            if idx_ == want:
                b.configure(bg=ACC, fg="#111", activebackground=ACC, activeforeground="#111")
            else:
                # 文字统一用正文色（不再有的黄有的灰）；有没有专属图看底色深浅
                b.configure(bg=(PANEL2 if has else PANEL), fg=TEXT,
                            activebackground=LINE, activeforeground=TEXT)

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
        np = self.doc.page_count()
        size = self.doc.packed_size()
        if size >= sdtheme.FILE_SIZE_V3:
            ver = 3
        elif size >= sdtheme.FILE_SIZE_V2:
            ver = 2
        else:
            ver = 1
        name = os.path.basename(self.doc.path) if self.doc.path else "（新建，未保存）"
        self.lbl_file.configure(text=name)
        wall_txt = ("%d/6（共用一张）" % n) if self.shared_wall() else ("%d/6" % n)
        self.status.configure(text="第 %d 套 · 壁纸 %s · 单独页面 %d/7 · 保存后 %d 字节（v%d）· %s"
                              % (self.slot + 1, wall_txt, np, size, ver,
                                 "颜色点色块改，图标点格子换，页面按钮配单独背景"))

    # ------------------------------------------------------------ 操作
    def pick_color(self, idx):
        cur = "#%02x%02x%02x" % self.doc.pals[self.slot][idx]
        rgb, _hx = colorchooser.askcolor(color=cur, title="第 %d 套 · %s"
                                         % (self.slot + 1, ROLES[idx][0]))
        if not rgb:
            return
        self.doc.pals[self.slot][idx] = tuple(int(v) for v in rgb)
        self.refresh_all()

    def pick_wall(self):
        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind)
        srcs = self.page_src if kind == "page" else self.wall_src
        sts = self.page_state if kind == "page" else self.wall_state
        ttl = ("%s页的背景图" % sdtheme.PAGE_NAMES[i]) if kind == "page" \
            else ("第 %d 套的壁纸（之后可拖动调整裁剪）" % (i + 1))
        p = filedialog.askopenfilename(
            title=ttl, filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif"), ("全部", "*.*")])
        if not p:
            return
        try:
            im = Image.open(p).convert("RGB")
        except Exception as e:
            messagebox.showerror(APP_TITLE, "打不开这张图：\n%s" % e)
            return
        dlg = CropDialog(self.root, im, sts[i])
        if dlg.result is None:
            return
        srcs[i] = dlg.src
        sts[i] = dlg.state
        rgb[i] = dlg.result
        dirty[i] = True
        self.refresh_all()
        self.status.configure(
            text=("%s页已换图（不随主题套变）" % sdtheme.PAGE_NAMES[i]) if kind == "page"
            else ("第 %d 套的壁纸已换（所有页面共用，除非某页单独设过）" % (i + 1)))

    def recrop_wall(self):
        """用同一张原图重新裁剪（换位置/缩放），不用再选一次文件。"""
        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind)
        srcs = self.page_src if kind == "page" else self.wall_src
        sts = self.page_state if kind == "page" else self.wall_state
        if srcs[i] is None:
            messagebox.showinfo(APP_TITLE, "这张壁纸没有原图信息\n"
                                           "（比如是从 theme.bin 打开的）。\n"
                                           "请重新点「选图片…」。")
            return
        dlg = CropDialog(self.root, srcs[i], sts[i])
        if dlg.result is None:
            return
        sts[i] = dlg.state
        rgb[i] = dlg.result
        dirty[i] = True
        self.refresh_all()

    def clear_wall(self):
        """清掉当前这个槽位（这套的默认图，或某一页的专属图）。"""
        kind, i = self._target()
        rgb, idx, pal, dirty = self.doc.wall_arrays(kind)
        srcs = self.page_src if kind == "page" else self.wall_src
        sts = self.page_state if kind == "page" else self.wall_state
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
                "这一页没有单独的图，现在显示的是第 %d 套的主题图。\n\n"
                "要把这套的主题图也移除吗？移除后，所有没单独配图的页面都会变成纯色。"
                % (i + 1)):
            return
        self.doc.wall_rgb[i] = None
        self.doc.wall_idx[i] = None
        self.doc.wall_pal[i] = None
        self.doc.wall_dirty[i] = False
        self.wall_src[i] = None
        self.wall_state[i] = None
        self.page_focus = None
        self.refresh_all()
        self.status.configure(text="第 %d 套的主题图已移除" % (i + 1))

    def use_set_for_page(self):
        """把当前套的默认图原样搬给正在看的这一页（共享量化结果，字节一样）。"""
        kind, i = self._target()
        if kind != "page":
            return
        if self.doc.wall_rgb[self.slot] is None:
            messagebox.showinfo(APP_TITLE, "第 %d 套还没有默认图，先在「默认」里选一张。"
                                % (self.slot + 1))
            return
        w, pl = self.doc.wall_bytes(self.slot)
        self.doc.page_rgb[i] = self.doc.wall_rgb[self.slot].copy()
        self.doc.page_idx[i] = w
        self.doc.page_pal[i] = list(pl) if pl is not None else None
        self.doc.page_dirty[i] = False
        self.page_src[i] = self.wall_src[self.slot]
        self.page_state[i] = self.wall_state[self.slot]
        self.refresh_all()
        self.status.configure(text="%s页用的是第 %d 套这张图" % (sdtheme.PAGE_NAMES[i],
                                                          self.slot + 1))

    def copy_wall(self):
        """把当前这套的壁纸 + 裁剪状态铺给其余 5 套（会变成 6 套共用一张）。"""
        i = self.slot
        if self.doc.wall_rgb[i] is None:
            messagebox.showinfo(APP_TITLE, "第 %d 套还没有壁纸。" % (i + 1))
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "把第 %d 套的这张图复制给其余 5 套？\n\n"
                "复制之后 6 套就是同一张图了：切主题时背景不会变。\n\n"
                "想让每套背景不一样，别点这个 —— 直接切到各套分别「选图片…」。" % (i + 1)):
            return
        for k in range(6):
            if k == i:
                continue
            self.doc.wall_rgb[k] = self.doc.wall_rgb[i].copy()
            self.doc.wall_idx[k] = self.doc.wall_idx[i]
            self.doc.wall_pal[k] = (list(self.doc.wall_pal[i])
                                    if self.doc.wall_pal[i] is not None else None)
            self.doc.wall_dirty[k] = self.doc.wall_dirty[i]
            self.wall_src[k] = (self.wall_src[i].copy()
                                if self.wall_src[i] is not None else None)
            self.wall_state[k] = self.wall_state[i]
        self.refresh_all()
        self.status.configure(text="第 %d 套的壁纸已复制到其余 5 套（= 一张图共用，文件 61,680）"
                              % (i + 1))

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
        self.doc.icon_imgs[self.slot][n] = _fit_icon(im)
        self.doc.icon_dirty[self.slot] = True
        self.refresh_all()

    def copy_icons(self):
        src = [ic.copy() for ic in self.doc.icon_imgs[self.slot]]
        for i in range(6):
            if i == self.slot:
                continue
            self.doc.icon_imgs[i] = [ic.copy() for ic in src]
            self.doc.icon_dirty[i] = True
        self.refresh_all()
        self.status.configure(text="已把第 %d 套的 17 个图标复制到其余 5 套" % (self.slot + 1))

    def reset_icons(self):
        self.doc.icon_raw[self.slot] = (bytes(fwassets.ICONS_IMG),
                                        list(fwassets.ICONS_PAL))
        self.doc.icon_imgs[self.slot] = default_icons()   # 固件原版
        self.doc.icon_dirty[self.slot] = True
        self.refresh_all()

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
        self.wall_src = [None] * 6    # 新文件没有原图信息，想调裁剪得重选一次
        self.wall_state = [None] * 6
        # 页面图的原图缓存和编辑目标也要清，否则会带着上一个文件的状态
        self.page_src = [None] * sdtheme.PAGE_COUNT
        self.page_state = [None] * sdtheme.PAGE_COUNT
        self.page_focus = None
        self.refresh_all()
        self._mark_saved()
        self.status.configure(text="已打开 %s（%d 字节，v%d，壁纸 %d/6 张）"
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
        self.doc = Doc()
        self.slot = 0
        self.page_focus = None
        self.wall_src = [None] * 6
        self.wall_state = [None] * 6
        self.page_src = [None] * sdtheme.PAGE_COUNT
        self.page_state = [None] * sdtheme.PAGE_COUNT
        self.refresh_all()
        self._mark_saved()


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
