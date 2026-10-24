"""自检：编辑器里「底栏会压住壁纸底部几行」这套数字，和固件源码对不对得上。

编辑器只靠 `sdedit.PAGE_BAR_TOP` 画遮挡提示；这张表一旦和 `menu.c` 跑的偏移不一致，
用户就会照着错的提示去构图 —— 所以让脚本直接去固件源码里把数字抠出来比对。

用法: python tools/test_bar_cover.py [src 目录，默认 ref/superfw-fix/src]
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import sdedit                                     # noqa: E402
import sdtheme                                    # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "ref", "superfw-fix", "src")
MENU = os.path.join(SRC, "menu.c")

# 页面函数 → 页面序号。序号跟 sdtheme.PAGE_NAMES 一致（最近/文件/烧录/设置/界面/工具/关于）
PAGE_FN = {
    "render_recent": 0,
    "render_browser": 1,
    "render_flashbrowser": 2,
    "render_settings": 3,
    "render_ui_settings": 4,
    "render_tools": 5,
    "render_info": 6,
}

fails = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def split_functions(text):
    """把 menu.c 剁成 {函数名: 函数体}，只认顶层 void/static 函数。"""
    out = {}
    for m in re.finditer(r"^(?:static\s+)?(?:inline\s+)?(?:void|int)\s+([a-z_0-9]+)\s*\(",
                         text, re.M):
        name = m.group(1)
        i = text.find("{", m.end())
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    out[name] = text[i:j + 1]
                    break
    return out


def _num(expr):
    """把 SCREEN_WIDTH / SCREEN_HEIGHT 换成数字后求值；带变量的表达式返回 None。"""
    e = expr.replace("SCREEN_WIDTH", "240").replace("SCREEN_HEIGHT", "160")
    if not re.match(r"^[\d\s\+\-\*/\(\)]+$", e):
        return None
    try:
        return float(eval(e, {"__builtins__": {}}, {}))
    except Exception:
        return None


def _args(text, start):
    """从 start 处的 '(' 开始，按顶层逗号拆出参数列表。"""
    depth = 0
    parts, cur = [], []
    for ch in text[start:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                parts.append("".join(cur))
                return parts
        elif ch == "," and depth == 1:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    return parts


def bar_top_of(body):
    """在函数体里找「从第 0 列开始整条铺满」的底栏填充，返回起始行；没有就 160。

    认两种写法：
      - 老写法 `dma_memset16(&frame[SCREEN_WIDTH * top], dup8(FG_COLOR), W*rows/2)`
        （ref/superfw-fix 的 r75 还是这样）
      - r78 起的 `render_bar(frame, top)` —— 半透明底栏把它包起来了，
        起始行变成第二个实参，所以这里单独认一下，别让重构把这张表改坏了还没人发现。
    只认 x 从 0 起、宽度是整屏的填充；`draw_box_outline` 那种带 `+left` 的局部框不算。
    """
    best = 160
    for m in re.finditer(r"dma_memset16\s*\(", body):
        args = _args(body, m.end() - 1)
        if len(args) != 3:
            continue
        dst, colour, width = (a.strip() for a in args)
        md = re.match(r"^&frame\[([^\[\]]+)\]$", dst)
        if not md:
            continue
        row_px = _num(md.group(1))
        w = _num(width)
        if row_px is None or w is None:
            continue
        if row_px % 240 != 0 or row_px > 240 * 160:      # 必须从第 0 列起
            continue
        if w % 120 != 0 or w > 240 * 160 / 2:           # 宽度 = 240 * 行数 / 2
            continue
        rows = int(round(w / 120))
        if 1 <= rows <= 160 and re.search(r"dup8\(", colour):
            best = min(best, int(row_px // 240))
    for m in re.finditer(r"render_bar\s*\(", body):
        args = _args(body, m.end() - 1)
        if len(args) != 2 or args[0].strip() != "frame":
            continue
        top = _num(args[1].strip())
        if top is not None:
            best = min(best, int(top))
    return best


def main():
    if not os.path.exists(MENU):
        print("找不到 %s —— 这台机器上没有分支固件源码，跳过比对。" % MENU)
        return 0
    text = open(MENU, encoding="utf-8", errors="replace").read()
    fns = split_functions(text)

    print("== 从 %s 抠出各页底栏起始行 ==" % os.path.relpath(MENU, ROOT))
    found = {}
    for fn, page in PAGE_FN.items():
        if fn not in fns:
            check(False, "menu.c 里没有 %s()" % fn)
            continue
        top = bar_top_of(fns[fn])
        found[page] = top
        print("  %-22s 页 %d %-6s 底栏起始行 %d"
              % (fn, page, sdtheme.PAGE_NAMES[page], top))

    print("\n== 和 sdedit.PAGE_BAR_TOP 比对 ==")
    check(len(found) == len(PAGE_FN),
          "7 个页面函数全部找到（实际 %d）" % len(found))
    for page, top in sorted(found.items()):
        want = sdedit.PAGE_BAR_TOP[page]
        check(top == want, "%s页：固件 %d / 编辑器 %d"
                           % (sdtheme.PAGE_NAMES[page], top, want))

    print("\n== 覆盖行数 ==")
    for page in range(len(sdtheme.PAGE_NAMES)):
        n = sdedit.bar_cover_rows(page)
        want = 160 - sdedit.PAGE_BAR_TOP[page]
        check(n == want, "%s页压住 %d 行" % (sdtheme.PAGE_NAMES[page], n))
    check(sdedit.bar_cover_rows(None) == max(sdedit.bar_cover_rows(p)
                                             for p in range(7)),
          "page=None 取的是最大值 %d" % sdedit.bar_cover_rows(None))
    check(sdedit.BAR_COVER_MAX == 22, "最坏情况 22 行（关于页）")
    check(sdedit.BAR_COVER_MAX < sdtheme.WALL_H,
          "压住的行数比壁纸矮（否则整张图都没了）")

    print("\n== v0.39：不再压扁，壁纸一律铺满 240×144 ==")
    from PIL import Image
    check(not hasattr(sdedit, "compose_fit"),
          "compose_fit 已删除（不再把图压进可见行）")
    check(not hasattr(sdedit.Doc, "fit_wall_fill"),
          "fit_wall_fill 已删除（改透明度不会再动壁纸）")

    # 造一张「老文件」的壁纸：上面 122 行是图，底下 22 行是底色
    BG = (9, 9, 9)
    sq = Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H), BG)
    sq.paste(Image.new("RGB", (sdtheme.WALL_W, sdtheme.WALL_H - 22), (200, 0, 0)), (0, 0))
    check(sq.getpixel((120, 143)) == BG, "老文件的壁纸底下 22 行是底色")

    full = sdedit.expand_squashed_wall(sq, 22)
    check(full.size == sq.size, "展开后还是 240×144 %s" % (full.size,))
    check(full.getpixel((120, 143)) != BG,
          "最底下一行变成图了 %s（底栏半透明时透出来的就是它）"
          % (full.getpixel((120, 143)),))
    check(full.getpixel((120, 0)) == (200, 0, 0), "顶上第一行还是原来的红")
    check(sdedit.expand_squashed_wall(sq, 0) is sq, "rows=0（这页没底栏）时原样返回")
    wide = sdedit.expand_squashed_wall(sq, 999)
    check(wide.size == sq.size, "rows 超过高度也不越界 %s" % (wide.size,))

    print("\n== 三选一已经删掉 ==")
    for name in ("MODE_FILL", "MODE_SQUASH", "MODE_LETTER", "norm_mode"):
        check(not hasattr(sdedit, name), "%s 已删除" % name)

    print("\n== 缩略图不许盖住图 ==")
    check(not hasattr(sdedit, "mark_covered"),
          "mark_covered 已删除（它会把暗色糊在缩略图上）")

    print("\n结论: %s" % ("全部通过" if not fails else "有 %d 项不对" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
