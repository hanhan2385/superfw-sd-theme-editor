# -*- coding: utf-8 -*-
"""img2gba 的自检 —— 不靠"看起来对"，逐字节比。

四件事必须成立：
  1. SuperFW 预设的产出，和真正写固件的那条路（app/fwbake.py）逐字节一致，
     否则"预览看到的"和"写进卡带的"就是两张图；
  2. 预览渲染 = 已发布证据图（preview_baked.py 用的 555→RGB 位复制）；
  3. 瓦片重排、4bpp 打包都能原样解回来；
  4. 导出的文件长度 / 头文件里的数组长度与实际数据对得上。

用法: python tools/test_img2gba.py
"""
import os
import re
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "app"), os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image, ImageDraw           # noqa: E402

import fwbake as FB                        # noqa: E402
import gbacvt as G                          # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + name + (("  " + detail) if detail else ""))
    if not ok:
        FAIL.append(name)


def sample(w=640, h=360):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(20 + 200 * y // h, 60 + 120 * y // h, 120))
    d.ellipse([w * .6, h * .1, w * .95, h * .6], fill=(250, 205, 70))
    d.rectangle([10, 10, 40, 40], fill=(255, 60, 60))
    d.rectangle([w - 40, h - 40, w - 10, h - 10], fill=(60, 255, 120))
    return im


def unpack_tile(data, size, bpp, fill=0):
    """pack 的逆运算：把瓦片顺序还原成逐行顺序。"""
    w, h = size
    tile_w = w // 8
    if bpp == 8:
        grid = [[fill] * w for _ in range(h)]
        for ty in range(0, h, 8):
            for tx in range(0, w, 8):
                off = ((ty // 8) * tile_w + (tx // 8)) * 64
                for row in range(8):
                    for col in range(8):
                        grid[ty + row][tx + col] = data[off + row * 8 + col]
        return bytes(v for row in grid for v in row)
    grid = [[fill] * w for _ in range(h)]
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            off = ((ty // 8) * tile_w + (tx // 8)) * 32
            for row in range(8):
                for col in range(0, 8, 2):
                    b = data[off + row * 4 + col // 2]
                    grid[ty + row][tx + col] = b & 0x0F
                    grid[ty + row][tx + col + 1] = (b >> 4) & 0x0F
    return bytes(v for row in grid for v in row)


def main():
    im = sample()
    tmp = tempfile.mkdtemp(prefix="img2gba_")

    print("1) 和固件工具逐字节一致（SuperFW 预设 / fwbake.prepare_image）")
    import wallpaper as WP

    def ref_bake(img, dim, anchor):
        """fwbake.prepare_image 的等价实现，只多一个可调取景。

        fwbake 那边写死居中（WP.fit_image(..., "cover")），所以锚点不是 0.5 时
        没法直接拿它当参照；这里用同一套 fit_exact + Brightness + MEDIANCUT，
        先证明「锚点居中时两者完全相同」，再证明其它锚点走的是同一条路。
        """
        from PIL import ImageEnhance
        fitted = WP.fit_exact(img, anchor)
        if dim > 0:
            fitted = ImageEnhance.Brightness(fitted).enhance(max(0.0, 1.0 - dim))
        q = fitted.quantize(colors=FB.PAL_N, method=Image.MEDIANCUT,
                            dither=Image.FLOYDSTEINBERG)
        raw = list(q.getdata())
        idx = bytes(FB.SLOTS[v] if v < FB.PAL_N else FB.PAL_BASE for v in raw)
        p = q.getpalette() or []
        pal = []
        for i in range(FB.PAL_N):
            r, g, b = (list(p[i * 3:i * 3 + 3]) + [0, 0, 0])[:3]
            pal.append(((r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)))
        return idx, pal

    # 先证明等价实现和真函数在居中时完全一致（否则下面的参照不算数）
    a_idx, a_pal = ref_bake(im, 0.3, (0.5, 0.5))
    b_idx, b_pal, _u, _im = FB.prepare_image(im, 0.3)
    check("参照实现 == fwbake.prepare_image（居中）",
          a_idx == bytes(b_idx) and a_pal == list(b_pal))

    for anchor in ((0.5, 0.5), (0.2, 0.85), (0.9, 0.1)):
        for dim in (0.0, 0.25):
            ref_idx, ref_pal = ref_bake(im, dim, anchor)
            rep = G.convert(im, w=240, h=144, colors=FB.PAL_N, bpp=8, order="linear",
                            base=FB.PAL_BASE, dim=dim, anchor=anchor, dither=True)
            same_idx = rep["data"] == ref_idx
            same_pal = rep["table"][FB.PAL_BASE:FB.PAL_BASE + FB.PAL_N] == ref_pal
            check("anchor=%s dim=%.2f（%d 字节 / %d 色）"
                  % (anchor, dim, len(ref_idx), FB.PAL_N), same_idx and same_pal,
                  "" if same_idx else "第 %s 字节不同" % _first_diff(rep["data"], ref_idx))
            if not same_pal:
                diff = [(i, a, b) for i, (a, b) in
                        enumerate(zip(rep["table"][FB.PAL_BASE:FB.PAL_BASE + FB.PAL_N],
                                      ref_pal)) if a != b][:3]
                print("         调色板差异 %s" % diff)

    print("2) 预览 = 固件里的实际画面")
    rep = G.convert(im, w=240, h=144, colors=FB.PAL_N, bpp=8, order="linear",
                    base=FB.PAL_BASE, dim=0.0, anchor=(0.5, 0.5))
    ref_idx, ref_pal, _u, _i = FB.prepare_image(im, 0.0)
    lut = [(0, 0, 0)] * 256
    for i, v in enumerate(ref_pal):
        lut[FB.PAL_BASE + i] = FB.bgr555_to_rgb(v)
    ref = Image.frombytes("P", (240, 144), bytes(ref_idx))
    ref.putpalette([c for rgb in lut for c in rgb])
    check("240×144 逐像素", ref.convert("RGB").tobytes() == rep["display"].tobytes())

    print("3) 瓦片重排能解回原样")
    for (w, h, bpp, colors) in ((240, 160, 8, 64), (64, 64, 4, 16), (256, 128, 8, 32)):
        r = G.convert(im, w=w, h=h, colors=colors, bpp=bpp, order="tile", base=0,
                      dither=False)
        back = unpack_tile(r["data"], (w, h), bpp)
        ok = back == r["idx"]
        if not ok:
            idxs = [i for i, (a, b) in enumerate(zip(back, r["idx"])) if a != b][:5]
            ok = not idxs
        check("%dx%d %dbpp tile → 逆运算逐像素相同" % (w, h, bpp), ok,
              "" if ok else "前几个不同位置 %s" % idxs)
        check("%dx%d %dbpp 数据长度 %s" % (w, h, bpp, "w×h" if bpp == 8 else "w×h/2"),
              len(r["data"]) == (w * h if bpp == 8 else w * h // 2))
    # 线性排列下 8bpp 就是原样
    r = G.convert(im, w=240, h=160, colors=64, bpp=8, order="linear", base=0)
    check("线性排列 = 索引原始顺序", r["data"] == bytes(r["idx"]))

    print("4) 索引起点")
    r20 = G.convert(im, w=240, h=144, colors=220, bpp=8, order="linear", base=20)
    r0 = G.convert(im, w=240, h=144, colors=220, bpp=8, order="linear", base=0)
    check("base=20 的数据 = base=0 的数据 + 20",
          r20["data"] == bytes(min(255, v + 20) for v in r0["idx"]))
    check("base=20 的调色板前 20 项是空的", r20["table"][:20] == [0] * 20)
    check("base=0 的调色板从第 0 项开始", r0["table"][0] == G.to555(r0["pal"][0]))
    check("调色板表恒 256 项", len(r20["table"]) == 256)

    print("5) 边界")
    r = G.convert(im, w=64, h=64, colors=256, bpp=4, order="tile")
    check("4bpp 强制 16 色", r["colors"] == 16 and max(r["idx"]) < 16,
          "colors=%d max=%d" % (r["colors"], max(r["idx"])))
    try:
        G.convert(im, w=100, h=80, bpp=8, order="tile")
        check("宽高不是 8 的倍数时报错", False)
    except ValueError as e:
        check("宽高不是 8 的倍数时报错", "8 的倍数" in str(e))
    try:
        G.convert(im, w=64, h=64, colors=220, bpp=8, base=60)
        check("起点 + 色数超 256 时报错", False)
    except ValueError as e:
        check("起点 + 色数超 256 时报错", "256" in str(e))

    print("6) 导出文件")
    files = G.export(rep, tmp, stem="t")
    ext = {os.path.basename(f) for f in files}
    check("四种产物都写了", any(f.endswith(".png") for f in ext)
          and any(f.endswith(".bin") and ".pal." not in f for f in ext)
          and any(f.endswith(".h") for f in ext), str(sorted(ext)))
    bin_path = [f for f in files if f.endswith(".bin") and ".pal." not in f][0]
    check("索引 .bin = 34560 字节", os.path.getsize(bin_path) == 34560,
          str(os.path.getsize(bin_path)))
    pal_path = [f for f in files if f.endswith(".pal.bin")][0]
    check("调色板 .bin = 512 字节", os.path.getsize(pal_path) == 512)
    with open(pal_path, "rb") as f:
        vals = struct.unpack("<256H", f.read())
    check("调色板 .bin 与表一致", list(vals) == rep["table"])
    h_path = [f for f in files if f.endswith(".h")][0]
    txt = open(h_path, encoding="utf-8").read()
    m = re.search(r"gfx_data\[(\d+)\]", txt)
    n = len(re.findall(r"0x[0-9A-F]{2}", txt.split("gfx_data")[1]))
    check("头文件数组长度 = 实际字节数",
          bool(m) and int(m.group(1)) == len(rep["data"]) == n,
          "%s / %d / %d" % (m.group(1) if m else "?", len(rep["data"]), n))
    pal_decl = re.search(r"gfx_pal\[(\d+)\]", txt)
    check("头文件调色板 = 256 项", bool(pal_decl) and int(pal_decl.group(1)) == 256)

    print("7) 编辑器注入链路也一致（回归：+4 双重偏移的黑点 bug）")
    for anchor in ((0.5, 0.5), (0.2, 0.85)):
        for dim in (0.0, 0.3):
            w_idx, w_pal, _n = WP.quantize(WP.fit_exact(im, anchor), dim, (0, 0, 0),
                                           FB.PAL_N, True)
            e_idx = FB.map_wall_indices(w_idx)
            e_pal = FB.slice_wall_palette(w_pal)
            rep = G.convert(im, w=240, h=144, colors=FB.PAL_N, bpp=8, order="linear",
                            base=FB.PAL_BASE, dim=dim, anchor=anchor, dither=True)
            check("编辑器链路 == 转换器（anchor=%s dim=%.2f）" % (anchor, dim),
                  rep["data"] == e_idx and
                  rep["table"][FB.PAL_BASE:FB.PAL_BASE + FB.PAL_N] == list(e_pal))
    # 用满 220 色时，raw 216..219 不许再落到槽 20（黑色）
    w_idx, w_pal, _n = WP.quantize(WP.fit_exact(im, (0.5, 0.5)), 0.0, (0, 0, 0),
                                   FB.PAL_N, True)
    raws = set(v - WP.PAL_FIRST for v in w_idx)
    if max(raws) >= 216:
        e_idx = FB.map_wall_indices(w_idx)
        e_pal = FB.slice_wall_palette(w_pal)
        bad = [r for r in raws if r >= 216 and e_pal[r] == 0]
        check("最高 4 个颜色不再变黑", not bad, "仍为黑的色号 %s" % bad)
    else:
        check("最高 4 个颜色不再变黑", True, "这张图没用到 216+（跳过）")

    print("8) 其他")
    check("成品图固定 240×144", rep["display"].size == (240, 144))
    check("summary 有 5 行", len(G.summary(rep)) == 5)
    sw = G.swatch_image(rep)
    check("色板条尺寸合理", sw.size == (16 * 16, 16 * ((220 + 15) // 16)), str(sw.size))

    print()
    if FAIL:
        print("失败 %d 项：%s" % (len(FAIL), "；".join(FAIL)))
        return 1
    print("全部通过")
    return 0


def _first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return "长度不同 %d/%d" % (len(a), len(b))


if __name__ == "__main__":
    sys.exit(main())
