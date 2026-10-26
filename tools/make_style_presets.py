#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「风格提案」：6 套配色 + 壁纸，打包成一个 6 套的 theme.bin，并出预览图。

为什么是 6 套一包：theme.bin 本来就装 6 套，固件在「界面设置 → 主题」里直接切。
所以一包六种风格 = 拷一次卡就能在真机上挨个比，不用反复换文件。

预览图不是示意图，走的是 app/sdedit.py 的 render_preview()——它用固件自己的
字库和布局常量画，和真机同一套坐标。

配色不是随手挑的：`--audit` 会算 WCAG 对比度，四条底线
    正文/内容底 ≥ 7      正文/选中条 ≥ 4.5
    选中条/内容底 ≥ 2.15 顶栏暗字/顶栏 ≥ 3.5
GBA 屏本来就偏暗偏灰，低于这个数在真机上会糊成一片。
（选中条那条从 1.6 提到 2.15：1.6 是「能分辨」的下限，但真机上表现为
「一选中那行反而糊」，因为它同时压低了正文/选中条。2.15 才是能看清字的下限。）

用法：
    .venv314/Scripts/python.exe tools/make_style_presets.py
    .venv314/Scripts/python.exe tools/make_style_presets.py --audit
    .venv314/Scripts/python.exe tools/make_style_presets.py -o out/styles
