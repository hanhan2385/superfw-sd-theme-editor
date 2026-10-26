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
    # 常量可能写在 .c 或 .h 里（SDT_EXP_* 在头文件，menu.c 也要用），两个都扫
    src = ""
    for fn in ("sdtheme.c", "sdtheme.h"):
        src += open(os.path.join(ROOT, "fw", "src", fn), encoding="utf-8").read() + "\n"
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
        # v5 只是「老布局 + 36 字节扩展色」，布局必须按 base_version 走，
        # 否则 v1+v5 会被当成 v4 去读 42 段掩码（固件里同样用 base）。
        self.file_ver = r["version"]
        self.ver = r.get("base_version", r["version"])
        self.v2 = self.ver >= 2
        self.v3 = self.ver >= 3
        self.v4 = self.ver >= 4
        self.excolors = r.get("extra_cols")
        self.logo = r.get("logo")
        self.pmask = r.get("set_page_mask", 0) if self.v4 else 0
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
        if (self.v4 and thnum is not None and thnum < S.THEME_COUNT
                and page is not None and page < S.PAGE_COUNT):
            bit = thnum * S.PAGE_COUNT + page
            if (self.pmask >> bit) & 1:
                return S.OFF_V4 + bit * S.WALL_STRIDE
        if self.v3 and page is not None and page < S.PAGE_COUNT and (self.pagemask >> page) & 1:
            return S.OFF_PAGES + page * S.WALL_STRIDE
        if thnum is not None and thnum < S.THEME_COUNT and (self.wallmask >> thnum) & 1:
            return (S.OFF_WALL + thnum * S.WALL_STRIDE) if self.v2 else S.OFF_WALL
        return 0

    # sdt_sync_wall
    def excol(self, thnum):
        """等价于固件的 sdt_apply_excolors：v5 才有独立色，否则 None（跟 BG/FG）。"""
        if not self.excolors or thnum is None or thnum >= S.THEME_COUNT:
            return None
        return tuple(self.excolors[thnum])

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
            im = solid(c)
            for s_ in range(6):          # 6 套一致 → 存盘合并回 v3 全局段
                d.sp_rgb[s_][p] = im
                d.sp_dirty[s_][p] = True
    elif kind == "setpages":
        for i in range(6):
            d.wall_rgb[i] = solid(cols[i])
            d.wall_dirty[i] = True
        # 只有第 4 套的「最近」页换一张，其余套必须不受影响
        d.sp_rgb[3][0] = solid((240, 240, 40))
        d.sp_dirty[3][0] = True
    elif kind == "excol":
        # 只给第 3 套的「整屏界面」三个色，别的什么都不动 → 最小文件 + 36
        d.excol[2] = [(10, 20, 30), (240, 30, 30), (200, 200, 250)]
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
        ("SDT_OFF_V4MASK", S.OFF_V4MASK), ("SDT_OFF_V4", S.OFF_V4),
        ("SDT_FILE_SIZE_V4", S.FILE_SIZE_V4), ("SDT_PAGE_SLOTS", S.PAGE_SLOTS),
        ("SDT_ICONS_ONLY_SIZE", S.ICONS_ONLY_SIZE),
        ("SDT_NPAGE", S.PAGE_COUNT), ("SDT_FLAG_PAGE_BASE", S.FLAG_PAGE_BASE),
        ("SDT_WALL_BYTES", S.WALL_BYTES), ("SDT_WALLPALN", S.WALLPAL_N),
        ("SDT_VERSION_V5", S.VERSION_V5), ("SDT_EXCOL_N", S.EXCOL_N),
        ("SDT_EXCOL_BYTES", S.EXCOL_BYTES),
        ("SDT_EXP_LOADBG", 246), ("SDT_EXP_LOADFG", 247), ("SDT_EXP_IGMBG", 245),
        ("SDT_VERSION_V6", S.VERSION_V6), ("SDT_LOGO_IMG", S.LOGO_IMG_BYTES),
        ("SDT_LOGO_BYTES", S.LOGO_BYTES), ("SDT_V6_TAIL", S.V6_TAIL),
    ]
    for name, expect in pairs:
        ok(fw.get(name) == expect, "%s = %s（编辑器 %s）" % (name, fw.get(name), expect))
    ok(fw.get("SDT_VERSION_V3") == 3, "固件支持版本 3")
    ok(fw.get("SDT_VERSION_V4") == 4, "固件支持版本 4")


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

    d, raw = make("setpages")
    r = S.parse(raw)
    ok(len(raw) == fw["SDT_FILE_SIZE_V4"] and r["version"] == 4, "按套页面图 → 1,951,688 / v4")
    ok(r["set_page_mask"] == (1 << S.slot_index(3, 0)), "掩码只有第 4 套第 1 页")
    ok(r["page_walls"] == [None] * 7, "退化成按套存时不再写全局页图")


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


