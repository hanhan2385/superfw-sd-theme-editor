# -*- coding: utf-8 -*-
"""端到端自检：编辑器产出的 theme.bin，固件能不能按预期读出来。

做的事：
  1. 对照 `fw/src/sdtheme.c` 的 #define 和 `app/sdtheme.py` 的常量；
  2. 用编辑器生成四种布局（无壁纸 / 6 套共用 / 每套一张 / 带页面图）；
  3. 按 `sdtheme.c` 的装载状态机（`sdt_wall_off_for` / `sdt_sync_wall` /
     `sdtheme_apply_theme` / `sdtheme_set_page` / dirty 重载）逐步模拟，
     断言"当前该显示哪一段"和编辑器模型一致；
  4. 检查文件头、长度、版本、flags。

注意：这是**行为等价**的复刻，不是跑真机代码。真机仍需实机验证一次
（尤其是 SD 读取速度和 SDRAM 缓存落点）。

用法：python tools/test_e2e_theme.py
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from PIL import Image                      # noqa: E402

import sdtheme as S                        # noqa: E402
import sdedit                              # noqa: E402

FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


def fw_defines():
    src = open(os.path.join(ROOT, "fw", "src", "sdtheme.c"), encoding="utf-8").read()
    vals = {}
    for line in src.splitlines():
        s = line.strip()
        if not s.startswith("#define SDT_"):
            continue
        s = s[len("#define "):]
        for cut in ("/*", "//"):
            if cut in s:
                s = s.split(cut)[0]
        name, _, expr = s.partition(" ")
        expr = expr.strip()
        if not expr:
            continue
        if expr.startswith('"'):
            vals[name] = expr.strip('"')
            continue
        try:
            vals[name] = int(eval(expr, {"__builtins__": {}}, dict(vals)))  # noqa: S307
        except Exception:                                   # noqa: BLE001
            vals[name] = expr
    return vals


# ---------------------------------------------------------------- 固件状态机复刻
class FwSim:
    """照着 fw/src/sdtheme.c 写的等价状态机（只关心"显示哪一段"）。"""

    def __init__(self, data):
        r = S.parse(data)
        self.ver = r["version"]
        self.v2 = self.ver >= 2
        self.v3 = self.ver >= 3
        flags = r["flags"]
        self.wallmask = ((flags >> 1) & 0x3F) if self.v2 else (0x3F if flags & 1 else 0)
        self.pagemask = ((flags >> S.FLAG_PAGE_BASE) & 0x7F) if self.v3 else 0
        self.data = data
        self.cur = None          # sdt_cur
        self.page = None         # sdt_page（None = 未知）
        self.wall_src = 0        # sdt_wall_src
        self.wall_ready = False  # sdt_wall_ready
        self.loads = 0           # 读卡次数（诊断用）

    # sdt_wall_off_for
    def off_for(self, thnum, page):
        if self.v3 and page is not None and page < S.PAGE_COUNT and (self.pagemask >> page) & 1:
            return S.OFF_PAGES + page * S.WALL_STRIDE
        if thnum is not None and thnum < S.THEME_COUNT and (self.wallmask >> thnum) & 1:
            return (S.OFF_WALL + thnum * S.WALL_STRIDE) if self.v2 else S.OFF_WALL
        return 0

    # sdt_sync_wall
    def sync(self):
        off = self.off_for(self.cur, self.page)
        if off == self.wall_src and (off == 0 or self.wall_ready):
            return
        self.wall_src = 0
        self.wall_ready = False
        if not off:
            return
        self.wall_src = off
        self.wall_ready = True
        self.loads += 1

    # sdtheme_load
    def load(self, thnum):
        self.cur = thnum
        self.page = None
        self.wall_src = 0
        self.wall_ready = False
        self.sync()

    # sdtheme_apply_theme
    def apply_theme(self, thnum):
        if thnum == self.cur:
            want = self.off_for(thnum, self.page)
            if want == self.wall_src and (want == 0 or self.wall_ready):
                return False
            self.sync()
            return True
        self.cur = thnum
        self.sync()
        return True

    # sdtheme_set_page
    def set_page(self, page):
        if page == self.page:
            return False
        self.page = page
        self.sync()
        return True

    # sdtheme_invalidate + sdtheme_poll
    def invalidate(self):
        self.wall_src = 0
        self.wall_ready = False

    def shown(self):
        """当前缓存里那段壁纸的字节（None = 纯色）。"""
        if not self.wall_ready or not self.wall_src:
            return None
        return self.data[self.wall_src:self.wall_src + S.WALL_STRIDE]


# ---------------------------------------------------------------- 造文件
def solid(rgb):
    return Image.new("RGB", (S.WALL_W, S.WALL_H), rgb)


def make(kind):
    """kind: none / shared / six / pages"""
    d = sdedit.Doc()
    cols = [(200, 30, 30), (220, 120, 20), (30, 150, 70),
            (30, 80, 170), (140, 50, 160), (20, 140, 150)]
    if kind == "shared":
        for i in range(6):
            d.wall_rgb[i] = solid(cols[0])
            d.wall_dirty[i] = True
    elif kind == "six":
        for i in range(6):
            d.wall_rgb[i] = solid(cols[i])
            d.wall_dirty[i] = True
    elif kind == "pages":
        for i in range(6):
            d.wall_rgb[i] = solid(cols[i])
            d.wall_dirty[i] = True
        for p, c in ((0, (10, 30, 90)), (3, (80, 20, 80))):
            d.page_rgb[p] = solid(c)
            d.page_dirty[p] = True
    return d, d.to_bytes()


def near(a, b, tol=24):
    if a is None or b is None:
        return a is b
    return sum(abs(a[i] - b[i]) for i in range(3)) <= tol


def px(buf, x=120, y=72):
    """从一段壁纸（34560 索引 + 220 调色板）取一个像素的 RGB。

    索引值是调色板槽号（20..239），所以走 sdedit 里那份转换，别自己按 0 起算。
    """
    pal = list(struct.unpack_from("<%dH" % S.WALLPAL_N, buf, S.WALL_BYTES))
    return sdedit._wall_to_rgb(buf[:S.WALL_BYTES], pal).getpixel((x, y))


# ---------------------------------------------------------------- 检查
def check_consts(fw):
    print("== 固件常量 vs 编辑器常量 ==")
    pairs = [
        ("SDT_ICON_STRIDE", S.ICON_STRIDE), ("SDT_OFF_ICONS", S.OFF_ICONS),
        ("SDT_OFF_WALL", S.OFF_WALL), ("SDT_WALL_STRIDE", S.WALL_STRIDE),
        ("SDT_FILE_SIZE", S.FILE_SIZE), ("SDT_FILE_SIZE_V2", S.FILE_SIZE_V2),
        ("SDT_OFF_PAGES", S.OFF_PAGES), ("SDT_FILE_SIZE_V3", S.FILE_SIZE_V3),
        ("SDT_ICONS_ONLY_SIZE", S.ICONS_ONLY_SIZE),
        ("SDT_NPAGE", S.PAGE_COUNT), ("SDT_FLAG_PAGE_BASE", S.FLAG_PAGE_BASE),
        ("SDT_WALL_BYTES", S.WALL_BYTES), ("SDT_WALLPALN", S.WALLPAL_N),
    ]
    for name, expect in pairs:
        ok(fw.get(name) == expect, "%s = %s（编辑器 %s）" % (name, fw.get(name), expect))
    ok(fw.get("SDT_VERSION_V3") == 3, "固件支持版本 3")


def check_layouts(fw):
    print("== 四种布局的文件结构与版本 ==")
    d, raw = make("none")
    r = S.parse(raw)
    ok(len(raw) == fw["SDT_ICONS_ONLY_SIZE"] and r["version"] == 1, "无壁纸 → 26,680 / v1")
    ok(r["page_walls"] == [None] * 7, "无壁纸时没有页面图")

    d, raw = make("shared")
    r = S.parse(raw)
    ok(len(raw) == fw["SDT_FILE_SIZE"] and r["version"] == 1, "6 套同图 → 61,680 / v1")
    ok(all(w is not None for w in r["walls"]), "v1 解析成 6 套都有这张")

    d, raw = make("six")
    r = S.parse(raw)
    ok(len(raw) == fw["SDT_FILE_SIZE_V2"] and r["version"] == 2, "6 套不同 → 236,680 / v2")

    d, raw = make("pages")
    r = S.parse(raw)
    ok(len(raw) == fw["SDT_FILE_SIZE_V3"] and r["version"] == 3, "带页面图 → 481,680 / v3")
    ok((r["flags"] >> S.FLAG_PAGE_BASE) & 0x7F == 0x09, "页面掩码只标了 0、3")


def check_state_machine(fw):
    print("== 固件装载状态机（复刻 sdtheme.c）==")
    d, raw = make("pages")
    sim = FwSim(raw)

    # 开机：第 1 套，页面未知 → 用套图
    sim.load(0)
    buf = sim.shown()
    ok(near(px(buf), (200, 30, 30)), "开机（页未知）显示第 1 套的套图")
    ok(sim.loads == 1, "开机只读 1 次")

    # 切到最近页（有页面图）→ 换成页面图
    loads0 = sim.loads
    sim.set_page(0)
    ok(near(px(sim.shown()), (10, 30, 90)), "最近页显示页面图")
    ok(sim.loads == loads0 + 1, "切到有图的页面读 1 次")

    # 切到文件页（无页面图）→ 回落套图
    loads0 = sim.loads
    sim.set_page(1)
    ok(near(px(sim.shown()), (200, 30, 30)), "文件页回落到第 1 套")

    # 切主题：文件页跟着变
    loads0 = sim.loads
    sim.apply_theme(1)
    ok(near(px(sim.shown()), (220, 120, 20)), "文件页切到第 2 套 → 变橙")
    ok(sim.loads == loads0 + 1, "切主题读 1 次")

    # 切回最近页 → 页面图（不随套变）
    sim.set_page(0)
    ok(near(px(sim.shown()), (10, 30, 90)), "最近页仍是页面图（不随套变）")

    # 在最近页切主题 → 不该读卡
    loads0 = sim.loads
    sim.apply_theme(2)
    ok(near(px(sim.shown()), (10, 30, 90)), "最近页切主题，画面不变")
    ok(sim.loads == loads0, "最近页切主题不读卡")

    # 切到设置页（有页面图）
    sim.set_page(3)
    ok(near(px(sim.shown()), (80, 20, 80)), "设置页显示自己的图")

    # 切到工具页（无页面图）→ 回落到第 3 套
    sim.set_page(5)
    ok(near(px(sim.shown()), (30, 150, 70)), "工具页回落到第 3 套（绿）")

    # 写 NOR 后缓存失效 → poll 重读当前套（sdtheme_poll 的等价逻辑）
    sim.invalidate()
    ok(sim.shown() is None, "失效后不画旧图")
    loads0 = sim.loads
    cur = sim.cur
    sim.cur = None
    sim.cur = cur
    sim.sync()
    ok(sim.loads == loads0 + 1, "失效后重读 1 次")
    ok(near(px(sim.shown()), (30, 150, 70)), "重读后恢复工具页应有的图")


def check_all_combos(fw):
    print("== 7 页 × 6 套全组合：页面图优先 / 否则套图 ==")
    d, raw = make("pages")
    sim = FwSim(raw)
    bad = []
    for th in range(6):
        for pg in range(7):
            sim.cur, sim.page = th, pg
            sim.wall_src, sim.wall_ready = 0, False
            sim.sync()
            off = sim.off_for(th, pg)
            got = sim.shown()
            want = raw[off:off + S.WALL_STRIDE] if off else None
            if (got is None) != (want is None) or (got is not None and got != want):
                bad.append((th, pg))
    ok(not bad, "42 种组合全部命中正确段落" + ("" if not bad else "（错在 %s）" % bad[:4]))


def check_files(fw):
    print("== 发布产物 ==")
    for name, want in (("SuperFW-Chis-SDTheme.fw", 1671680),
                       ("SuperFW-Chis-SDTheme-Diag.fw", 1671680),
                       ("theme-test.bin", 481680)):
        p = os.path.join(ROOT, "dist", "v0.21", name)
        if os.path.exists(p):
            ok(os.path.getsize(p) == want, "%s 大小 %d" % (name, os.path.getsize(p)))
        else:
            ok(False, "%s 不存在" % name)
    exe = os.path.join(ROOT, "dist", "v0.21", "SuperFW-SD-Theme-Editor-v0.21.exe")
    ok(os.path.exists(exe) and os.path.getsize(exe) > 10 * 1024 * 1024, "编辑器 exe 存在")


def main():
    fw = fw_defines()
    check_consts(fw)
    check_layouts(fw)
    check_state_machine(fw)
    check_all_combos(fw)
    check_files(fw)

    print()
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for m in FAIL:
            print("  -", m)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
