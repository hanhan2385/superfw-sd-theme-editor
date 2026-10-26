# -*- coding: utf-8 -*-
"""撤销栈 + 破坏性操作确认 + 小屏布局 的回归自测。

不弹任何对话框（把 colorchooser / messagebox / 选图都换成假的），
不写仓库里任何文件，只在内存里改 Doc。

    .venv314/Scripts/python.exe tools/test_undo_ui.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

import tkinter as tk                     # noqa: E402
from PIL import Image                    # noqa: E402

import sdedit                            # noqa: E402
import sdtheme                           # noqa: E402

OK, BAD = [], []


def ck(cond, msg):
    (OK if cond else BAD).append(msg)
    print(("  ok   " if cond else "  FAIL ") + msg)


class FakeBox:
    """替身 messagebox：记录被问了什么，按预设答案回复。"""

    def __init__(self, yes=True):
        self.yes = yes
        self.asked = []

    def askyesno(self, title, msg, **kw):
        self.asked.append(msg)
        return self.yes

    def showinfo(self, *a, **k):
        self.asked.append(a)

    def showerror(self, *a, **k):
        raise AssertionError("不该弹错误框: %r" % (a,))

    def askyesnocancel(self, *a, **k):
        return False


def red_wall(color=(200, 40, 40)):
    return Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), color)


def main():
    root = tk.Tk()
    root.withdraw()
    box = FakeBox(yes=True)
    sdedit.messagebox = box
    sdedit.colorchooser.askcolor = lambda **k: ((240, 0, 0), "#F00000")

    app = sdedit.App(root, sdedit.Doc())
    root.update_idletasks()

    # ---------------------------------------------------------------- 布局
    print("== 小屏布局 ==")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = root.winfo_width(), root.winfo_height()
    ck(w <= sw - 40, "窗口宽 %d 没超出屏幕 %d" % (w, sw))
    ck(h <= sh - 80, "窗口高 %d 没超出屏幕 %d（含任务栏）" % (h, sh))
    mw, mh = root.minsize()
    ck(mh <= h and mw <= w, "minsize %dx%d 不超过初始窗口" % (mw, mh))
    ck(len(app._scrollers) == 2, "左右两栏各有一个滚动容器")
    # 内容比可视区高时 scroll() 必须真的动
    sc = app._scrollers[1]
    root.update_idletasks()
    need = sc.body.winfo_reqheight()
    ck(need > 300, "右栏内容请求高度 %d（说明内容确实存在）" % need)
    ck(app.preview_scale in (1.5, 2.0), "预览倍数 %.1f" % app.preview_scale)

    # ---------------------------------------------------------------- 撤销：颜色
    print("== 撤销：改颜色 ==")
    before = tuple(app.doc.pals[0][0])
    app.pick_color(0)
    ck(tuple(app.doc.pals[0][0]) == (240, 0, 0), "颜色已改成 #F00000")
    ck(len(app.undo) == 1, "压了一格撤销栈")
    app.undo_last()
    ck(tuple(app.doc.pals[0][0]) == before, "撤回后颜色复原 %r" % (before,))
    ck(len(app.undo) == 0, "撤销栈已空")

    # 同一颜色再选一次不该压栈
    app.pick_color(0)
    n1 = len(app.undo)
    sdedit.colorchooser.askcolor = lambda **k: ((240, 0, 0), "#F00000")
    app.doc.pals[0][0] = (240, 0, 0)
    app.pick_color(0)
    ck(len(app.undo) == n1, "选同一个颜色不重复压栈")
    app.undo_last()
    app.undo_last()

    # ---------------------------------------------------------------- 确认 + 撤销：整套换图
    print("== 整套换图：确认 + 撤销 ==")
    app.doc.sp_rgb[0][2] = red_wall((10, 200, 10))     # 先放一张页面专属图
    app.doc.sp_dirty[0][2] = True
    ck(app.doc.page_count_set(0) == 1, "第 1 套有 1 张页面图")

    box.asked.clear()
    box.yes = False                                    # 用户选「否」
    app._choose_wall_image = lambda title, state=None: (
        Image.new("RGB", (400, 300), (0, 0, 200)), None, red_wall((0, 0, 200)))
    app.pick_wall()
    ck(any("专属图" in str(m) for m in box.asked), "弹了「会清掉页面图」的确认")
    ck(app.doc.sp_rgb[0][2] is not None, "选「否」时页面图没被清掉")
    ck(app.doc.wall_rgb[0] is None, "选「否」时壁纸没被换")

    box.yes = True                                     # 用户选「是」
    app.pick_wall()
    ck(app.doc.wall_rgb[0] is not None, "选「是」后壁纸换上了")
    ck(app.doc.page_count_set(0) == 0, "页面图被清掉（和确认文案一致）")
    ck(len(app.undo) >= 1, "换图前压了快照")
    app.undo_last()
    ck(app.doc.sp_rgb[0][2] is not None, "撤回后页面图回来了")
    ck(app.doc.wall_rgb[0] is None, "撤回后壁纸回到未设置")

    # ---------------------------------------------------------------- 确认：复制图标
    print("== 复制图标：确认 + 撤销 ==")
    app.doc.icon_imgs[0][0] = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    old1 = app.doc.icon_imgs[1][0].tobytes()
    box.asked.clear()
    box.yes = False
    app.copy_icons()
    ck(any("复制" in str(m) for m in box.asked), "弹了复制确认")
    ck(app.doc.icon_imgs[1][0].tobytes() == old1, "选「否」时别的套图标没变")
    box.yes = True
    app.copy_icons()
    ck(app.doc.icon_imgs[1][0].tobytes() == app.doc.icon_imgs[0][0].tobytes(),
       "选「是」后其余 5 套图标一致")
    app.undo_last()
    ck(app.doc.icon_imgs[1][0].tobytes() == old1, "撤回后第 2 套图标复原")

    # ---------------------------------------------------------------- 确认：重置图标
    print("== 重置图标：确认 + 撤销 ==")
    cur = app.doc.icon_imgs[0][0].tobytes()
    box.asked.clear()
    box.yes = False
    app.reset_icons()
    ck(any("原版" in str(m) for m in box.asked), "弹了重置确认")
    ck(app.doc.icon_imgs[0][0].tobytes() == cur, "选「否」时图标没变")
    box.yes = True
    app.reset_icons()
    ck(app.doc.icon_imgs[0][0].tobytes() != cur, "选「是」后变成内置图标")
    app.undo_last()
    ck(app.doc.icon_imgs[0][0].tobytes() == cur, "撤回后自定义图标回来")

    # ---------------------------------------------------------------- 多步回退
    print("== 多步回退 ==")
    app.undo.clear()
    app._refresh_undo()
    for i, c in enumerate([(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4)]):
        sdedit.colorchooser.askcolor = lambda c=c, **k: (c, "#000000")
        app.pick_color(0)
    ck(len(app.undo) == 4, "连改 4 次 → 4 格")
    app.undo_last()
    app.undo_last()
    ck(tuple(app.doc.pals[0][0]) == (2, 2, 2), "退两步回到第 2 次改的值")
    app.undo_last()
    app.undo_last()
    ck(len(app.undo) == 0, "退到底")
    ck(tuple(app.doc.pals[0][0]) == before, "退到底等于初始值")

    # 栈深度上限
    print("== 栈深度上限 ==")
    app.undo.clear()
    for i in range(app.undo.limit + 6):
        app.undo.push("x%d" % i, {"doc": app.doc.snapshot(), "wall_src": [],
                                  "wall_state": [], "page_src": [], "page_state": []})
    ck(len(app.undo) == app.undo.limit, "栈被截到上限 %d" % app.undo.limit)

    # ---------------------------------------------------------------- 换文件清栈
    print("== 打开新文件清空撤销栈 ==")
    app.undo.clear()
    app.undo.push("脏", {"doc": app.doc.snapshot(), "wall_src": [None] * 6,
                         "wall_state": [None] * 6,
                         "page_src": [[None] * 7 for _ in range(6)],
                         "page_state": [[None] * 7 for _ in range(6)]})
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="sdedit_undo_"), "t.bin")
    with open(tmp, "wb") as fh:
        fh.write(sdedit.Doc().to_bytes())
    sdedit.filedialog.askopenfilename = lambda **k: tmp
    app.open_file()
    ck(len(app.undo) == 0, "换文件后撤销栈清空")

    root.destroy()

    # ---------------------------------------------------------------- 1366×768
    print("== 1366×768 模拟（最常见的小屏）==")
    r2 = tk.Tk()
    r2.winfo_screenwidth = lambda: 1366
    r2.winfo_screenheight = lambda: 768
    a2 = sdedit.App(r2, sdedit.Doc())
    r2.update_idletasks()
    r2.update()
    r2.update_idletasks()
    w2, h2 = r2.winfo_width(), r2.winfo_height()
    ck(w2 <= 1366 - 40, "窗口宽 %d ≤ %d" % (w2, 1366 - 40))
    ck(h2 <= 768 - 100, "窗口高 %d ≤ %d（留出任务栏和标题栏）" % (h2, 768 - 100))
    ck(a2.preview_scale == 1.5, "预览自动退到 1.5 倍（%d×%d）"
       % (int(sdedit.W * 1.5), int(sdedit.H * 1.5)))
    for tag, sc in (("左栏", a2._scrollers[0]), ("右栏", a2._scrollers[1])):
        view_h = sc.canvas.winfo_height()
        need_h = sc.body.winfo_reqheight()
        if need_h > view_h + 1:
            v0 = sc.canvas.yview()
            moved = sc.scroll(4)
            ck(moved and sc.canvas.yview() != v0,
               "%s 内容 %d > 可视 %d，滚轮生效" % (tag, need_h, view_h))
        else:
            ck(True, "%s 内容 %d ≤ 可视 %d，不需要滚" % (tag, need_h, view_h))
    # 顶栏按钮全部落在窗口内
    tops = [w for w in r2.winfo_children()[0].winfo_children()] \
        if r2.winfo_children() else []
    ck(all(w.winfo_x() + w.winfo_width() <= w2 for w in tops if w.winfo_ismapped()),
       "顶栏 %d 个控件都没超出窗口右边" % len(tops))
    r2.destroy()

    print("\n通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    if BAD:
        for m in BAD:
            print("  FAILED:", m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