"""

import argparse
import json
import os
import sys

from PIL import Image, ImageEnhance, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import sdedit                 # noqa: E402
import sdtheme                # noqa: E402


# --------------------------------------------------------------------- 配色
# 六色槽位顺序（必须和 themes[] 一致）：
#   fg 顶栏底 / bg 内容底 / ft 正文 / hi 高亮 / hi_blend 选中条 / sh 顶栏暗字
#
# 三条定色经验：
#   1. 选中条不能太接近内容底 —— 真机上 1.2:1 等于看不见；1.6 只是「能看出有条」，
#      要做到 2.15 才保证选中行里的字仍然清楚（这一版全部按 2.15 调过）
#   2. 顶栏是深色时，sh（右上角 1/12、次要文字）必须比 fg 亮够，否则读不出
#   3. 正文色别用纯黑纯白，RGB555 会把 0 和 31 端点压得很硬
STYLES = [
    dict(
        key="paper",
        name="米白纸感",
        note="亮底暗字，非背光 AGB-001 上最保险 —— 靠环境光就够看",
        pal=["#4A4640", "#F5F1E6", "#2B2822", "#B5651D", "#BBA372", "#BDB6A6"],
        excol=["#EDE7D8", "#B5651D", "#4A4640"],
        wall="paper",
        icon="keep",
    ),
    dict(
        key="bios",
        name="深蓝终端",
        note="从 GBA 自带界面的调子来，白字压深蓝，背光机上最稳",
        pal=["#101830", "#1B2A4A", "#F2F6FF", "#FFB020", "#3C5C9A", "#6B7FA8"],
        excol=["#0C1428", "#FFB020", "#101830"],
        wall="bios",
        icon="keep",
    ),
    dict(
        key="dmg",
        name="掌机绿",
        note="GB 那四阶绿做底，图标留原色（像 GBC），复古又好认",
        pal=["#0F380F", "#9BBC0F", "#0F380F", "#306230", "#FDFFEA", "#C4D46B"],
        excol=["#8BAC0F", "#DDEB9E", "#0F380F"],
        wall="dmg",
        icon="keep",        # 想全绿就 --mono-icons 3，见 recolor_ramp()
        # 这四阶绿是 GB 原色，正文对底色只有 6.02。提亮底子能到 7，但选中条
        # 就掉到 1.5 以下（更亏）。8px 点阵字 6.0 仍然清楚，所以放行。
        exempt=("body",),
    ),
    dict(
        key="night",
        name="暗夜霓虹",
        note="暗底亮字配青色强调，IPS 改造屏上最舒服，黑位压得下去",
        pal=["#14161C", "#1E2128", "#E6E9EF", "#35D0C0", "#435863", "#8A94A4"],
        excol=["#0E1014", "#35D0C0", "#14161C"],
        wall="night",
        icon="keep",
    ),
    dict(
        key="slate",
        name="灰蓝极简",
        note="低饱和、只有一个蓝强调色，长时间用眼睛不累，最不容易看腻",
        pal=["#3B4652", "#DDE3E8", "#232A31", "#2D7DD2", "#869DB3", "#9FAEBB"],
        excol=["#CFD8DF", "#2D7DD2", "#3B4652"],
        wall="slate",
        icon="keep",
    ),
    dict(
        key="sunset",
        name="暖橙日落",
        note="深紫压到暖橙，暖色调里对比最好的一档，夜里不刺眼",
        pal=["#3A2233", "#241726", "#FFE9D6", "#FF7A45", "#6F4361", "#B08A9C"],
        excol=["#241726", "#FF7A45", "#3A2233"],
        wall="sunset",
        icon="keep",
    ),
]

# 单色化图标用的色阶（GB 四阶 + 一档高光）。--mono-icons 时才会用到。
DMG_RAMP = ["#0F380F", "#306230", "#8BAC0F", "#9BBC0F", "#E3EFAE"]

# 对比度底线（看 --audit 的输出）。sel_vs_bg 从 1.6 提到 2.15：
# 1.6 只保证「看得出选中条在哪」，选中的那一行字仍然发糊 —— 真机反馈过。
LIMITS = dict(body=7.0, body_on_sel=4.5, sel_vs_bg=2.15, dim_on_bar=3.5)


def hx(s):
    v = s.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luma(c):
    r, g, b = (_lin(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luma(a), luma(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------- 壁纸
def _grad(w, h, stops):
    """按 y 分段渐变。stops = [(位置0..1, 颜色), ...]，位置递增。"""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = stops[-1][1]
        for i in range(len(stops) - 1):
            a, b = stops[i], stops[i + 1]
            if a[0] <= t <= b[0]:
                k = 0.0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
                c = lerp(a[1], b[1], k)
                break
        for x in range(w):
            px[x, y] = c
    return im


def wall_paper(w, h):
    """米白纸纹：横线 + 左侧竖线，像一张稿纸。很淡，不抢正文。"""
    base, line, margin = hx("#F5F1E6"), hx("#EDE7D8"), hx("#E3DCC9")
    im = Image.new("RGB", (w, h), base)
    px = im.load()
    for y in range(7, h, 8):
        for x in range(w):
            px[x, y] = line
    for y in range(h):
        px[18, y] = margin
    return im


def wall_bios(w, h):
    """深蓝终端：上略亮下更暗，加一层 16px 点阵。"""
    im = _grad(w, h, [(0.0, hx("#1B2C4E")), (0.55, hx("#152340")), (1.0, hx("#0C1428"))])
    px = im.load()
    dot = hx("#24365C")
    for y in range(8, h, 16):
        for x in range(8, w, 16):
            px[x, y] = dot
    return im


def wall_dmg(w, h):
    """DMG 绿：底色 + 45° 细点纹，只用两种绿，量化后不会出噪点。"""
    im = Image.new("RGB", (w, h), hx("#9BBC0F"))
    px = im.load()
    d = hx("#8BAC0F")
    for y in range(h):
        for x in range(w):
            if (x + y) % 4 == 0:
                px[x, y] = d
    return im


def wall_night(w, h):
    """暗夜：极暗底 + 右下角一点冷光，左上留空给列表文字。"""
    im = _grad(w, h, [(0.0, hx("#1E2128")), (1.0, hx("#191C22"))])
    px = im.load()
    cx, cy = w * 0.78, h * 0.92
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d < 90:
                px[x, y] = lerp(px[x, y], hx("#2F6F72"), (1 - d / 90) ** 2 * 0.55)
    for sx, sy in [(196, 22), (214, 41), (176, 12), (228, 63), (204, 88), (166, 34), (232, 18)]:
        if sx < w and sy < h:
            px[sx, sy] = hx("#3B4550")
    return im


def wall_slate(w, h):
    """灰蓝极简：一层很轻的竖向渐变 + 细点阵。刻意压到几乎看不出花纹。"""
    im = _grad(w, h, [(0.0, hx("#E9EDF0")), (1.0, hx("#D2DBE2"))])
    px = im.load()
    dot = hx("#DCE3E9")
    for y in range(6, h, 12):
        for x in range(6, w, 12):
            px[x, y] = dot
            px[x + 1, y] = dot
    return im


def wall_sunset(w, h):
    """暖橙日落：紫 → 橙竖向渐变，靠底部压一条更亮的带当太阳。"""
    im = _grad(w, h, [
        (0.00, hx("#241726")), (0.38, hx("#4A2335")),
        (0.68, hx("#8A3A34")), (0.86, hx("#C4623F")), (1.00, hx("#E08A4E")),
    ])
    px = im.load()
    for y in range(int(h * 0.80), h):
        k = max(0.0, 1 - abs(y - h * 0.90) / (h * 0.10))
        for x in range(w):
            px[x, y] = lerp(px[x, y], hx("#F2A65A"), k * 0.45)
    return im


WALLS = dict(paper=wall_paper, bios=wall_bios, dmg=wall_dmg,
             night=wall_night, slate=wall_slate, sunset=wall_sunset)


# ------------------------------------------------------------------- 图标
def recolor_ramp(im, ramp):
    """把一张图按亮度压到一条色阶上（保留 alpha）。GB 游戏就是这么做的。

    注意：只按亮度映射，所以深色描边会一起变深 —— 顶栏是深色时图标会发闷，
    这也是默认不用的原因（`--mono-icons` 才开）。
    """
    cols = [hx(c) for c in ramp]
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    src, dst = im.load(), out.load()
    n = len(cols)
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = src[x, y]
            if a == 0:
                continue
            i = min(n - 1, ((r * 76 + g * 150 + b * 29) >> 8) * n // 256)
            dst[x, y] = cols[i] + (255,)
    return out


# ------------------------------------------------------------------- 合成
def build_doc(mono=()):
    doc = sdedit.Doc()
    for i, st in enumerate(STYLES):
        doc.pals[i] = [sdtheme.rgb555(sdtheme.color555(c)) for c in st["pal"]]
        doc.excol[i] = [sdtheme.rgb555(sdtheme.color555(c)) for c in st["excol"]]
        im = WALLS[st["wall"]](sdtheme.WALL_W, sdtheme.WALL_H)
        # 先压到 200 色以内：这样固件那边的 FS 抖动没有余量可抖，成品不出噪点
        im = im.quantize(colors=200, method=Image.MEDIANCUT).convert("RGB")
        doc.wall_rgb[i] = im
        doc.wall_idx[i] = None
        doc.wall_pal[i] = None
        doc.wall_dirty[i] = True
        if st["icon"] == "dmg" or (i + 1) in mono:
            doc.icon_imgs[i] = [recolor_ramp(x, DMG_RAMP) for x in sdedit.default_icons()]
            doc.icon_dirty[i] = True
    return doc


def lcd_sim(im, strength=1.0):
    """粗略模拟非背光 AGB-001：对比度塌、颜色褪、整体偏暖灰。"""
    out = ImageEnhance.Brightness(im).enhance(1 - 0.10 * strength)
    out = ImageEnhance.Contrast(out).enhance(1 - 0.26 * strength)
    out = ImageEnhance.Color(out).enhance(1 - 0.34 * strength)
    return Image.blend(out, Image.new("RGB", im.size, (112, 108, 96)), 0.10 * strength)


def upscale(im, k):
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def _font(size=13):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def sheet(panels, cols, k, labels, title=None, pad=8):
    """拼图。字号用系统字体 —— 固件点阵字库只收了常用字，写风格名会缺字。"""
    pw, ph = panels[0].width * k, panels[0].height * k
    f = _font(13)
    lab_h = 18 if labels else 0
    title_h = 26 if title else 0
    rows = (len(panels) + cols - 1) // cols
    Wt = cols * pw + (cols + 1) * pad
    Ht = title_h + rows * (ph + lab_h) + (rows + 1) * pad
    out = Image.new("RGB", (Wt, Ht), (24, 24, 26))
    if title:
        from PIL import ImageDraw
        ImageDraw.Draw(out).text((pad + 2, 5), title, font=_font(15), fill=(228, 228, 232))
    for i, p in enumerate(panels):
        r, c = divmod(i, cols)
        x = pad + c * (pw + pad)
        y = title_h + pad + r * (ph + lab_h + pad)
        out.paste(upscale(p, k), (x, y))
        if labels:
            from PIL import ImageDraw
            ImageDraw.Draw(out).text((x + 1, y + ph + 2), labels[i], font=f, fill=(206, 206, 212))
    return out


def audit():
    print("%-10s %8s %8s %8s %8s" % ("套", "正文/底", "正文/条", "条/底", "暗字/栏"))
    bad = 0
    for st in STYLES:
        fg, bg, ft, hi, hib, sh = [hx(c) for c in st["pal"]]
        row = (contrast(ft, bg), contrast(ft, hib), contrast(hib, bg), contrast(sh, fg))
        keys = ("body", "body_on_sel", "sel_vs_bg", "dim_on_bar")
        flags = [k for k, v in zip(keys, row) if v < LIMITS[k] and k not in st.get("exempt", ())]
        print("%-10s %8.2f %8.2f %8.2f %8.2f  %s"
              % (st["name"], *row,
                 "OK" if not flags else "低于 " + ",".join(flags)))
        bad += bool(flags)
    print("底线 %s" % LIMITS)
    return bad


def main():
    ap = argparse.ArgumentParser(description="生成 6 套风格提案 + 预览图")
    ap.add_argument("-o", "--out", default=os.path.join("dist", "主题风格提案"))
    ap.add_argument("--audit", action="store_true", help="只算对比度，不出图")
    ap.add_argument("--mono-icons", type=int, nargs="*", default=[],
                    help="把指定第几套（1..6）的图标压成 GB 绿阶，默认不压")
    ap.add_argument("--compare", metavar="THEME.BIN",
                    help="再把另一份 theme.bin 的六套也渲成对照图（用来比现有主题）")
    a = ap.parse_args()

    if a.audit:
        return 1 if audit() else 0

    audit()
    os.makedirs(a.out, exist_ok=True)
    pv = os.path.join(a.out, "预览")
    os.makedirs(pv, exist_ok=True)

    doc = build_doc(mono=set(a.mono_icons))

    panels, panels_load, labels = [], [], []
    for i, st in enumerate(STYLES):
        main_im = sdedit.render_preview(doc, i)                   # 文件列表页
        load_im = sdedit.render_preview(doc, i, extra="loading")  # 载入进度画面
        panels.append(main_im)
        panels_load.append(load_im)
        labels.append("%d  %s" % (i + 1, st["name"]))
        main_im.save(os.path.join(pv, "%02d_%s.png" % (i + 1, st["key"])))
        upscale(main_im, 3).save(os.path.join(pv, "%02d_%s@3x.png" % (i + 1, st["key"])))

    sheet(panels, 2, 2, labels, "六套风格 · 文件列表页（2x）").save(
        os.path.join(pv, "对比图-6套.png"))
    sheet(panels_load, 2, 2, labels, "六套风格 · 载入进度画面（2x）").save(
        os.path.join(pv, "对比图-6套-载入画面.png"))
    sheet([lcd_sim(p) for p in panels], 2, 2, labels,
          "模拟非背光 AGB-001：对比度与饱和度的损失").save(
        os.path.join(pv, "对比图-模拟非背光.png"))

    data = doc.to_bytes()
    with open(os.path.join(a.out, "theme.bin"), "wb") as fh:
        fh.write(data)

    if a.compare:
        cmp_doc = sdedit.Doc.load(a.compare)
        cps = [sdedit.render_preview(cmp_doc, i) for i in range(sdtheme.THEME_COUNT)]
        cl = ["%d  现有第 %d 套" % (i + 1, i + 1) for i in range(sdtheme.THEME_COUNT)]
        sheet(cps, 2, 2, cl, "对照：%s 里的六套" % os.path.basename(a.compare)).save(
            os.path.join(pv, "对照-现有六套.png"))
        # 同一位置并排：现有第 1 套 vs 提案第 1 套，方便看差异
        sheet([cps[0], panels[0]], 2, 3, ["现有第 1 套", "提案第 1 套 米白纸感"],
              "同一屏位置的两版对比（3x）").save(
            os.path.join(pv, "对照-并排.png"))
        print("对照图已出：对照-现有六套.png / 对照-并排.png")

    meta = dict(file="theme.bin", size=len(data), version=data[8],
                styles=[dict(i=i + 1, key=st["key"], name=st["name"], note=st["note"],
                             pal=st["pal"], excol=st["excol"], wall=st["wall"],
                             icon=st["icon"]) for i, st in enumerate(STYLES)])
    with open(os.path.join(a.out, "styles.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print("\n输出目录 %s" % os.path.abspath(a.out))
    print("theme.bin  %d B  格式 v%d  6 套" % (len(data), data[8]))
    for i, st in enumerate(STYLES):
        print("  %d  %-8s %s" % (i + 1, st["name"], "  ".join(st["pal"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
