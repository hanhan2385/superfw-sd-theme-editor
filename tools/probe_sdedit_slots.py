"""临时探针：确认编辑器里 6 套配色的切换 / 编辑是否真的独立。

只跑模型层 + 界面刷新，不弹任何对话框。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import tkinter as tk  # noqa: E402
import sdedit  # noqa: E402


def main():
    root = tk.Tk()
    root.geometry("1120x720+40+40")
    app = sdedit.App(root, sdedit.Doc())
    root.update()

    print("== 逐套切换 ==")
    for n in range(6):
        app.select_slot(n)
        root.update()
        shown = [v.cget("text") for _sw, v in app.color_rows]
        model = ["#%02x%02x%02x" % c for c in app.doc.pals[n]]
        same = all(a.upper() == b.upper() for a, b in zip(shown, model))
        print("第%d套 slot=%d 显示=%s 与模型一致=%s" % (n + 1, app.slot, shown[:3], same))
        # 预览图左上角像素（顶栏颜色 = FG）
        print("   预览 FG 像素 =", app._preview_src.getpixel((2, 2)))

    print("== 改第2套的一个颜色，看第1套是否被连带 ==")
    before1 = list(app.doc.pals[0])
    app.select_slot(1)
    app.doc.pals[1][3] = (255, 0, 255)
    app.refresh_all()
    root.update()
    print("第1套是否被改 =", before1 != list(app.doc.pals[0]))
    print("第2套 HIB =", app.doc.pals[1][3], " 界面显示 =",
          app.color_rows[3][1].cget("text"))

    print("== 存盘再读，看 6 套是否各自不同 ==")
    data = app.doc.to_bytes()
    r = sdedit.sdtheme.parse(data)
    for i, p in enumerate(r["pals"]):
        print("  第%d套 = %s" % (i + 1, ["#%02x%02x%02x" % c for c in p]))

    print("== 每套各放一张不同壁纸 ==")
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (255, 0, 255), (0, 255, 255)]
    from PIL import Image
    for i in range(6):
        app.select_slot(i)
        app.doc.wall_rgb[i] = Image.new("RGB", (240, 144), cols[i])
        app.doc.wall_dirty[i] = True
        app.wall_src[i] = None
        root.update()
        print("  切到第%d套 状态=%s 缩略图中心=%s"
              % (i + 1, app.status.cget("text")[:34],
                 app.doc.wall_rgb[i].getpixel((120, 72))))
    print("  计数 =", app.doc.wall_count(), " 大小 =", app.doc.packed_size())
    data = app.doc.to_bytes()
    r = sdedit.sdtheme.parse(data)
    print("  存盘 %d 字节 v%d" % (len(data), r["version"]))
    back = sdedit.Doc.from_bytes(data)
    for i in range(6):
        px = back.wall_rgb[i].getpixel((120, 72))
        print("    第%d套读回 %s 期望 %s 一致=%s"
              % (i + 1, px, cols[i],
                 sum(abs(px[c] - cols[i][c]) for c in range(3)) <= 24))

    print("== 只删第 3 套的壁纸 ==")
    app.select_slot(2)
    app.clear_wall()
    root.update()
    print("  剩下 %d 张" % app.doc.wall_count(),
          " 每套有/无 =", [app.doc.wall_rgb[i] is not None for i in range(6)])

    print("== 把第 1 套的图铺给全部 6 套 ==")
    app.select_slot(0)
    app.copy_wall()
    root.update()
    print("  每套有/无 =", [app.doc.wall_rgb[i] is not None for i in range(6)],
          " 大小 =", app.doc.packed_size())

    root.destroy()


if __name__ == "__main__":
    main()
