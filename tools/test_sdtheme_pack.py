# -*- coding: utf-8 -*-
"""SD 卡主题文件（/.superfw/theme.bin）打包自检。

做法是**往返验证**：拿一份已经过固件侧核对的文件 → 拆开 → 重新打包 →
必须逐字节一模一样。这样能确认 app/sdtheme.py 的偏移/字节序/补齐都对得上
fw/src/sdtheme.h，而不是"看起来差不多"。

用法：python tools/test_sdtheme_pack.py [theme.bin] [theme-icons-only.bin]
      不带参数时用 out/sdtheme/ 下现有的两份。
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

# 「固件侧核对过」的样本放在哪。2026-10-20 整理目录后挪进了 archive，
# 但 out/sdtheme 和发布目录也认，谁在就用谁。
SAMPLE_DIRS = [
    os.path.join(ROOT, "archive", "2026-10-20", "debug", "misc", "sdtheme"),
    os.path.join(ROOT, "out", "sdtheme"),
    os.path.join(ROOT, "dist", "v0.21"),
]


def sample(name):
    for d in SAMPLE_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def _synthetic_icons_only():
    """样本都找不到时自造一份最小合法文件，保证脚本能跑（仅作冒烟）。"""
    return S.pack([("#FF0000",) * 6], [(bytes(S.ICON_BYTES), [0] * S.ICON_PAL_N)], None, None)

import sdtheme as S                       # noqa: E402

FAIL = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


def _fw_defines():
    """把 fw/src/sdtheme.c 里的 #define SDT_* 逐个求值（含算术表达式）。"""
    src = open(os.path.join(ROOT, "fw", "src", "sdtheme.c"), encoding="utf-8").read()
    vals = {}
    for line in src.splitlines():
        s = line.strip()
        if not s.startswith("#define SDT_"):
            continue
        s = s[len("#define "):]
        # 去掉行尾注释
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


def check_offsets():
    print("== 常量与 fw/src/sdtheme.c 对齐 ==")
    fw = _fw_defines()
    pairs = [
        ("SDT_OFF_COLORS", S.OFF_COLORS),
        ("SDT_OFF_ICONS", S.OFF_ICONS),
        ("SDT_OFF_WALL", S.OFF_WALL),
        ("SDT_OFF_WALLPAL", S.OFF_WALLPAL),
        ("SDT_FILE_SIZE", S.FILE_SIZE),
        ("SDT_ICON_STRIDE", S.ICON_STRIDE),
        ("SDT_NTHEME", S.THEME_COUNT),
        ("SDT_ICON_BYTES", S.ICON_BYTES),
        ("SDT_ICON_PALN", S.ICON_PAL_N),
        ("SDT_WALLPALN", S.WALLPAL_N),
        ("SDT_WALL_BYTES", S.WALL_BYTES),
        ("SDT_VERSION", S.VERSION),
        ("SDT_VERSION_V2", S.VERSION_V2),
        ("SDT_WALL_STRIDE", S.WALL_STRIDE),
        ("SDT_FILE_SIZE_V2", S.FILE_SIZE_V2),
    ]
    for name, val in pairs:
        ok(fw.get(name) == val, "%s = %s（固件 %s）" % (name, hex(val), fw.get(name)))
    ok(fw.get("SDT_PATH") == "/.superfw/theme.bin",
       "SDT_PATH = %r" % fw.get("SDT_PATH"))
    ok(fw.get("SDT_MAGIC") == S.MAGIC.decode(),
       "SDT_MAGIC = %r" % fw.get("SDT_MAGIC"))


def roundtrip(path):
    name = os.path.basename(path)
    print("== 往返：%s ==" % name)
    if not os.path.exists(path):
        print("  - 跳过（文件不存在）")
        return
    raw = open(path, "rb").read()
    info = S.parse(raw)
    ok(info["version"] == S.VERSION, "版本 %d" % info["version"])
    ok(len(raw) == (S.FILE_SIZE if info["has_wall"] else S.ICONS_ONLY_SIZE),
       "长度 %d（带壁纸=%s）" % (len(raw), info["has_wall"]))

    repack = S.pack(info["pals"], info["icons"],
                    info["wall"], info["wall_pal"])
    ok(repack == raw, "重新打包与原文件逐字节一致")

    # 逐段核对，失败时能立刻看出是哪一段
    for tag, a, b in (("配色", S.OFF_COLORS, S.OFF_ICONS),
                      ("图标", S.OFF_ICONS, S.OFF_WALL)):
        ok(raw[a:b] == repack[a:b], "%s段一致（0x%X..0x%X）" % (tag, a, b))
    if info["has_wall"]:
        ok(raw[S.OFF_WALL:S.OFF_WALLPAL] == repack[S.OFF_WALL:S.OFF_WALLPAL],
           "壁纸段一致")
        ok(raw[S.OFF_WALLPAL:] == repack[S.OFF_WALLPAL:], "壁纸调色板一致")

    # 标志位/长度字段本身也要对
    ver, flags, size = struct.unpack_from("<HHI", raw, 8)
    ok(size == len(raw), "头部长度字段 = 实际长度")
    ok(bool(flags & S.FLAG_WALL) == info["has_wall"], "壁纸标志位与实际内容一致")


