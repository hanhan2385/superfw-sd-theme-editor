# -*- coding: utf-8 -*-
"""壁纸作用范围回归测试。

1. 「整套换图」= 这张图成为这一套的默认图，这一套所有页面都用它；
2. 「只换这一页」= 只动这一页，其它页面和别的套都不动；
3. 「清页面图」= 只清当前这一套。

跑法：python tools/test_wall_scope.py
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "app"))

import tkinter as tk  # noqa: E402
from PIL import Image  # noqa: E402

import sdedit as S  # noqa: E402
import sdtheme as T  # noqa: E402


def img(c):
    return Image.new("RGB", (T.WALL_W, T.WALL_H), c)


class FakeCrop:
    """替掉裁剪对话框：直接返回一张纯色 240x144。"""
    result_rgb = (10, 200, 30)

    def __init__(self, master, im, state=None, **kw):
        # v0.36 起还有 cover_rows / fgcolor / bgcolor / whole_set 等参数，
        # 这个桩不关心它们，全收下就行 —— 写死签名会在改对话框时炸。
        self.src = im
        self.state = None
        self.result = img(FakeCrop.result_rgb)


def main():
    root = tk.Tk()
    root.withdraw()
    doc = S.Doc()
    app = S.App(root, doc)

    # 6 套各一张；第 1 套有 3 页专属图，第 2 套有 1 页
    for i in range(6):
        doc.wall_rgb[i] = img((20 * i, 60, 120))
        doc.wall_dirty[i] = True
    for p in (0, 2, 5):
        doc.sp_rgb[0][p] = img((200, 30 * p, 40))
        doc.sp_dirty[0][p] = True
    doc.sp_rgb[1][4] = img((10, 220, 90))
    doc.sp_dirty[1][4] = True
    app.slot = 0
    app.refresh_all()

    ok = []
    assert doc.page_count_set(0) == 3 and doc.page_count_set(1) == 1
    ok.append("初始：第 1 套 3 页、第 2 套 1 页")

    # ---- 整套换图 ----
    S.CropDialog = FakeCrop
    S.filedialog.askopenfilename = lambda **k: "dummy.png"
    S.Image.open = staticmethod(lambda p: Image.new("RGB", (480, 288), (1, 2, 3)))
    # pick_wall 会先问「换整套会清掉已有页面图，继续吗」—— 无人值守必须自动答 yes
    S.messagebox.askyesno = lambda *a, **k: True

    app.pick_wall()
    assert doc.page_count_set(0) == 0, "整套换图后，这套的页面图应该全部清掉"
    assert doc.page_count_set(1) == 1, "第 2 套被连累了"
    px = doc.wall_rgb[0].getpixel((120, 72))
    assert px == FakeCrop.result_rgb, px
    ok.append("整套换图：第 1 套 7 页全部改用它，第 2 套没动")

    # ---- 只换这一页 ----
    FakeCrop.result_rgb = (200, 40, 200)
    app.select_target(3)
    before = [doc.sp_rgb[0][p] for p in range(7)]
    app.pick_page_wall()
    assert doc.sp_rgb[0][3] is not None, "这一页没换成功"
    assert doc.sp_rgb[0][3].getpixel((120, 72)) == (200, 40, 200)
    for p in (0, 1, 2, 4, 5, 6):
        assert doc.sp_rgb[0][p] is before[p], "第 %d 页被顺带改了" % (p + 1)
    ok.append("只换这一页：其它 6 页和别的套都没动")

    # ---- 清页面图（只清当前套）----
    S.messagebox.askyesno = lambda *a, **k: True
    app.select_target(-1)
    app.clear_page_walls()
    assert doc.page_count_set(0) == 0
    assert doc.page_count_set(1) == 1, "清页面图把第 2 套也清了"
    ok.append("清页面图：只清第 1 套，第 2 套保留")

    # ---- 按钮状态 ----
    app.select_target(-1)
    assert str(app.btn_page.cget("state")) == "disabled", "没选页时「只换这一页」应禁用"
    app.select_target(2)
    assert str(app.btn_page.cget("state")) == "normal"
    ok.append("「只换这一页」只在选中某页时可用")

    # ---- 打开文件 / 恢复内置之后，页面级操作不能崩 ----
    # v0.29 修的坑：open_file/reset_doc 把 page_src/page_state 重置成 7 个 None，
    # 而它们是 6 套 x 7 页的二维表 → 打开文件后再点「只换这一页」会 TypeError。
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "reopen.bin")
    doc.save(tmp)
    S.filedialog.askopenfilename = lambda **k: tmp
    S.messagebox.askyesno = lambda *a, **k: True
    # 有未保存改动时 open_file 问的是 askyesnocancel（是=保存/否=丢弃/取消=停），
    # 这里回「否」= 丢掉当前改动继续打开
    S.messagebox.askyesnocancel = lambda *a, **k: False
    app.open_file()
    assert app.page_focus == 1, "打开文件后应停在文件页，实际 %r" % (app.page_focus,)
    assert len(app.page_src) == S.sdtheme.THEME_COUNT, "page_src 应是 6 套"
    assert len(app.page_src[0]) == S.sdtheme.PAGE_COUNT, "每套应是 7 页"
    app.select_target(2)
    app.pick_page_wall()                     # 以前这里抛 TypeError
    assert app.doc.sp_rgb[0][2] is not None, "打开文件后「只换这一页」没生效"
    app.reset_doc()
    assert len(app.page_src) == S.sdtheme.THEME_COUNT and app.page_focus == 1
    app.select_target(3)
    app.pick_page_wall()
    assert app.doc.sp_rgb[0][3] is not None
    ok.append("打开文件 / 恢复内置之后，页面级壁纸操作仍然正常")

    # ---- 默认状态（v0.26 修的坑）----
    # 以前默认 page_focus=None（整套），但预览画的就是文件列表页，
    # 用户以为自己在文件页，点「只换这一页」时按钮是灰的 —— 以为坏了。
    # 用 Toplevel 当第二个 App 的容器：同进程里再开一个 Tk() 会让 PhotoImage
    # 归属到另一个解释器，报 "image pyimageN does not exist"。
    root2 = tk.Toplevel(root)
    root2.withdraw()
    app2 = S.App(root2, S.Doc())
    assert app2.page_focus == 1, "默认应停在文件页，实际 %r" % (app2.page_focus,)
    assert str(app2.btn_page.cget("state")) == "normal", "默认状态下「只换这一页」不该是灰的"
    assert not hasattr(app2, "btn_all"), "页面条上的「整套」按钮已删除（v0.37）"
    app2.select_target(-1)
    assert str(app2.btn_page.cget("state")) == "disabled"
    assert app2.btn_default.cget("fg") == S.ACC, "切到整套视图后右上角入口应该点亮"
    root2.destroy()
    ok.append("默认停在文件页、「只换这一页」直接可用；整套视图走右上角入口")

    # ---- 只读预览 ----
    for key, nm in S.EXTRA_SCREENS:
        assert key in S.EXTRA_USES, "%s 缺用色说明" % key
        assert S.EXTRA_USES[key].strip(), "%s 的用色说明是空的" % key
        app.select_extra(key)
        im = S.render_preview(doc, 0, extra=key)
        assert im.size == (240, 160)
        assert str(app.btn_set.cget("state")) == "disabled", "预览时壁纸按钮应禁用"
        note = app.wall_note.cget("text")
        assert "整屏底" in note or "底图" in note, \
            "%s 的提示里没写清楚吃哪几格颜色：%r" % (key, note[:60])
    app.select_extra(app.extra)               # 再点同一个 = 取消
    assert app.extra is None
    assert str(app.btn_set.cget("state")) == "normal"
    # 看完预览回来要停在原来那一页，不能掉回「整套」（否则按钮又灰了）
    app.select_target(4)
    app.select_extra("ingame")
    app.select_extra("ingame")
    assert app.page_focus == 4, "退出只读预览后应回到第 5 页，实际 %r" % (app.page_focus,)
    assert str(app.btn_page.cget("state")) == "normal"
    ok.append("四个只读预览能画出来；退出后回到原来那一页")

    # 游戏内菜单的文字色是 FT 不是 HI（固件里 menu_palette[2] 存的是 ft_color，
    # ingame_menu.c:1157 把它塞进自己的 HI_COLOR 槽 —— 别照着名字猜）
    keep = list(doc.pals[0])
    doc.pals[0] = [(255, 0, 0), (0, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)]
    used = {c[:3] for _, c in S.render_preview(doc, 0, extra="ingame").getcolors(4096)}
    assert (0, 255, 0) in used, "游戏内菜单没用 FT 画文字"
    assert (0, 0, 255) not in used, "游戏内菜单把 HI 当成文字色了"
    assert (0, 255, 255) in used, "游戏内菜单的置灰项没用 SH"
    doc.pals[0] = keep
    ok.append("游戏内菜单：文字 FT / 置灰 SH / 底色 BG（HI 不参与）")

    # ---- 存盘不出错 ----
    raw = doc.to_bytes()
    back = S.Doc.from_bytes(raw)
    assert back.page_count() == doc.page_count()
    ok.append("保存/读回一致（%d 字节，v%d）" % (len(raw), back.ver))

    root.destroy()
    for line in ok:
        print("  ✓", line)
    print("全部通过")


if __name__ == "__main__":
    main()
