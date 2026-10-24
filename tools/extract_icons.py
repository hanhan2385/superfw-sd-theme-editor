# -*- coding: utf-8 -*-
"""把固件内置的 17 个菜单图标提取成图片素材。

数据源是 app/fwassets.py 里的 ICONS_IMG（4352 字节精灵）+ ICONS_PAL（38 项
RGB555），也就是 fw/src/res/icons.h 那份资源，**不是**从截图里量出来的 ——
截图是 16×16 放大后的渲染结果，量出来的像素既不准也不完整（透明区、调色板
网格都会丢），所以只用来做颜色核对。

索引规则和编辑器一致：像素值 v → pal[v]，槽 0 恒透明。别写成 pal[v-1]，
那会让整套图标颜色错一位（黄文件夹变橙红、金边变灰）。

用法：
    .venv314/Scripts/python.exe tools/extract_icons.py
    .venv314/Scripts/python.exe tools/extract_icons.py -o out/icons
    .venv314/Scripts/python.exe tools/extract_icons.py --check 某截图.png

输出目录结构（默认 dist/图标素材）：
    说明.txt                规格与用法
    预览-5列.png            4 倍深色底格子图，一眼看全
    整排精灵图.png / @8x    17 张横排成一条
    16x16/                  17 张原图，改图就改这里的
    128x128/                同图放大 8 倍，只用来看
    原始数据/               icons.raw / icons.pal / icons.json
"""
import argparse
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import fwassets            # noqa: E402
import sdtheme             # noqa: E402

# 17 个图标的用途（顺序 = 固件 enum ICON_*，超出一律按序号命名）
NAMES_CN = {
    "FOLDER": "文件夹", "BINFILE": "普通文件", "UPDFILE": "升级文件",
    "GBCART": "GB 卡带", "GBCCART": "GBC 卡带", "GBACART": "GBA 卡带",
    "SMSCART": "SMS 卡带", "NESCART": "NES 卡带", "RECENT": "最近使用",
    "DISK": "卡带浏览", "FLASH": "NOR 闪存", "SETTINGS": "设置",
    "UILANG_SETTINGS": "界面语言", "TOOLS": "工具", "INFO": "关于",
    "HFOLDER": "隐藏文件夹", "HFILE": "隐藏文件",
}

TILE = 16
COLS = 5        # 效果图的列数，和编辑器里那一排一致

# 输出目录下的三个子目录（名字定了就别改，说明.txt 和回灌自检都按这个找）
SUB_SET = "内置原始"     # 这一套（固件自带那份）在图标素材根目录下的文件夹
SUB_ORIG = "16x16"      # 原图
SUB_BIG = "128x128"     # 8 倍放大
SUB_RAW = "原始数据"     # 精灵块 / 调色板 / 索引 json


def decode():
    """→ (17 张 RGBA, 调色板 RGB 列表)"""
    sprite = bytes(fwassets.ICONS_IMG)
    pal = list(fwassets.ICONS_PAL)
    rgb = [sdtheme.rgb555(v) for v in pal]
    imgs = []
    for i in range(17):
        im = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
        px = im.load()
        base = i * 256
        for t in range(4):
            ox = (t & 1) * 8
            oy = (t >> 1) * 8
            for y in range(8):
                row = base + t * 64 + y * 8
                for x in range(8):
                    v = sprite[row + x]
                    if v == 0:
                        continue
                    c = rgb[v] if v < len(rgb) else (255, 0, 255)
                    px[ox + x, oy + y] = (c[0], c[1], c[2], 255)
        imgs.append(im)
    return imgs, rgb


