# -*- coding: utf-8 -*-
"""
把任意 SuperFW 固件适配成"带主题 + 带背景图"的版本（不改源码、不重编译）。
=========================================================================
流程：探测构建 → 写入第 6 个主题槽 → 打背景图补丁 → 重算 sha256 → 补齐卡带尺寸

用法：
  python tools/adapt_firmware.py in.bin [in2.fw ...] --outdir dist/adapted
  python tools/adapt_firmware.py in.bin --theme 1B2A38,0E1A22,E8F4F2,2C4A5A,6FF2C0,0A1419
  python tools/adapt_firmware.py in.bin --no-wall
"""
import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbuild as FB      # noqa: E402
import wallpaper as WP    # noqa: E402

# 默认主题：深青底 + 薄荷强调，正好用来检验"文字在深色背景图上也读得清"
DEFAULT_THEME = ["1B2A38", "0E1A22", "E8F4F2", "2C4A5A", "6FF2C0", "0A1419"]


def demo_wallpaper():
    """生成一张低对比度的深色背景图（480x288 再缩到 240x144）。"""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = 480, 288
    im = Image.new("RGB", (w, h), (10, 20, 26))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.rectangle([0, y, w, y], fill=(int(12 + 30 * (1 - t)), int(26 + 40 * (1 - t)),
                                        int(34 + 26 * (1 - t))))
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([w * 0.42, -h * 0.35, w * 1.05, h * 0.62], fill=(20, 96, 92))
    g.ellipse([-w * 0.25, h * 0.55, w * 0.42, h * 1.3], fill=(30, 48, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(48))
    im = Image.blend(im, glow, 0.55)
    return im


def load_icons():
    """取重绘好的 17 个图标（tools/redraw_icons.py 是唯一真源）。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import redraw_icons as RI
    data, colors, _tiles = RI.build()
    return data, [FB.rgb2gba(*c) for c in colors]


def adapt(path, outdir, colors, wall_img, dim, slot=5, icons=None, suffix="-Themed"):
    rom = bytearray(open(path, "rb").read())
    layout = FB.detect(rom)
    ok, why = FB.validate(rom)
    if not ok:
        raise SystemExit("%s: %s" % (path, why))

    # 1) 主题
    off, vals = WP.patch_theme(rom, colors, slot=slot, layout=layout)

    # 2) 图标
    icon_note = "未替换"
    if icons is not None:
        data, pal = icons
        ioff, poff = WP.patch_icons(rom, data, pal, layout)
        icon_note = "17 个 @ 0x%X，%d 色 @ 0x%X" % (ioff, len(pal) + 1, poff)

    # 3) 背景图
    info = None
    if wall_img is not None:
        idx, pal, n = WP.quantize(WP.fit_image(wall_img, "cover"), dim, (0, 0, 0), 236, True)
        info = WP.apply(rom, idx, pal, layout)
        info["colors"] = n
    else:
        FB.reseal(rom)

    # 3) 补齐到卡带尺寸（仅当写了壁纸载荷时需要；纯主题/图标不能改文件大小）
    size_before = len(rom)
    if wall_img is not None and len(rom) < layout.padded_size():
        rom += b"\xFF" * (layout.padded_size() - len(rom))

    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    name = stem.replace(" ", "").replace("-", "-") + suffix + ext
    out = os.path.join(outdir, name)
    open(out, "wb").write(rom)

    ok2, why2 = FB.validate(rom)
    return {
        "input": base, "output": name, "build": layout.build,
        "theme_at": hex(off), "theme": vals, "icons": icon_note,
        "wall": info, "size": "%d → %d" % (size_before, len(rom)),
        "valid": ok2, "why": why2, "layout": layout,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dist", "adapted"))
    ap.add_argument("--theme", default=",".join(DEFAULT_THEME))
    ap.add_argument("--wall", help="用自己的图片当背景（png/jpg）")
    ap.add_argument("--dim", type=float, default=0.45, help="背景淡化 0..1")
    ap.add_argument("--no-wall", action="store_true")
    ap.add_argument("--no-icons", action="store_true")
    ap.add_argument("--suffix", default="-Themed", help="输出文件名后缀")
    ap.add_argument("--slot", type=int, choices=range(1, 7), default=6,
                    help="写到第几套主题（1..6，默认第 6 套）")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    if args.no_wall:
        wall = None
    elif args.wall:
        from PIL import Image
        wall = Image.open(args.wall)
    else:
        wall = demo_wallpaper()

    colors = [c.strip() for c in args.theme.split(",")]
    if len(colors) != 6:
        raise SystemExit("--theme 需要 6 个 RRGGBB")

    icons = None if args.no_icons else load_icons()
    for p in args.files:
        r = adapt(p, args.outdir, colors, wall, args.dim, slot=args.slot - 1,
                  icons=icons, suffix=args.suffix)
        print("=" * 66)
        print("输入  ", r["input"])
        print("构建  ", r["build"])
        print("输出  ", r["output"], r["size"], "字节")
        print("主题槽", r["theme_at"], "->", ["0x%04X" % v for v in r["theme"]])
        print("图标  ", r["icons"])
        if r["wall"]:
            print("背景  ", r["wall"]["colors"], "色 · 位图", r["wall"]["image"],
                  "· 跳板", r["wall"]["tramp"])
        print("校验  ", "通过" if r["valid"] else "失败 " + r["why"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
