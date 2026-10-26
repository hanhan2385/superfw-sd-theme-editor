"""把烘焙进固件的位图还原出来，叠上菜单 UI，看看实际会是什么样。

用法: python tools/preview_baked.py <已烘焙固件> [输出.png] [--preset 0]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

from PIL import Image          # noqa: E402

import fwbake as FB            # noqa: E402
import main as M               # noqa: E402


def baked_wall(rom):
    """从固件里读回 8bpp 位图 + 调色板，还原成 240x144 的 RGB 图。"""
    meta = FB.find_baked(rom)
    if not meta:
        raise SystemExit("这个固件里没有烘焙标记")
    idx = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
    idx += bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
    lut = {}
    for i in range(meta["pal_n"]):
        v = rom[meta["pal_at"] + i * 2] | (rom[meta["pal_at"] + i * 2 + 1] << 8)
        lut[FB.PAL_BASE + i] = FB.bgr555_to_rgb(v)
    px = [lut.get(b, (0, 0, 0)) for b in idx]
    im = Image.new("RGB", (FB.IMG_W, FB.IMG_H))
    im.putdata(px)
    return im, meta


def main():
    path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "out/baked-preview.png"
    preset = 0
    if "--preset" in sys.argv:
        preset = int(sys.argv[sys.argv.index("--preset") + 1])
    rom = bytearray(open(path, "rb").read())
    wall, meta = baked_wall(rom)

    r = M.Renderer()
    r.wall = wall
    pal = M.PRESETS[preset][1]
    r.pal_ref = pal
    sheet = Image.new("RGB", (M.W * 2, M.H * 6), "#000000")
    names = ["browser", "uisettings", "loadinfo", "loadpatch", "tools", "info"]
    for i, n in enumerate(names):
        shot = r.screen(n, pal, "zh", 2).convert("RGB")
        sheet.paste(shot.resize((M.W * 2, M.H * 2), Image.NEAREST), (0, i * M.H * 2))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    sheet.save(out)
    wall.resize((FB.IMG_W * 2, FB.IMG_H * 2), Image.NEAREST).save(
        os.path.join(os.path.dirname(out) or ".", "baked-wall-only.png"))
    print("背景图 %d 色  写出 %s" % (meta["pal_n"], out))


if __name__ == "__main__":
    main()