def _latest_dist():
    """dist/v0.21、v0.22… 里取版本号最大的那个，别把版本写死在测试里。"""
    base = os.path.join(ROOT, "dist")
    cands = []
    for n in os.listdir(base):
        if n.startswith("v") and n[1:].replace(".", "").isdigit():
            cands.append((tuple(int(x) for x in n[1:].split(".")), n))
    return os.path.join(base, max(cands)[1]) if cands else None


def _dist_dirs():
    base = os.path.join(ROOT, "dist")
    out = []
    for n in os.listdir(base):
        if n.startswith("v") and n[1:].replace(".", "").isdigit():
            out.append((tuple(int(x) for x in n[1:].split(".")), os.path.join(base, n)))
    return [d for _, d in sorted(out, reverse=True)]


def check_v4_isolated(fw):
    print("== v4：按套页面图与回退 ==")
    d, raw = make("setpages")
    sim = FwSim(raw)
    sim.load(3)
    sim.set_page(0)
    ok(near(px(sim.shown()), (240, 240, 40)), "第 4 套·最近页用它自己的图")
    sim.apply_theme(0)
    ok(near(px(sim.shown()), (200, 30, 30)), "切到第 1 套 → 最近页回落到第 1 套的套图")
    sim.apply_theme(3)
    ok(near(px(sim.shown()), (240, 240, 40)), "切回第 4 套 → 又是它自己的图")
    sim.set_page(1)
    ok(near(px(sim.shown()), (30, 80, 170)), "第 4 套·文件页没有专属图 → 回落到第 4 套套图")


def check_v4_mixed(fw):
    """v4 可以同时有「v3 全局页图」和「v4 按套页图」。

    打开这种文件时不能只看 set_page_walls —— 否则全局页图会被静默丢掉，
    再保存时那些页就回退成套图/纯色了。
    """
    print("== v4：全局页图 + 按套页图共存 ==")
    d = sdedit.Doc()
    g = Image.new("RGB", (S.WALL_W, S.WALL_H), (200, 10, 10))
    for s in range(S.THEME_COUNT):
        d.sp_rgb[s][0] = g
        d.sp_dirty[s][0] = True
    p = Image.new("RGB", (S.WALL_W, S.WALL_H), (10, 200, 10))
    d.sp_rgb[2][1] = p
    d.sp_dirty[2][1] = True

    raw = d.to_bytes()
    r = S.parse(raw)
    ok(r["version"] == 4 and r["page_walls"][0] is not None
       and r["set_page_mask"] == (1 << S.slot_index(2, 1)),
       "生成 v4：全局第 1 页 + 第 3 套第 2 页")
    back = sdedit.Doc.from_bytes(raw)
    ok(all(back.sp_rgb[s][0] is not None for s in range(S.THEME_COUNT)),
       "重新打开：全局页图还在 6 套里")
    ok(back.sp_rgb[2][1] is not None, "重新打开：按套页图还在")
    r2 = S.parse(back.to_bytes())
    ok(r2["page_walls"][0] is not None and
       r2["set_page_mask"] == (1 << S.slot_index(2, 1)),
       "再保存：全局页图 + 按套页图都保留")


