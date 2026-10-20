# -*- coding: utf-8 -*-
"""用固件自己的点阵字库渲染文字，让编辑器预览和真机一个像素。

真机菜单的文字不是 TTF，是 `fw/res/fonts/unscii-16-full.hex` 生成的位图字库
（`fw/src/fonts/font_render.c` 按列数据绘制）。所以预览里用微软雅黑永远对不上：
字形、字宽、间距全都不同。

这个模块直接读同一个 .hex，按固件的规则画：

    ASCII 等 8 宽字形 → 裁掉左右空白列，宽度 1..8，字后留 1 像素间距
    CJK 等 16 宽字形 → 宽度 16，不留间距（固件里是 FW16 块）

字形来源：http://viznut.fi/unscii/ （GPL），和固件用的是同一份。
"""

import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEX_PATH = os.path.join(ROOT, "fw", "res", "fonts", "unscii-16-full.hex")

FONT_H = 16
SPACING = 1          # 固件 CHAR_SPACING

# 顶层导入一次：PyInstaller 静态分析能看见，exe 里才会带上这个子集模块。
try:
    from uifont_data import GLYPHS as _EMBED_GLYPHS, LOGO_PNG as _EMBED_LOGO
except Exception:            # 开发环境还没生成子集时走 .hex 回落
    _EMBED_GLYPHS = None
    _EMBED_LOGO = None

_font = None


def _parse():
    """码点 -> (宽, 16 行整数)。行是 16 位，最高位是最左列。

    优先用 tools/make_uifont.py 生成的子集（exe 里只有这个，几 KB）；
    开发时没有子集就回落到完整的 unscii .hex。
    """
    if _EMBED_GLYPHS is not None:
        return dict(_EMBED_GLYPHS)

    data = {}
    with open(HEX_PATH, "r", encoding="ascii", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or ":" not in line:
                continue
            cp_s, bits = line.split(":", 1)
            try:
                cp = int(cp_s, 16)
                raw = bytes.fromhex(bits)
            except ValueError:
                continue
            if len(raw) == FONT_H:            # 8 宽
                rows = [raw[y] for y in range(FONT_H)]
                w = 8
            elif len(raw) == FONT_H * 2:      # 16 宽
                rows = [(raw[y * 2] << 8) | raw[y * 2 + 1] for y in range(FONT_H)]
                w = 16
            else:
                continue
            data[cp] = (w, rows)
    return data


def load():
    global _font
    if _font is None:
        _font = _parse()
    return _font


def glyph(ch):
    """取一个字符的字形；缺字回落 '?'。返回 (宽, 行数据)。"""
    g = load()
    return g.get(ord(ch)) or g.get(26) or (8, [0] * FONT_H)


def _trim(rows, w):
    """裁掉左右全空列，返回 (新宽, 左偏移, 新的行数据)。8 宽字形才裁。"""
    used = 0
    for y in range(FONT_H):
        used |= rows[y]
    if not used:
        return 4, 0, rows
    left = 0
    while left < w and not any((rows[y] >> (w - 1 - left)) & 1 for y in range(FONT_H)):
        left += 1
    right = w - 1
    while right > left and not any((rows[y] >> (w - 1 - right)) & 1 for y in range(FONT_H)):
        right -= 1
    neww = right - left + 1
    if left == 0 and neww == w:
        return w, 0, rows
    out = []
    for y in range(FONT_H):
        v = rows[y] >> (w - 1 - right)
        out.append(v & ((1 << neww) - 1))
    return neww, left, out


_cache = {}


def cell(ch):
    """返回 (宽, 蒙版 Image)，宽 = 字形宽 + 字后间距。带缓存。"""
    if ch in _cache:
        return _cache[ch]

    w, rows = glyph(ch)
    left = 0
    if w != 16:                      # CJK 不裁，ASCII 裁空白
        w, left, rows = _trim(rows, w)
    adv = w + (0 if w == 16 else SPACING)

    mask = Image.new("L", (max(w, 1), FONT_H), 0)
    px = mask.load()
    for y in range(FONT_H):
        bits = rows[y]
        for dx in range(w):
            if (bits >> (w - 1 - dx)) & 1:
                px[dx, y] = 255
    _cache[ch] = (adv, mask)
    return _cache[ch]


def width(text):
    """和固件 font_width() 一致：每字累加 (字宽 + 间距)。"""
    return sum(cell(c)[0] for c in text)


def draw(im, x, y, text, color, limit=None):
    """把 text 画到 RGB 图 im 的 (x, y)。limit 给定时超出就截断（固件是滚动）。

    返回实际画到的 x。
    """
    cx = x
    for ch in text:
        adv, mask = cell(ch)
        if limit is not None and cx + adv - (0 if mask.width == 16 else 0) > x + limit:
            break
        if mask.width:
            im.paste(color, (cx, y), mask)
        cx += adv
    return cx


def draw_center(im, y, text, color, x0=0, x1=240):
    """居中（固件 draw_central_text 的语义：按 font_width 居中）。"""
    w = width(text)
    return draw(im, x0 + (x1 - x0 - w) // 2, y, text, color)


def draw_right(im, x_right, y, text, color, limit=None):
    """右对齐到 x_right。"""
    w = width(text)
    if limit is not None and w > limit:
        w = limit
    return draw(im, x_right - w, y, text, color, limit)


def fit(text, maxw, ellipsis=".."):
    """按像素宽度截断（固件是滚动，预览里静态截断）。

    真机超宽会滚动，静态预览看不出"后面还有字"，所以截断时补省略号占位；
    本来放得下就原样返回，不加任何东西。
    """
    if width(text) <= maxw:
        return text
    ew = width(ellipsis) if ellipsis else 0
    out = ""
    for ch in text:
        if width(out + ch) + ew > maxw:
            break
        out += ch
    return out + ellipsis if ellipsis else out


def snap(im, x, y, text, color, maxw=240):
    """画一行可能超宽的文字，超了就截断。"""
    return draw(im, x, y, fit(text, maxw), color)


# ---------------------------------------------------------------- logo
_LOGO = None


def logo():
    """固件的 SUPERFW 标志（31x7 RGBA），真机是 4 倍放大画的。"""
    global _LOGO
    if _LOGO is None:
        try:
            import base64
            import io
            _LOGO = Image.open(io.BytesIO(base64.b64decode(_EMBED_LOGO))).convert("RGBA")
        except Exception:
            p = os.path.join(ROOT, "fw", "res", "logo.png")
            _LOGO = Image.open(p).convert("RGBA")
    return _LOGO


def draw_logo(im, cx, y, scale=4):
    lg = logo()
    big = lg.resize((lg.width * scale, lg.height * scale), Image.NEAREST)
    im.paste(big, (cx - big.width // 2, y), big)
    return big.size
