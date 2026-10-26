# -*- coding: utf-8 -*-
"""
SuperFW 主题编辑器（桌面版）
----------------------------
用固件自己的点阵字库、图标和布局常量做实时预览；可替换全部 17 个图标，
导出 theme.txt / themes[] 片段 / icons.png / icons.h。

所有素材都内嵌在 assets.py 里，不依赖仓库路径。
"""
import base64
import hashlib
import io
import os
import struct
try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk
except ImportError:  # 允许在无 Tk 的环境里单独调用渲染器
    tk = None

from PIL import Image, ImageDraw
try:
    from PIL import ImageTk
except ImportError:
    ImageTk = None

import assets
try:
    import wallpaper as WP
    import fwbuild
    import fwbake
    import sdtheme
    import version as VER
except ImportError:  # 单独跑渲染器时允许缺席
    WP = None
    fwbuild = None
    fwbake = None
    sdtheme = None
    VER = None
try:
    import gbacvt
except ImportError:  # 没带转换器模块也能用（只是少一个按钮）
    gbacvt = None

W, H = 240, 160
SCALE = 2

BG = "#0B0B09"
PANEL = "#131311"
PANEL2 = "#191916"
LINE = "#2A2A25"
TEXT = "#ECE8DE"
MUTED = "#8C8779"
DIM = "#5D594F"
ACC = "#D9FF57"

PRESETS = [
    ("White",  ["AAAAAA", "FFFFFF", "000000", "CCCCCC", "9999BB", "C08888"]),
    ("Orange", ["ECA551", "E7C092", "000000", "BDA27B", "90816E", "615D58"]),
    ("Blue",   ["26879C", "8FB1B8", "000000", "5296A5", "1D7F95", "6F8185"]),
    ("Green",  ["308855", "88AA99", "000000", "778888", "777777", "606060"]),
    ("Purple", ["AD11C8", "E47AF6", "000000", "AD5DC6", "724095", "72667A"]),
    ("Dark",   ["222222", "444444", "EEEEEE", "737573", "AAAAAA", "606060"]),
]

ROLES = [
    ("FG", "顶栏和底栏"),
    ("BG", "内容区背景"),
    ("FT", "文字和图标"),
    ("HI", "按钮和边框"),
    ("HIB", "选中项底色"),
    ("SH", "游戏内菜单"),
]


class _Slider(tk.Canvas if tk else object):
    """Canvas 滑块：Tk 原生 Scale 在深色主题下滑块几乎看不见。"""

    def __init__(self, master, value=0, command=None, **kw):
        super().__init__(master, height=18, bg=PANEL, highlightthickness=0, bd=0, **kw)
        self.value = int(value)
        self.command = command
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_click)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self.configure(cursor="hand2"))

    def get(self):
        return self.value

    def set(self, v, notify=False):
        v = int(max(0, min(100, v)))
        if v == self.value:
            return
        self.value = v
        self._draw()
        if notify and self.command:
            self.command()

    def _on_click(self, e):
        w = max(1, self.winfo_width())
        self.set(round(max(0, min(1, (e.x - 6) / max(1, w - 12))) * 100), notify=True)

    def _draw(self):
        self.delete("all")
        w = max(1, self.winfo_width())
        cy = 9
        x0, x1 = 6, max(7, w - 6)
        span = x1 - x0
        self.create_rectangle(x0, cy - 2, x1, cy + 2, fill="#2C2C26", width=0)
        fw = int(span * self.value / 100)
        if fw >= 1:
            self.create_rectangle(x0, cy - 2, x0 + fw, cy + 2, fill=ACC, width=0)
        hx = x0 + fw
        self.create_rectangle(hx - 4, cy - 7, hx + 4, cy + 7, fill=TEXT, width=0)

ICON_NAMES = [
    ("folder", "文件夹"), ("binfile", "二进制文件"), ("updfile", "升级文件"),
    ("gbcart", "GB 卡带"), ("gbccart", "GBC 卡带"), ("gbacart", "GBA 卡带"),
    ("smscart", "SMS 卡带"), ("nescart", "NES 卡带"), ("recent", "最近游玩"),
    ("disk", "磁盘 / SD"), ("flash", "NOR 闪存"), ("settings", "设置"),
    ("uilang", "界面 / 语言"), ("tools", "工具"), ("info", "关于"),
    ("hfolder", "隐藏文件夹"), ("hfile", "隐藏文件"),
]

# 每个图标在真机菜单里出现在哪（照 menu.c 的调用点写的，不是猜的）
#   文件类 → 文件列表每行左侧（menu.c:1406-1407 / 1187-1192）
#   顶栏   → 第 1..7 格（menu.c:2072，从 ICON_RECENT 起顺序排）
ICON_WHERE = [
    "文件浏览里，所有文件夹那一行的左边",
    "文件浏览里，不是游戏/升级包的普通文件",
    "文件浏览里，.fw 固件升级包那一行",
    "文件浏览里，.gb 游戏那一行",
    "文件浏览里，.gbc 游戏那一行",
    "文件浏览里，.gba 游戏那一行；NOR 闪存列表每行也是它",
    "文件浏览里，.sms 游戏那一行",
    "文件浏览里，.nes 游戏那一行",
    "顶栏第 1 格：最近游玩",
    "顶栏第 2 格：卡带浏览；载入选项里「记住设置」右边也有一个",
    "顶栏第 3 格：NOR 闪存浏览",
    "顶栏第 4 格：设置",
    "顶栏第 5 格：界面 / 语言",
    "顶栏第 6 格：工具",
    "顶栏第 7 格：关于",
    "文件浏览里，被标成隐藏的文件夹（平时不显示）",
    "文件浏览里，被标成隐藏的文件（平时不显示）",
    "「关于」页放大 4 倍画一次；游戏内菜单顶部放大 2 倍",
]

# 点这个图标时预览该切到哪一屏（None = 当前这屏就有，不用切）
ICON_SCREEN = ["browser"] * 8 + [None] * 7 + ["browser", "browser", "info"]

FILES = [("HOMEBREW", "", 0), ("SAVES", "", 0), ("MOTHER.3.GBA", "32M", 5),
         ("POKEMON.CRYSTAL.GBC", "2M", 4), ("TETRIS.GB", "256K", 3),
         ("SUPER.MARIO.NES", "40K", 7), ("SONIC.SMS", "512K", 6),
         ("UPDATE.FW", "1.6M", 2)]

# 示例列表里没有这类文件时，说清楚为什么看不到黄框（而不是让人以为工具坏了）
ICON_EMPTY = {
    1: "示例里没有 .bin 文件，真机才有",
    15: "示例里没有隐藏文件夹",
    16: "示例里没有隐藏文件",
}


def hx(v):
    return v.replace("#", "").upper()


def rgb(v):
    v = hx(v)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def q5(c):
    return tuple(round(x * 31 / 255) * 255 // 31 for x in c)


def css(v, quant=True):
    c = q5(rgb(v)) if quant else rgb(v)
    return "#%02X%02X%02X" % c


def bgr555(v):
    r, g, b = (x >> 3 for x in rgb(v))
    return (b << 10) | (g << 5) | r


def contrast_ratio(c1, c2):
    """WCAG 相对亮度对比度。用来预警“换了背景字就看不见”。"""
    def ch(x):
        x /= 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    def lum(v):
        r, g, b = rgb(v)
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

    a, b = lum(c1), lum(c2)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


def mix(c1, c2, alpha):
    """alpha 比例的 c1 叠在 c2 上，返回 RRGGBB。"""
    a, b = rgb(c1), rgb(c2)
    return "%02X%02X%02X" % tuple(round(x * alpha + y * (1 - alpha)) for x, y in zip(a, b))


class _Tip:
    """鼠标停一会儿弹出的小黑框：这个图标叫什么、出现在菜单哪一块。"""

    def __init__(self, widget, text_fn):
        self.w = widget
        self.fn = text_fn
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _e=None):
        self._cancel()
        self.job = self.w.after(320, self._show)

    def _leave(self, _e=None):
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def _cancel(self):
        if self.job is not None:
            try:
                self.w.after_cancel(self.job)
            except Exception:      # noqa: BLE001
                pass
            self.job = None

    def _show(self):
        self.job = None
        txt = self.fn()
        if not txt:
            return
        t = tk.Toplevel(self.w)
        t.wm_overrideredirect(True)
        try:
            t.attributes("-topmost", True)
        except Exception:          # noqa: BLE001
            pass
        tk.Label(t, text=txt, bg="#16160F", fg=TEXT, font=("Segoe UI", 9),
                 justify="left", bd=1, relief="solid", padx=8, pady=6).pack()
        t.update_idletasks()
        tw, th = t.winfo_reqwidth(), t.winfo_reqheight()
        top = self.w.winfo_toplevel()
        wx, wy = top.winfo_rootx(), top.winfo_rooty()
        ww, wh = top.winfo_width(), top.winfo_height()
        x = self.w.winfo_rootx() + 2
        if x + tw > wx + ww - 6:                # 靠右就贴着窗口右边，别被窗口切掉
            x = max(wx + 6, wx + ww - tw - 6)
        y = self.w.winfo_rooty() + self.w.winfo_height() + 2
        if y + th > wy + wh - 6:                # 下面放不下就翻到上面
            y = max(wy + 6, self.w.winfo_rooty() - th - 2)
        t.wm_geometry("+%d+%d" % (x, y))
        self.tip = t


