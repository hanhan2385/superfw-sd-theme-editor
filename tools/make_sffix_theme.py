#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 superfw-fix（ref/superfw-fix，r75 chis）做一份 SD 卡主题包。

跟「主题风格提案」那份的区别，全在「适配分支的真实布局」上：

  * 壁纸按分支 menu.c 的行格画，不再是一张抽象渐变：
      y=0..15    顶栏 —— 菜单自己用 dup8(FG_COLOR) 铺实心条 + 每 16px 一个图标
      y=16..159  内容区 —— sdtheme_render_wall() 铺 240x144 的壁纸
                 文件页列表行 i 画在 y=(i+1)*16，所以壁纸里第 i 条横带 = y'=16i
      y=144..159 底部路径行（同一张壁纸的最后 16 行）
    于是壁纸做成 8 条 16px 行带 + 一条 16px 底栏，长列表一眼能顺着走。
  * 图标用彩色两套（tools/make_icon_sets.py 的 C/D），按每套内容底亮度自动配。
  * 带 v5 三色（载入底 / 进度条 / 游戏内菜单底）和 v6 logo（31x7）。
  * 可选 42 张页面图（6 套 × 7 页），每页一个水印级的大图标剪影。

为什么默认不启用页面图：分支的 sdtheme_set_page() 在页面变化时才读卡，
每张页面图 35 KB。日常用 v2（每套一张）切页零读取，最跟手；
想每页都有专属背景再用那份大的。

配色沿用 tools/make_style_presets.py 的六套（已过四项 WCAG 底线）。

用法：
    .venv314/Scripts/python.exe tools/make_sffix_theme.py
    .venv314/Scripts/python.exe tools/make_sffix_theme.py --pages-only
    .venv314/Scripts/python.exe tools/make_sffix_theme.py --audit
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PIL import Image, ImageDraw, ImageFont     # noqa: E402

import fwbake                                    # noqa: E402
import sdedit                                    # noqa: E402
import sdtheme                                   # noqa: E402
import wallpaper as WP                           # noqa: E402
import make_icon_sets as MIG                     # noqa: E402  （彩色图标）
import make_style_presets as MSP                 # noqa: E402  （六套配色）

OUT = os.path.join(ROOT, "dist", "主题包-superfw-fix")

# ---- 分支布局常量（ref/superfw-fix/src/menu.c，别凭记忆改） -----------------
SCREEN_W, SCREEN_H = 240, 160
BAR_H = 16            # 顶栏高度：菜单画的 FG 色条 + 图标行
ROW_H = 16            # 列表行高：draw_text(..., (i+1)*16)
LIST_ROWS = 8         # BROWSER_ROWS
WALL_W, WALL_H = sdtheme.WALL_W, sdtheme.WALL_H        # 240x144 = 内容区
PATH_TOP = WALL_H - BAR_H                              # 128：底栏在壁纸里的 y'

# 页面图的水印图标，顺序 = MENUTAB_*（最近 / 文件 / 烧录 / 设置 / 界面 / 工具 / 关于）
PAGE_ICON = ["RECENT", "FOLDER", "FLASH", "SETTINGS", "UILANG_SETTINGS", "TOOLS", "INFO"]
PAGE_NAME = ["最近游玩", "文件浏览", "NOR 烧录", "设置", "界面语言", "工具", "关于"]

# logo 三个色：深底 / 白字 / 橙饰。和内置 logo 同一路子（深色铭牌）。
LOGO_COLS = [(12, 14, 20), (242, 244, 250), (255, 122, 69)]


