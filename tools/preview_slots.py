"""出一张「6 套主题在实机上分别长什么样」的对比图。

用法: python tools/preview_slots.py [输出路径]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image, ImageDraw          # noqa: E402

import main as APP                        # noqa: E402

W, H = 240, 160
SCALE = 2
GAP = 10
LABEL = 18


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "dist", "v" + APP.VERSION, "预览", "6套主题.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    r = APP.Renderer()
    r.pal_ref = APP.PRESETS[0][1][:]
    if hasattr(r, "wall"):
        r.wall = None

    tw, th = W * SCALE, H * SCALE
    sheet = Image.new("RGB", (3 * tw + 4 * GAP, 2 * (th + LABEL) + 3 * GAP), (24, 24, 22))
    d = ImageDraw.Draw(sheet)

    for i, (name, cols) in enumerate(APP.PRESETS):
        img = r.screen("browser", cols, "zh", 2).convert("RGB")
        img = img.resize((tw, th), Image.NEAREST)
        cx = GAP + (i % 3) * (tw + GAP)
        cy = GAP + (i // 3) * (th + LABEL + GAP)
        sheet.paste(img, (cx, cy + LABEL))
        d.rectangle([cx - 1, cy + LABEL - 1, cx + tw, cy + LABEL + th],
                    outline=(70, 70, 64))
        d.text((cx + 2, cy + 3), "%d  %s" % (i + 1, name), fill=(228, 226, 214))

    sheet.save(out)
    print("已写出", out, sheet.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