class Renderer:
    """把固件界面画到 240x160 的 RGBA 画布上。"""

    def __init__(self):
        sheet = Image.open(io.BytesIO(base64.b64decode(assets.ICONS_PNG))).convert("RGBA")
        self.icons = [sheet.crop((i * 16, 0, i * 16 + 16, 16)) for i in range(17)]
        try:
            import iconoverride
            iconoverride.apply(self)          # 内置图标重设计（界面/语言等）
        except Exception:                     # noqa: BLE001
            pass
        self.logo = Image.open(io.BytesIO(base64.b64decode(assets.LOGO_PNG))).convert("RGBA")
        self._masks = {}
        self._tints = {}
        self.pal_ref = PRESETS[0][1][:]
        self.wall = None   # 240x144 RGB，铺在内容区（y=16..159）
        self.hits = {}     # 这一帧里每个图标画在哪些坐标：{图标号: [(x, y), ...]}

    # ---------- text ----------
    def mask(self, cp):
        m = self._masks.get(cp)
        if m is not None:
            return m
        data = assets.FONT.get(cp)
        if data is None:
            return None
        wide = cp > 127
        w = 16 if wide else 8
        img = Image.new("L", (w, 16), 0)
        px = img.load()
        for row in range(16):
            if wide:
                v = (data[row * 2] << 8) | data[row * 2 + 1]
                for col in range(16):
                    if v & (0x8000 >> col):
                        px[col, row] = 255
            else:
                v = data[row]
                for col in range(8):
                    if v & (0x80 >> col):
                        px[col, row] = 255
        self._masks[cp] = img
        return img

    def tint(self, cp, color):
        key = (cp, color)
        t = self._tints.get(key)
        if t is not None:
            return t
        m = self.mask(cp)
        if m is None:
            return None
        img = Image.new("RGBA", m.size, rgb(color) + (255,))
        img.putalpha(m)
        self._tints[key] = img
        return img

    def text(self, canvas, s, x, y, color, maxw=None):
        cx = x
        for ch in s:
            cp = ord(ch)
            w = 16 if cp > 127 else 8
            t = self.tint(cp, color)
            if t is None:
                cx += w
                continue
            if maxw is not None and cx - x + w > maxw:
                break
            canvas.alpha_composite(t, (cx, y))
            cx += w
        return cx - x

    def text_w(self, s):
        return sum(16 if ord(c) > 127 else 8 for c in s)

    def ctext(self, canvas, s, y, color, maxw=None, cx=120):
        w = min(self.text_w(s), maxw or 240)
        self.text(canvas, s, round(cx - w / 2), y, color, maxw)

    def rtext(self, canvas, s, xr, y, color):
        self.text(canvas, s, xr - self.text_w(s), y, color)

    # ---------- primitives ----------
    @staticmethod
    def rect(canvas, color, box):
        ImageDraw.Draw(canvas).rectangle(box, fill=rgb(color) + (255,))

    def icon(self, canvas, idx, x, y, dim=False):
        self.hits.setdefault(idx, []).append((x, y, 16, 16))
        ic = self.icons[idx]
        if dim:
            ic = ic.copy()
            ic.putalpha(ic.getchannel("A").point(lambda v: int(v * 0.45)))
        canvas.alpha_composite(ic, (x, y))

    def box(self, canvas, color, l, r, t, b):
        self.rect(canvas, color, (l, t, r - 1, t + 1))
        self.rect(canvas, color, (l, b - 2, r - 1, b - 1))
        self.rect(canvas, color, (l, t, l + 1, b - 1))
        self.rect(canvas, color, (r - 2, t, r - 1, b - 1))

    @staticmethod
    def band(canvas, color, y, h, alpha=140):
        ov = Image.new("RGBA", (240, h), rgb(color) + (alpha,))
        canvas.alpha_composite(ov, (0, y))

    @staticmethod
    def arrow(canvas, color, x, y, direction):
        d = ImageDraw.Draw(canvas)
        c = rgb(color) + (255,)
        for i in range(5):
            if direction < 0:
                d.rectangle((x + i, y + 3 + i, x + i, y + 11 - i), fill=c)
            else:
                d.rectangle((x + 4 - i, y + 3 + i, x + 4 - i, y + 11 - i), fill=c)

    # ---------- screens ----------
    def screen(self, name, pal, lang, sel=2):
        self.hits = {}
        P = dict(fg=css(pal[0]), bg=css(pal[1]), ft=css(pal[2]),
                 hi=css(pal[3]), hib=css(pal[4]), sh=css(pal[5]))
        band = rgb(P["hib"]) + (140,)
        img = Image.new("RGBA", (W, H), rgb(P["bg"]) + (255,))
        self.rect(img, P["bg"], (0, 0, W - 1, H - 1))
        if self.wall is not None:
            # 背景图顶替 menu.c:2077 的纯色填充，顶栏(0..15)与底栏(144..159)随后照常覆盖
            img.alpha_composite(self.wall.convert("RGBA"), (0, 16))
        getattr(self, "_" + name)(img, P, band, lang, sel)
        return img

    def _tabs(self, img, P, active):
        self.rect(img, P["fg"], (0, 0, 239, 15))
        for i in range(7):
            self.icon(img, 8 + i, i * 16, 0, dim=(i != active))

    def _browser(self, img, P, band, lang, sel):
        self._tabs(img, P, 1)
        self.rtext(img, "12/41", 238, 1, P["ft"])
        for i, (nm, sz, ic) in enumerate(FILES):
            y = 16 + i * 16
            self.icon(img, ic, 2, y)
            self.text(img, nm, 20, y, P["ft"], 238 - 20 - self.text_w(sz) - 4)
            if sz:
                self.rtext(img, sz, 238, y, P["ft"])
            if i == sel:
                self.band(img, P["hib"], y, 16)
        self.rect(img, P["fg"], (0, 144, 239, 159))
        self.text(img, "/GAMES", 8, 144, P["ft"], 232)

    def _uisettings(self, img, P, band, lang, sel):
        self._tabs(img, P, 4)
        rows = [
            (T("MSG_UIS_THEME", lang), "< %d >" % theme_index(self.pal_ref)),
            (T("MSG_UIS_LANG", lang), "< " + T("MSG_LANG_NAME", lang) + " >"),
            (T("MSG_UIS_RECNT", lang), T("MSG_KNOB_ENABLED", lang)),
            (T("MSG_UIS_ANSPD", lang), "< " + T("MSG_UIS_SPD2", lang) + " >"),
            (T("MSG_UIS_BHID", lang), T("MSG_KNOB_ENABLED", lang)),
        ]
        for i, (k, v) in enumerate(rows):
            y = 22 + i * 20
            self.text(img, k, 8, y, P["ft"], 218)
            self.ctext(img, v, y, P["ft"], 120, cx=170)
            if i == 0:
                self.band(img, P["hib"], y, 20)
        self.box(img, P["fg"], 20, 220, 132, 152)
        self.ctext(img, T("MSG_UIS_SAVE", lang), 134, P["ft"], 200)

    def _loadinfo(self, img, P, band, lang, sel):
        self._tabs(img, P, 1)
        self.box(img, P["fg"], 2, 238, 18, 158)
        self.arrow(img, P["ft"], 9, 21, -1)
        self.arrow(img, P["ft"], 221, 21, 1)
        self.ctext(img, T("MSG_GBALOAD_MINFO", lang), 23, P["ft"], 210)
        self.ctext(img, "MOTHER.3.GBA", 52, P["ft"], 220)
        self.ctext(img, T("MSG_LOADINFO_GAME", lang).replace("%s", "BM3J").replace("%d", "0"), 82, P["ft"], 220)
        self.ctext(img, T("MSG_LOADINFO_SAVE", lang).replace("%s", T("MSG_SAVETYPE_EEPROM", lang)).replace("%s", "8KB"), 102, P["ft"], 220)
        self.box(img, P["fg"], 20, 220, 132, 152)
        self.ctext(img, T("MSG_LOAD_GBA", lang), 134, P["ft"], 200)

    def _loadpatch(self, img, P, band, lang, sel):
        self._tabs(img, P, 1)
        self.box(img, P["fg"], 2, 238, 18, 158)
        self.arrow(img, P["ft"], 9, 21, -1)
        self.arrow(img, P["ft"], 221, 21, 1)
        self.ctext(img, T("MSG_GBALOAD_MPATCH", lang), 23, P["ft"], 210)
        rows = [
            (T("MSG_DEFS_PATCH", lang), T("MSG_PATCH_TYPE3", lang)),
            (T("MSG_LOADER_SAVET", lang), T("MSG_LOADER_ST0", lang)),
            (T("MSG_LOADER_MENU", lang), T("MSG_KNOB_ENABLED", lang)),
            (T("MSG_LOADER_RTCE", lang), T("MSG_KNOB_ENABLED", lang)),
        ]
        for i, (k, v) in enumerate(rows):
            y = 44 + i * 18
            self.text(img, k, 12, y, P["ft"], 150)
            self.ctext(img, v, y, P["ft"], 120, cx=170)
        self.band(img, P["hib"], 43, 18)
        self.text(img, "补丁:" if lang == "zh" else "Patch:", 12, 116, P["ft"], 60)
        self.box(img, P["fg"], 150, 190, 114, 132)
        self.arrow(img, P["ft"], 163, 118, 1)
        self.text(img, T("MSG_PATCH_TYPE_I3", lang), 10, 136, P["ft"], 220)

    def _tools(self, img, P, band, lang, sel):
        self._tabs(img, P, 5)
        items = [T(k, lang) for k in ("MSG_TOOLS0_SDRAM", "MSG_TOOLS1_SRAM", "MSG_TOOLS2_BAT",
                                      "MSG_TOOLS3_BENCH", "MSG_TOOLS4_FBAK")]
        items.append("清除闪存" if lang == "zh" else "Flash clear")
        for i, s in enumerate(items):
            y = 26 + i * 22
            self.text(img, s, 22, y, P["ft"], 214)
            if i == 0:
                self.band(img, P["hib"], y - 1, 18)
        self.arrow(img, P["ft"], 10, 29, 1)

    def _info(self, img, P, band, lang, sel):
        self._tabs(img, P, 6)
        logo = self.logo.resize((124, 28), Image.NEAREST)
        img.alpha_composite(logo, (58, 26))
        self.hits.setdefault(17, []).append((58, 26, 124, 28))
        self.ctext(img, "by davidgf", 68, P["ft"], 220)
        self.ctext(img, "Version 0.21 (1c9ff091)", 93, P["ft"], 220)
        self.ctext(img, "SD variant", 112, P["ft"], 220)


def T(key, lang):
    e = assets.STRINGS.get(key, {"en": "", "zh": ""})
    return e["zh"] if lang == "zh" else e["en"]


def theme_index(pal):
    for i, (_, c) in enumerate(PRESETS):
        if [hx(x) for x in c] == [hx(x) for x in pal]:
            return i + 1
    return 7


def slot_name(pal):
    """一套配色最接近哪个预设；都不是就叫「自定义」。"""
    i = theme_index(pal)
    return PRESETS[i - 1][0] if i <= len(PRESETS) else "自定义"


FW_THEMES = [
    [0x56B5, 0x7FFF, 0x0000, 0x6739, 0x5E73, 0x4638],
    [0x2A9D, 0x4B1C, 0x0000, 0x3E97, 0x3612, 0x2D6C],
    [0x4E04, 0x5ED1, 0x0000, 0x524A, 0x49E3, 0x420D],
    [0x2A26, 0x4EB1, 0x0000, 0x462E, 0x39CE, 0x318C],
    [0x6455, 0x79FC, 0x0000, 0x6175, 0x490E, 0x3D8E],
    [0x1084, 0x2108, 0x77BD, 0x39CE, 0x56B5, 0x318C],
]


def fw_validate(img):
    try:
        return fwbuild.validate(img)
    except Exception as e:
        return False, str(e)


def fw_patch_theme(img, colors, slot=0):
    """把 6 个颜色写进指定主题槽，并重算头部 sha256。"""
    layout = fwbuild.detect(img)
    off, vals = WP.patch_theme(img, colors, slot=slot, layout=layout)
    digest = fwbuild.reseal(img)
    return off, vals, digest