def check_v2():
    """v2（每套一张壁纸）的往返：6 张各不相同的图必须各回各的位置。"""
    print("== 往返：v2 每套一张壁纸 ==")
    src = sample("theme-6wall.bin") or sample("theme.bin")
    if not src:
        print("  - 跳过（没有 v2 样本）")
        return
    info = S.parse(open(src, "rb").read())

    # 造 6 张互不相同、又能一眼看出是哪一套的假壁纸
    walls, pals = [], []
    for i in range(S.THEME_COUNT):
        w = bytearray(S.WALL_BYTES)
        w[0] = 20 + i                       # 第 0 个像素的索引就带套号
        w[1] = 0xA5 ^ i
        w[-1] = 0x5A + i
        walls.append(bytes(w))
        p = list(range(S.WALLPAL_N))
        p[0] = 0x1000 + i                   # 调色板也带套号
        pals.append(p)

    raw = S.pack(info["pals"], info["icons"], walls=walls, wall_pals=pals)
    ok(len(raw) == S.FILE_SIZE_V2, "长度 %d（应为 %d）" % (len(raw), S.FILE_SIZE_V2))
    ver, flags, size = struct.unpack_from("<HHI", raw, 8)
    ok(ver == S.VERSION_V2, "版本字段 = %d" % ver)
    ok(size == len(raw), "头部长度 = 实际长度")
    ok(flags & S.FLAG_WALL, "FLAG_WALL 置位")

    got = S.parse(raw)
    ok(got["version"] == S.VERSION_V2, "解出来是 v2")
    for i in range(S.THEME_COUNT):
        ok(bool((flags >> (1 + i)) & 1), "第 %d 套的壁纸位 = 1" % (i + 1))
        ok(got["walls"][i] == walls[i], "第 %d 套索引逐字节一致" % (i + 1))
        ok(got["wall_pals"][i] == pals[i], "第 %d 套调色板一致" % (i + 1))
    # 段偏移必须落在 0x6838 + i*35000
    for i in range(S.THEME_COUNT):
        o = S.OFF_WALL + i * S.WALL_STRIDE
        ok(raw[o] == w0of(i), "第 %d 套段首字节在 0x%X" % (i + 1, o))

    repack = S.pack(got["pals"], got["icons"],
                    walls=got["walls"], wall_pals=got["wall_pals"])
    ok(repack == raw, "v2 重新打包逐字节一致")

    # 只给其中 2 套 → 仍是 v2，缺的套是 None
    part_w = [walls[0], None, None, walls[3], None, None]
    part_p = [pals[0], None, None, pals[3], None, None]
    raw2 = S.pack(info["pals"], info["icons"], walls=part_w, wall_pals=part_p)
    ok(len(raw2) == S.FILE_SIZE_V2, "只有 2 张时长度 %d" % len(raw2))
    g2 = S.parse(raw2)
    ok(g2["version"] == S.VERSION_V2, "只有 2 张时是 v2")
    ok([i for i in range(6) if g2["walls"][i] is not None] == [0, 3],
       "有壁纸的是第 1、4 套（实际 %s）"
       % [i + 1 for i in range(6) if g2["walls"][i] is not None])
    ok(g2["walls"][0] == walls[0] and g2["walls"][3] == walls[3],
       "第 1、4 套的图没串位")

    # 6 套同一张 → 退回 v1（老固件也能读）
    same = S.pack(info["pals"], info["icons"],
                  walls=[walls[2]] * 6, wall_pals=[pals[2]] * 6)
    ok(len(same) == S.FILE_SIZE, "6 套同图时退回 v1（%d 字节）" % len(same))
    ok(struct.unpack_from("<H", same, 8)[0] == S.VERSION, "版本字段回到 1")
    g3 = S.parse(same)
    ok(all(g3["walls"][i] == walls[2] for i in range(6)), "v1 读出来 6 套都有这张图")

    # 一张都没有 → v1 无壁纸
    none = S.pack(info["pals"], info["icons"], walls=[None] * 6)
    ok(len(none) == S.ICONS_ONLY_SIZE, "无壁纸时 %d 字节" % len(none))
    ok(struct.unpack_from("<H", none, 8)[0] == S.VERSION, "无壁纸 = v1")


def w0of(i):
    """假壁纸第 i 套的首字节。"""
    return 20 + i


def check_edge():
    print("== 边界：少于 6 套 → 复制末套 ==")
    p = sample("theme-icons-only.bin")
    raw = open(p, "rb").read() if p else _synthetic_icons_only()
    info = S.parse(raw)
    one = S.pack([("#FF0000",) * 6], [info["icons"][0]], None, None)
    got = S.parse(one)
    ok(len(one) == S.ICONS_ONLY_SIZE, "只有一套时大小 %d" % len(one))
    ok(all(got["icons"][i] == got["icons"][0] for i in range(6)), "6 套图标都等于第 1 套")
    ok(got["pals"][4][1] == (255, 0, 0), "第 5 套配色 = 给的红色")

    print("== 边界：坏输入要报错 ==")
    for desc, fn in (
        ("魔数不对", lambda: S.parse(b"XXXXXXXX" + raw[8:])),
        ("长度被改大", lambda: S.parse(raw[:100])),
    ):
        try:
            fn()
        except ValueError as e:
            ok(True, "%s → ValueError(%s)" % (desc, str(e)[:34]))
        else:
            ok(False, "%s 居然没报错" % desc)


def main():
    if sys.argv[1:]:
        paths = sys.argv[1:]
    else:
        paths = [p for p in (sample("theme.bin"), sample("theme-icons-only.bin")) if p]
        if not paths:
            print("  没找到固件侧样本，改用自造的冒烟样本")
            paths = []
    check_offsets()
    for p in paths:
        roundtrip(p)
    if not paths:
        print("  （跳过往返验证：没有可用的固件侧样本）")
    check_v2()
    check_edge()

    print()
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