def upscale(im, k):
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def make_grid(imgs, scale=4, bg=(19, 19, 17), gap=8, pad=8):
    """按 COLS 列排一张深色底效果图（和编辑器预览的观感一致）。"""
    rows = (len(imgs) + COLS - 1) // COLS
    cell = TILE * scale
    w = pad * 2 + COLS * cell + (COLS - 1) * gap
    h = pad * 2 + rows * cell + (rows - 1) * gap
    out = Image.new("RGB", (w, h), bg)
    for i, im in enumerate(imgs):
        cx = pad + (i % COLS) * (cell + gap)
        cy = pad + (i // COLS) * (cell + gap)
        big = upscale(im, scale)
        # 用图标自身 alpha 当掩码，透明区就是背景色
        out.paste(big, (cx, cy), big)
    return out


def make_sheet(imgs, scale=1, cols=None):
    cols = cols or len(imgs)
    rows = (len(imgs) + cols - 1) // cols
    cell = TILE * scale
    sheet = Image.new("RGBA", (cols * cell, rows * cell), (0, 0, 0, 0))
    for i, im in enumerate(imgs):
        sheet.alpha_composite(upscale(im, scale), ((i % cols) * cell, (i // cols) * cell))
    return sheet


def _bg_filter(shot):
    """算出「背景判定」函数：出现面积 ≥5% 的颜色算底色（棋盘底会有两三档）。"""
    from collections import Counter
    total = shot.width * shot.height
    cnt = Counter(shot.getdata())
    bg = [c for c, n in cnt.items() if n >= total * 0.05]

    def is_bg(c, tol=9):
        return any(abs(c[0] - b[0]) <= tol and abs(c[1] - b[1]) <= tol and abs(c[2] - b[2]) <= tol
                   for b in bg)
    return is_bg, bg


def _grid_boxes(shot, is_bg):
    """在截图里找出图标格的列/行区间（每格是一个 16×16 图标放大后的方块）。"""
    w, h = shot.size

    def spans(vals):
        out, s, p = [], vals[0], vals[0]
        for v in vals[1:]:
            if v != p + 1:
                out.append((s, p))
                s = v
            p = v
        out.append((s, p))
        return out

    cols = [x for x in range(w) if any(not is_bg(shot.getpixel((x, y))) for y in range(h))]
    rows = [y for y in range(h) if any(not is_bg(shot.getpixel((x, y))) for x in range(w))]
    return spans(cols), spans(rows)


def check_screenshot(path, imgs, rgb):
    """拿截图核对：逐格取出图标块里的颜色，看是否恰好等于该图标用到的调色板项。

    截图是放大渲染（缩放比不是整数），逐像素比没意义；但「每一格用到的
    调色板索引集合完全一致」这一条只要成立，就证明截的就是这套内置图标。
    """
    shot = Image.open(path).convert("RGB")
    is_bg, bg = _bg_filter(shot)
    cols, rows = _grid_boxes(shot, is_bg)
    print("截图 %s  %dx%d" % (os.path.basename(path), shot.width, shot.height))
    print("  网格 %d 列 x %d 行   底色 %s" % (len(cols), len(rows), sorted(bg)))
    if len(cols) * len(rows) < len(imgs):
        print("  网格数量不够 17 格，无法核对")
        return False

    def nearest(c):
        best, bd = 0, 1 << 30
        for i, m in enumerate(rgb):
            d = (c[0] - m[0]) ** 2 + (c[1] - m[1]) ** 2 + (c[2] - m[2]) ** 2
            if d < bd:
                bd, best = d, i
        return best, bd

    bad = 0
    for i, im in enumerate(imgs):
        cx0, cx1 = cols[i % len(cols)]
        cy0, cy1 = rows[i // len(cols)]
        cell = shot.crop((cx0, cy0, cx1 + 1, cy1 + 1))
        got = set()
        for c in cell.getdata():
            if is_bg(c):
                continue
            idx, d = nearest(c)
            if d <= 30:
                got.add(idx)
        want = {nearest(p[:3])[0] for p in im.getdata() if p[3]}
        ok = got == want
        bad += 0 if ok else 1
        name = fwassets.ICON_ORDER[i] if i < len(fwassets.ICON_ORDER) else "ICON%02d" % i
        print("  %02d %-16s 截图 %2d 色 / 原图 %2d 色  %s"
              % (i, name, len(got), len(want), "OK" if ok else "不一致 多%s 缺%s"
                 % (sorted(got - want), sorted(want - got))))
    print("结论: %s" % ("17 张全部对得上，就是这套内置图标" if bad == 0 else "%d 张对不上" % bad))
    return bad == 0


def verify_roundtrip(outdir):
    """把刚写出的 17 张 PNG 用编辑器的编码规则回灌一遍，看能不能还原出原始 4352 字节。

    这是这套素材可用性的硬证据：PNG 拿回去导进编辑器，出来的精灵块必须和固件里
    那一份逐字节相同，否则说明解码时丢了像素或者索引偏了一位。
    """
    try:
        import sdedit
    except Exception as e:                                  # 没装 tkinter 之类
        print("回灌自检跳过（%s）" % e)
        return None
    imgs = []
    for i in range(17):
        name = fwassets.ICON_ORDER[i] if i < len(fwassets.ICON_ORDER) else "ICON%02d" % i
        imgs.append(Image.open(os.path.join(outdir, SUB_ORIG, "%02d_%s.png" % (i, name))).convert("RGBA"))
    sprite, pal = sdedit.icons_to_raw(imgs)
    orig = bytes(fwassets.ICONS_IMG)
    same = sprite == orig
    print("回灌自检: 精灵块 %s（%d B），调色板 %d 项 %s"
          % ("逐字节一致" if same else "**不一致**", len(sprite), len(pal),
             "一致" if list(pal) == list(fwassets.ICONS_PAL) else "有差异（正常，量化后会重排）"))
    return same


def main():
    ap = argparse.ArgumentParser(description="提取固件内置的 17 个菜单图标")
    ap.add_argument("-o", "--out", default=os.path.join("dist", "图标素材"))
    ap.add_argument("--check", metavar="PNG", help="拿截图核对颜色（不写文件）")
    ap.add_argument("--scale", type=int, default=4, help="效果图放大倍数，默认 4")
    a = ap.parse_args()

    imgs, rgb = decode()

    if a.check:
        ok = check_screenshot(a.check, imgs, rgb)
        return 0 if ok else 1

    setdir = os.path.join(a.out, SUB_SET)
    os.makedirs(setdir, exist_ok=True)
    d_orig = os.path.join(setdir, SUB_ORIG)
    d_big = os.path.join(setdir, SUB_BIG)
    d_raw = os.path.join(setdir, SUB_RAW)
    for d in (d_orig, d_big, d_raw):
        os.makedirs(d, exist_ok=True)

    # 1) 单张 16x16 透明 PNG（16x16/）与 8 倍放大版（128x128/）
    #    两个目录里同名，靠目录区分尺度 —— 不用记 @8x 后缀。
    for i, im in enumerate(imgs):
        name = fwassets.ICON_ORDER[i] if i < len(fwassets.ICON_ORDER) else "ICON%02d" % i
        base = "%02d_%s.png" % (i, name)
        im.save(os.path.join(d_orig, base))
        upscale(im, 8).save(os.path.join(d_big, base))

    # 2) 整排精灵图（17x1，透明）与放大版，放根目录当预览
    make_sheet(imgs).save(os.path.join(setdir, "整排精灵图.png"))
    make_sheet(imgs, 8).save(os.path.join(setdir, "整排精灵图@8x.png"))
    make_grid(imgs, a.scale).save(os.path.join(setdir, "预览-5列.png"))

    # 3) 原始数据，方便直接塞回固件/编辑器
    open(os.path.join(d_raw, "icons.raw"), "wb").write(bytes(fwassets.ICONS_IMG))
    open(os.path.join(d_raw, "icons.pal"), "wb").write(
        b"".join(int(v).to_bytes(2, "little") for v in fwassets.ICONS_PAL))

    # 4) 索引：名字 / 中文 / 调色板 / 每张用了几个色
    meta = {
        "size": [TILE, TILE],
        "count": len(imgs),
        "palette_rgb555": ["0x%04X" % v for v in fwassets.ICONS_PAL],
        "palette_rgb": [list(c) for c in rgb],
        "index_rule": "像素值 v -> pal[v]，槽 0 恒透明",
        "icons": [
            {
                "i": i,
                "name": fwassets.ICON_ORDER[i] if i < len(fwassets.ICON_ORDER) else "ICON%02d" % i,
                "cn": NAMES_CN.get(fwassets.ICON_ORDER[i] if i < len(fwassets.ICON_ORDER) else "", ""),
                "colors": len({p[:3] for p in im.getdata() if p[3]}),
            }
            for i, im in enumerate(imgs)
        ],
    }
    with open(os.path.join(d_raw, "icons.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("输出目录: %s" % os.path.abspath(setdir))
    print("  %s/   17 张 16x16 PNG（改图改这里）" % SUB_ORIG)
    print("  %s/ 同图 8 倍放大，只用来看" % SUB_BIG)
    print("  预览-5列.png / 整排精灵图.png(+@8x)")
    print("  %s/   icons.raw (4352 B) / icons.pal (76 B) / icons.json" % SUB_RAW)
    for it in meta["icons"]:
        print("  %02d %-16s %-8s %2d 色" % (it["i"], it["name"], it["cn"], it["colors"]))

    verify_roundtrip(setdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