class App:
    def __init__(self, root):
        self.root = root
        self.r = Renderer()
        self.r.pal_ref = PRESETS[0][1][:]
        # 固件里一共 6 套主题（menu.c 的 themes[]），实机在「界面设置 → 主题颜色」
        # 里左右切换 < 1 > … < 6 >。这里给每一套各存一份配色：切槽不会把上一套
        # 改过的颜色带走，注入时逐槽写进去，实机上就能切 6 种不同的主题。
        self.slot_pals = [list(c) for _, c in PRESETS[:6]]
        self.pal = self.slot_pals[0][:]
        self.slot = 0          # 当前在编辑第几套主题（0 基）
        self.scale = 2
        self.screen = "browser"
        self.lang = "zh"
        self.sel = 2
        self.originals = [ic.copy() for ic in self.r.icons]
        self.icons = [ic.copy() for ic in self.originals]
        self.icon_bg = [None] * 17
        self.modified = set()
        self.logo_orig = self.r.logo.copy()
        self.logo_img = self.r.logo.copy()
        # 6 套主题各带一套图标（17 个 + Logo）。self.icons / icon_bg / logo_img /
        # modified 始终是「当前这一套」的视图，切槽时搬一下就行 ——
        # 和 slot_pals 一个思路：改哪套只记在哪套上。
        self.slot_icons = [[ic.copy() for ic in self.originals] for _ in range(6)]
        self.slot_icon_bg = [[None] * 17 for _ in range(6)]
        self.slot_logo = [self.logo_orig.copy() for _ in range(6)]
        self.slot_modified = [set() for _ in range(6)]
        self.pristine_icons = [ic.copy() for ic in self.originals]
        self.pristine_logo = self.logo_orig.copy()
        self.sel_icon = 0
        self.quant = True
        self.wall_src = None
        self.wall_mode = "crop"        # 固定：铺满 240×144 再裁
        self.wall_anchor = (0.5, 0.5)  # 保留源图的哪一块（点缩略图可改）
        self.wall_dim = 0.0
        self.wall_colors = fwbake.PAL_N if fwbake else 220
        self.wall_dither = True
        self.wall_idx = None
        self.wall_pal = None
        self.wall_n = 0
        self._wall_key = None
        self.fw_path = None
        self.fw_img = None
        self._cv_win = None

        root.title("SuperFW 主题编辑器  v%s" % (VER.VERSION if VER else "?"))
        root.configure(bg=BG)
        root.geometry("1280x920")
        root.minsize(1160, 860)
        self._style()
        self._build()
        self.refresh()

    # ---------------- styling ----------------
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 10))
        s.configure("Head.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 11, "bold"))
        s.configure("Mono.TLabel", background=PANEL, foreground=DIM, font=("Consolas", 9))
        s.configure("TButton", background=PANEL2, foreground=TEXT, borderwidth=0,
                    focusthickness=0, padding=(10, 6), font=("Segoe UI", 10))
        s.map("TButton", background=[("active", "#2A2A24")])
        s.configure("Accent.TButton", background=ACC, foreground="#0B0B09", font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("active", "#C8EE45")])

    # ---------------- layout ----------------
    def _build(self):
        root = self.root
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)

        # ── 顶栏：一句话说明怎么用，加两个辅助按钮 ──────────────
        head = tk.Frame(root, bg=PANEL, height=54)
        head.grid(row=0, column=0, sticky="ew")
        head.pack_propagate(False)
        tk.Label(head, text="S", bg=ACC, fg="#0B0B09", font=("Consolas", 14, "bold"),
                 width=2).pack(side="left", padx=(14, 10), pady=12)
        tk.Label(head, text="SuperFW 主题编辑器", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Label(head, text="① 配颜色   ② 放背景图   ③ 换图标   ④ 导出 / 注入",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 10)).pack(side="left", padx=18)
        ttk.Button(head, text="导出图片…", command=self.export).pack(
            side="right", padx=(6, 14), pady=11)
        ttk.Button(head, text="恢复默认配色", command=self.reset).pack(
            side="right", padx=6, pady=11)
        ttk.Button(head, text="自检", command=self.selfcheck).pack(
            side="right", padx=6, pady=11)

        # ── 三栏 ──────────────────────────────────────────
        body = tk.Frame(root, bg=BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        left = tk.Frame(body, bg=PANEL, width=318)
        left.grid(row=0, column=0, sticky="ns")
        left.pack_propagate(False)
        self._build_left(left)

        center = tk.Frame(body, bg=BG)
        center.grid(row=0, column=1, sticky="nsew")
        self._build_center(center)

        right = tk.Frame(body, bg=PANEL, width=382)
        right.grid(row=0, column=2, sticky="ns")
        right.pack_propagate(False)
        self._build_right(self._scroll_pane(right))

        # ── 底部：改动清单 + 写进固件 ───────────────────────
        foot = tk.Frame(root, bg=PANEL, height=214)
        foot.grid(row=2, column=0, sticky="ew")
        foot.pack_propagate(False)
        self._build_foot(foot)

        # ── 状态条 ────────────────────────────────────────
        status = tk.Frame(root, bg="#0E0E0C", height=24)
        status.grid(row=3, column=0, sticky="ew")
        status.pack_propagate(False)
        self.status = tk.Label(status, text="", bg="#0E0E0C", fg=DIM,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=14)
        tk.Label(status, text="改完记得先「自检」，再「注入到固件」",
                 bg="#0E0E0C", fg=DIM, font=("Segoe UI", 9)).pack(side="right", padx=14)

    def _sec(self, root, title, sub=None, top=14):
        """一节的标题 + 一句人话说明。"""
        tk.Label(root, text=title, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(top, 1))
        if sub:
            tk.Label(root, text=sub, bg=PANEL, fg=DIM, font=("Segoe UI", 9),
                     justify="left", anchor="w", wraplength=332).pack(
                anchor="w", padx=14, pady=(0, 7))

    def _scroll_pane(self, parent):
        """右栏能滚：窗口矮一点、系统缩放 125% 时，按钮不会被挤出画面。"""
        cv = tk.Canvas(parent, bg=PANEL, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=PANEL)
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)

        def on_region(_e=None):
            cv.configure(scrollregion=cv.bbox("all"))
            need = inner.winfo_reqheight() > cv.winfo_height() + 1
            if need and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y")
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()

        inner.bind("<Configure>", on_region)

        def on_size(e):
            cv.itemconfigure(win, width=e.width)   # 内容宽度跟着画布走
            on_region()

        cv.bind("<Configure>", on_size)

        def wheel(e):
            x, y = cv.winfo_pointerxy()
            if not (cv.winfo_rootx() <= x < cv.winfo_rootx() + cv.winfo_width()
                    and cv.winfo_rooty() <= y < cv.winfo_rooty() + cv.winfo_height()):
                return
            if inner.winfo_reqheight() <= cv.winfo_height():
                return
            cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

        self.root.bind_all("<MouseWheel>", wheel, add="+")
        return inner

    def _build_left(self, root):
        self._sec(root, "① 配色 · 6 套主题", "固件里有 6 套配色，实机「界面设置 → 主题颜色」"
                                            "里切 1..6。点一张卡片就编辑那一套。")
        grid = tk.Frame(root, bg=PANEL)
        grid.pack(fill="x", padx=10)
        self.preset_btns = []
        self.preset_lbls = []
        self.preset_strips = []      # 每张卡片的 6 个色块，切换/改色时要跟着刷
        for i in range(6):
            cols = self.slot_pals[i]
            cell = tk.Frame(grid, bg=PANEL2, highlightthickness=1, highlightbackground=LINE)
            cell.grid(row=i // 3, column=i % 3, padx=2, pady=2, sticky="nsew")
            strip = tk.Frame(cell, bg=PANEL2, height=16)
            strip.pack(fill="x")
            chips = []
            for c in cols:
                ch = tk.Frame(strip, bg="#" + c, width=12, height=16)
                ch.pack(side="left", fill="x", expand=True)
                chips.append(ch)
            lbl = tk.Label(cell, text="%d 套" % (i + 1), bg=PANEL2, fg=MUTED,
                           font=("Segoe UI", 10))
            lbl.pack(anchor="w", padx=6, pady=(2, 4))
            for w in [cell, strip, lbl] + chips:
                w.bind("<Button-1>", lambda e, k=i: self.select_slot(k))
            self.preset_btns.append(cell)
            self.preset_lbls.append(lbl)
            self.preset_strips.append(chips)

        # 一行小色条：点一下就把那套现成配色填进「当前这一套」
        prow = tk.Frame(root, bg=PANEL)
        prow.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(prow, text="套预设", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        for i, (_name, cols) in enumerate(PRESETS):
            b = tk.Frame(prow, bg=PANEL2, highlightthickness=1,
                         highlightbackground=LINE, cursor="hand2")
            b.pack(side="left", padx=(3, 0), expand=True, fill="both")
            st = tk.Frame(b, bg=PANEL2, height=15, cursor="hand2")
            st.pack(fill="x")
            kids = []
            for c in cols:
                ch = tk.Frame(st, bg="#" + c, height=15, cursor="hand2")
                ch.pack(side="left", fill="x", expand=True)
                kids.append(ch)
            b.bind("<Button-1>", lambda e, k=i: self.apply_preset(k))
            st.bind("<Button-1>", lambda e, k=i: self.apply_preset(k))
            for ch in kids:
                ch.bind("<Button-1>", lambda e, k=i: self.apply_preset(k))

        self.slot_lbl = tk.Label(root, text="", bg=PANEL, fg=DIM, font=("Segoe UI", 9),
                                 justify="left", anchor="w", wraplength=286)
        self.slot_lbl.pack(fill="x", padx=14, pady=(7, 0))
        ttk.Button(root, text="把当前这套配色铺到 6 套",
                   command=self.copy_slot_to_all).pack(fill="x", padx=12, pady=(6, 0))

        tk.Frame(root, bg=LINE, height=1).pack(fill="x", pady=11)
        self._sec(root, "6 个颜色", "点色块换色；输入框里是真的写进固件的色值。", top=0)

        self.swatches, self.hexvars, self.bgrlabels = [], [], []
        for i, (code, desc) in enumerate(ROLES):
            row = tk.Frame(root, bg=PANEL)
            row.pack(fill="x", padx=12, pady=3)

            sw = tk.Button(row, width=2, height=1, bg="#" + self.pal[i], relief="flat",
                           bd=0, highlightthickness=0, activebackground="#" + self.pal[i],
                           command=lambda k=i: self.pick_color(k))
            sw.pack(side="left", padx=(0, 7))

            bgr = tk.Label(row, text="", bg=PANEL, fg="#6C675C", font=("Consolas", 8),
                           width=7, anchor="e")
            bgr.pack(side="right")

            var = tk.StringVar(value=self.pal[i])
            ent = tk.Entry(row, textvariable=var, width=7, bg=PANEL2, fg=TEXT,
                           insertbackground=TEXT, relief="flat",
                           font=("Consolas", 10), justify="center")
            ent.pack(side="right", padx=(0, 5))
            ent.bind("<Return>", lambda e, k=i: self.set_hex(k, self.hexvars[k].get()))
            ent.bind("<FocusOut>", lambda e, k=i: self.set_hex(k, self.hexvars[k].get()))

            tk.Label(row, text=code, bg=PANEL, fg="#6C675C", font=("Consolas", 9),
                     width=4, anchor="w").pack(side="left")
            tk.Label(row, text=desc, bg=PANEL, fg=TEXT, font=("Segoe UI", 10),
                     anchor="w").pack(side="left", fill="x", expand=True)

            self.swatches.append(sw)
            self.hexvars.append(var)
            self.bgrlabels.append(bgr)

        tk.Frame(root, bg=PANEL).pack(fill="both", expand=True)
        tk.Frame(root, bg=LINE, height=1).pack(fill="x", pady=9)
        rhead = tk.Frame(root, bg=PANEL)
        rhead.pack(fill="x", padx=12)
        tk.Label(rhead, text="文字看得清吗", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.read_grade = tk.Label(rhead, text="", bg=PANEL, fg=DIM,
                                   font=("Segoe UI", 9, "bold"))
        self.read_grade.pack(side="right")
        self.read_bar = tk.Canvas(root, height=5, bg=PANEL2, highlightthickness=0)
        self.read_bar.pack(fill="x", padx=12, pady=(5, 3))
        self.read_label = tk.Label(root, text="", bg=PANEL, fg="#6C675C",
                                   font=("Consolas", 8), justify="left", anchor="w")
        self.read_label.pack(fill="x", padx=12)

        self.quant_var = tk.BooleanVar(value=True)
        chk = tk.Checkbutton(root, text="按真机颜色显示（15 位色）", variable=self.quant_var,
                             bg=PANEL, fg=MUTED, selectcolor=PANEL2, activebackground=PANEL,
                             activeforeground=TEXT, font=("Segoe UI", 10), bd=0,
                             command=self.refresh)
        chk.pack(anchor="w", padx=12, pady=(9, 0))
        tk.Label(root, text="GBA 每个通道只有 5 位色，勾上就是真机效果。",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 9), justify="left",
                 anchor="w", wraplength=286).pack(anchor="w", padx=14, pady=(2, 0))

    def _build_center(self, root):
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 4))

        tabs = tk.Frame(top, bg=BG)
        tabs.pack(side="left")
        self.screen_btns = {}
        for key, label in [("browser", "文件浏览"), ("uisettings", "界面设置"),
                           ("loadinfo", "载入信息"), ("loadpatch", "补丁选项"),
                           ("tools", "工具"), ("info", "关于")]:
            b = tk.Button(tabs, text=label, bg=BG, fg=DIM, relief="flat", bd=0,
                          font=("Segoe UI", 11), padx=10, pady=5,
                          command=lambda k=key: self.set_screen(k))
            b.pack(side="left", padx=1)
            self.screen_btns[key] = b

        bar = tk.Frame(root, bg=BG)
        bar.pack(side="bottom", fill="x", padx=16, pady=(4, 8))

        zoom = tk.Frame(bar, bg=BG)
        zoom.pack(side="right")
        self.zoom_btns = {}
        for z in (1, 2):
            b = tk.Button(zoom, text="%d×" % z, bg=PANEL2, fg=DIM, relief="flat", bd=0,
                          font=("Consolas", 10), padx=9, pady=2, cursor="hand2",
                          command=lambda k=z: self.set_zoom(k))
            b.pack(side="left", padx=(4, 0))
            self.zoom_btns[z] = b

        self.caption = tk.Label(bar, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.caption.pack(side="left")

        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True)
        self.wrap = wrap
        bezel = tk.Frame(wrap, bg="#1C1C18", highlightthickness=1,
                         highlightbackground="#33332C")
        bezel.place(relx=0.5, rely=0.5, anchor="center")
        self.bezel = bezel
        self.canvas = tk.Canvas(bezel, width=W * self.scale, height=H * self.scale,
                                bg="#000", highlightthickness=0, bd=0)
        self.canvas.pack(padx=8, pady=8)
        self.canvas_img = self.canvas.create_image(0, 0, anchor="nw")

    def set_zoom(self, z):
        self.scale = z
        self.canvas.configure(width=W * z, height=H * z)
        self.refresh()

    def _build_right(self, root):
        # ── 背景图 ───────────────────────────────────────
        self._sec(root, "② 背景图",
                  "什么图都能用：会自动裁成 240×144（5:3）。\n"
                  "建议原图 ≥ 480×288，别太亮——菜单文字直接压在图上。",
                  top=14)
        wrow = tk.Frame(root, bg=PANEL)
        wrow.pack(fill="x", padx=12)
        self.wall_thumb = tk.Canvas(wrow, width=126, height=76, bg=PANEL2,
                                    highlightthickness=1, highlightbackground=LINE,
                                    bd=0, cursor="hand2")
        self.wall_thumb.pack(side="left")
        self.wall_thumb.bind("<Button-1>", self.on_thumb_click)
        self.wall_thumb.bind("<B1-Motion>", self.on_thumb_click)
        wbtn = tk.Frame(wrow, bg=PANEL)
        wbtn.pack(side="left", padx=(8, 0), fill="x", expand=True)
        ttk.Button(wbtn, text="选择图片…", command=self.pick_wall).pack(fill="x", pady=(0, 4))
        ttk.Button(wbtn, text="不用背景图", command=self.clear_wall).pack(fill="x")
        self.wall_state = tk.Label(wbtn, text="纯色", bg=PANEL, fg=DIM,
                                   font=("Segoe UI", 9), anchor="w")
        self.wall_state.pack(fill="x", pady=(5, 0))

        # 裁切结果：一句话说清裁掉了多少 / 放大了多少
        self.wall_info = tk.Label(root, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                                  justify="left", anchor="w", wraplength=340)
        self.wall_info.pack(fill="x", padx=12, pady=(6, 0))

        dimrow = tk.Frame(root, bg=PANEL)
        dimrow.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(dimrow, text="压暗", bg=PANEL, fg=MUTED, font=("Segoe UI", 10)).pack(side="left")
        self.dim_lbl = tk.Label(dimrow, text="0%", bg=PANEL, fg=TEXT, width=4,
                                font=("Consolas", 9))
        self.dim_lbl.pack(side="right")
        self.dim_var = _Slider(dimrow, value=0, command=self.on_dim)
        self.dim_var.pack(side="left", fill="x", expand=True, padx=(6, 6))

        tk.Frame(root, bg=LINE, height=1).pack(fill="x", pady=10)

        # ── 图标 ─────────────────────────────────────────
        hdr = tk.Frame(root, bg=PANEL)
        hdr.pack(fill="x", padx=14, pady=(0, 2))
        tk.Label(hdr, text="③ 图标", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        self.mark_var = tk.BooleanVar(value=True)
        tk.Checkbutton(hdr, text="预览里标位置", variable=self.mark_var,
                       command=self.refresh, bg=PANEL, fg=MUTED, selectcolor=PANEL2,
                       activebackground=PANEL, activeforeground=TEXT,
                       font=("Segoe UI", 9), highlightthickness=0, bd=0,
                       cursor="hand2").pack(side="right")
        tk.Label(root, text="点一个图标 → 左边预览切到它出现的界面，黄虚框圈出来。",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 9), justify="left", anchor="w",
                 wraplength=332).pack(anchor="w", padx=14, pady=(0, 6))
        grid = tk.Frame(root, bg=PANEL)
        grid.pack(padx=12, anchor="w")
        self.icon_btns, self.icon_imgs, self.icon_tips, self.icon_cells = [], [], [], []
        for i in range(18):
            img = self.thumb(i)
            ph = ImageTk.PhotoImage(img)
            # 外面套一层 2px 的「底」，选中时把底刷成亮黄绿 = 现成的选中框
            cell = tk.Frame(grid, bg=PANEL, bd=0, highlightthickness=0)
            cell.grid(row=i // 6, column=i % 6, padx=1, pady=1)
            b = tk.Button(cell, image=ph, bg=PANEL2, relief="flat", bd=0, width=32, height=32,
                          activebackground="#2A2A24", command=lambda k=i: self.select_icon(k))
            b.pack(padx=1, pady=1)
            self.icon_tips.append(_Tip(b, lambda k=i: self._icon_tip(k)))
            self.icon_cells.append(cell)
            self.icon_btns.append(b)
            self.icon_imgs.append(ph)

        info = tk.Frame(root, bg=PANEL)
        info.pack(fill="x", padx=12, pady=(7, 0))
        self.icon_prev = tk.Label(info, bg=PANEL2, width=50, height=50)
        self.icon_prev.pack(side="left")
        side = tk.Frame(info, bg=PANEL)
        side.pack(side="left", padx=10, fill="x", expand=True)
        self.icon_name = tk.Label(side, text="", bg=PANEL, fg=TEXT,
                                  font=("Segoe UI", 11, "bold"))
        self.icon_name.pack(anchor="w")
        self.icon_sub = tk.Label(side, text="", bg=PANEL, fg=DIM, font=("Segoe UI", 9),
                                 justify="left", anchor="w", wraplength=286)
        self.icon_sub.pack(anchor="w", pady=(2, 0))

        btns = tk.Frame(root, bg=PANEL)
        btns.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Button(btns, text="换这个图标的图…", command=self.import_icon).pack(
            fill="x", pady=(0, 3))
        row = tk.Frame(btns, bg=PANEL)
        row.pack(fill="x")
        ttk.Button(row, text="底色…", command=self.icon_bg_pick).pack(
            side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(row, text="还原", command=self.icon_reset).pack(
            side="left", fill="x", expand=True, padx=(3, 0))
        ttk.Button(btns, text="把这套图标铺到 6 套",
                   command=self.copy_icons_to_all).pack(fill="x", pady=(3, 0))

    def _build_foot(self, root):
        rf = tk.Frame(root, bg=PANEL, width=296)
        rf.pack(side="right", fill="y", padx=(10, 14), pady=10)
        rf.pack_propagate(False)

        lf = tk.Frame(root, bg=PANEL)
        lf.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=10)

        th = tk.Frame(lf, bg=PANEL)
        th.pack(fill="x")
        tk.Label(th, text="④ 将要改动的地方", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        self.ver_lab = tk.Label(th, text="v%s" % (VER.VERSION if VER else "?"),
                                bg=PANEL, fg=ACC, font=("Consolas", 9, "bold"))
        self.ver_lab.pack(side="left", padx=8)
        hf = tk.Frame(lf, bg=PANEL2)
        hf.pack(fill="both", expand=True, pady=(6, 0))
        hf.columnconfigure(0, weight=1)
        hf.rowconfigure(0, weight=1)
        self.hint = tk.Text(hf, height=9, bg=PANEL2, fg=MUTED, bd=0,
                            highlightthickness=0, font=("Consolas", 9),
                            wrap="none", padx=10, pady=6, spacing1=0, spacing3=1)
        self.hint_sb = ttk.Scrollbar(hf, orient="vertical", command=self.hint.yview)
        self.hint.configure(yscrollcommand=self._hint_yscroll)
        self.hint.grid(row=0, column=0, sticky="nsew")
        self.hint_sb.grid(row=0, column=1, sticky="ns")
        self.hint_sb.grid_remove()          # 内容装得下就不显示滚动条
        self.hint.configure(state="disabled")

        brow = tk.Frame(rf, bg=PANEL)
        brow.pack(fill="x")
        brow.columnconfigure(0, weight=1, uniform="fw")
        brow.columnconfigure(1, weight=1, uniform="fw")
        ttk.Button(brow, text="选择固件…", command=self.pick_firmware).grid(
            row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(brow, text="注入到固件…", style="Accent.TButton",
                   command=self.inject_firmware).grid(row=0, column=1, sticky="ew")
        ttk.Button(brow, text="固件体检…", command=self.check_firmware_dlg).grid(
            row=1, column=0, sticky="ew", pady=(4, 0), padx=(0, 4))
        ttk.Button(brow, text="导出 SD 主题…", command=self.export_sdtheme).grid(
            row=1, column=1, sticky="ew", pady=(4, 0))
        self.fw_lab = tk.Label(rf, text="还没选固件", bg=PANEL, fg=DIM,
                               font=("Consolas", 9), anchor="w", justify="left",
                               wraplength=268)
        self.fw_lab.pack(fill="x", pady=(5, 0))
        self.cv_btn = ttk.Button(rf, text="图片 → GBA 转换器…",
                                 command=self.open_converter)
        self.cv_btn.pack(fill="x", pady=(8, 0))
        tk.Label(rf, text="算尺寸、看真机效果、导出都在里面。",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 8), anchor="w", justify="left",
                 wraplength=268).pack(fill="x", pady=(3, 0))
        self.all_slots = tk.BooleanVar(value=True)
        tk.Checkbutton(rf, text="6 套各写各自的配色 + 图标",
                       variable=self.all_slots,
                       justify="left",
                       command=self._update_hints,
                       bg=PANEL, fg=MUTED, selectcolor=PANEL2, activebackground=PANEL,
                       activeforeground=TEXT, font=("Segoe UI", 9),
                       highlightthickness=0, bd=0, anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(rf, text="支持 .bin .fw .gba，写完自动重算校验和。",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 9), anchor="w").pack(
            fill="x", pady=(2, 0))

    def _hint_yscroll(self, first, last):
        """内容超出才露出滚动条，否则藏起来，别在空面板上挂一根线。"""
        need = not (float(first) <= 0.0 and float(last) >= 1.0)
        if need:
            self.hint_sb.grid()
        else:
            self.hint_sb.grid_remove()
        self.hint_sb.set(first, last)

    # ---------------- actions ----------------
    def _stash_icons(self):
        """把当前编辑的图标记到它所属的那一套上。"""
        s = self.slot
        self.slot_icons[s] = list(self.icons)
        self.slot_icon_bg[s] = list(self.icon_bg)
        self.slot_logo[s] = self.logo_img
        self.slot_modified[s] = set(self.modified)

    def _icons_differ(self):
        """6 套图标是不是不完全一样。只比图不比量化结果，够快。

        6 套完全一样时没必要写图标表（省 26KB，钩子也不用每帧比 menu_theme），
        所以这个判断决定「要不要走 6 套图标那条路」，跟配色开关无关。
        """
        self._stash_icons()

        def sig(s):
            return (tuple(ic.tobytes() for ic in self.slot_icons[s]),
                    tuple(self.slot_icon_bg[s]),
                    self.slot_logo[s].tobytes())

        first = sig(0)
        return any(sig(s) != first for s in range(1, 6))

    def _load_icons(self):
        """把当前这一套记着的图标装进编辑视图。"""
        s = self.slot
        self.icons = list(self.slot_icons[s])
        self.icon_bg = list(self.slot_icon_bg[s])
        self.logo_img = self.slot_logo[s]
        self.modified = set(self.slot_modified[s])

    def select_slot(self, i):
        """切到第 i 套主题：配色和图标都换成这一套自己记着的那份。"""
        if i != self.slot:
            self._stash_icons()
            self.slot = i
            self.pal = self.slot_pals[i][:]
            self._load_icons()
            for k in range(6):
                self.hexvars[k].set(self.pal[k])
        self.refresh()

    def apply_preset(self, i):
        """把现成配色套到「当前这一套」上，其他 5 套不受影响。"""
        self.slot_pals[self.slot] = [hx(c) for c in PRESETS[i][1]]
        self.pal = self.slot_pals[self.slot][:]
        for k in range(6):
            self.hexvars[k].set(self.pal[k])
        self.refresh()

    def copy_slot_to_all(self):
        """把当前配色铺到全部 6 套（旧版「6 套同色」的效果）。"""
        self.slot_pals = [list(self.pal) for _ in range(6)]
        self.refresh()

    def copy_icons_to_all(self):
        """把当前这套的图标铺到全部 6 套（旧版「6 套共用一套图标」的效果）。"""
        self._stash_icons()
        cur = self.slot
        for s in range(6):
            self.slot_icons[s] = list(self.slot_icons[cur])
            self.slot_icon_bg[s] = list(self.slot_icon_bg[cur])
            self.slot_logo[s] = self.slot_logo[cur]
            self.slot_modified[s] = set(self.slot_modified[cur])
        self.refresh()

    def _icon_set_of(self, s):
        """第 s 套的图标 → (4352 字节精灵块, [GBA 调色板])。

        临时把编辑视图切到第 s 套，量化完再切回来 —— _quantize() 读的是
        self.icons / self.icon_bg，不切就只会编出当前这套。

        调色板**第 0 项必须是 0（透明）**：_quantize() 给的索引是 nearest+1，
        精灵像素值就是槽号，槽 0 恒透明。返回的列表因此要在最前面补一个 0
        （和 wallpaper.patch_icons 的做法一致），否则整套颜色错位一格。
        """
        keep = (self.slot, list(self.icons), list(self.icon_bg),
                self.logo_img, set(self.modified))
        self.slot = s
        self._load_icons()
        try:
            _sheet, palette, indexed = self._quantize()
            return (self._icon_bytes(indexed),
                    [0] + [fwbuild.rgb2gba(*c) for c in palette])
        finally:
            (self.slot, self.icons, self.icon_bg,
             self.logo_img, self.modified) = keep

    def pick_color(self, k):
        c = colorchooser.askcolor(color="#" + self.pal[k], parent=self.root, title=ROLES[k][1])
        if c and c[1]:
            self.set_hex(k, c[1].replace("#", ""))

    def set_hex(self, k, v):
        v = hx(v)
        if len(v) != 6:
            return
        try:
            int(v, 16)
        except ValueError:
            return
        self.pal[k] = v
        self.slot_pals[self.slot][k] = v
        self.hexvars[k].set(v)
        self.refresh()

    def set_screen(self, key):
        self.screen = key
        self.refresh()

    def select_icon(self, i):
        self.sel_icon = i
        tgt = ICON_SCREEN[i] if i < len(ICON_SCREEN) else None
        if tgt:
            self.screen = tgt          # 文件类图标只在「文件浏览」里有，切过去才看得见
        self.refresh()

    def _icon_tip(self, i):
        """悬停提示：编号 · 名字 / 出现在哪。"""
        if i == 17:
            return "★ 品牌 Logo（31×7）\n%s" % ICON_WHERE[17]
        return "%02d · %s（16×16）\n%s" % (i + 1, ICON_NAMES[i][1], ICON_WHERE[i])

    def import_icon(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="选择图标（PNG / BMP / GIF）",
            filetypes=[("图片", "*.png *.bmp *.gif *.jpg *.jpeg"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            im = Image.open(path).convert("RGBA")
        except Exception as e:
            messagebox.showerror("打不开", str(e), parent=self.root)
            return
        if self.sel_icon == 17:
            im.thumbnail((31, 7), Image.NEAREST)
            canvas = Image.new("RGBA", (31, 7), (0, 0, 0, 0))
            canvas.alpha_composite(im, ((31 - im.width) // 2, (7 - im.height) // 2))
            self.logo_img = canvas
            self.modified.add(17)
            self.refresh()
            return
        # 等比缩到 16x16 内，居中
        im.thumbnail((16, 16), Image.NEAREST)
        canvas = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        canvas.alpha_composite(im, ((16 - im.width) // 2, (16 - im.height) // 2))
        self.icons[self.sel_icon] = canvas
        self.modified.add(self.sel_icon)
        self.refresh()

    def icon_reset(self):
        if self.sel_icon == 17:
            self.logo_img = self.logo_orig.copy()
            self.modified.discard(17)
            self.refresh()
            return
        self.icons[self.sel_icon] = self.originals[self.sel_icon].copy()
        self.icon_bg[self.sel_icon] = None
        self.modified.discard(self.sel_icon)
        self.refresh()

    def icon_bg_pick(self):
        if self.sel_icon == 17:
            return
        c = colorchooser.askcolor(parent=self.root, title="图标底色（取消 = 透明）")
        if c and c[1]:
            self.icon_bg[self.sel_icon] = c[1].lstrip("#").upper()
            self.modified.add(self.sel_icon)
        else:
            self.icon_bg[self.sel_icon] = None
        self.refresh()

    # ---------------- wallpaper ----------------
    # ---------------- 图片 → GBA 转换器（内嵌）
    def open_converter(self):
        """在当前窗口里打开转换器。

        已经把图选好就带过去当起点；在转换器里按「用作背景图」直接送回这里，
        不用先导出再重新选一遍。
        """
        if gbacvt is None:
            messagebox.showwarning("少了模块",
                                   "这个版本没带转换器（app/gbacvt.py 找不到）。",
                                   parent=self.root)
            return
        win = self._cv_win
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:                                  # noqa: BLE001
                self._cv_win = None
        self._cv_win = gbacvt.main_gui(
            pre_image=self.wall_src, parent=self.root,
            on_apply=self._wall_from_converter, apply_hint="用作背景图")
        try:
            self._cv_win.protocol("WM_DELETE_WINDOW", self._close_converter)
        except Exception:                                      # noqa: BLE001
            pass
        self._update_hints()

    def _close_converter(self):
        win, self._cv_win = self._cv_win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:                                  # noqa: BLE001
                pass
        try:
            self.root.lift()
        except Exception:                                      # noqa: BLE001
            pass

    def _wall_from_converter(self, im, rep=None):
        """转换器交回来的 240×144 成品 → 直接当背景图（取景、压暗都已定好）。"""
        self.wall_src = im
        self.wall_anchor = (0.5, 0.5)
        if rep and "dim" in rep:
            self.wall_dim = float(rep["dim"])
            try:
                self.dim_var.set(int(round(self.wall_dim * 100)))
                self.dim_lbl.configure(text="%d%%" % self.dim_var.get())
            except Exception:                                  # noqa: BLE001
                pass
        self._wall_key = None
        self.refresh()

    def pick_wall(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="选择背景图片（会自动裁成 240×144）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            im = Image.open(path)
            im.load()
        except Exception as e:
            messagebox.showerror("打不开", str(e), parent=self.root)
            return
        self.wall_src = im
        self.wall_anchor = (0.5, 0.5)
        self._wall_key = None
        self.refresh()

    def clear_wall(self):
        self.wall_src = None
        self.wall_anchor = (0.5, 0.5)
        self._wall_key = None
        self.refresh()

    def on_thumb_click(self, e):
        """点缩略图上的哪个位置，那个位置就变成保留区的中心（可拖着挪）。"""
        if self.wall_src is None or WP is None:
            return
        cw = self.wall_thumb.winfo_width()
        ch = self.wall_thumb.winfo_height()
        if cw <= 24 or ch <= 24:
            cw, ch = 126, 76
        sw, sh = self.wall_src.size
        k = min(cw / float(sw), ch / float(sh))
        ox, oy = (cw - int(round(sw * k))) // 2, (ch - int(round(sh * k))) // 2
        # 画布坐标 → 原图坐标
        sx = max(0.0, min(float(sw), (e.x - ox) / k))
        sy = max(0.0, min(float(sh), (e.y - oy) / k))
        m = WP.crop_metrics((sw, sh), self.wall_anchor)
        l, t, r, b = m["win"]
        ww, wh = r - l, b - t
        ax = (sx - ww / 2.0) / (sw - ww) if sw - ww > 0.5 else 0.5
        ay = (sy - wh / 2.0) / (sh - wh) if sh - wh > 0.5 else 0.5
        self.wall_anchor = (max(0.0, min(1.0, ax)), max(0.0, min(1.0, ay)))
        self._wall_key = None
        self.refresh()

    def set_fit(self, key):        # 兼容旧调用：现在只有一种模式
        self.wall_anchor = (0.5, 0.5)
        self._wall_key = None
        self.refresh()

    def on_dim(self):
        self.wall_dim = self.dim_var.get() / 100.0
        self.dim_lbl.configure(text="%d%%" % self.dim_var.get())
        self._wall_key = None
        self.refresh()

    def _wall_recalc(self):
        """源图 → 正好 240x144、≤236 色、索引整体移到 4..239 的设备画面。"""
        if WP is None or self.wall_src is None:
            self.wall_idx = None
            self.wall_pal = None
            self.wall_n = 0
            self.r.wall = None
            return
        bgc = q5(rgb(self.pal[1])) if self.quant else rgb(self.pal[1])
        key = (id(self.wall_src), self.wall_anchor, round(self.wall_dim, 2),
               self.wall_colors, self.wall_dither, bgc)
        if key == self._wall_key:
            return
        self._wall_key = key
        im = WP.fit_exact(self.wall_src, self.wall_anchor)
        idx, pal, n = WP.quantize(im, self.wall_dim, bgc, self.wall_colors, self.wall_dither)
        self.wall_idx, self.wall_pal, self.wall_n = idx, pal, n
        flat = []
        for v in pal:
            flat += [(v & 0x1F) * 255 // 31, ((v >> 5) & 0x1F) * 255 // 31,
                     ((v >> 10) & 0x1F) * 255 // 31]
        prev = Image.frombytes("P", (240, 144), idx)
        prev.putpalette(flat)
        self.r.wall = prev.convert("RGB")

    def reset(self):
        self.slot_pals = [list(c) for _, c in PRESETS[:6]]
        self.slot = 0
        self.pal = self.slot_pals[0][:]
        # 用 __init__ 时存下的原始图标，别从 r.icons 里取 —— refresh() 已经把
        # 编辑过的图标写进 r.icons 了，从这里取等于把修改当成原始图标。
        self.originals = [ic.copy() for ic in self.pristine_icons]
        self.logo_orig = self.pristine_logo.copy()
        self.slot_icons = [[ic.copy() for ic in self.originals] for _ in range(6)]
        self.slot_icon_bg = [[None] * 17 for _ in range(6)]
        self.slot_logo = [self.logo_orig.copy() for _ in range(6)]
        self.slot_modified = [set() for _ in range(6)]
        self._load_icons()
        for k in range(6):
            self.hexvars[k].set(self.pal[k])
        self.refresh()

    # ---------------- render ----------------
    def thumb(self, i):
        if i == 17:
            base = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            lg = self.logo_img.resize((31, 7), Image.NEAREST)
            base.alpha_composite(lg, (0, 12))
            return base
        return self.composed_icon(i).resize((32, 32), Image.NEAREST)

    def composed_icon(self, i):
        if i == 17:
            return self.logo_img
        ic = self.icons[i]
        if self.icon_bg[i]:
            bg = Image.new("RGBA", (16, 16), rgb(self.icon_bg[i]) + (255,))
            bg.alpha_composite(ic)
            return bg
        return ic

    def refresh(self):
        self.quant = self.quant_var.get() if hasattr(self, "quant_var") else True
        if hasattr(self, "slot_icons"):
            self._stash_icons()          # 当前改动先记到它所属的那一套
        # swap icons into renderer
        for i in range(17):
            self.r.icons[i] = self.composed_icon(i)
        self.r.logo = self.logo_img
        self.r.pal_ref = self.pal
        self._wall_recalc()
        img = self.r.screen(self.screen, self.pal, self.lang, self.sel)
        big = img.convert("RGB").resize((W * self.scale, H * self.scale), Image.NEAREST)
        self.photo = ImageTk.PhotoImage(big)
        self.canvas.itemconfigure(self.canvas_img, image=self.photo)
        self._draw_marks()
        self._sync_ui()
        self._update_hints()

    def _mark_spots(self):
        """选中的图标（或 Logo）这一屏出现在哪几块。"""
        return self.r.hits.get(self.sel_icon, [])

    def _draw_marks(self):
        """在预览上圈出选中图标的位置——黄框压在图上，但不改任何像素。"""
        self.canvas.delete("mark")
        if not self.mark_var.get():
            return
        s = self.scale
        for (x, y, w, h) in self._mark_spots():
            x0, y0 = (x - 2) * s, (y - 2) * s
            x1, y1 = (x + w + 2) * s - 1, (y + h + 2) * s - 1
            # 先垫一圈黑，亮背景上也看得清
            self.canvas.create_rectangle(x0 - 1, y0 - 1, x1 + 1, y1 + 1,
                                         outline="#14140F", width=2, tags="mark")
            self.canvas.create_rectangle(x0, y0, x1, y1,
                                         outline="#FFC53D", width=2, tags="mark")

    def _crop_text(self):
        """一句话讲清：裁掉了多少、放大了多少、亮暗配不配。"""
        m = WP.crop_metrics(self.wall_src.size, self.wall_anchor)
        parts = ["成品 240×144"]
        if m["cut_x"] >= 1:
            parts.append("左右共裁 %d px（%d%%）"
                         % (round(m["cut_x"]), round(m["pct_x"] * 100)))
        if m["cut_y"] >= 1:
            parts.append("上下共裁 %d px（%d%%）"
                         % (round(m["cut_y"]), round(m["pct_y"] * 100)))
        if m["cut_x"] < 1 and m["cut_y"] < 1:
            parts.append("不用裁")
        zoom = m["zoom"]
        if zoom > 1.02:
            parts.append("放大 %.1f×（原图偏小，可能糊）" % zoom)
        elif zoom < 0.98:
            parts.append("缩小 %.1f×" % (1 / zoom))
        verdict = self._wall_contrast_note()
        if verdict:
            parts.append(verdict)
        return "   ·   ".join(parts)

    def _wall_contrast_note(self):
        """图和文字色撞了就直说——文字是压在背景图上的，不是压在主题底色上。"""
        im = self.r.wall if self.r is not None else None
        if im is None:
            return ""
        small = im.convert("L").resize((24, 14), Image.BILINEAR)
        px = list(small.getdata())
        lum = sum(px) / (255.0 * len(px))
        ft = rgb(self.pal[2])
        ft_lum = (0.299 * ft[0] + 0.587 * ft[1] + 0.114 * ft[2]) / 255.0
        if lum < 0.45 and ft_lum < 0.45:
            return "图偏暗，深色字看不清 → 建议用 Dark 主题"
        if lum > 0.6 and ft_lum > 0.6:
            return "图偏亮，浅色字看不清 → 把文字色调深"
        return ""

    def _draw_wall_thumb(self):
        """缩略图：整张原图压暗，亮着的那块就是会被保留的部分。"""
        c = self.wall_thumb
        c.delete("all")
        cw = c.winfo_width()
        ch = c.winfo_height()
        if cw <= 24 or ch <= 24:            # 还没布局完，先用设计尺寸
            cw, ch = 126, 76
        if self.wall_src is None:
            c.create_text(cw // 2, ch // 2, text="没选图", fill=DIM,
                          font=("Segoe UI", 9))
            return
        sw, sh = self.wall_src.size
        k = min(cw / float(sw), ch / float(sh))
        dw, dh = max(1, int(round(sw * k))), max(1, int(round(sh * k)))
        ox, oy = (cw - dw) // 2, (ch - dh) // 2

        base = self.wall_src.convert("RGB").resize((dw, dh), Image.LANCZOS)
        plate = Image.new("RGB", (cw, ch), PANEL2)
        plate.paste(Image.blend(base, Image.new("RGB", (dw, dh), (10, 10, 8)), 0.74),
                    (ox, oy))

        m = WP.crop_metrics((sw, sh), self.wall_anchor)
        l, t, r, b = m["win"]
        rx0, ry0 = ox + int(l * k), oy + int(t * k)
        rx1, ry1 = ox + int(round(r * k)), oy + int(round(b * k))
        rx0, ry0 = max(ox, min(rx0, ox + dw - 2)), max(oy, min(ry0, oy + dh - 2))
        rx1, ry1 = max(rx0 + 2, min(ox + dw, rx1)), max(ry0 + 2, min(oy + dh, ry1))
        keep = base.crop((rx0 - ox, ry0 - oy, rx1 - ox, ry1 - oy))
        plate.paste(keep.resize((rx1 - rx0, ry1 - ry0), Image.LANCZOS), (rx0, ry0))

        ph = ImageTk.PhotoImage(plate)
        c.create_image(0, 0, anchor="nw", image=ph)
        c.image = ph
        c.create_rectangle(rx0, ry0, rx1 - 1, ry1 - 1, outline=ACC, width=2)

    def _sync_ui(self):
        for k in range(6):
            c = css(self.pal[k], self.quant)
            self.swatches[k].configure(bg=c, activebackground=c)
            self.bgrlabels[k].configure(text="0x%04X" % bgr555(self.pal[k]))
        if hasattr(self, "read_grade"):
            qq = lambda v: "%02X%02X%02X" % q5(rgb(v))
            ft = qq(self.pal[2])
            on_bg = contrast_ratio(ft, qq(self.pal[1]))
            on_sel = contrast_ratio(ft, mix(qq(self.pal[4]), qq(self.pal[1]), 0.55))
            worst = min(on_bg, on_sel)
            txt = "FT/BG %.1f:1   FT/选中 %.1f:1" % (on_bg, on_sel)
            src = ""
            if self.r.wall is not None:
                # 背景图逐行取均值，找文字最难读的那一行
                rp = self.r.wall.resize((1, 144), Image.BOX).load()
                wc = min(contrast_ratio(ft, "%02X%02X%02X" % rp[0, y]) for y in range(144))
                if wc < worst:
                    src = "图上文字"
                worst = min(worst, wc)
                txt += "   图片最差行 %.1f:1" % wc
            if worst >= 7:
                grade, col = "对比充足", "#7BD88F"
            elif worst >= 4.5:
                grade, col = "对比偏弱", "#E8C15A"
            else:
                grade, col = "对比不足", "#E06C6C"
            self.read_grade.configure(text=src + grade, fg=col)
            self.read_label.configure(text=txt)
            w = self.read_bar.winfo_width()
            if w <= 1:
                w = 300
            self.read_bar.delete("all")
            self.read_bar.create_rectangle(0, 0, max(2, int(w * min(1, worst / 15))), 4,
                                           fill=col, width=0)

        for i, cell in enumerate(self.preset_btns):
            cell.configure(highlightbackground=ACC if i == self.slot else LINE,
                           highlightthickness=2 if i == self.slot else 1)
            icon_tag = (" · 图标" if (self.slot_modified[i] or
                                      self.slot_icon_bg[i] != [None] * 17) else "")
            self.preset_lbls[i].configure(
                text="%d 套 · %s%s" % (i + 1, slot_name(self.slot_pals[i]), icon_tag),
                fg=TEXT if i == self.slot else MUTED)
            for ch, c in zip(self.preset_strips[i], self.slot_pals[i]):
                ch.configure(bg="#" + c)
        allslots = self.all_slots.get() if hasattr(self, "all_slots") else True
        if allslots:
            slot_txt = ("注入目标：6 套各用各自配色（改完颜色自动记在当前这套上）\n"
                        "实机「界面设置 → 主题颜色」左右切 < 1 > … < 6 >")
        else:
            slot_txt = ("注入目标：只写第 %d 套（%s），其余 5 套保持固件原样"
                        % (self.slot + 1, slot_name(self.pal)))
        self.slot_lbl.configure(text=slot_txt, fg=TEXT)
        for key, b in self.screen_btns.items():
            on = key == self.screen
            b.configure(fg=TEXT if on else DIM, bg=PANEL2 if on else BG)
        for z, b in self.zoom_btns.items():
            on = z == self.scale
            b.configure(fg=TEXT if on else DIM,
                        bg=("#3A3A30" if on else PANEL2))
        # icon panel
        for i, b in enumerate(self.icon_btns):
            self.icon_cells[i].configure(bg=ACC if i == self.sel_icon else PANEL)
            b.configure(bg=PANEL2)
            ph = ImageTk.PhotoImage(self.thumb(i))
            b.configure(image=ph)
            self.icon_imgs[i] = ph
        if self.sel_icon == 17:
            name, zh = "logo", "品牌 Logo"
        else:
            name, zh = ICON_NAMES[self.sel_icon]
        if self.sel_icon == 17:
            big = Image.new("RGBA", (50, 50), (25, 25, 22, 255))
            lg = self.logo_img.resize((48, 11), Image.NEAREST)
            big.alpha_composite(lg, (1, 19))
        else:
            big = self.composed_icon(self.sel_icon).resize((50, 50), Image.NEAREST)
        self.icon_prev_photo = ImageTk.PhotoImage(big)
        self.icon_prev.configure(image=self.icon_prev_photo, bg=PANEL2)
        self.icon_name.configure(text=(f"{self.sel_icon + 1:02d} · {zh}" if self.sel_icon < 17 else "★ · " + zh))
        self.icon_sub.configure(
            text="第 %d 套 · %s" % (self.slot + 1, ICON_WHERE[self.sel_icon]))
        # wallpaper panel
        self._draw_wall_thumb()
        self.dim_lbl.configure(text="%d%%" % round(self.wall_dim * 100))
        if self.wall_src is None:
            self.wall_state.configure(text="还没选图", fg=DIM)
            self.wall_info.configure(
                text="不放图时，内容区就是「内容背景」那一格纯色，顶栏底栏不受影响。",
                fg=DIM)
        else:
            self.wall_state.configure(text="%d × %d" % self.wall_src.size, fg="#7BD88F")
            self.wall_info.configure(text=self._crop_text(), fg=MUTED)

        caps = {
            "browser": "文件浏览 · 顶排是标签页",
            "uisettings": "界面设置 · 5 行选项",
            "loadinfo": "载入信息 · 236×140 弹窗",
            "loadpatch": "补丁选项 · 4 行开关",
            "tools": "工具 · 纯文字列表",
            "info": "关于 · Logo 放大 4 倍",
        }
        n = len(self._mark_spots())
        if self.sel_icon >= 17:
            tail = "   ·   Logo 在「关于」页"
        elif n:
            tail = "   ·   黄框 = 它的位置（%d 处）" % n
        else:
            tail = "   ·   " + ICON_EMPTY.get(self.sel_icon, "示例里没有这类文件")
        self.caption.configure(text=caps[self.screen] + tail)
        self.status.configure(
            text="第 %d 套 %s   ·   图标 %d 个已换   ·   背景 %s   ·   %s"
                 % (self.slot + 1, slot_name(self.pal), len(self.modified),
                    ("%d 色" % self.wall_n) if self.wall_src is not None else "纯色",
                    "按真机颜色显示" if self.quant else "按原色显示"))

    # ---------------- 修改提示 ----------------
    def pick_firmware(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="选择 SuperFW 固件（.bin / .fw / .gba）",
            filetypes=[("SuperFW 固件", "*.bin *.fw *.gba"), ("所有文件", "*.*")])
        if not path:
            return
        img = bytearray(open(path, "rb").read())
        ok, why = fw_validate(img)
        if not ok:
            messagebox.showerror("这个固件不能改", why, parent=self.root)
            return
        self.fw_path, self.fw_img = path, img
        self._update_hints()

    def clear_firmware(self):
        self.fw_path = self.fw_img = None
        self._update_hints()

    def _update_hints(self):
        """把「这次会改到哪里、改多少字节」逐条写出来。一行一条，别绕。"""
        if not hasattr(self, "hint"):
            return
        L = []
        # 导出 SD 主题跟「注入到固件」是两条路：theme.bin 里 6 套只是数据，
        # 不占固件空间，所以总是 6 套全写，不受下面那个开关影响。
        if sdtheme is not None:
            has_w = (self.wall_src is not None and self.wall_idx is not None)
            L.append("SD 主题  导出 → theme.bin %s 字节（6 套全写，%s）"
                     % (format(sdtheme.FILE_SIZE if has_w
                               else sdtheme.ICONS_ONLY_SIZE, ","),
                        "带壁纸" if has_w else "不带壁纸"))
        allslots = self.all_slots.get() if hasattr(self, "all_slots") else True
        if allslots:
            L.append("主题   6 套各写各自配色                  72 字节")
            for s in range(6):
                L.append("       %d 套 %-9s #%s #%s #%s #%s #%s #%s"
                         % ((s + 1, slot_name(self.slot_pals[s]))
                            + tuple(self.slot_pals[s])))
        else:
            L.append("主题   只写第 %d 套（%s）             12 字节"
                     % (self.slot + 1, slot_name(self.pal)))
            L.append("       #%s #%s #%s #%s #%s #%s" % tuple(self.pal))
        L.append("图标   第 %d 套换了 %d 个" % (self.slot + 1, len(self.modified)))
        if fwbake is not None and (allslots or self._icons_differ()):
            n_icon_sets = sum(1 for s in range(6)
                              if self.slot_modified[s] or self.slot_icon_bg[s] != [None] * 17)
            if n_icon_sets:
                L.append("       6 套各一套图标（%d 套改过 → 表占 %d 字节）"
                         % (n_icon_sets, fwbake.ICON_STRIDE * 6))
            else:
                L.append("       6 套图标目前一模一样 → 只写 1 份（省 26KB）")
        if self.wall_src is None:
            L.append("背景   不放图，内容区还是纯色")
            if fwbake is not None and (allslots or self._icons_differ()):
                L.append("       6 套图标要挂钩子 → 注入时自动垫一张同色底（看不出）")
        else:
            L.append("背景   240×144 · %d 色 · %d 字节（原图 %d×%d）"
                     % (self.wall_n or 220, WP.IMG_BYTES,
                        self.wall_src.size[0], self.wall_src.size[1]))
            L.append("       调色板 → 槽 20..239；位图 → 固件内部空白区")
            L.append("       钩子 → EWRAM 载荷（首次时装，之后换图沿用）")

        if self.fw_img is None:
            L.append("固件   还没选 —— 点右边「选择固件…」")
            self.fw_lab.configure(text="还没选固件", fg=DIM)
        else:
            img = self.fw_img
            try:
                lay = fwbuild.detect(img)
                L.append("固件   %s  ·  %s  ·  %s"
                         % (os.path.basename(self.fw_path), lay.build,
                            "%d 字节" % len(img)))
                L.append("       主题表 0x%06X   图标 0x%06X   补丁点 0x%06X（帧在 %s）"
                         % (lay.themes, lay.icons, lay.patch_off, lay.frame_reg))
                baked = fwbake.find_baked(img) if fwbake else None
                if baked:
                    L.append("       这个固件已经有背景图，换图会直接替换那一张")
                    L.append("       位图 @ 0x%06X + 0x%06X（钩子会一起升级成本版写法）"
                             % (baked["a_rom"], baked["b_rom"]))
                elif self.wall_src is not None:
                    hook_off = fwbake.find_hook_slot(img, fwbake.find_layout(img))
                    if hook_off is None:
                        L.append("       ⚠ 载荷里没空位放钩子，背景图写不进去")
                    else:
                        L.append("       钩子空位 EWRAM 0x%08X"
                                 % (fwbake.EWRAM_MIRROR + hook_off))
                if lay.padded_size() > len(img):
                    L.append("       会先补齐到 %s 字节再写"
                             % format(lay.padded_size(), ","))
                self.fw_lab.configure(text=os.path.basename(self.fw_path), fg=ACC)
            except Exception as e:      # noqa: BLE001
                L.append("固件   不认识这个文件：%s" % e)
                self.fw_lab.configure(text="固件不认识", fg="#E06C60")

        self.hint.configure(state="normal")
        self.hint.delete("1.0", "end")
        self.hint.insert("1.0", "\n".join(L))
        self.hint.configure(state="disabled")
        self.hint.see("1.0")

    # ------------------------------------------------------------ 导出 SD 主题
    def _sdtheme_bytes(self):
        """当前 6 套（配色 + 图标 + 背景图）→ theme.bin 字节串。"""
        self._stash_icons()
        pals = [list(p) for p in self.slot_pals]
        sets = [self._icon_set_of(s) for s in range(6)]
        wall = wall_pal = None
        if self.wall_src is not None and WP is not None:
            if self.wall_idx is None:
                self._wall_recalc()
            if self.wall_idx is not None:
                wall = fwbake.map_wall_indices(self.wall_idx)
                wall_pal = fwbake.slice_wall_palette(self.wall_pal)
        return sdtheme.pack(pals, sets, wall, wall_pal)

    def export_sdtheme(self):
        """把皮肤直接打成 SD 卡主题文件（不用再注入固件）。

        这条路线对应的固件是「SD 主题版」（固件里带 /.superfw/theme.bin 的
        加载器）；换皮肤只换这个文件，不重刷。
        """
        if sdtheme is None or fwbake is None or fwbuild is None:
            messagebox.showwarning(
                "导出不了", "主题打包模块没加载（缺 sdtheme / fwbake）。",
                parent=self.root)
            return
        try:
            data = self._sdtheme_bytes()
        except Exception as e:                              # noqa: BLE001
            messagebox.showerror("打包失败", str(e), parent=self.root)
            return

        path = filedialog.asksaveasfilename(
            parent=self.root, title="保存 SD 卡主题文件（theme.bin）",
            initialfile="theme.bin", defaultextension=".bin",
            filetypes=[("SuperFW SD 主题", "*.bin"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            messagebox.showerror("写文件失败", str(e), parent=self.root)
            return

        info = sdtheme.parse(data)
        n_icon = len([1 for i in range(1, 6) if info["icons"][i] != info["icons"][0]])
        note = ""
        if self.fw_img is not None:
            try:
                lay = fwbuild.detect(bytearray(self.fw_img))
                if not getattr(lay, "sdtheme", False):
                    note = ("\n\n注意：当前选中的固件不带 SD 主题加载器，它不认这个文件。"
                            "\n要用这份 theme.bin，固件得刷「SD 主题版」。")
                else:
                    note = "\n\n当前选中的固件就是 SD 主题版，可以直接用。"
            except Exception:                               # noqa: BLE001
                pass
        msg = ("%s\n\n%d 字节 · %s\n配色 6 套 · 图标 %s · %s\n\n"
               "用法：把文件名改成 theme.bin，放到 SD 卡的\n"
               "/.superfw/theme.bin（跟 settings.txt 同一个目录），\n"
               "然后重启进菜单。\n\n"
               "前提：固件要刷「SD 主题版」（能读这个文件的版本）；\n"
               "官方固件不认这个文件，它只能靠「注入到固件」那条路。"
               % (os.path.basename(path), len(data),
                  "带背景图" if info["has_wall"] else "不带背景图",
                  "6 套各一套" if n_icon else "6 套相同（1 份）",
                  "内容区跟主题底色一致" if not info["has_wall"] else "壁纸 240×144"))
        messagebox.showinfo("已导出 SD 卡主题", msg + note, parent=self.root)

    def inject_firmware(self):
        if self.fw_img is None:
            self.pick_firmware()
            if self.fw_img is None:
                return
        path = self.fw_path
        img = bytearray(self.fw_img)
        ok, why = fw_validate(img)
        if not ok:
            messagebox.showerror("不能注入", why, parent=self.root)
            return
        try:
            layout = fwbuild.detect(img)
        except ValueError as e:
            messagebox.showerror("这个固件不支持", str(e), parent=self.root)
            return

        has_wall = (self.wall_src is not None and WP is not None
                    and self.wall_idx is not None)
        baked = None
        wall_err = None
        if has_wall and fwbake is not None:
            try:
                baked = fwbake.find_baked(img)
                if not baked:
                    fwbake.check_rom(img)      # 首次烘焙：先探一遍能不能塞进去
            except Exception as e:             # noqa: BLE001
                wall_err = str(e)
            if wall_err:
                has_wall = False
        if self.wall_src is not None and not has_wall and wall_err is None:
            wall_err = "当前版本没加载背景模块"
        if wall_err:
            messagebox.showwarning(
                "背景图这次不能写入",
                wall_err + "\n\n主题和图标照常写入，背景图保持固件里原来那张不变。",
                parent=self.root)

        # 6 套主题各自的图标要先算好 —— 首次烘焙要跟位图一起进 bake()。
        # 跟「6 套配色」开关无关：只要 6 套图标不完全一样就得写图标表。
        allslots = self.all_slots.get()
        icon_sets = None
        icon_sets_err = None
        want_icon_sets = fwbake is not None and (allslots or self._icons_differ())
        if want_icon_sets:
            try:
                icon_sets = [self._icon_set_of(s) for s in range(6)]
            except Exception as e:             # noqa: BLE001
                icon_sets_err = str(e)
        icon_sets_ok = icon_sets is not None
        icon_rebake = None
        # 6 套图标全靠钩子，而钩子只随背景图一起装。没选图、固件里也没有背景图 →
        # 用当前这套的「内容区背景」做一个 240×144 纯色底垫上：画面跟原来一模一样，
        # 但钩子有了落点，6 套图标才能跟着主题换。（注入完再把面板恢复原样）
        auto_wall = False
        wall_saved = self.wall_src
        if icon_sets_ok and not has_wall and not baked and fwbake is not None:
            try:
                solid = Image.new("RGB", (240, 144), rgb(self.slot_pals[self.slot][1]))
                self.wall_src = solid
                self._wall_key = None
                self.refresh()
                if self.wall_idx is not None:
                    has_wall = True
                    auto_wall = True
            except Exception:                  # noqa: BLE001
                self.wall_src = wall_saved
                self.refresh()
        # 钩子只在烘焙背景图时安装，而 6 套图标要靠钩子每帧比对 menu_theme。
        # 没选新图、但固件里已经有一张 → 按原样取出来重烘一遍，只为了带上图标。
        if icon_sets_ok and not has_wall:
            if baked:
                icon_rebake = (
                    bytes(img[baked["a_rom"]:baked["a_rom"] + baked["a_len"]]) +
                    bytes(img[baked["data_b_at"]:baked["data_b_at"] + baked["b_len"]]),
                    list(struct.unpack_from("<%dH" % baked["pal_n"], img,
                                            baked["pal_at"])))
            else:
                icon_sets_ok = False
                messagebox.showwarning(
                    "6 套图标这次不能写入",
                    "6 套各带一套图标，是靠背景图那条钩子装进固件的，"
                    "而这个固件里还没有背景图。\n\n"
                    "先挑一张背景图（或换一个本来带背景图的固件）再注入，"
                    "6 套图标就会一起写进去。", parent=self.root)
        icon_arg = icon_sets if icon_sets_ok else None
        if icon_sets_ok and auto_wall:
            extra_icon = ("\n\n图标：6 套各带一套。这个固件里本来没有背景图，会先垫一张"
                          "\n跟「内容区背景」同色的纯色底（画面看不出变化），"
                          "\n只为了让钩子有落点 —— 6 套图标就挂在这条钩子上。")
        elif icon_sets_ok and not icon_rebake and has_wall:
            extra_icon = ("\n\n图标：6 套各带一套，实机切主题时会自动换上那一套。")
        elif icon_sets_ok and icon_rebake:
            extra_icon = ("\n\n图标：6 套各带一套；背景图保持固件里原来那张，"
                          "只为把钩子重装一遍。")
        else:
            extra_icon = ""
        pad = layout.padded_size() > len(img)
        extra = ""
        if has_wall:
            if baked:
                extra += ("\n\n背景图：这个固件里已经有一张，会把那张**换成现在这张**"
                          "\n（240×144 8bpp，钩子会按现在的写法重新生成一遍）。")
            else:
                extra += ("\n\n背景图：240×144 8bpp（%d 字节）+ 220 色 → 槽 20..239，"
                          "\n位图塞进固件内部空白区、钩子写进 EWRAM 载荷。" % WP.IMG_BYTES)
        if pad:
            extra += ("\n\n这个文件比卡带小，会补齐到 %s 字节（0xFF），"
                      % format(layout.padded_size(), ","))
        extra += extra_icon
        if self.all_slots.get():
            theme_desc = "6 套主题各用各自配色（" + " / ".join(
                slot_name(p) for p in self.slot_pals) + "）"
        else:
            theme_desc = "第 %d 套主题（%s），其余 5 套保持原样" % (
                self.slot + 1, slot_name(self.pal))
        if not messagebox.askokcancel(
                "注入到固件",
                "检测到构建：%s\n主题表 0x%X · 补丁点 0x%X（帧在 %s）\n\n"
                "写入：%s + 当前 17 个图标%s\n继续？"
                % (layout.build, layout.themes, layout.patch_off, layout.frame_reg,
                   theme_desc,
                   ("\n+ 背景图（换掉里面原来那张）" if baked else "\n+ 背景图")
                   if has_wall else ""),
                parent=self.root):
            return
        notes = ["构建 %s" % layout.build]
        wall_info = None
        wall_fail = None

        # ① 背景图必须最先写：bake() 会校验头部 sha256，
        #    主题/图标一写进去校验就失败，背景会被静默跳过（老版本的坑）。
        if has_wall:
            try:
                idx = fwbake.map_wall_indices(self.wall_idx)
                pal = fwbake.slice_wall_palette(self.wall_pal)
                if baked:
                    wall_info = fwbake.rebake(img, indexed=idx, palette=pal,
                                              icon_sets=icon_arg)
                    img = wall_info["rom"]
                    notes.append("背景图 换成新图（位图 @ 0x%06X + 0x%06X，%d 色）"
                                 % (wall_info["a_rom"], wall_info["b_rom"],
                                    wall_info["colors"]))
                    notes.append("背景图 钩子已按新方式（Flash 直读、只写当前帧）重写 EWRAM 0x%08X"
                                 % wall_info["hook_addr"])
                else:
                    wall_info = fwbake.bake(img, None, indexed=idx, palette=pal,
                                            site=layout, icon_sets=icon_arg)
                    img = wall_info["rom"]
                    notes.append("背景图 位图 A @ 0x%06X → Flash 0x%08X（%d 字节）"
                                 % (wall_info["a_rom"], wall_info["a_flash"], wall_info["a_len"]))
                    notes.append("背景图 位图 B @ 0x%06X → Flash 0x%08X（%d 字节）"
                                 % (wall_info["b_rom"], wall_info["b_flash"], wall_info["b_len"]))
                    notes.append("背景图 调色板 Flash 0x%08X（%d 色，槽位 %d..%d）"
                                 % (wall_info["pal_flash"], fwbake.PAL_N,
                                    fwbake.PAL_BASE, fwbake.PAL_BASE + fwbake.PAL_N - 1))
                    notes.append("背景图 钩子 EWRAM 0x%08X（%d 字节，Flash 直读）"
                                 % (wall_info["hook_addr"], len(wall_info["hook"])))
                    notes.append("补丁点 0x%06X（%d 字节，帧在 %s）" % (
                        layout.patch_off, layout.patch_len, layout.frame_reg))
            except Exception as e:             # noqa: BLE001
                wall_fail = str(e)
                wall_info = None
        elif icon_rebake:
            # 没选新图，但要把 6 套图标装进钩子：壁纸原样重烘一遍
            try:
                wall_info = fwbake.rebake(img, indexed=icon_rebake[0],
                                          palette=icon_rebake[1],
                                          icon_sets=icon_arg)
                img = wall_info["rom"]
                notes.append("背景图 保持固件里原来那张（重烘只为把 6 套图标装进钩子）")
            except Exception as e:             # noqa: BLE001
                wall_info = None
                icon_arg = None
                notes.append("6 套图标 没写成：%s" % e)
        # ② 主题：6 套各写各自的配色；关掉开关时只动当前这一套
        try:
            slots = range(6) if allslots else [self.slot]
            for s in slots:
                WP.patch_theme(img, self.slot_pals[s], slot=s, layout=layout)
        except Exception as e:
            messagebox.showerror("注入失败", str(e), parent=self.root)
            return
        if allslots:
            notes.append("主题 6 套各写各自配色 @ 0x%06X（每槽 12 字节，共 72 字节）"
                         % layout.themes)
            for s in range(6):
                notes.append("      第 %d 套 %s  #%s #%s #%s #%s #%s #%s"
                             % ((s + 1, slot_name(self.slot_pals[s]))
                                + tuple(self.slot_pals[s])))
        else:
            notes.append("主题 只写第 %d 套 %s @ 0x%06X（12 字节）"
                         % (self.slot + 1, slot_name(self.pal), layout.themes))

        # ③ 图标：按固件格式重新编码后整块写入
        icon_note = "图标未写入"
        try:
            _sheet, palette, indexed = self._quantize()
            payload = self._icon_bytes(indexed)
            ioff, poff = WP.patch_icons(
                img, payload, [fwbuild.rgb2gba(*c) for c in palette], layout)
            icon_note = "图标 17 个 @ 0x%X（%d 色调色板 @ 0x%X）" % (
                ioff, len(palette) + 1, poff)
        except Exception as e:
            icon_note = "图标未写入：%s" % e
        notes.append(icon_note)
        if icon_sets_err:
            notes.append("6 套图标 没写成：%s" % icon_sets_err)
        elif not icon_sets:
            pass
        elif wall_info is None:
            notes.append("6 套图标 没写成（背景图那一步没走通）")
        elif wall_info.get("icon_sets"):
            notes.append("图标 6 套各一套 @ 0x%06X（每套 %d 字节，实机切主题自动换）"
                         % (wall_info["icon_rom"], fwbake.ICON_STRIDE))
        else:
            notes.append("图标 6 套内容完全一样 → 只写 1 份（省 26KB）")

        fwbuild.reseal(img)

        base, ext = os.path.splitext(path)
        tag = "6themes" if allslots else "theme%d" % (self.slot + 1)
        out = base + "-" + tag + ("-wall" if wall_info else "") + ext
        if auto_wall:
            notes.append("背景图 这个固件里原来没有 → 垫了一张「内容区背景」同色的纯色底"
                         "（240×144，画面不变），只为给 6 套图标挂钩子")
        open(out, "wb").write(img)
        ok2, why2 = fw_validate(img)
        if wall_fail:
            notes.append("背景图 没写进去：%s" % wall_fail)

        # 卡带浏览器只认 .fw 走固件升级；.gba/.bin 会被当成游戏直接运行（不刷写！）
        fw_copy = None
        if ext.lower() != ".fw" and fwbuild.fw_size_of(img) == len(img):
            fw_copy = base + "-" + tag + ("-wall" if wall_info else "") + ".fw"
            try:
                open(fw_copy, "wb").write(img)
                notes.append("已生成同名 .fw 副本（刷机请用它）")
            except OSError as e:                           # noqa: BLE001
                fw_copy = None
                notes.append(".fw 副本没写出来：%s" % e)

        # 自动垫的那张纯色底只是注入时借用，写完就把面板恢复成「还没选图」
        if auto_wall:
            self.wall_src = wall_saved
            self._wall_key = None
            self.refresh()

        # 写完立刻体检输出文件：fw_size / sha256 / 烘焙结构 / 解出里面那张图
        chk_lines, chk_bad, chk_wall, thumb = [], [], None, None
        try:
            import fwcheck
            rep = fwcheck.check(open(out, "rb").read(), out)
            chk_lines, chk_bad, chk_wall = rep["lines"], rep["problems"], rep["wall"]
            if chk_wall is not None and ImageTk is not None:
                thumb = ImageTk.PhotoImage(chk_wall.resize((240, 144), Image.NEAREST))
        except Exception as e:                                 # noqa: BLE001
            chk_lines = [("体检没跑起来：%s" % e, True)]

        tip = ("刷入后在 界面设置 → 主题颜色 里左右切 < 1 > … < 6 >，"
               "选好按「将设置保存到SD卡」才会记住。")
        if not allslots:
            tip = ("刷入后在 界面设置 → 主题颜色 里选第 %d 项；另外 5 套还是固件原样。"
                   % (self.slot + 1))
        body = ("已写出：\n%s（%s 字节）\n\n%s\n复验：%s\n\n%s" % (
            out, format(len(img), ","), "\n".join(notes),
            "通过" if ok2 else "失败 - " + why2,
            tip + ("\n背景图会显示在除顶栏/底栏之外的内容区，切主题不会盖掉它。"
                   if wall_info else "")))
        self._show_inject_result(body, out, chk_lines, chk_bad, thumb,
                                 fw_copy=fw_copy)

    def check_firmware_dlg(self):
        """对任意固件文件体检（刷机前验文件、刷完读回来对比都用它）。"""
        path = filedialog.askopenfilename(
            title="选要体检的固件文件",
            filetypes=[("固件文件", "*.fw *.gba *.bin"), ("所有文件", "*.*")],
            parent=self.root)
        if not path:
            return
        try:
            rom = open(path, "rb").read()
        except OSError as e:
            messagebox.showerror("读不了这个文件", str(e), parent=self.root)
            return
        try:
            import fwcheck
            rep = fwcheck.check(rom, path)
        except Exception as e:                                 # noqa: BLE001
            messagebox.showerror("体检失败", str(e), parent=self.root)
            return
        thumb = None
        if rep["wall"] is not None and ImageTk is not None:
            thumb = ImageTk.PhotoImage(rep["wall"].resize((240, 144), Image.NEAREST))
        body = ("体检对象：\n%s（%s 字节）" % (path, format(len(rom), ",")))
        self._show_inject_result(body, path, rep["lines"], rep["problems"], thumb)

    def _show_inject_result(self, body, out, chk_lines, chk_bad, thumb,
                            fw_copy=None):
        """注入结果 + 固件体检（含输出文件里那张背景图的缩略图）。"""
        ext = os.path.splitext(out)[1].lower()
        win = tk.Toplevel(self.root)
        win.title("注入完成")
        win.configure(bg=PANEL)
        win.transient(self.root)
        win.resizable(False, False)
        frm = tk.Frame(win, bg=PANEL)
        frm.pack(fill="both", expand=True, padx=16, pady=12)
        head = tk.Label(frm, text=body, bg=PANEL, fg=TEXT, font=("Segoe UI", 9),
                        justify="left", anchor="w")
        head.pack(fill="x")
        if ext != ".fw" and fw_copy:
            tk.Label(frm, text="刷机请用 .fw 那份：%s" % os.path.basename(fw_copy),
                     bg=PANEL, fg=ACC, font=("Segoe UI", 9, "bold"),
                     justify="left", anchor="w", wraplength=430).pack(fill="x",
                                                                      pady=(6, 0))
        if ext != ".fw":
            tk.Label(frm, text="⚠ 卡带浏览器只对 .fw 文件做「固件升级」；"
                               "%s 文件会被当成游戏直接运行，永远刷不进去！"
                               % (ext or "无扩展名"),
                     bg=PANEL, fg="#E05A4E", font=("Segoe UI", 9, "bold"),
                     justify="left", anchor="w", wraplength=430).pack(fill="x",
                                                                      pady=(4, 0))
        if chk_lines:
            tk.Label(frm, text="—— 出厂体检 ——", bg=PANEL, fg=DIM,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 2))
            for ln, bad in chk_lines:
                tk.Label(frm, text=("✗ " if bad else "· ") + ln,
                         bg=PANEL, fg="#E05A4E" if bad else MUTED,
                         font=("Segoe UI", 9), justify="left", anchor="w",
                         wraplength=430).pack(fill="x")
        if thumb is not None:
            tk.Label(frm, text="输出文件里现在这张背景图（实机显示的就是它）：",
                     bg=PANEL, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))
            tk.Label(frm, image=thumb, bg=PANEL2, bd=1, relief="solid").pack(anchor="w")
            win._thumb = thumb          # 防 GC
        btns = tk.Frame(frm, bg=PANEL)
        btns.pack(fill="x", pady=(12, 0))
        if chk_bad:
            tk.Label(btns, text="发现 %d 个问题，先解决再刷！" % len(chk_bad),
                     bg=PANEL, fg="#E05A4E", font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Button(btns, text="关闭", command=win.destroy, bg=PANEL2, fg=TEXT,
                  relief="flat", padx=18, pady=5).pack(side="right")
        win.update_idletasks()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        win.geometry("+%d+%d" % (rx + max(0, (rw - win.winfo_reqwidth()) // 2),
                                 ry + max(0, (rh - win.winfo_reqheight()) // 3)))

    @staticmethod
    def _icon_bytes(indexed):
        """把 17 个图标的索引（4 个 8×8 tile）拍成固件要的 4352 字节。"""
        out = bytearray()
        for tile in indexed:
            for row in tile:
                out += bytes(row)
        return bytes(out)

    def selfcheck(self):
        """把当前图标导出成 icons.h，再按固件的方式解码回来比对。"""
        try:
            sheet, palette, indexed = self._quantize()
        except Exception as e:
            messagebox.showerror("自检失败", str(e), parent=self.root)
            return
        diffs = 0
        for icon_i in range(17):
            src = self.composed_icon(icon_i).copy()
            px = src.load()
            for y in range(16):
                for x in range(16):
                    idx = indexed[icon_i][((y // 8) * 2 + (x // 8)) * 8 + (y % 8)][x % 8]
                    r, g, b, a = px[x, y]
                    if a <= 128:
                        if idx != 0:
                            diffs += 1
                    else:
                        if idx == 0:
                            diffs += 1
                            continue
                        c = palette[idx - 1]
                        if max(abs(c[0] - r), abs(c[1] - g), abs(c[2] - b)) > 8:
                            diffs += 1
        maxidx = max(max(row) for icon in indexed for row in icon)
        checks = [
            ("调色板 <= 256", len(palette) + 1 <= 256),
            ("索引在范围内", maxidx <= len(palette)),
            ("图标 17 个", len(indexed) == 17),
            ("每个 4x8x8", all(len(icon) == 32 for icon in indexed)),
            ("导出→解码无偏差", diffs == 0),
        ]
        ok = all(c for _, c in checks)
        body = "\n".join(f"{'✓' if c else '✗'}  {n}" for n, c in checks)
        text = (f"自检{'通过' if ok else '未通过'}\n\n{body}\n\n"
                f"调色板 {len(palette)} 色 + 透明\n像素偏差 {diffs} 处")
        (messagebox.showinfo if ok else messagebox.showwarning)("自检", text, parent=self.root)

    # ---------------- export ----------------
    def export(self):
        folder = filedialog.askdirectory(parent=self.root, title="选择导出文件夹")
        if not folder:
            return
        try:
            self._write_all(folder)
        except Exception as e:
            messagebox.showerror("导出失败", str(e), parent=self.root)
            return
        messagebox.showinfo("完成", "已导出到：\n" + folder, parent=self.root)

    def _write_all(self, folder):
        os.makedirs(folder, exist_ok=True)
        # theme.txt
        with open(os.path.join(folder, "theme.txt"), "w", encoding="utf-8", newline="\n") as f:
            for (code, _), v in zip(ROLES, self.pal):
                f.write(f"{code.lower()}={hx(v)}\n")
        # C snippet：当前这一套（历史格式，保持兼容）
        c = [hx(x) for x in self.pal]
        with open(os.path.join(folder, "themes-snippet.c"), "w", encoding="utf-8", newline="\n") as f:
            f.write("/* 粘贴到 src/menu.c 的 themes[] 末尾，重新编译即可 */\n")
            f.write("  { RGB2GBA(0x%s), RGB2GBA(0x%s), RGB2GBA(0x%s),\n"
                    "    RGB2GBA(0x%s), RGB2GBA(0x%s), RGB2GBA(0x%s) },  // Custom\n" % tuple(c))
        # 6 套一起导：整段替换 themes[] 的内容
        with open(os.path.join(folder, "themes-all.c"), "w", encoding="utf-8", newline="\n") as f:
            f.write("/* 6 套主题：整段替换 src/menu.c 里 themes[] = { ... } 的内容 */\n")
            for i, pal6 in enumerate(self.slot_pals):
                c6 = tuple(hx(x) for x in pal6)
                f.write("  { RGB2GBA(0x%s), RGB2GBA(0x%s), RGB2GBA(0x%s),\n"
                        "    RGB2GBA(0x%s), RGB2GBA(0x%s), RGB2GBA(0x%s) },  // %d %s\n"
                        % (c6 + (i + 1, slot_name(pal6))))
        # icons
        sheet, palette, indexed = self._quantize()
        sheet.save(os.path.join(folder, "icons.png"))
        with open(os.path.join(folder, "icons.h"), "w", encoding="utf-8", newline="\n") as f:
            f.write(self._icons_h(palette, indexed))
        # logo
        self.logo_img.save(os.path.join(folder, "logo.png"))
        with open(os.path.join(folder, "logo.h"), "w", encoding="utf-8", newline="\n") as f:
            f.write(self._logo_h())
        # wallpaper
        if self.wall_idx is not None:
            self.r.wall.save(os.path.join(folder, "wallpaper.png"))
            with open(os.path.join(folder, "wallpaper.idx"), "wb") as f:
                f.write(self.wall_idx)
            with open(os.path.join(folder, "wallpaper-pal.bin"), "wb") as f:
                f.write(struct.pack("<256H", *self.wall_pal))
            with open(os.path.join(folder, "wallpaper-patch.md"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(self._wall_md())
        # previews
        for key in ("browser", "uisettings", "loadinfo", "loadpatch", "tools", "info"):
            img = self.r.screen(key, self.pal, self.lang, self.sel).convert("RGB")
            img.resize((W * 3, H * 3), Image.NEAREST).save(
                os.path.join(folder, f"preview-{key}.png"))
        with open(os.path.join(folder, "reload-theme-clamp.c"), "w", encoding="utf-8", newline="\n") as f:
            f.write(CLAMP_C)
        with open(os.path.join(folder, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(README_TXT)

    def _quantize(self):
        """把 17 个图标合成 272x16 的索引色图（调色板 ≤ 37 色 + 透明）。"""
        sheet = Image.new("RGBA", (272, 16), (0, 0, 0, 0))
        for i in range(17):
            ic = self.composed_icon(i).copy()
            px = ic.load()
            for y in range(16):
                for x in range(16):
                    r, g, b, a = px[x, y]
                    if a > 128:
                        px[x, y] = ((r + 4) // 8 * 8, (g + 4) // 8 * 8, (b + 4) // 8 * 8, 255)
                    else:
                        px[x, y] = (0, 0, 0, 0)
            sheet.alpha_composite(ic, (i * 16, 0))

        colors = sorted({p[:3] for p in sheet.getdata() if p[3] > 128})
        if len(colors) > 37:
            flat = Image.new("RGB", (272, 16), (0, 0, 0))
            for i in range(17):
                src = sheet.crop((i * 16, 0, i * 16 + 16, 16))
                for y in range(16):
                    for x in range(16):
                        r, g, b, a = src.getpixel((x, y))
                        if a > 128:
                            flat.putpixel((i * 16 + x, y), (r, g, b))
            q = flat.quantize(colors=37, method=Image.MEDIANCUT)
            pal = q.getpalette()[:37 * 3]
            colors = [tuple(pal[i * 3:i * 3 + 3]) for i in range(37)]
        palette = colors
        indexed = []
        for i in range(17):
            tile = []
            for sy in range(2):
                for sx in range(2):
                    for r in range(8):
                        row = []
                        for c in range(8):
                            p = sheet.getpixel((i * 16 + sx * 8 + c, sy * 8 + r))
                            if p[3] <= 128:
                                row.append(0)
                            else:
                                row.append(self._nearest(p[:3], palette) + 1)
                        tile.append(row)
            indexed.append(tile)
        return sheet, palette, indexed

    @staticmethod
    def _nearest(c, palette):
        best, bi = 1 << 30, 0
        for i, p in enumerate(palette):
            d = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2
            if d < best:
                best, bi = d, i
        return bi

    def _logo_h(self):
        from collections import Counter
        px = self.logo_img.load()
        def snap(c):
            # 近白/近黑拉正，避免 238 灰被写成 0x77bd
            if min(c) >= 235:
                return (255, 255, 255)
            if max(c) <= 10:
                return (0, 0, 0)
            return c

        cnt = Counter()
        for y in range(7):
            for x in range(31):
                r, g, b, a = px[x, y]
                if a > 128:
                    cnt[snap((r, g, b))] += 1
        cols = [c for c, _ in cnt.most_common(3)]
        while len(cols) < 3:
            cols.append((0, 0, 0))
        bg = cols[0]
        rows = []
        for y in range(7):
            row = []
            for x in range(31):
                r, g, b, a = px[x, y]
                if a <= 128:
                    row.append(cols.index(bg))
                    continue
                r, g, b = snap((r, g, b))
                d = [((r - c[0]) ** 2 + (g - c[1]) ** 2 + (b - c[2]) ** 2, i) for i, c in enumerate(cols)]
                row.append(min(d)[1])
            rows.append(row)

        def cc(c):
            return ((c[0] >> 3) << 0) | ((c[1] >> 3) << 5) | ((c[2] >> 3) << 10)

        out = ["/* 由 SuperFW 主题编辑器生成；替换 src/res/logo.h */\n"]
        out.append("const uint8_t logo_img[7][31] = {\n")
        for row in rows:
            out.append(" {" + ",".join(str(v) for v in row) + "},\n")
        out.append("};\n\n")
        out.append("const uint16_t logo_pal[3] = {\n")
        out.append("  " + ",".join("0x%04x" % cc(c) for c in cols) + "\n};\n")
        return "".join(out)

    def _wall_md(self):
        """导出里附一份补丁说明（偏移由工具探测，不写死）。"""
        if WP is None or self.wall_idx is None:
            return ""
        return f"""# 背景图片补丁说明

生成自 SuperFW 主题编辑器。位图 240×144，{self.wall_n} 色，索引在 20..239 之间。

## 写到哪里

| 内容 | 位置 |
|---|---|
| 位图 A（上半 95 行，22,800 B） | 固件内部全 0 区（assets 之后） |
| 位图 B（下半 49 行，11,760 B）+ 头 + 220 色调色板 | 另一段全 0 区 |
| 钩子（Thumb，约 90 B） | EWRAM 载荷的空位里 |

两段位图的落点会被 assets 的 `sdram_copy` 一起搬进 SDRAM（0x09E00000 起），
钩子随 EWRAM 载荷被拷到 0x027C0000 起执行。

## 补丁点改了什么

`src/menu.c:2077` 的 `dma_memset16(&frame[16*240], dup8(BG_COLOR), 240*144/2)`
被 `-flto -flto-partition=none` 内联成直接写 DMA3 寄存器的指令。不同编译这段不一样：

| 构建 | 补丁点 | 长度 | 帧地址寄存器 |
|---|---|---|---|
| r75-2mb（2,097,152 B） | 0xA7B4 | 10 | r4 |
| chis-1.6mb（1,671,680 B） | 0x6118 | 16 | r0 |

工具先按机器码特征认出是哪一版，再把这段换成 `bl 钩子` + 真 NOP（`mov r8,r8`）。
钩子先搬 220 色调色板（CPU），再用 `ldmia`/`stmia` 把位图铺到 frame+0xF00。

**不要改成 DMA3 从 SDRAM 读位图**：那块区域挂 CPLD，固件自己从没用 DMA 读过它
（字体、全局变量全是 CPU 读），DMA 写 SDRAM 倒是常用。用 DMA 读在真机上取不到
数据 —— 调色板正常、位图全黑，正是 v0.12 及更早的表现。

## 没有动的东西

- 顶栏（0..15 行）仍然是 FG 纯色
- 底栏、弹窗、选中条、图标、文字全部原样
- BG 调色板 0..3（Logo）、16..19（FG/BG/FT/HI）、240..244（游戏内菜单）一个都没碰

## 附带文件

- `wallpaper.png` —— 量化后的 240×144 预览（就是设备上会显示的样子）
- `wallpaper.idx` —— 34,560 字节索引位图，直接对应 VRAM 内容区
- `wallpaper-pal.bin` —— 512 字节，256 项小端 RGB555
"""

    @staticmethod
    def _icons_h(palette, indexed):
        def cc(c):
            return ((c[0] >> 3) << 0) | ((c[1] >> 3) << 5) | ((c[2] >> 3) << 10)

        out = []
        out.append("/* 由 SuperFW 主题编辑器生成；替换 src/res/icons.h */\n")
        out.append("enum {\n")
        for i, (nm, _) in enumerate(ICON_NAMES):
            if nm == "flash":
                out.append("#ifdef SUPPORT_NORGAMES\n ICON_FLASH,\n#endif\n")
            elif nm == "settings":
                out.append(" ICON_SETTINGS,\n")
            elif nm == "uilang":
                out.append(" ICON_UILANG_SETTINGS,\n")
            elif nm == "tools":
                out.append(" ICON_TOOLS,\n")
            elif nm == "info":
                out.append(" ICON_INFO,\n")
            elif nm == "hfolder":
                out.append(" ICON_HFOLDER,\n")
            elif nm == "hfile":
                out.append(" ICON_HFILE,\n")
            else:
                out.append(" ICON_%s,\n" % nm.upper())
        out.append("};\n")
        out.append("const uint8_t icons_img[][4][8][8] = {\n")
        for i, (nm, _) in enumerate(ICON_NAMES):
            if nm == "flash":
                out.append("#ifdef SUPPORT_NORGAMES\n")
            out.append("  {\n")
            for t in range(4):
                out.append("    {\n")
                for r in range(8):
                    out.append("     {" + ",".join("0x%02x" % v for v in indexed[i][t * 8 + r]) + "},\n")
                out.append("    },\n")
            out.append("  },\n")
            if nm == "flash":
                out.append("#endif\n")
        out.append("};\n\n")
        out.append("const uint16_t icons_pal[%d] = {\n" % (len(palette) + 1))
        out.append("  0x0000," + ",".join("0x%04x" % cc(c) for c in palette) + "\n};\n")
        return "".join(out)


CLAMP_C = """/* 可选安全补丁：防止 theme=6 在旧固件上越界读 themes[]
 *
 * 位置：src/menu.c 的 reload_theme() 开头
 * 作用：把越界的主题序号夹回 0，避免读到数组外的垃圾配色。
 */
void reload_theme(unsigned thnum) {
  if (thnum >= THEME_COUNT)
    thnum = 0;
  /* ... 原有代码不变 ... */
  MEM_PALETTE[FG_COLOR] = themes[thnum].fg_color;
}
"""

README_TXT = """SuperFW 主题编辑器 — 导出说明
================================

这里有两类东西：主题（6 个颜色）和图标（17 个 16x16）。

一、只换颜色（最快，今天就能用）
1. 打开 src/menu.c，找到 themes[] 数组（约第 220 行）
2. 把 themes-snippet.c 里的那行粘到数组末尾
3. 重新编译：make（需要 devkitARM）
4. 刷入固件后，界面设置 -> Theme color 选第 7 项

二、换图标
1. 用 icons.png 替换 res/icons.png（272x16，17 个 16x16 图块）
2. 运行 python res/iconcv.py res/icons.png > src/res/icons.h
   （或直接用本工具生成的 icons.h 覆盖 src/res/icons.h）
3. 重新编译

三、注意：主题序号有越界风险
settings.c 的 parse_ui_settings() 读 theme=N 时没有做范围检查。
如果你把主题选到第 7 项（theme=6），之后刷回只有 6 个主题的旧固件，
启动时 reload_theme(6) 会读到 themes[] 数组外面（垃圾配色，可能更糟）。

避免办法（二选一）：
  a. 刷回旧固件前，先在本工具里切回 1-6 号主题并保存
  b. 用 reload-theme-clamp.c 里那两行，给 reload_theme() 加个夹紧

四、theme.txt（需要固件支持）
当前固件不读这个文件。它需要一段约 40 行的读取逻辑：
启动时读 /.superfw/theme.txt，把 6 个十六进制值写进
MEM_PALETTE[16..19] 和 [240..243]，并作为第 7 个主题。
没有这段代码时，theme.txt 不会生效。

五、背景图片（wallpaper.idx / wallpaper-pal.bin）
固件本身只有纯色填充，但可以把自己的位图烘焙进去（"注入到固件"就是这么干的）：

  menu.c:2077  dma_memset16(&frame[16*240], dup8(BG_COLOR), 240*144/2)
  -> 编译内联后是几条写 DMA3 寄存器的指令（r75 在 0x0800A7B4 起 10 字节，
     chis 构建在 0x08006118 起 16 字节），整段换成 bl 钩子 + NOP

钩子在 EWRAM 载荷（运行地址 0x027C0000 起）的空位里，做两件事：
  1. 把 220 色调色板搬到 BG 调色板 20..239 号
  2. 用 CPU 的 ldmia/stmia 把 34560 字节位图铺到 frame+0xF00

位图本身写进固件内部不被任何拷贝覆盖的全 0 区，并把 bootloader 里 assets 的
sdram_copy 块数改大，让它们随 assets 一起被搬进 SDRAM。

注意：钩子里**不要用 DMA3 从卡带 SDRAM 读位图**。那块区域挂在 CPLD 后面，
固件自身读它全是 CPU（字体渲染、全局变量），DMA 只用来写；用 DMA 读在真机上
拿不到数据，表现就是调色板正常、位图全黑（v0.12 及更早的 bug）。

调色板 0..3（Logo）、16..19（FG/BG/FT/HI）、240..244（游戏内菜单）一个都没碰，
所以文字、图标、弹窗不受影响。

六、限制
- 顶栏（0..15 行）仍是 FG 纯色，只换内容区（16..159 行，240x144）。
- 图标共用一套 38 色调色板（含透明），本工具会自动量化到 37 色 + 透明。
- 背景图最多 236 色；淡化滑块可以把图压向 BG 色，保证文字可读。
"""


def selftest():
    import tempfile
    r = Renderer()
    r.pal_ref = PRESETS[0][1][:]
    img = r.screen("browser", PRESETS[0][1], "zh")
    out = os.path.join(tempfile.gettempdir(), "sfw_selftest.png")
    img.convert("RGB").resize((720, 480), Image.NEAREST).save(out)
    return out


def walltest(rom_path=None, out_dir=None):
    """打包后的自检：真的建界面 → 选图 → 渲染 → 注入固件，把结果写成文件。"""
    import tempfile
    out_dir = out_dir or tempfile.gettempdir()
    lines = []
    root = tk.Tk()
    root.withdraw()
    app = App(root)
    app.pal = PRESETS[4][1][:]
    for k in range(6):
        app.hexvars[k].set(app.pal[k])
    src = Image.new("RGB", (480, 288))
    d = ImageDraw.Draw(src)
    for y in range(288):
        d.rectangle([0, y, 480, y], fill=(20 + y // 2, 30 + y // 3, 120 - y // 4))
    d.ellipse([300, 40, 460, 200], fill=(250, 200, 60))
    app.wall_src = src
    app.wall_dim = 0.35
    app.dim_var.set(35)
    app._wall_key = None
    app.refresh()
    lines.append("壁纸: %s / %d 色 / idx %d" % (app.wall_state.cget("text"), app.wall_n,
                                             len(app.wall_idx or b"")))
    lines.append("可读性: %s / %s" % (app.read_grade.cget("text"), app.read_label.cget("text")))
    p1 = os.path.join(out_dir, "walltest-preview.png")
    app.r.screen("browser", app.pal, "zh", 2).convert("RGB").resize((720, 480), Image.NEAREST).save(p1)
    lines.append("预览: " + p1)
    if rom_path:
        rom = bytearray(open(rom_path, "rb").read())
        WP.apply(rom, app.wall_idx, app.wall_pal)
        p2 = os.path.join(out_dir, "walltest-injected.bin")
        open(p2, "wb").write(rom)
        lines.append("注入: %s 校验=%s" % (p2, fw_validate(rom)[0]))
    root.destroy()
    rep = os.path.join(out_dir, "walltest.txt")
    open(rep, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return rep


def main():
    import sys
    if "--selftest" in sys.argv:
        print("selftest:", selftest())
        return
    if "--walltest" in sys.argv:
        i = sys.argv.index("--walltest")
        rom = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        print("walltest:", walltest(rom))
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
