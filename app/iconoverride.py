# -*- coding: utf-8 -*-
"""内置图标重设计 —— 覆盖固件里不好认的默认图标。

目前只有「界面 / 语言」（ICON_ORDER[12] UILANG_SETTINGS）：
原设计是齿轮旁边挤一个「中」，16px 下两个符号都糊了；
改成对话气泡 + 白色「中」，一眼能读出"语言/界面"。
想再加别的覆盖，往 OVERRIDES 里添 {槽位: 16x16 字符画} 即可。
"""

import base64
import io

# 字符画 → 颜色：o 描边 / b 气泡底 / w 白色笔画 / . 透明
_PALETTE = {
    "o": (36, 48, 66, 255),      # 深藏青描边
    "b": (72, 138, 220, 255),    # 气泡蓝
    "w": (250, 252, 255, 255),   # 白
    ".": (0, 0, 0, 0),
}

# 「中」：竖笔 2px 贯穿（y4..y9），方框 x4..10 / y5..8
_UILANG_GRID = [
    "................",
    "................",
    "..ooooooooooo...",
    ".obbbbbbbbbbo...",
    ".obbbbbwwbbbbo..",
    ".obbwwwwwwwbbo..",
    ".obbwbbwwbwbbo..",
    ".obbwbbwwbwbbo..",
    ".obbwwwwwwwbbo..",
    ".obbbbbwwbbbbo..",
    ".obbbbbbbbbbo...",
    "..ooooooooooo...",
    "...ooo..........",
    "....oo..........",
    "................",
    "................",
]


def _from_grid(grid):
    from PIL import Image
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            img.putpixel((x, y), _PALETTE[ch])
    return img


# 槽位 → 图标（槽位与 fwassets.ICON_ORDER 对齐）
OVERRIDES = {12: _from_grid(_UILANG_GRID)}

_PNG_CACHE = {}


def png_b64(slot):
    """图标的 16×16 PNG base64（给预览/调试用）。"""
    if slot not in _PNG_CACHE:
        img = OVERRIDES[slot]
        buf = io.BytesIO()
        img.save(buf, "PNG")
        _PNG_CACHE[slot] = base64.b64encode(buf.getvalue()).decode()
    return _PNG_CACHE[slot]


def apply(renderer):
    """把覆盖图标换进 Renderer（原值存进 _overridden 便于还原）。"""
    if getattr(renderer, "_icon_overridden", False):
        return
    for slot, img in OVERRIDES.items():
        if slot < len(renderer.icons):
            renderer.icons[slot] = img.copy()
    renderer._icon_overridden = True