def check_v5_excol(fw):
    print("== v5：整屏界面独立色 ==")
    d, raw = make("excol")
    r = S.parse(raw)
    ok(len(raw) == S.ICONS_ONLY_SIZE + S.EXCOL_BYTES and r["version"] == 5,
       "只改三个色 → %d 字节 / 文件写 v5" % len(raw))
    # 关键：v5 不能把基础布局带成 v4，否则固件会去 0x75990 读不存在的掩码
    ok(r["base_version"] == 1, "基础布局仍是 v1")
    ok(len(r["set_page_walls"][0]) == S.PAGE_COUNT and
       all(w is None for row in r["set_page_walls"] for w in row),
       "没有凭空冒出按套页面图")

    sim = FwSim(raw)
    ok(sim.ver == 1 and sim.v2 is False, "FwSim 按 v1 解析（不是 v4）")
    want = ((10, 20, 30), (240, 30, 30), (200, 200, 250))
    got = sim.excol(2)
    near = all(abs(got[j][k] - want[j][k]) <= 8 for j in range(3) for k in range(3))
    ok(near, "第 3 套三个色读回 %s（RGB555 量化误差内）" % (got,))
    c0, c2 = sim.excol(0), sim.excol(2)
    ok(c0 != c2, "切套时整屏界面颜色会跟着变")
    bg0, fg0 = d.pals[0][1], d.pals[0][0]
    ok(c0 == (bg0, fg0, bg0), "没单独设的套仍跟随 BG/FG")

    back = sdedit.Doc.from_bytes(raw)
    ok(back.to_bytes() == raw, "v5 打开再保存逐字节一致")
    ok(back.has_excol(), "文件里确实有自定义色")
    ok(all(v is None for v in back.excol[0]), "第 1 套仍算「跟随」（值等于 BG/FG 就折成 None）")

    # 全部改回跟随 → 掉回 v1，文件少 36 字节
    back.excol[2] = [None] * S.EXCOL_N
    raw2 = back.to_bytes()
    ok(len(raw2) == S.ICONS_ONLY_SIZE and S.parse(raw2)["version"] == 1,
       "全部跟随后退回 v1（%d 字节）" % len(raw2))


def check_v6_logo(fw):
    print("== v6：自定义 logo ==")
    d = sdedit.Doc()
    d.logo_pal = [(255, 255, 255), (0, 128, 255), (0, 0, 0)]
    raw = d.to_bytes()
    r = S.parse(raw)
    ok(len(raw) == S.ICONS_ONLY_SIZE + S.V6_TAIL and r["version"] == 6,
       "只改 logo → %d 字节 / 文件写 v6" % len(raw))
    ok(r["base_version"] == 1, "基础布局仍是 v1（没被 v6 带成 v4）")
    ok(r["logo"] is not None and len(r["logo"][0]) == S.LOGO_IMG_BYTES,
       "logo 索引 %d 字节" % len(r["logo"][0]))

    want = (255, 255, 255), (0, 128, 255), (0, 0, 0)
    got = r["logo"][1]
    near = all(abs(got[j][k] - want[j][k]) <= 8 for j in range(3) for k in range(3))
    ok(near, "logo 三色读回 %s（RGB555 量化误差内）" % (got,))

    sim = FwSim(raw)
    ok(sim.ver == 1 and sim.logo is not None, "FwSim 按 v1 解析且拿到了 logo")

    back = sdedit.Doc.from_bytes(raw)
    ok(back.has_logo(), "读回后仍算「自定义 logo」")
    ok(back.to_bytes() == raw, "v6 打开再保存逐字节一致")
    back.reset_logo()
    ok(not back.has_logo() and back.packed_size() == S.ICONS_ONLY_SIZE,
       "恢复内置后退回 v1（%d 字节）" % back.packed_size())

    # v6 也带 36 字节扩展色，两者能共存
    d2 = sdedit.Doc()
    d2.excol[1] = [(10, 20, 30), None, None]
    d2.logo_pal = [(255, 255, 255), (0, 200, 0), (0, 0, 0)]
    raw2 = d2.to_bytes()
    r2 = S.parse(raw2)
    ok(len(raw2) == S.ICONS_ONLY_SIZE + S.V6_TAIL and r2["version"] == 6,
       "扩展色 + logo 共存 → %d 字节 / v6" % len(raw2))
    ok(r2["extra_cols"][1][0] != r2["extra_cols"][0][0], "扩展色也读得回来")


