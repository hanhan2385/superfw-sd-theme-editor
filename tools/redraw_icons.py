# -*- coding: utf-8 -*-
"""
SuperFW 图标重绘（17 个 16×16）
==============================
约束来自固件本身，不是随便画：
  * 每图标 16×16，切成 4 个 8×8 tile，顺序 (0,0)(8,0)(0,8)(8,8)
  * 索引 0 = 透明，其余共用**一套 38 色调色板**（本套 23 色）
  * 轮廓统一 1px 深色，保证在浅色/深色主题上都立得住

输出：
  dist/icons/icons.png      272×16（res/icons.png 的格式）
  dist/icons/icons.h        直接替换 src/res/icons.h
  dist/icons/preview.png    8× 放大对照图（带编号）
  dist/icons/icons-sheet.png 单个图标 8× 拼版
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image, ImageDraw, ImageChops, ImageFilter  # noqa: E402
import fwassets  # noqa: E402

W = H = 16
OUT = os.path.join(ROOT, "dist", "icons")

# ---------------------------------------------------------------- 调色板
# 名称 -> RGB。23 色，足够 17 个图标共用而不"糊成一片"。
PAL = {
    "K": (20, 17, 15),      # 轮廓
    "W": (244, 242, 232),   # 白
    "L": (201, 198, 184),   # 浅灰
    "M": (139, 135, 120),   # 中灰
    "D": (86, 82, 71),      # 深灰
    "X": (53, 50, 43),      # 更深的灰
    "Y": (240, 194, 76),    # 黄（文件夹）
    "Q": (168, 116, 30),    # 暗黄
    "R": (217, 69, 59),     # 红
    "E": (140, 35, 29),     # 暗红
    "G": (79, 191, 106),    # 绿
    "F": (42, 125, 69),     # 暗绿
    "B": (76, 143, 217),    # 蓝
    "U": (37, 81, 140),     # 暗蓝
    "C": (79, 201, 201),    # 青
    "P": (160, 92, 208),    # 紫
    "V": (95, 46, 134),     # 暗紫
    "O": (232, 137, 47),    # 橙
    "T": (47, 143, 138),    # 蓝绿
    "N": (139, 90, 43),     # 棕
}
ORDER = list(PAL.keys())


def _c(ch):
    if isinstance(ch, tuple):
        return ch
    return PAL[ch] + (255,)


# ---------------------------------------------------------------- 工具
def r(d, x0, y0, x1, y1, ch):
    d.rectangle([x0, y0, x1, y1], fill=_c(ch))


def poly(d, pts, ch):
    d.polygon(pts, fill=_c(ch))


def ell(d, x0, y0, x1, y1, ch):
    d.ellipse([x0, y0, x1, y1], fill=_c(ch))


def ring(d, x0, y0, x1, y1, ch, w=1):
    for i in range(w):
        d.ellipse([x0 + i, y0 + i, x1 - i, y1 - i], outline=_c(ch))


def line(d, x0, y0, x1, y1, ch, w=1):
    d.line([x0, y0, x1, y1], fill=_c(ch), width=w)


def outline(im, color=(20, 17, 15, 255)):
    """给不透明区域加 1px 外轮廓（3×3 膨胀 - 原图）。"""
    a = im.getchannel("A").point(lambda v: 255 if v > 0 else 0)
    grown = a.filter(ImageFilter.MaxFilter(3))
    ringm = ImageChops.subtract(grown, a)
    im.paste(color, (0, 0), ringm)
    return im


# ---------------------------------------------------------------- 17 个图标
def i_folder(d):
    r(d, 2, 3, 7, 5, "Y")          # 标签
    r(d, 1, 5, 14, 13, "Y")        # 主体
    r(d, 1, 12, 14, 13, "Q")       # 底部压暗
    r(d, 2, 6, 6, 6, "W")          # 高光


def i_binfile(d):
    r(d, 4, 2, 12, 14, "L")        # 页面
    poly(d, [(9, 2), (12, 2), (12, 5), (9, 5)], "W")   # 折角
    r(d, 5, 7, 7, 7, "D")          # 0/1 内容
    r(d, 9, 7, 10, 7, "D")
    r(d, 5, 9, 6, 9, "D")
    r(d, 8, 9, 10, 9, "D")
    r(d, 5, 11, 9, 11, "D")


def i_updfile(d):
    r(d, 4, 2, 12, 14, "L")
    poly(d, [(9, 2), (12, 2), (12, 5), (9, 5)], "W")
    r(d, 7, 8, 8, 11, "G")         # 箭头杆（2px，别和头部粘成十字）
    poly(d, [(4, 8), (11, 8), (8, 4)], "G")            # 箭头头
    r(d, 7, 9, 8, 11, "F")         # 杆身压暗，读起来有层次


def i_gbcart(d):
    r(d, 4, 2, 11, 14, "M")        # 卡身
    r(d, 4, 2, 5, 4, "M")
    r(d, 5, 4, 10, 9, "L")         # 标签
    r(d, 6, 6, 9, 7, "D")
    r(d, 4, 12, 11, 13, "D")       # 金手指
    line(d, 6, 12, 6, 13, "M")
    line(d, 9, 12, 9, 13, "M")


def i_gbccart(d):
    poly(d, [(5, 2), (10, 2), (11, 4), (11, 14), (4, 14), (4, 4)], "T")
    r(d, 5, 5, 10, 9, "C")         # 标签
    r(d, 6, 6, 9, 7, "T")
    r(d, 4, 12, 11, 13, "D")


def i_gbacart(d):
    r(d, 2, 4, 13, 12, "P")        # 横向卡身
    r(d, 3, 6, 9, 10, "V")         # 标签
    r(d, 4, 7, 8, 7, "P")
    r(d, 11, 6, 12, 9, "V")        # 侧槽


def i_smscart(d):
    r(d, 3, 3, 12, 13, "R")
    r(d, 4, 5, 11, 8, "W")         # 白标
    r(d, 5, 6, 10, 7, "E")
    r(d, 5, 10, 10, 11, "E")
    r(d, 3, 12, 12, 13, "E")


def i_nescart(d):
    r(d, 2, 4, 13, 13, "D")
    r(d, 3, 2, 12, 4, "D")         # 上沿
    r(d, 4, 6, 11, 10, "L")        # 标签
    r(d, 5, 7, 10, 8, "X")
    r(d, 2, 12, 13, 13, "X")
    r(d, 2, 4, 2, 11, "M")         # 高光边
    r(d, 13, 4, 13, 11, "X")


def i_recent(d):
    ring(d, 2, 2, 13, 13, "G", 2)   # 环形箭头
    poly(d, [(9, 0), (14, 0), (14, 5)], "G")   # 右上角箭头（顺时针）
    r(d, 7, 4, 7, 8, "W")          # 指针：竖
    r(d, 8, 8, 10, 8, "W")         # 指针：横


def i_disk(d):
    r(d, 2, 3, 13, 14, "B")
    r(d, 4, 3, 10, 7, "L")         # 滑盖
    r(d, 6, 4, 8, 7, "M")
    r(d, 4, 9, 11, 13, "W")        # 标签
    r(d, 5, 10, 10, 10, "B")
    r(d, 5, 12, 8, 12, "L")


def i_flash(d):
    r(d, 4, 3, 11, 12, "D")        # 芯片体
    r(d, 5, 4, 10, 11, "M")
    r(d, 6, 5, 9, 5, "X")
    for y in (4, 7, 10):           # 引脚
        r(d, 2, y, 3, y, "L")
        r(d, 12, y, 13, y, "L")
    r(d, 5, 9, 5, 9, "L")


def i_settings(d):
    ell(d, 2, 2, 13, 13, "M")      # 齿轮主体
    for x0, y0, x1, y1 in ((7, 0, 8, 2), (7, 13, 8, 15),
                           (0, 7, 2, 8), (13, 7, 15, 8),
                           (3, 3, 4, 4), (11, 3, 12, 4),
                           (3, 11, 4, 12), (11, 11, 12, 12)):
        r(d, x0, y0, x1, y1, "M")  # 八颗小齿
    ell(d, 6, 6, 9, 9, "X")        # 轴孔
    r(d, 7, 7, 8, 8, "K")
    r(d, 4, 4, 5, 4, "L")          # 高光


def i_uilang(d):
    r(d, 2, 4, 13, 12, "W")        # 卡片
    r(d, 2, 4, 13, 5, "L")
    # 左：A
    line(d, 4, 10, 5, 7, "B")
    line(d, 5, 7, 6, 10, "B")
    r(d, 4, 9, 6, 9, "B")
    # 右：文（三横 + 交叉）
    r(d, 9, 7, 12, 7, "O")
    r(d, 9, 9, 12, 9, "O")
    r(d, 9, 11, 12, 11, "O")
    line(d, 10, 7, 10, 11, "O")
    line(d, 11, 8, 12, 10, "O")


def i_tools(d):
    # 开口扳手：窄头 + 深缺口 + 细长柄；缺口是识别关键，宽度必须 >=4px，
    # 否则 1px 描边会把口子糊死，整块读成 "Y"。
    r(d, 4, 1, 11, 7, "L")                                    # 头部（8 宽）
    r(d, 6, 1, 9, 5, (0, 0, 0, 0))                            # 爪口（4 宽 5 深）
    r(d, 4, 6, 11, 7, "M")                                    # 颈部压暗
    r(d, 4, 1, 5, 5, "M")                                     # 左爪压暗
    r(d, 7, 8, 8, 12, "L")                                    # 手柄
    r(d, 8, 8, 8, 12, "M")                                    # 手柄右缘压暗
    r(d, 6, 13, 9, 14, "L")                                   # 柄尾加粗
    r(d, 5, 2, 5, 4, "W")                                     # 高光


def i_info(d):
    ell(d, 2, 2, 13, 13, "B")
    r(d, 7, 5, 8, 5, "W")          # i 的点
    r(d, 7, 7, 8, 10, "W")         # i 的杆
    r(d, 6, 10, 9, 10, "W")


def i_hfolder(d):
    r(d, 2, 3, 7, 5, "M")          # 标签（灰：表示隐藏）
    r(d, 1, 5, 14, 13, "M")        # 主体
    r(d, 1, 12, 14, 13, "D")
    line(d, 9, 10, 14, 4, "E", 2)  # 只划过右下角，不盖住标签


def i_hfile(d):
    i_binfile(d)
    line(d, 3, 13, 13, 3, "E", 2)


ICONS = [
    ("folder", "文件夹", i_folder),
    ("binfile", "二进制文件", i_binfile),
    ("updfile", "升级文件", i_updfile),
    ("gbcart", "GB 卡带", i_gbcart),
    ("gbccart", "GBC 卡带", i_gbccart),
    ("gbacart", "GBA 卡带", i_gbacart),
    ("smscart", "SMS 卡带", i_smscart),
    ("nescart", "NES 卡带", i_nescart),
    ("recent", "最近游玩", i_recent),
    ("disk", "磁盘 / SD", i_disk),
    ("flash", "NOR 闪存", i_flash),
    ("settings", "设置", i_settings),
    ("uilang", "界面 / 语言", i_uilang),
    ("tools", "工具", i_tools),
    ("info", "关于", i_info),
    ("hfolder", "隐藏文件夹", i_hfolder),
    ("hfile", "隐藏文件", i_hfile),
]


# ---------------------------------------------------------------- 量化 / 导出
def build():
    os.makedirs(OUT, exist_ok=True)
    sheet = Image.new("RGBA", (W * len(ICONS), H), (0, 0, 0, 0))
    tiles = []
    for i, (name, zh, fn) in enumerate(ICONS):
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        fn(d)
        im = outline(im)
        sheet.alpha_composite(im, (i * W, 0))
        tiles.append(im)

    # 调色板：只用实际出现的颜色，保持确定性顺序
    used = set()
    for im in tiles:
        for px in im.getdata():
            if px[3] > 128:
                used.add(px[:3])
    colors = [PAL[k] for k in ORDER if PAL[k] in used]
    extra = [c for c in sorted(used) if c not in colors]
    colors += extra
    assert len(colors) <= 37, "颜色 %d 超过 37" % len(colors)

    def nearest(c):
        best, bi = 1 << 30, 0
        for i, p in enumerate(colors):
            dd = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2
            if dd < best:
                best, bi = dd, i
        return bi

    data = bytearray()
    idx_maps = []
    for im in tiles:
        px = im.load()
        idx = [[0] * W for _ in range(H)]
        for y in range(H):
            for x in range(W):
                r_, g_, b_, a_ = px[x, y]
                if a_ <= 128:
                    idx[y][x] = 0
                else:
                    idx[y][x] = nearest((r_, g_, b_)) + 1
        idx_maps.append(idx)
        for sy in range(2):            # 固件的 tile 顺序
            for sx in range(2):
                for r0 in range(8):
                    data += bytes(idx[sy * 8 + r0][sx * 8:sx * 8 + 8])
    assert len(data) == 4352, len(data)

    # icons.png：与仓库里的 res/icons.png 同样是 RGBA 272×16
    sheet.save(os.path.join(OUT, "icons.png"))

    # preview：8× 放大 + 编号
    scale = 8
    pad = 6
    cell = W * scale + pad * 2
    prev = Image.new("RGB", (cell * 6, (cell + 18) * 3), (19, 19, 17))
    dp = ImageDraw.Draw(prev)
    for i, im in enumerate(tiles):
        big = im.resize((W * scale, H * scale), Image.NEAREST)
        bg = Image.new("RGB", big.size, (31, 31, 27))
        bg.paste(big, (0, 0), big)
        x = (i % 6) * cell + pad
        y = (i // 6) * (cell + 18) + pad
        prev.paste(bg, (x, y))
        dp.text((x, y + H * scale + 3), "%02d %s" % (i, ICONS[i][1]), fill=(200, 196, 184))
    prev.save(os.path.join(OUT, "preview.png"))

    # 单图标 8×
    sheet.resize((W * scale * len(ICONS), H * scale), Image.NEAREST).save(
        os.path.join(OUT, "icons-sheet.png"))

    # icons.h
    with open(os.path.join(OUT, "icons.h"), "w", encoding="utf-8", newline="\n") as f:
        f.write("/* 由 tools/redraw_icons.py 生成 —— 整份替换 src/res/icons.h */\n")
        f.write("enum {\n")
        for i, (name, zh, _) in enumerate(ICONS):
            if name == "flash":
                f.write("#ifdef SUPPORT_NORGAMES\n ICON_FLASH,\n#endif\n")
            else:
                f.write(" ICON_%s,\n" % name.upper())
        f.write("};\n")
        f.write("const uint8_t icons_img[][4][8][8] = {\n")
        p = 0
        for i, (name, zh, _) in enumerate(ICONS):
            if name == "flash":
                f.write("#ifdef SUPPORT_NORGAMES\n")
            f.write("  {\n")
            for t in range(4):
                f.write("    {\n")
                for row in range(8):
                    f.write("     {" + ",".join("0x%02x" % v for v in data[p:p + 8]) + "},\n")
                    p += 8
                f.write("    },\n")
            f.write("  },\n")
            if name == "flash":
                f.write("#endif\n")
        f.write("};\n\n")
        f.write("const uint16_t icons_pal[%d] = {\n" % (len(colors) + 1))
        def g(c):
            return ((c[0] >> 3) << 0) | ((c[1] >> 3) << 5) | ((c[2] >> 3) << 10)
        f.write("  0x0000," + ",".join("0x%04x" % g(c) for c in colors) + "\n};\n")

    return data, colors, tiles


def selftest(data, colors):
    """按固件的方式解码回来，逐像素比对。"""
    pal = [0] + colors
    bad = 0
    for i, im in enumerate(build_tiles()):
        px = im.load()
        for y in range(16):
            for x in range(16):
                t = (y // 8) * 2 + (x // 8)
                v = data[i * 256 + t * 64 + (y % 8) * 8 + (x % 8)]
                r_, g_, b_, a_ = px[x, y]
                if a_ <= 128:
                    if v != 0:
                        bad += 1
                elif v == 0:
                    bad += 1
                else:
                    c = pal[v]
                    if max(abs(c[0] - r_), abs(c[1] - g_), abs(c[2] - b_)) > 9:
                        bad += 1
    return bad


def build_tiles():
    out = []
    for name, zh, fn in ICONS:
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        fn(d)
        out.append(outline(im))
    return out


if __name__ == "__main__":
    data, colors, tiles = build()
    bad = selftest(data, colors)
    print("图标 %d 个 · 调色板 %d 色（+透明） · 数据 %d 字节" % (len(ICONS), len(colors), len(data)))
    print("解码回比对：偏差 %d 像素" % bad)
    print("输出目录:", OUT)