# --------------------------------------------------------------------- 壁纸
def wall_for(style):
    """该套的 240x144 内容区壁纸：原配方 + 行带 + 底栏 + 顶线。"""
    fg, bg, ft, hi, hib, sh = [MSP.hx(c) for c in style["pal"]]
    im = MSP.WALLS[style["wall"]](WALL_W, WALL_H).convert("RGB")
    px = im.load()

    # 行带：偶数条往强调色靠 5%，奇数条不动。5% 是试出来的上限 ——
    # 再重就会和「选中条」抢视觉，再轻在非背光屏上就看不见了。
    for i in range(LIST_ROWS):
        if i % 2:
            continue
        for y in range(i * ROW_H, min(WALL_H, (i + 1) * ROW_H)):
            for x in range(WALL_W):
                px[x, y] = MSP.lerp(px[x, y], hi, 0.05)

    # 底栏（路径行）：往 sh 靠 10%，形成一条能"收住"列表的带
    for y in range(PATH_TOP, WALL_H):
        for x in range(WALL_W):
            px[x, y] = MSP.lerp(px[x, y], sh, 0.10)

    # 顶线：紧贴顶栏下沿的一条细亮线，把 16 行的顶栏和内容区分开
    for x in range(WALL_W):
        px[x, 0] = MSP.lerp(px[x, 0], ft, 0.18)
    return im


def add_watermark(im, style, page, icon_style):
    """在右下角印一个 5 倍大的图标剪影，对比度只比底色高 7%。

    页面图是当背景用的，上面还要压列表文字和文件名，所以图形只能到
    「看得见轮廓、读不出颜色」这一档 —— 再亮就会和正文抢。
    """
    out = im.copy()
    fg, bg, ft, hi, hib, sh = [MSP.hx(c) for c in style["pal"]]
    tint = MSP.lerp(bg, ft, 0.07)
    ic = MIG.render(PAGE_ICON[page], icon_style).resize((80, 80), Image.NEAREST)
    src = ic.load()
    px = out.load()
    ox, oy = WALL_W - 88, 36          # 右边留 8px，上面避开顶线、下面避开底栏
    for y in range(80):
        ty = oy + y
        if not (0 <= ty < PATH_TOP):
            continue
        for x in range(80):
            tx = ox + x
            if 0 <= tx < WALL_W and src[x, y][3] > 128:
                px[tx, ty] = tint
    return out


def wall_bytes(im):
    """图 → (34560 字节索引, 220 个 RGB555)。规矩和编辑器一致：
    量化输出已 +PAL_FIRST，必须过 map_wall_indices / slice_wall_palette 剥掉。"""
    fitted = im.convert("RGB")
    if fitted.size != (WALL_W, WALL_H):
        fitted = WP.fit_image(fitted, mode="cover")
    ind, pal, _used = WP.quantize(fitted, colors=fwbake.PAL_N)
    return fwbake.map_wall_indices(ind), fwbake.slice_wall_palette(pal)


# --------------------------------------------------------------------- logo
def make_logo():
    """31x7：深色铭牌 + 白字 + 两端橙条。三个色正好是固件支持的上限。

    字宽是量出来的：31px 一排只够 7 个字符，先试 "SuperFW"，放不下退 "SFW"。
    """
    im = Image.new("RGB", (sdtheme.LOGO_W, sdtheme.LOGO_H), LOGO_COLS[0])
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=sdtheme.LOGO_H)
    txt = "SFW"
    for cand in ("SuperFW", "SUPERFW", "SFW"):
        if d.textlength(cand, font=f) <= sdtheme.LOGO_W - 4:
            txt = cand
            break
    tw = d.textlength(txt, font=f)
    d.text(((sdtheme.LOGO_W - tw) / 2, -1), txt, font=f, fill=LOGO_COLS[1])
    d.rectangle([0, 0, 2, 0], fill=LOGO_COLS[2])
    d.rectangle([0, sdtheme.LOGO_H - 1, 2, sdtheme.LOGO_H - 1], fill=LOGO_COLS[2])
    d.rectangle([sdtheme.LOGO_W - 3, 0, sdtheme.LOGO_W - 1, 0], fill=LOGO_COLS[2])
    d.rectangle([sdtheme.LOGO_W - 3, sdtheme.LOGO_H - 1, sdtheme.LOGO_W - 1,
                 sdtheme.LOGO_H - 1], fill=LOGO_COLS[2])
    return im, txt