def check_files(fw):
    print("== 发布产物 ==")
    dirs = _dist_dirs()
    ok(bool(dirs), "找到 dist/v* 目录")
    if not dirs:
        return
    # 只看最新那个发布目录，而且**递归**找：
    # v0.30 起 exe 在 `免解压版/` 子目录里，固件在 `分支固件-*/` 子目录里，
    # 都不在根上了（根上只有说明和 theme.bin）。
    newest = dirs[0]
    print("  最新发布目录", os.path.basename(newest))

    exes, fws, bins = [], [], []
    for dp, _dn, fn in os.walk(newest):
        for x in fn:
            p = os.path.join(dp, x)
            low = x.lower()
            if low.endswith(".exe"):
                exes.append(p)
            elif low.endswith(".fw"):
                fws.append(p)
            elif low.endswith(("-2m.bin", "-2mib.bin")):
                bins.append(p)
    ok(bool(exes), "编辑器 exe 存在（共 %d 个）" % len(exes))
    ok(bool(fws), "固件 .fw 存在（共 %d 个）" % len(fws))
    ok(len(fws) == len(bins), "每个 .fw 都有对应的 2 MiB 整片镜像（%d / %d）"
       % (len(fws), len(bins)))

    for p in sorted(fws):
        var, sz = _fw_header(p)
        real = os.path.getsize(p)
        name = os.path.basename(p)
        # 铁律：头部 fw_size 必须等于文件大小，否则升级器拒收
        ok(sz == real, "%s：fw_size %d == 文件大小 %d" % (name, sz, real))
        ok(var != 0, "%s：变体标识 %08x（0 表示头部没写对）" % (name, var))

    for p in sorted(bins):
        var, sz = _fw_header(p)
        real = os.path.getsize(p)
        name = os.path.basename(p)
        ok(real == 2 * 1024 * 1024, "%s：镜像 %d 字节" % (name, real))
        ok(0 < sz < real, "%s：fw_size %d 落在固件区内" % (name, sz))
        with open(p, "rb") as fh:
            fh.seek(sz)
            tail = fh.read(64)
        ok(all(b == 0xFF for b in tail), "%s：fw_size 之后是 0xFF 填充" % name)

    # 主题包：v1..v6 的定长尺寸 ± 尾巴都算合法
    known = (26680, 61680, 236680, 481680, 1951688)
    for name in os.listdir(newest):
        if not name.lower().endswith(".bin") or name.lower().endswith(("-2m.bin", "-2mib.bin")):
            continue
        sz = os.path.getsize(os.path.join(newest, name))
        ok(sz in known or sz - 36 in known or sz - 259 in known,
           "%s 大小 %d（v1..v6 合法）" % (name, sz))


def _fw_header(path):
    """返回 (0x0C8 变体标识, 0x0CC fw_size)。"""
    with open(path, "rb") as fh:
        h = fh.read(0x100)
    return struct.unpack_from("<I", h, 0xC8)[0], struct.unpack_from("<I", h, 0xCC)[0]


def main():
    fw = fw_defines()
    check_consts(fw)
    check_layouts(fw)
    check_state_machine(fw)
    check_v4_isolated(fw)
    check_v4_mixed(fw)
    check_v5_excol(fw)
    check_v6_logo(fw)
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
