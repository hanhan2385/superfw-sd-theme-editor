# -*- coding: utf-8 -*-
"""SD 卡主题「只读一套」装载逻辑自检。

v0.20 起固件不再开机把 6 套图标 + 6 张壁纸全搬进内存，而是只读当前这一套，
切到别的套时按 `SDT_OFF_ICONS + set*SDT_ICON_STRIDE` 和
`SDT_OFF_WALL + set*SDT_WALL_STRIDE` 现取。这个脚本按固件里那套偏移去
theme.bin 里取数，验证「切到第 n 套拿到的就是第 n 套」，防止错位一格。

顺带检查缓存布局（0x0000 头 / 0x0058 图标 / 0x2000 壁纸）不重叠、不越界。

用法：python tools/test_sdtheme_lazy.py
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

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


def check_cache_layout(fw):
    print("== 缓存布局（只放一套）==")
    hdr, icons, wall = fw["SDT_C_HDR"], fw["SDT_C_ICONS"], fw["SDT_C_WALL"]
    size = fw["SDT_C_SIZE"]
    stride, wstride = fw["SDT_ICON_STRIDE"], fw["SDT_WALL_STRIDE"]

    ok(hdr == 0, "文件头在缓存起点")
    ok(icons >= hdr + fw["SDT_OFF_ICONS"], "图标段没有压到文件头（%d >= %d + %d）"
       % (icons, hdr, fw["SDT_OFF_ICONS"]))
    ok(wall >= icons + stride, "壁纸段没有压到图标段（%d >= %d + %d）"
       % (wall, icons, stride))
    ok(wall + wstride <= size, "壁纸段没越出缓存（%d + %d <= %d）"
       % (wall, wstride, size))
    ok(size <= 64 * 1024, "缓存 %d 字节，没到 64KB" % size)
    ok(fw["SDT_OFF_ICONS"] + 6 * stride == fw["SDT_OFF_WALL"],
       "6 套图标刚好排到壁纸块起点")


def _wallmask(fw, ver, flags):
    """和 sdtheme.c::sdtheme_load() 里解析 flags 的写法保持一致。"""
    if ver >= fw["SDT_VERSION_V2"]:
        return (flags >> 1) & 0x3F
    return 0x3F if (flags & 1) else 0


def _pagemask(fw, ver, flags):
    if ver < fw.get("SDT_VERSION_V3", 3):
        return 0
    return (flags >> fw["SDT_FLAG_PAGE_BASE"]) & 0x7F


def _fetch(fw, data, ver, mask, setn, page=None, pmask=0):
    """模拟 sdt_fetch_icons + sdt_wall_off_for：取第 setn 套的图标段，
    以及「当前套 + 当前页」该显示的那一段壁纸。"""
    o = fw["SDT_OFF_ICONS"] + setn * fw["SDT_ICON_STRIDE"]
    icon = data[o:o + fw["SDT_ICON_STRIDE"]]

    wo = None
    if pmask and page is not None and ((pmask >> page) & 1):
        wo = fw["SDT_OFF_PAGES"] + page * fw["SDT_WALL_STRIDE"]
    elif (mask >> setn) & 1:
        if ver >= fw["SDT_VERSION_V2"]:
            wo = fw["SDT_OFF_WALL"] + setn * fw["SDT_WALL_STRIDE"]
        else:
            wo = fw["SDT_OFF_WALL"]
    wall = data[wo:wo + fw["SDT_WALL_STRIDE"]] if wo is not None else None
    return icon, wall


def make_six_distinct():
    """6 套配色、图标、壁纸全都不同，任何错位一眼能看出来。"""
    pals = [[(16 * i, 8 * i, 255 - 16 * i)] * 6 for i in range(6)]
    icons = [(bytes([i + 1]) * 4352, [i + 1] * 38) for i in range(6)]
    walls = [bytes([0xA0 + i]) * 34560 for i in range(6)]
    wpals = [[0x7C00 + i] * 220 for i in range(6)]
    return pals, icons, walls, wpals


def check_v2(fw):
    print("== v2：6 套各不相同，切到哪套读哪套 ==")
    pals, icons, walls, wpals = make_six_distinct()
    data = S.pack(pals, icons, walls=walls, wall_pals=wpals)
    ver = struct.unpack_from("<H", data, 8)[0]
    flags = struct.unpack_from("<H", data, 10)[0]
    mask = _wallmask(fw, ver, flags)

    ok(ver == fw["SDT_VERSION_V2"], "版本 = 2")
    ok(len(data) == fw["SDT_FILE_SIZE_V2"], "长度 %d = SDT_FILE_SIZE_V2" % len(data))
    ok(mask == 0x3F, "6 套都标了有壁纸（mask=%02x）" % mask)

    for i in range(6):
        icon, wall = _fetch(fw, data, ver, mask, i)
        ok(icon[:4352] == bytes([i + 1]) * 4352, "第 %d 套图标没错位" % i)
        pal = struct.unpack_from("<38H", icon, 4352)
        ok(pal == tuple([i + 1] * 38), "第 %d 套图标调色板跟着走" % i)
        ok(wall is not None and wall[:34560] == bytes([0xA0 + i]) * 34560,
           "第 %d 套壁纸没错位" % i)
        wp = struct.unpack_from("<220H", wall, 34560)
        ok(wp[0] == 0x7C00 + i, "第 %d 套壁纸调色板跟着走" % i)


def check_partial(fw):
    print("== v2：只有 2 套有壁纸 ==")
    pals, icons, walls, wpals = make_six_distinct()
    # 只让第 1、4 套带图，其余 4 套留空
    pick = [None] * 6
    pickp = [None] * 6
    for i in (1, 4):
        pick[i] = walls[i]
        pickp[i] = wpals[i]
    data = S.pack(pals, icons, walls=pick, wall_pals=pickp)
    ver = struct.unpack_from("<H", data, 8)[0]
    flags = struct.unpack_from("<H", data, 10)[0]
    mask = _wallmask(fw, ver, flags)

    ok(ver == fw["SDT_VERSION_V2"], "版本 = 2")
    ok(mask == ((1 << 1) | (1 << 4)), "mask 只标了第 1、4 套（实际 %02x）" % mask)
    for i in range(6):
        icon, wall = _fetch(fw, data, ver, mask, i)
        ok(icon[:4352] == bytes([i + 1]) * 4352, "第 %d 套图标没错位" % i)
        if i in (1, 4):
            ok(wall[:34560] == bytes([0xA0 + i]) * 34560, "第 %d 套壁纸对" % i)
        else:
            ok(wall is None, "第 %d 套没壁纸 → 不读（固件走 BG 纯色）" % i)


def check_v3(fw):
    print("== v3：7 个页面各一张（叠加在 6 套默认图上）==")
    pals, icons, walls, wpals = make_six_distinct()
    pages = [None] * 7
    ppals = [None] * 7
    for p in (0, 3, 6):
        pages[p] = bytes([0x50 + p]) * 34560
        ppals[p] = [0x2000 + p] * 220
    data = S.pack(pals, icons, walls=walls, wall_pals=wpals,
                  page_walls=pages, page_pals=ppals)
    ver = struct.unpack_from("<H", data, 8)[0]
    flags = struct.unpack_from("<H", data, 10)[0]
    mask = _wallmask(fw, ver, flags)
    pmask = _pagemask(fw, ver, flags)

    ok(ver == fw["SDT_VERSION_V3"], "版本 = 3")
    ok(len(data) == fw["SDT_FILE_SIZE_V3"], "长度 %d = SDT_FILE_SIZE_V3" % len(data))
    ok(pmask == ((1 << 0) | (1 << 3) | (1 << 6)),
       "页面掩码只有第 0/3/6 页（实际 %02x）" % pmask)

    # 7 页 × 6 套交叉取：设了页面图的页拿页面图，其余落到该套的默认图
    bad = []
    for p in range(7):
        for s in range(6):
            _icon, wall = _fetch(fw, data, ver, mask, s, p, pmask)
            want = bytes([0x50 + p]) * 34560 if p in (0, 3, 6) else bytes([0xA0 + s]) * 34560
            if wall is None or wall[:34560] != want:
                bad.append((p, s))
    ok(not bad, "7 页 × 6 套交叉取图全对" + ("" if not bad else "（错在 %s）" % bad[:4]))

    _icon, wall = _fetch(fw, data, ver, mask, 2, 0, pmask)
    ok(wall[:34560] == bytes([0x50]) * 34560, "页面图优先于这一套的默认图")

    # 页面图的调色板也跟着走
    wo = fw["SDT_OFF_PAGES"] + 3 * fw["SDT_WALL_STRIDE"]
    wp = struct.unpack_from("<220H", data, wo + 34560)
    ok(wp[0] == 0x2003, "第 3 页的壁纸调色板跟着走")

    print("== v3：只配了页面图，6 套默认图全空 ==")
    data2 = S.pack(pals, icons,
                   page_walls=[bytes([0x77]) * 34560] + [None] * 6,
                   page_pals=[[0x777] * 220] + [None] * 6)
    ver2 = struct.unpack_from("<H", data2, 8)[0]
    flags2 = struct.unpack_from("<H", data2, 10)[0]
    mask2 = _wallmask(fw, ver2, flags2)
    pmask2 = _pagemask(fw, ver2, flags2)
    ok(ver2 == fw["SDT_VERSION_V3"], "还是 v3")
    ok(mask2 == 0, "6 套都没有默认图")
    _i, w0 = _fetch(fw, data2, ver2, mask2, 3, 0, pmask2)
    ok(w0 is not None and w0[:34560] == bytes([0x77]) * 34560, "第 0 页有图")
    _i, w2 = _fetch(fw, data2, ver2, mask2, 3, 2, pmask2)
    ok(w2 is None, "第 2 页没图 → 不读，走 BG 纯色")


def check_v1(fw):
    print("== v1：6 套共用一张壁纸 ==")
    pals, icons, walls, wpals = make_six_distinct()
    data = S.pack(pals, icons, wall=walls[2], wall_pal=wpals[2])
    ver = struct.unpack_from("<H", data, 8)[0]
    flags = struct.unpack_from("<H", data, 10)[0]
    mask = _wallmask(fw, ver, flags)

    ok(ver == fw["SDT_VERSION"], "版本 = 1")
    ok(len(data) == fw["SDT_FILE_SIZE"], "长度 %d = SDT_FILE_SIZE" % len(data))
    ok(mask == 0x3F, "v1 一张图算 6 套都有")
    for i in range(6):
        icon, wall = _fetch(fw, data, ver, mask, i)
        ok(icon[:4352] == bytes([i + 1]) * 4352, "第 %d 套图标没错位" % i)
        ok(wall[:34560] == bytes([0xA0 + 2]) * 34560, "第 %d 套读到的是同一张图" % i)

    print("== v1：没有壁纸 ==")
    data = S.pack(pals, icons)
    ver = struct.unpack_from("<H", data, 8)[0]
    flags = struct.unpack_from("<H", data, 10)[0]
    mask = _wallmask(fw, ver, flags)
    ok(mask == 0, "没有壁纸时 mask = 0，一套都不读")
    icon, wall = _fetch(fw, data, ver, mask, 3)
    ok(wall is None, "不读壁纸")
    ok(icon[:4352] == bytes([4]) * 4352, "图标照样按套取")


def check_header(fw):
    print("== 开机只读文件头 88 字节 ==")
    pals, icons, walls, wpals = make_six_distinct()
    data = S.pack(pals, icons, walls=walls, wall_pals=wpals)
    hdr = data[:fw["SDT_OFF_ICONS"]]
    ok(len(hdr) == 88, "头部 88 字节")
    ok(hdr[:8] == b"SFWTHM01", "魔数")
    ok(fw["SDT_OFF_COLORS"] == 0x10, "配色从 0x10 开始")
    ok(len(hdr) - fw["SDT_OFF_COLORS"] == 72, "6 套配色共 72 字节，全在头部里")


def main():
    fw = _fw_defines()
    check_cache_layout(fw)
    check_header(fw)
    check_v2(fw)
    check_partial(fw)
    check_v3(fw)
    check_v1(fw)

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