def pack_logo(im):
    """按固定三色量化（不用 ADAPTIVE，避免三个色自己漂）。"""
    small = im.convert("RGB").resize((sdtheme.LOGO_W, sdtheme.LOGO_H), Image.NEAREST)
    px = small.load()
    idx = bytearray()
    for y in range(sdtheme.LOGO_H):
        for x in range(sdtheme.LOGO_W):
            c = px[x, y]
            k = min(range(3), key=lambda i: sum((c[j] - LOGO_COLS[i][j]) ** 2
                                                for j in range(3)))
            idx.append(k)
    return bytes(idx), list(LOGO_COLS)


# --------------------------------------------------------------------- 图标
def icon_style_for(style):
    """按内容底亮度选彩色图标：亮的用「彩绘浅底」，暗的用「彩绘深底」。

    阈值 0.55 不是 0.5：掌机绿那张底 (#9BBC0F) 亮度 0.42 但饱和度很高，
    淡彩主体压上去会飘，配深底那套的硬描边反而更稳。
    """
    return MIG.STYLES["C" if MSP.luma(MSP.hx(style["pal"][1])) > 0.55 else "D"]


def icon_set_for(style):
    imgs, colors = MIG.build(icon_style_for(style))
    assert len(colors) <= 37, "第 %s 套图标 %d 色，超上限" % (style["name"], len(colors))
    sprite, pal = sdedit.icons_to_raw(imgs)
    assert len(sprite) == sdtheme.ICON_BYTES, "精灵块应为 4352 字节"
    return sprite, pal


# --------------------------------------------------------------------- 打包
def build(with_pages):
    pals, icon_sets, excols = [], [], []
    walls, wall_pals = [], []
    page_idx = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]
    page_pal = [[None] * sdtheme.PAGE_COUNT for _ in range(sdtheme.THEME_COUNT)]

    for i, st in enumerate(MSP.STYLES):
        pals.append([MSP.hx(c) for c in st["pal"]])
        excols.append([MSP.hx(c) for c in st["excol"]])
        icon_sets.append(icon_set_for(st))

        base = wall_for(st)
        wi, wp = wall_bytes(base)
        walls.append(wi)
        wall_pals.append(wp)

        if with_pages:
            istyle = icon_style_for(st)
            for p in range(sdtheme.PAGE_COUNT):
                pi, pp = wall_bytes(add_watermark(base, st, p, istyle))
                page_idx[i][p] = pi
                page_pal[i][p] = pp
        print("  第 %d 套 %-6s 壁纸 OK%s"
              % (i + 1, st["name"], "（+7 页）" if with_pages else ""))

    logo_img, logo_pal = pack_logo(make_logo()[0])
    raw = sdtheme.pack(pals, icon_sets, walls=walls, wall_pals=wall_pals,
                       set_page_walls=page_idx, set_page_pals=page_pal,
                       extra_cols=excols, logo=(logo_img, logo_pal))

    # 自检：读回来必须和写进去的一致
    r = sdtheme.parse(raw)
    q = lambda c: sdtheme.rgb555(sdtheme.color555(c))
    assert r["version"] == sdtheme.VERSION_V6, "版本应为 v6，实得 v%d" % r["version"]
    assert r["base_version"] == (sdtheme.VERSION_V4 if with_pages
                                 else sdtheme.VERSION_V2), r["base_version"]
    assert len(raw) == (sdtheme.FILE_SIZE_V4 if with_pages
                        else sdtheme.FILE_SIZE_V2) + sdtheme.V6_TAIL, \
        "长度 %d 对不上 v%d + v6 尾巴" % (len(raw), 4 if with_pages else 2)
    assert r["logo"][0] == logo_img, "logo 索引没存对"
    assert all(r["walls"][i] is not None for i in range(sdtheme.THEME_COUNT))
    for i, st in enumerate(MSP.STYLES):
        assert r["pals"][i] == [q(MSP.hx(c)) for c in st["pal"]], "第 %d 套配色" % (i + 1)
        assert r["extra_cols"][i] == [q(MSP.hx(c)) for c in st["excol"]]
    if with_pages:
        want = 0
        for i in range(sdtheme.THEME_COUNT):
            for p in range(sdtheme.PAGE_COUNT):
                want |= 1 << sdtheme.slot_index(i, p)
        assert r["set_page_mask"] == want, "页面图掩码不完整"
    return raw


