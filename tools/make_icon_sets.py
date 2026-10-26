"""生成两套 16x16 菜单图标的矢量画法，并导出 PNG / 整排精灵图 / 固件原始数据。

设计约束（都来自固件和编辑器，不是审美偏好）：
  * 一张 16x16，alpha=0 才算透明；
  * 17 个图标**共用一张 38 项调色板**（槽 0 占位透明，颜色只能有 37 个）；
  * 精灵按 8x8 四块 tile 存（左上/右上/左下/右下），跨 tile 的 1px 线容易出缝，
    所以所有线都落在整数像素上、不画 0.5px 的东西；
  * 顺序必须等于 fwassets.ICON_ORDER（enum ICON_* 下标），错一位整套错位。

做法：每个图标只描述两件事 —— 剪影（silhouette）和细节（detail）。
  * 「墨线」风格：只画剪影的 1px 边，内部留空 → 适合浅底主题；
  * 「实心」风格：内部填主体色 + 1px 深色边 → 适合深底主题。
同一份几何两种渲染，形状不会在两套之间漂。

用法：
    .venv314/Scripts/python.exe tools/make_icon_sets.py
    .venv314/Scripts/python.exe tools/make_icon_sets.py --preview   # 只重画预览
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import fwassets  # noqa: E402
import sdedit  # noqa: E402  （图标编码 + 真机布局预览都用它，保证和编辑器一致）
import sdtheme  # noqa: E402

W = H = 16
OUT = os.path.join("dist", "图标素材")

# 细节层用的角色名。INK/BODY 由风格决定，另外三个是固定色。
AC, DIM, ERASE = "AC", "DIM", "ERASE"


def snap(c):
    """对齐到编辑器/固件真正会还原出来的色值。

    固件是 15bit 色深，两个方向不是同一套网格：
      写入 color555(c) = c >> 3
      读出 rgb555(v)   = (v << 3) | (v >> 2)   ← 位复制，不是左移
    所以设计稿必须直接落在"读出后"的那一格上，否则导出再导入会整体偏一档
    （这也是 v0.18 图标错位那次的同类坑，只不过那次是索引，这次是色值）。
    """
    out = []
    for v in c[:3]:
        v5 = int(round(v * 31 / 255))
        out.append((v5 << 3) | (v5 >> 2))
    return tuple(out)


# ---------------------------------------------------------------- 画布（位掩码）


class Mask:
    """16x16 的布尔掩码，只负责「哪些像素在形状里」。"""

    def __init__(self, v=False):
        self.m = [[v] * W for _ in range(H)]

    # ---- 基本图元
    def rect(self, x0, y0, x1, y1, v=True):
        for y in range(max(0, y0), min(H - 1, y1) + 1):
            for x in range(max(0, x0), min(W - 1, x1) + 1):
                self.m[y][x] = v
        return self

    def px(self, x, y, v=True):
        if 0 <= x < W and 0 <= y < H:
            self.m[y][x] = v
        return self

    def line(self, x0, y0, x1, y1, v=True):
        """1px Bresenham。"""
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.px(x0, y0, v)
            if x0 == x1 and y0 == y1:
                break
            e2 = err * 2
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy
        return self

    def thick(self, x0, y0, x1, y1, w=2, v=True):
        """沿 1px 线盖 w x w 的方块，得到一条粗线（斜向也均匀）。"""
        tmp = Mask()
        tmp.line(x0, y0, x1, y1)
        for y in range(H):
            for x in range(W):
                if tmp.m[y][x]:
                    self.rect(x, y, x + w - 1, y + w - 1, v)
        return self

    def disc(self, cx, cy, r, v=True):
        for y in range(H):
            for x in range(W):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.m[y][x] = v
        return self

    def poly(self, pts, v=True):
        """扫描线填充多边形（偶奇规则）。"""
        ys = [p[1] for p in pts]
        for y in range(max(0, min(ys)), min(H - 1, max(ys)) + 1):
            xs = []
            for i in range(len(pts)):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % len(pts)]
                if y0 == y1:
                    continue
                if min(y0, y1) <= y < max(y0, y1):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                for x in range(int(round(xs[i])), int(round(xs[i + 1])) + 1):
                    self.px(x, y, v)
        return self

    # ---- 组合
    def sub(self, other):
        for y in range(H):
            for x in range(W):
                if other.m[y][x]:
                    self.m[y][x] = False
        return self

    def add(self, other):
        for y in range(H):
            for x in range(W):
                if other.m[y][x]:
                    self.m[y][x] = True
        return self

    def edge(self):
        """4 邻域有外部像素的点 → 1px 轮廓。"""
        out = Mask()
        for y in range(H):
            for x in range(W):
                if not self.m[y][x]:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < W and 0 <= ny < H) or not self.m[ny][nx]:
                        out.m[y][x] = True
                        break
        return out


# ---------------------------------------------------------------- 17 个图标
# 每个函数返回 (剪影, [(细节掩码, 角色), ...])。坐标 0..15，左上原点。


def ic_folder():
    """文件夹：左上 2 行高的标签页 + 主体，中间一道强调色纸带。"""
    s = Mask()
    s.rect(1, 3, 5, 4)          # 标签页（比主体窄 2/3，才有"文件夹"的台阶感）
    s.rect(1, 5, 14, 13)        # 主体
    d = [(Mask().rect(1, 3, 5, 4), DIM),     # 标签页压暗一档，主体才立得起来
         (Mask().rect(3, 8, 7, 9), AC)]
    return s, d


def ic_binfile():
    """普通文件：右上折角的纸，三条字行，末行短一点。"""
    s = Mask().rect(3, 1, 12, 14)
    s.sub(Mask().poly([(9, 0), (13, 0), (13, 5)]))     # 折角
    d = [(Mask().rect(5, 6, 10, 6), AC),
         (Mask().rect(5, 8, 10, 8), AC),
         (Mask().rect(5, 10, 8, 10), AC)]
    return s, d


def ic_updfile():
    """升级文件：纸 + 向上的粗箭头。

    箭头必须走细节层：放进剪影的话，它就成了"内部像素"，实心模式会被填平、
    线框模式直接看不见 —— 第一版就是这里画成了空白方框。
    箭头头用"每行左右各多 1px"的台阶拼，不用 Bresenham 三角形：16px 下三角形的
    1px 斜边会糊成一团蘑菇，台阶版在两种风格里都是直的。
    """
    s = Mask().rect(2, 1, 13, 14)
    a = Mask()
    for i, (xa, xb) in enumerate(((7, 8), (6, 9), (5, 10), (4, 11))):
        a.add(Mask().rect(xa, 3 + i, xb, 3 + i))       # 箭头：2 → 4 → 6 → 8 宽
    a.add(Mask().rect(4, 7, 11, 7))                    # 头的底边
    a.add(Mask().rect(7, 7, 8, 12))                    # 杆
    return s, [(a, AC)]


def ic_gbcart():
    """GB 卡带：竖版，右上角一个缺口。"""
    s = Mask().rect(3, 1, 12, 14)
    s.sub(Mask().rect(10, 1, 12, 2))
    d = [(Mask().rect(5, 4, 10, 6), AC),
         (Mask().rect(5, 10, 10, 10), DIM),
         (Mask().rect(5, 12, 10, 12), DIM)]
    return s, d


def ic_gbccart():
    """GBC 卡带：比 GB 窄一档、顶角切平，标签压在下半。"""
    s = Mask().rect(4, 2, 11, 14)
    s.sub(Mask().px(4, 2)).sub(Mask().px(11, 2))
    d = [(Mask().rect(6, 4, 9, 4), DIM),
         (Mask().rect(5, 9, 10, 11), AC)]
    return s, d


def ic_gbacart():
    """GBA 卡带：横版，标签靠左。"""
    s = Mask().rect(2, 4, 14, 11)
    d = [(Mask().rect(4, 6, 9, 9), AC),
         (Mask().rect(11, 6, 12, 9), DIM)]
    return s, d


def ic_smscart():
    """SMS 卡：比 GBA 薄一档的横卡，左侧三道金手指（1px 竖线）。"""
    s = Mask().rect(2, 5, 14, 10)
    d = [(Mask().rect(4, 6, 4, 9), AC),
         (Mask().rect(6, 6, 6, 9), AC),
         (Mask().rect(8, 6, 8, 9), AC)]
    return s, d


def ic_nescart():
    """NES 卡带：顶部一圈更宽的外沿 + 上下两道纹。"""
    s = Mask().rect(3, 2, 12, 14)
    s.add(Mask().rect(2, 1, 13, 2))
    d = [(Mask().rect(5, 4, 10, 4), DIM),
         (Mask().rect(5, 8, 10, 11), AC),
         (Mask().rect(5, 13, 10, 13), DIM)]
    return s, d


def ic_recent():
    """最近使用：时钟。分针朝上、时针朝右（3 点）。"""
    s = Mask().disc(8, 8, 6)
    d = [(Mask().disc(8, 8, 4), ERASE),               # 表盘挖空：线框模式才不会变双圈
         (Mask().rect(8, 4, 8, 7), AC),
         (Mask().rect(9, 8, 11, 8), AC),
         (Mask().px(8, 8), AC)]
    return s, d


def ic_disk():
    """卡带浏览：SD 卡，右上斜角 + 三片触点 + 一块标签。"""
    s = Mask().rect(2, 2, 13, 13)
    s.sub(Mask().poly([(9, 0), (14, 0), (14, 6)]))
    d = [(Mask().rect(4, 3, 5, 4), AC),
         (Mask().rect(7, 3, 8, 4), AC),
         (Mask().rect(10, 3, 11, 4), AC),
         (Mask().rect(4, 8, 11, 10), DIM)]
    return s, d


def ic_flash():
    """NOR 闪存：中间芯片 + 两侧各四根引脚。

    引脚和芯片之间必须留 1px 缝：贴死的话，引脚所在那一行的芯片竖边就不是
    "轮廓像素"了，线框模式下整个芯片会消失（第一版就是这么翻车的）。
    """
    s = Mask().rect(4, 3, 11, 12)
    for y in (4, 6, 8, 10):
        s.add(Mask().rect(1, y, 2, y))
        s.add(Mask().rect(13, y, 14, y))
    d = [(Mask().rect(5, 4, 6, 5), AC)]                 # 一号脚标记（1px 点在这个尺寸下看不见）
    return s, d


def ic_settings():
    """设置：8 齿齿轮。齿按 cos(8θ) 算出来，不是拿方块拼的 —— 拼的齿根会断。"""
    s = Mask()
    for y in range(H):
        for x in range(W):
            dx, dy = x + 0.5 - 8.0, y + 0.5 - 8.0
            r = math.hypot(dx, dy)
            if r > 7.0:                                # 半径 7 刚好贴边
                continue
            # 齿根 5.2 以内一律算实体；往外只有 8 个角度窗口算齿。
            # duty 取 -0.1 让齿占约 53% 的角宽，再宽齿就会糊成一圈。
            # 中心不挖空：挖空的话轴心那点亮色无处落笔，齿轮就只剩一圈灰环。
            if r <= 5.2 or math.cos(8 * math.atan2(dy, dx)) > -0.1:
                s.m[y][x] = True
    # 中心一个亮轴：16px 下这是"齿轮"和"花环"的唯一区别
    return s, [(Mask().disc(8, 8, 1.5), AC)]


def ic_uilang():
    """界面语言：对话气泡 + 字母 A。"""
    s = Mask().rect(2, 2, 13, 10)
    for x, y in ((2, 2), (13, 2), (2, 10), (13, 10)):   # 圆角
        s.sub(Mask().px(x, y))
    s.add(Mask().poly([(3, 10), (6, 10), (4, 13)]))     # 气泡尾
    d = [(Mask().line(8, 4, 6, 8), AC),
         (Mask().line(8, 4, 10, 8), AC),
         (Mask().rect(6, 7, 10, 7), AC)]
    return s, d


def ic_tools():
    """工具：工具箱（提手 + 箱体 + 锁扣）。

    试过开口扳手和梅花扳手各两版，16px 下都容易读成"镰刀 / 放大镜"——
    开口要么太大把钳口削没、要么太小糊成一坨。工具箱形状简单、辨识度反而最高。
    """
    s = Mask().rect(2, 6, 13, 13)
    h = Mask().rect(5, 2, 10, 5)
    h.sub(Mask().rect(6, 3, 9, 5))                     # 挖空 = 提手
    s.add(h)
    d = [(Mask().rect(2, 8, 13, 8), DIM),              # 盖缝
         (Mask().rect(7, 8, 8, 11), AC)]               # 锁扣
    return s, []


def ic_info():
    """关于：圆里的 i。竖笔 2px —— 1px 在 12px 的圆里缩到真机尺寸就看不见了。"""
    s = Mask().disc(8, 8, 6)
    d = [(Mask().rect(7, 3, 8, 4), AC),
         (Mask().rect(7, 6, 8, 10), AC)]
    return s, d


def ic_hfolder():
    """隐藏文件夹：文件夹 + 一道斜杠（"不显示"）。"""
    s = Mask()
    s.rect(1, 3, 5, 4)
    s.rect(1, 5, 14, 13)
    return s, [], Mask().line(3, 12, 12, 3)


def ic_hfile():
    """隐藏文件：折角纸 + 一道斜杠。"""
    s = Mask().rect(3, 1, 12, 14)
    s.sub(Mask().poly([(9, 0), (13, 0), (13, 5)]))
    return s, [], Mask().line(3, 12, 12, 3)


# 17 个图标，顺序必须等于 fwassets.ICON_ORDER。
# 第三列 = 色相，第四列 = 实心模式下主体填什么：
#   "hue"   → 主体填该色相（卡带、时钟这种有实体感的物件）
#   "paper" → 主体保持纸白/米白，色相只出现在字行、箭头这些细节上（文件类）
# 第五列 = 细节层的固定色相（None = 跟随主体色相）。
# 色相不是随手配的：五个卡带按真机标签色分（GB 灰绿 / GBC 紫 / GBA 靛蓝 / SMS 红 / NES 米灰+红纹），
# 这样扫一眼列表就知道哪个是哪个平台，颜色在这里是信息，不是装饰。
ICONS = [
    ("FOLDER", ic_folder, "AMBER", "hue", None),
    ("BINFILE", ic_binfile, "BLUE", "paper", None),
    ("UPDFILE", ic_updfile, "GREEN", "paper", None),
    ("GBCART", ic_gbcart, "GRAYGREEN", "hue", None),
    ("GBCCART", ic_gbccart, "PURPLE", "hue", None),
    ("GBACART", ic_gbacart, "BLUE", "hue", None),
    ("SMSCART", ic_smscart, "RED", "hue", None),
    ("NESCART", ic_nescart, "WARM", "hue", "RED"),
    ("RECENT", ic_recent, "CYAN", "hue", None),
    ("DISK", ic_disk, "TEAL", "hue", None),
    ("FLASH", ic_flash, "MAGENTA", "hue", None),
    ("SETTINGS", ic_settings, "STEEL", "hue", None),
    ("UILANG_SETTINGS", ic_uilang, "YELLOW", "hue", None),
    ("TOOLS", ic_tools, "ORANGE", "hue", None),
    ("INFO", ic_info, "AZURE", "hue", None),
    ("HFOLDER", ic_hfolder, "AMBER", "hue", None),
    ("HFILE", ic_hfile, "BLUE", "paper", None),
]

# 每色相两档：主色 + 暗色（暗色当标签页 / 纹路 / 阴影，16px 下比描边更能分出层次）。
HUES = {
    "AMBER": ((0xE0, 0xA4, 0x38), (0xA9, 0x72, 0x18)),
    "BLUE": ((0x4C, 0x6F, 0xD0), (0x33, 0x48, 0x9B)),
    "GREEN": ((0x5F, 0xAF, 0x4A), (0x3C, 0x7A, 0x2E)),
    "GRAYGREEN": ((0x94, 0xA4, 0x7C), (0x66, 0x74, 0x4E)),
    "PURPLE": ((0x8A, 0x6B, 0xC8), (0x5E, 0x45, 0x90)),
    "RED": ((0xC4, 0x46, 0x3A), (0x8E, 0x2A, 0x22)),
    "WARM": ((0xC8, 0xB6, 0x9A), (0x96, 0x86, 0x6E)),
    "CYAN": ((0x3F, 0xA8, 0xD0), (0x2A, 0x75, 0x93)),
    "TEAL": ((0x35, 0xAE, 0x9C), (0x20, 0x77, 0x6A)),
    "MAGENTA": ((0xC0, 0x46, 0x9C), (0x85, 0x30, 0x6B)),
    "STEEL": ((0xA0, 0xA8, 0xB4), (0x6C, 0x74, 0x80)),
    "YELLOW": ((0xD8, 0xBE, 0x3C), (0x9E, 0x8A, 0x1C)),
    "ORANGE": ((0xDE, 0x70, 0x38), (0xA4, 0x4A, 0x1E)),
    "AZURE": ((0x4C, 0x8F, 0xE8), (0x2E, 0x64, 0xB4)),
}
SLASH = "RED"     # 隐藏文件夹/文件那道斜杠：跟暖色主体拉开，黄斜杠会读成"施工胶带"


CN = {
    "FOLDER": "文件夹", "BINFILE": "普通文件", "UPDFILE": "升级文件",
    "GBCART": "GB 卡带", "GBCCART": "GBC 卡带", "GBACART": "GBA 卡带",
    "SMSCART": "SMS 卡带", "NESCART": "NES 卡带", "RECENT": "最近使用",
    "DISK": "卡带浏览", "FLASH": "NOR 闪存", "SETTINGS": "设置",
    "UILANG_SETTINGS": "界面语言", "TOOLS": "工具", "INFO": "关于",
    "HFOLDER": "隐藏文件夹", "HFILE": "隐藏文件",
}

# ---------------------------------------------------------------- 配色方案
# 每套总色数必须 <= 37（17 个图标共用一张调色板）。
# A/B 是"素色"两档（墨 + 单一强调色），C/D 是"彩绘"两档 —— 每个图标有自己的色相。

NEUTRAL = {
    "INK": (0x1E, 0x1A, 0x18),         # 描边墨色
    "PAPER": (0xF2, 0xEC, 0xDE),       # 纸白 / 浅主体
    "MID": (0x9C, 0x92, 0x80),         # 中性灰（次要面）
}


def mix(a, b, t):
    """按 t 把 a 往 b 混（0..1），结果仍落在读回后的色格上。"""
    return snap(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


STYLES = {
    "A": {
        "key": "A",
        "dir": "方案A-墨线",
        "name": "墨线",
        "mode": "line",
        "for": "浅底主题（米白纸感 / 灰蓝极简）",
        "pal": {
            "INK": snap((0x2E, 0x2A, 0x25)),      # 墨色轮廓
            "BODY": snap((0xF2, 0xEC, 0xE0)),     # line 模式不用，占位
            "AC": snap((0xC1, 0x65, 0x2A)),       # 铁锈橙
            "DIM": snap((0x8B, 0x83, 0x77)),      # 次要线
        },
    },
    "B": {
        "key": "B",
        "dir": "方案B-实心",
        "name": "实心",
        "mode": "solid",
        "for": "深底主题（深蓝终端 / 暗夜霓虹 / 暖橙日落）",
        "pal": {
            "INK": snap((0x0E, 0x12, 0x18)),      # 近黑轮廓
            "BODY": snap((0xE7, 0xED, 0xF3)),     # 浅灰主体
            "AC": snap((0x4F, 0xD1, 0xC5)),       # 青绿强调
            "DIM": snap((0x7A, 0x8B, 0x9E)),      # 次要面（比主体暗一档，16px 才看得出结构）
        },
    },
    "C": {
        "key": "C",
        "dir": "方案C-彩绘浅底",
        "name": "彩绘浅底",
        "mode": "soft",
        "for": "浅底主题（米白纸感 / 灰蓝极简 / 淡彩）",
        "tint": 0.36,     # 主体 = 纸白与色相的混合比例，太高会把描边吃掉
    },
    "D": {
        "key": "D",
        "dir": "方案D-彩绘深底",
        "name": "彩绘深底",
        "mode": "vivid",
        "for": "深底主题（深蓝终端 / 暗夜霓虹 / 暖橙日落）",
    },
}


def colors_for(style, hue_name, body, ac_hue=None):
    """把「风格 + 该图标的色相」解成四个角色色。返回的色值都已 snap 过。"""
    if style["mode"] in ("line", "solid"):
        p = style["pal"]
        return {"INK": p["INK"], "BODY": p["BODY"], "AC": p["AC"],
                "DIM": p["DIM"], "SLASH": p["AC"]}

    ink, paper = snap(NEUTRAL["INK"]), snap(NEUTRAL["PAPER"])
    hue, hue_d = HUES[hue_name]

    if style["mode"] == "soft":
        # 浅底：主体是极淡的该色相，颜色集中在字行/箭头/标签上，
        # 这样一屏 17 个是"一色系的彩"，不会花成贴纸。
        # 亮色相（黄/米）在纸白上不够跳，细节改用暗档，否则整张图会"没有笔触"。
        if ac_hue:
            ac = snap(HUES[ac_hue][0])
        elif luma(hue) > 165:
            ac = snap(hue_d)
        else:
            ac = snap(hue)
        return {"INK": ink,
                # 文件类在浅底方案里保持纸白：染了色就分不出"纸"和"文件夹"
                "BODY": paper if body == "paper" else mix(paper, hue, style["tint"]),
                "AC": ac,
                # 次要面用同色系深一档（不是灰），标签页才像"同一个物件的背面"
                "DIM": mix(paper, hue, 0.62),
                "SLASH": snap(HUES[SLASH][1])}

    # 深底：主体直接上色（文件类保留纸白，否则白纸蓝字反过来没法读）
    use_hue = body == "hue"
    if ac_hue:
        ac = snap(HUES[ac_hue][0])
    else:
        ac = paper if use_hue else snap(hue)
    return {"INK": ink,
            "BODY": snap(hue) if use_hue else paper,
            "AC": ac,
            "DIM": snap(hue_d),
            "SLASH": snap(HUES[SLASH][0])}


def render(name, style):
    """渲染成 16x16 RGBA。"""
    entry = dict((n, (f, h, b, x)) for n, f, h, b, x in ICONS)[name]
    fn, hue_name, body, ac_hue = entry
    res = fn()
    sil, det = res[0], res[1]
    slash = res[2] if len(res) > 2 else None
    c = colors_for(style, hue_name, body, ac_hue)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = im.load()

    edge = sil.edge()
    fill = style["mode"] != "line"
    for y in range(H):
        for x in range(W):
            if edge.m[y][x]:
                px[x, y] = c["INK"] + (255,)
            elif sil.m[y][x] and fill:
                px[x, y] = c["BODY"] + (255,)

    for m, role in det:
        if role == ERASE:
            for y in range(H):
                for x in range(W):
                    if m.m[y][x] and sil.m[y][x]:
                        px[x, y] = (0, 0, 0, 0)
        else:
            for y in range(H):
                for x in range(W):
                    if m.m[y][x] and sil.m[y][x]:
                        px[x, y] = c[role] + (255,)

    if slash is not None:
        # 斜杠压在图形上：先沿斜杠给一圈墨边，再填强调色，读起来像“切了一道口”
        halo = Mask()
        for y in range(H):
            for x in range(W):
                if not slash.m[y][x]:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and sil.m[ny][nx] and not slash.m[ny][nx]:
                        halo.m[ny][nx] = True
        for y in range(H):
            for x in range(W):
                if halo.m[y][x]:
                    px[x, y] = c["INK"] + (255,)
        for y in range(H):
            for x in range(W):
                if slash.m[y][x]:
                    px[x, y] = c["SLASH"] + (255,)
    return im


def build(style):
    imgs = [render(name, style) for name, *_ in ICONS]
    colors = {p[:3] for im in imgs for p in im.getdata() if p[3]}
    assert len(colors) <= 37, "总色数 %d 超过 37" % len(colors)
    return imgs, sorted(colors)


# ---------------------------------------------------------------- 输出


def upscale(im, k):
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def sheet(imgs, k=1, cols=17):
    pad = 2 * k
    rows = (len(imgs) + cols - 1) // cols
    out = Image.new("RGBA", (cols * (16 * k + pad) + pad,
                             rows * (16 * k + pad) + pad), (0, 0, 0, 0))
    for i, im in enumerate(imgs):
        out.alpha_composite(upscale(im, k), (pad + (i % cols) * (16 * k + pad),
                                             pad + (i // cols) * (16 * k + pad)))
    return out


def font(sz):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except OSError:
                pass
    return ImageFont.load_default()


def labeled_grid(imgs, k, cols, backgrounds, title):
    """把图标按 k 倍画在指定底色的格子里，带名字标签，便于一眼看出可读性。"""
    cell = 16 * k
    lh = 14
    pad = 6
    rows = (len(imgs) + cols - 1) // cols
    cw = cell + pad * 2
    ch = cell + pad * 2 + lh
    out = Image.new("RGB", (cols * cw + pad, rows * ch + pad + 22), backgrounds[0])
    dr = ImageDraw.Draw(out)
    f = font(11)
    dr.text((pad + 2, 4), title, font=font(13), fill=(230, 230, 230))
    for i, im in enumerate(imgs):
        cx = pad + (i % cols) * cw
        cy = pad + 22 + (i // cols) * ch
        bgc = backgrounds[i % len(backgrounds)]
        dr.rectangle([cx, cy, cx + cw - 3, cy + ch - 3], fill=bgc)
        out.paste(upscale(im, k), (cx + pad, cy + pad), upscale(im, k))
        dr.text((cx + pad, cy + pad + cell + 1), "%02d %s" % (i, CN[ICONS[i][0]]),
                font=f, fill=(150, 150, 150))
    return out


def export(style, imgs, colors, root):
    d = os.path.join(root, style["dir"])
    for sub in ("16x16", "128x128", "原始数据"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    names = [n for n, *_ in ICONS]
    for i, (n, im) in enumerate(zip(names, imgs)):
        base = "%02d_%s.png" % (i, n)
        im.save(os.path.join(d, "16x16", base))
        upscale(im, 8).save(os.path.join(d, "128x128", base))
    sheet(imgs, 1).save(os.path.join(d, "整排精灵图.png"))
    sheet(imgs, 8).save(os.path.join(d, "整排精灵图@8x.png"))

    # 回写成固件要的 4352 字节精灵 + 38 项调色板，用的是编辑器自己的编码函数，
    # 这样"导出 → 导入编辑器 → 写进 theme.bin"这条路和手工改图完全一致。
    sprite, pal = sdedit.icons_to_raw(imgs)
    assert len(sprite) == 4352, "精灵块应为 4352 字节，实得 %d" % len(sprite)
    assert len(pal) == 38, "调色板应为 38 项，实得 %d" % len(pal)
    open(os.path.join(d, "原始数据", "icons.raw"), "wb").write(sprite)
    open(os.path.join(d, "原始数据", "icons.pal"), "wb").write(
        b"".join(int(v).to_bytes(2, "little") for v in pal))

    # 无损自检：把刚写出的字节解回来，必须和设计稿逐像素一致
    back = sdedit.icons_from_raw(sprite, pal)
    same = all(list(back[i].convert("RGBA").getdata()) ==
               list(imgs[i].convert("RGBA").getdata()) for i in range(len(imgs)))
    assert same, "回写后与原图不一致"

    meta = {"style": style["name"], "mode": style["mode"], "for": style["for"],
            "colors": ["#%02X%02X%02X" % c for c in colors],
            "palette_len": len(pal),
            "roundtrip": "逐像素一致",
            "icons": [{"i": i, "name": n, "cn": CN[n],
                       "colors": len({p[:3] for p in im.getdata() if p[3]})}
                      for i, (n, im) in enumerate(zip(names, imgs))]}
    with open(os.path.join(d, "原始数据", "icons.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return d, len(pal)


def luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def presets_preview(root):
    """把彩绘两套塞进「6 套风格提案」的真实布局里出图。

    配哪套不由人挑，按那套主题的内容底色亮度自动配：底色亮的用「彩绘浅底」，
    底色暗的用「彩绘深底」。
    """
    src = os.path.join("dist", "主题风格提案", "theme.bin")
    if not os.path.exists(src):
        print("跳过上机效果图：没找到", src)
        return None
    data = open(src, "rb").read()
    doc = sdedit.Doc.from_bytes(data, src)
    C, _ = build(STYLES["C"])
    D, _ = build(STYLES["D"])

    cells, picks = [], []
    for i in range(6):
        bg = doc.pals[i][1]
        use_c = luma(bg) > 110
        doc.icon_imgs[i] = C if use_c else D
        doc.icon_dirty[i] = True
        picks.append("套%d %s" % (i + 1, "彩绘浅底" if use_c else "彩绘深底"))
        cells.append(sdedit.render_preview(doc, i))

    k = 2
    cw, ch = 240 * k, 160 * k
    out = Image.new("RGB", (3 * (cw + 8) + 8, 2 * (ch + 22) + 8 + 20), (46, 46, 46))
    dr = ImageDraw.Draw(out)
    f = font(12)
    dr.text((10, 4), "彩绘两套装进六套主题：底色亮的自动配「彩绘浅底」，底色暗的自动配「彩绘深底」",
            font=font(13), fill=(228, 228, 228))
    for i, im in enumerate(cells):
        x = 8 + (i % 3) * (cw + 8)
        y = 24 + (i // 3) * (ch + 22)
        out.paste(im.resize((cw, ch), Image.NEAREST), (x, y))
        dr.text((x + 2, y + ch + 3), picks[i], font=f, fill=(160, 160, 160))
    p = os.path.join(root, "预览-上机效果-六套主题.png")
    out.save(p)

    # 顺手出一份"已经套好新图标"的 theme.bin：编辑器图标页是 17 格逐个点选，
    # 两套就是 34 次，太麻烦。这份拷到 SD 卡就能直接看，配色和「主题风格提案」
    # 里那份完全一样，只有图标换了。
    t = os.path.join(root, "theme-带新图标.bin")
    data2 = doc.to_bytes()
    open(t, "wb").write(data2)
    r = sdtheme.parse(data2)
    print("上机用 theme.bin -> %s（v%d，%d B，图标 6 套已套用）"
          % (t, r["version"], len(data2)))
    print("  配色仍是 主题风格提案/theme.bin 那 6 套，只换图标；两套按底色亮度自动配")
    return p


def main():
    ap = argparse.ArgumentParser(description="生成多套 16x16 菜单图标")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--no-theme", action="store_true", help="不生成主题内效果图")
    a = ap.parse_args()

    for key in ("A", "B", "C", "D"):
        style = STYLES[key]
        imgs, colors = build(style)
        d, npal = export(style, imgs, colors, a.out)
        print("%s %s" % (style["dir"], style["for"]))
        print("   总色数 %d / 上限 37    调色板 %d 项    回写逐像素一致" %
              (len(colors), npal))
        print("   ->", d)

    A, _ = build(STYLES["A"])
    B, _ = build(STYLES["B"])
    C, _ = build(STYLES["C"])
    D, _ = build(STYLES["D"])
    labeled_grid(A, 4, 6, [(0xF5, 0xF1, 0xE6), (0xDD, 0xE3, 0xE8)],
                 "方案 A「墨线」— 配浅底主题").save(os.path.join(a.out, "预览-方案A-墨线.png"))
    labeled_grid(B, 4, 6, [(0x1B, 0x2A, 0x4A), (0x1E, 0x21, 0x28)],
                 "方案 B「实心」— 配深底主题").save(os.path.join(a.out, "预览-方案B-实心.png"))
    labeled_grid(C, 4, 6, [(0xF5, 0xF1, 0xE6), (0xE0, 0xE6, 0xEA)],
                 "方案 C「彩绘浅底」— 配浅底主题").save(
                     os.path.join(a.out, "预览-方案C-彩绘浅底.png"))
    labeled_grid(D, 4, 6, [(0x1B, 0x2A, 0x4A), (0x1E, 0x21, 0x28)],
                 "方案 D「彩绘深底」— 配深底主题").save(
                     os.path.join(a.out, "预览-方案D-彩绘深底.png"))
    if not a.no_theme:
        p = presets_preview(a.out)
        if p:
            print("上机效果图 ->", p)


if __name__ == "__main__":
    main()