# --------------------------------------------------------------------- 预览
def audit():
    print("%-10s %8s %8s %8s %8s" % ("套", "正文/底", "正文/条", "条/底", "暗字/栏"))
    bad = 0
    for st in MSP.STYLES:
        fg, bg, ft, hi, hib, sh = [MSP.hx(c) for c in st["pal"]]
        row = (MSP.contrast(ft, bg), MSP.contrast(ft, hib),
               MSP.contrast(hib, bg), MSP.contrast(sh, fg))
        keys = ("body", "body_on_sel", "sel_vs_bg", "dim_on_bar")
        flags = [k for k, v in zip(keys, row)
                 if v < MSP.LIMITS[k] and k not in st.get("exempt", ())]
        print("%-10s %8.2f %8.2f %8.2f %8.2f  %s"
              % (st["name"], *row, "OK" if not flags else "低于 " + ",".join(flags)))
        bad += bool(flags)
    print("底线 %s" % MSP.LIMITS)
    return bad


def previews(raw, out_dir):
    pv = os.path.join(out_dir, "预览")
    os.makedirs(pv, exist_ok=True)
    doc = sdedit.Doc.from_bytes(raw, "<sffix-theme>")

    panels, labels = [], []
    for i, st in enumerate(MSP.STYLES):
        im = sdedit.render_preview(doc, i)
        panels.append(im)
        labels.append("%d  %s" % (i + 1, st["name"]))
        im.save(os.path.join(pv, "%02d_%s.png" % (i + 1, st["key"])))
        MSP.upscale(im, 3).save(os.path.join(pv, "%02d_%s@3x.png" % (i + 1, st["key"])))
    MSP.sheet(panels, 2, 2, labels, "分支固件 · 文件列表页（2x）").save(
        os.path.join(pv, "对比图-六套.png"))

    # 七页标签页效果（用第 1 套，页面图有就显示页面图）
    pages = [sdedit.render_preview(doc, 0, page=p) for p in range(sdtheme.PAGE_COUNT)]
    MSP.sheet(pages, 2, 2, PAGE_NAME, "第 1 套 · 七个标签页（2x）").save(
        os.path.join(pv, "对比图-七页.png"))

    # 关于页 = 看 logo；载入画面 = 看 v5 三个色
    about = sdedit.render_preview(doc, 0, page=sdtheme.PAGE_COUNT - 1)
    about.save(os.path.join(pv, "关于页.png"))
    load = sdedit.render_preview(doc, 0, extra="loading")
    MSP.sheet([about, load], 2, 3, ["关于页（logo 在下方）", "载入进度画面（v5 三色）"],
              "logo 与整屏色").save(os.path.join(pv, "对比图-logo与载入.png"))
    # logo 本身单独放大看：31x7 的小块，只有放大才看得出字
    sdedit.logo_to_image(doc.logo_img, doc.logo_pal, scale=10).save(
        os.path.join(pv, "logo-31x7@10x.png"))
    return pv


def main():
    ap = argparse.ArgumentParser(description="生成 superfw-fix 专用主题包")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--audit", action="store_true", help="只算对比度")
    ap.add_argument("--pages-only", action="store_true", help="只出带页面图那一份")
    a = ap.parse_args()

    bad = audit()
    if a.audit:
        return 1 if bad else 0

    os.makedirs(a.out, exist_ok=True)

    if not a.pages_only:
        print("\n[v2 基础] 6 套壁纸，切页不读卡")
        raw = build(with_pages=False)
        p = os.path.join(a.out, "theme.bin")
        open(p, "wb").write(raw)
        print("  -> %s（%d 字节）" % (p, len(raw)))
        pv = previews(raw, a.out)

    print("\n[v4 基础] 6 套壁纸 + 42 张页面图")
    raw2 = build(with_pages=True)
    p2 = os.path.join(a.out, "theme-带页面图.bin")
    open(p2, "wb").write(raw2)
    print("  -> %s（%d 字节）" % (p2, len(raw2)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
