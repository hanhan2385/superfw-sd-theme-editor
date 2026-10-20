# -*- coding: utf-8 -*-
"""把编辑器里调好的皮肤直接打成 SD 卡主题文件 `/.superfw/theme.bin`。

固件侧格式见 `fw/src/sdtheme.c`，三版都支持：

    v1（老格式，一张壁纸 6 套共用）
    0x0000  char[8]      "SFWTHM01"
    0x0008  u16          版本 = 1
    0x000A  u16          标志位（bit0 = 带壁纸）
    0x000C  u32          文件长度
    0x0010  u16[36]      6 套配色（每套 fg, bg, ft, hi, hi_blend, sh）
    0x0058  u8[26592]    6 套图标（每套 4352 精灵 + 38 项调色板，补齐 4432）
    0x6838  u8[34560]    壁纸 240x144 8bpp（可选）
    0xEF38  u16[220]     壁纸调色板（可选，写进调色板槽 20..239）

    v2（每套一张壁纸）
    0x0000..0x6837 和 v1 完全一样，版本号 = 2，
                    标志位 bit(1+i) = 第 i 套有壁纸
    0x6838  u8[6*35000]  每套 34560 索引 + 440 调色板，6 段连排
                         （6 套全放满 = 236,680 字节）

    v3（v2 + 每页一张，即菜单 7 个标签页各自可以覆盖）
    0x0000..0x39CC7 和 v2 完全一样，版本号 = 3，
                    标志位 bit(1+i) = 第 i 套有默认壁纸
                           bit(7+p) = 第 p 个页面有自己的图（p = 0..6）
    0x39CC8 u8[7*35000] 7 个页面各一段，布局同上，连排
                         （全放满 = 481,680 字节）

    v4（v3 + 每套的每一页都能单独配图，42 个槽位）
    0x0000..0x7598F 和 v3 完全一样（含那 7 张全局页面图，留作兜底），版本号 = 4
    0x75990 u32 x2     42 位掩码，bit(set*7+page) = 这一套这一页有自己的图
    0x75998 u8[42*35000] 6 套 × 7 页，固定连排（全量，不管有没有图）
                         （总长 = 1,951,688 字节）
    空槽位照样占 35,000 字节（写 0），所以 v4 是定长文件。

页面的取图规则（固件 sdtheme.c 里也这么写）：
    这一套这一页有自己的图 → 用它的（仅 v4）
    否则这一页有全局图     → 用全局的（v3）
    否则                   → 用当前这一套的默认图
    再没有                 → 内容区用 BG 纯色

`pack()` 会自己挑布局：一张都没有 → 26,680（v1 无壁纸）；6 套一模一样 →
61,680 的 v1 单图；只有全局页面图 → v3；有「某一套某一页」的图 → v4；
其余 → v2。老固件读不懂 v4，所以不碰按套页面图时永远产出 v1/v2/v3。

这条路线不需要打补丁、不需要找空位、不用重算 sha256 —— 换皮肤就是换这个文件。
"""

import struct

import fwbuild

MAGIC = b"SFWTHM01"
VERSION = 1
VERSION_V2 = 2
VERSION_V3 = 3
VERSION_V4 = 4
FLAG_WALL = 0x0001

THEME_COUNT = 6
COLORS_PER_THEME = 6

ICON_BYTES = 4352              # 17 个 16x16 8bpp 精灵
ICON_PAL_N = 38                # 索引 0 = 透明
ICON_STRIDE = 4432             # 4352 + 76 = 4428，补齐到 16 的倍数

OFF_COLORS = 0x10
OFF_ICONS = OFF_COLORS + THEME_COUNT * COLORS_PER_THEME * 2      # 0x58
OFF_WALL = OFF_ICONS + THEME_COUNT * ICON_STRIDE                 # 0x6838

WALL_W, WALL_H = 240, 144
WALL_BYTES = WALL_W * WALL_H                                     # 34560
WALLPAL_N = 220
OFF_WALLPAL = OFF_WALL + WALL_BYTES                              # 0xEF38
WALL_STRIDE = WALL_BYTES + WALLPAL_N * 2                         # 35000
FILE_SIZE = OFF_WALLPAL + WALLPAL_N * 2                          # 0xF0F0 = 61680
FILE_SIZE_V2 = OFF_WALL + THEME_COUNT * WALL_STRIDE              # 0x39CC8 = 236680

# 页面图：紧接在 v2 的 6 段之后，顺序和菜单标签页一致
PAGE_COUNT = 7
FLAG_PAGE_BASE = 7                                               # bit7 + p
OFF_PAGES = FILE_SIZE_V2                                         # 0x39CC8 = 236680
FILE_SIZE_V3 = OFF_PAGES + PAGE_COUNT * WALL_STRIDE              # 0x75990 = 481680

# v4：每套 × 每页的专属图。掩码 42 位 + 42 段定长连排（空槽也占位）
PAGE_SLOTS = THEME_COUNT * PAGE_COUNT                            # 42
OFF_V4MASK = FILE_SIZE_V3                                        # 0x75990 = 481680
OFF_V4 = OFF_V4MASK + 8                                          # 0x75998 = 481688
FILE_SIZE_V4 = OFF_V4 + PAGE_SLOTS * WALL_STRIDE                 # 1,951,688

ICONS_ONLY_SIZE = OFF_WALL                                       # 0x6838 = 26680

# v5：给「整屏界面」单独三个颜色，追加在文件最后（不改动上面任何一个偏移）。
# 每套 3 个 u16：载入画面底、载入进度条、游戏内菜单底。
# 只有真改了这三个色才升 v5，否则文件保持原来的版本和大小。
VERSION_V5 = 5
EXCOL_N = 3                                                      # 每套三个
EXCOL_BYTES = THEME_COUNT * EXCOL_N * 2                          # 36
EXCOL_NAMES = ["载入画面底", "载入进度条", "游戏内菜单底"]

# v6：v5 的 36 字节后面再接一份 logo（31x7 的索引 + 3 色调色板）。
# 主菜单关于页和游戏内菜单都会用它。没有改 logo 就还是 v5 / v1..v4。
VERSION_V6 = 6
LOGO_W, LOGO_H = 31, 7
LOGO_IMG_BYTES = LOGO_W * LOGO_H                                 # 217
LOGO_PAL_N = 3                                                   # 0=白 1=红 2=黑
LOGO_BYTES = LOGO_IMG_BYTES + LOGO_PAL_N * 2                     # 223
V6_TAIL = EXCOL_BYTES + LOGO_BYTES                               # 259

# 老版本（v1..v4）按长度反推基础布局，v5/v6 只是在其后多一截
_BASE_BY_SIZE = {
    ICONS_ONLY_SIZE: VERSION,
    FILE_SIZE: VERSION,
    FILE_SIZE_V2: VERSION_V2,
    FILE_SIZE_V3: VERSION_V3,
    FILE_SIZE_V4: VERSION_V4,
}


def slot_index(set_i, page):
    """(第几套, 第几页) → v4 里 0..41 的槽号。两边的位序都靠它对齐。"""
    return set_i * PAGE_COUNT + page

# 菜单标签页（顺序必须和 fw/src/menu.c 的 MENUTAB_* 一致）
PAGE_NAMES = ["最近", "文件", "烧录", "设置", "页面设置", "工具", "关于"]


def color555(c):
    """'#RRGGBB' / 'RRGGBB' / (r,g,b) → GBA RGB555。"""
    if isinstance(c, str):
        v = c.lstrip("#")
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    else:
        r, g, b = [_clamp255(x) for x in c[:3]]
    return fwbuild.rgb2gba(r, g, b)


def _clamp255(v):
    v = int(v)
    return 0 if v < 0 else (255 if v > 255 else v)


def rgb555(v):
    """GBA RGB555 → (r,g,b)，位复制到 8 位（与 fwbake.bgr555_to_rgb 一致）。"""
    r = (v & 0x1F) << 3
    g = ((v >> 5) & 0x1F) << 3
    b = ((v >> 10) & 0x1F) << 3
    return (r | r >> 5, g | g >> 5, b | b >> 5)


def _norm_icon_sets(icon_sets):
    """校正到 6 套；某套不合法就退化成第 0 套。"""
    sets = list(icon_sets or [])
    if not sets:
        raise ValueError("没有任何一套图标")
    norm = []
    for s in range(THEME_COUNT):
        src = sets[min(s, len(sets) - 1)]
        sprite, pal = src[0], src[1]
        sprite = bytes(sprite)
        if len(sprite) != ICON_BYTES:
            raise ValueError("第 %d 套图标是 %d 字节，应该是 %d"
                             % (s + 1, len(sprite), ICON_BYTES))
        pal = [int(v) & 0xFFFF for v in pal][:ICON_PAL_N]
        pal += [0] * (ICON_PAL_N - len(pal))
        blk = bytearray(ICON_STRIDE)
        blk[:ICON_BYTES] = sprite
        struct.pack_into("<%dH" % ICON_PAL_N, blk, ICON_BYTES, *pal)
        norm.append(bytes(blk))
    return norm


def _norm_wall_list(items, pals, count, what):
    """校正到 count 项；每项 (索引字节 or None, [220 个 RGB555] or None)。"""
    items = list(items or [])
    pals = list(pals or [])
    while len(items) < count:
        items.append(None)
    while len(pals) < count:
        pals.append(None)

    norm = []
    for i in range(count):
        w = items[i]
        p = pals[i]
        if w is None:
            norm.append((None, None))
            continue
        w = bytes(w)
        if len(w) != WALL_BYTES:
            raise ValueError("%s第 %d 项是 %d 字节，应该是 %d（240x144）"
                             % (what, i + 1, len(w), WALL_BYTES))
        p = [int(v) & 0xFFFF for v in (p or [])][:WALLPAL_N]
        p += [0] * (WALLPAL_N - len(p))
        norm.append((w, p))
    return norm


def _norm_walls(walls, wall_pals):
    return _norm_wall_list(walls, wall_pals, THEME_COUNT, "每套的壁纸")


def _norm_pages(page_walls, page_pals):
    return _norm_wall_list(page_walls, page_pals, PAGE_COUNT, "每页的壁纸")


def _flatten_slots(x, n_outer, n_inner):
    """接受 6×7 嵌套或 42 平铺，统一成一维 42。"""
    if x is None:
        return None
    x = list(x)
    if len(x) == n_outer and all(isinstance(v, (list, tuple)) and len(v) == n_inner
                                 for v in x):
        return [v for row in x for v in row]
    return x


def pack(pals, icon_sets, wall=None, wall_pal=None, walls=None, wall_pals=None,
         page_walls=None, page_pals=None,
         set_page_walls=None, set_page_pals=None, extra_cols=None,
         logo=None):
    """打包成 theme.bin 字节串。

    pals       : 6 套 × 6 色（'#RRGGBB' 或 (r,g,b)）
    icon_sets  : 6 套 (4352 字节精灵, [38 个 RGB555])；少于 6 套时末套复制
    wall       : 老接口 —— 单张壁纸 34560 字节索引（None = 不打壁纸），6 套共用
    wall_pal   : 老接口 —— 220 个 RGB555
    walls      : 每套一张 —— 6 张壁纸索引（某个为 None = 那一套用 BG 纯色）
    wall_pals  : 每套一张 —— 6 × 220 个 RGB555
    page_walls : 每页一张 —— 7 张壁纸索引（None = 这一页跟随当前套的默认图）
    page_pals  : 每页一张 —— 7 × 220 个 RGB555
    set_page_walls : 每套每页一张 —— 6×7 嵌套或 42 平铺（None = 不单独设）
    set_page_pals  : 同上，配对的 220 色
    extra_cols     : 每套 3 个「整屏界面」颜色（载入底、进度条、游戏内菜单底）。
                     某个为 None = 跟随这一套的 BG/FG；全都没设就不升 v5。
    logo           : (217 字节索引, [3 个 RGB555 颜色])；给了就升 v6（+259 字节）

    优先级：walls/wall_pals > wall/wall_pal；page_* 与 set_page_* 独立叠加。
    布局自动挑：有「某一套某一页」的图 → v4；只有全局页面图 → v3；
    什么都没 → v1 无壁纸；6 套同一张 → v1 单图；其余 → v2。
    """
    pals = [list(p) for p in (pals or [])]
    if not pals:
        raise ValueError("没有任何配色")
    while len(pals) < THEME_COUNT:
        pals.append(list(pals[-1]))
    if len(pals) > THEME_COUNT:
        pals = pals[:THEME_COUNT]
    for i, p in enumerate(pals):
        if len(p) != COLORS_PER_THEME:
            raise ValueError("第 %d 套配色有 %d 个值，应该是 %d"
                             % (i + 1, len(p), COLORS_PER_THEME))

    icons = _norm_icon_sets(icon_sets)

    if walls is None and wall is not None:
        walls = [wall] * THEME_COUNT
        wall_pals = [wall_pal] * THEME_COUNT
    sets = _norm_walls(walls, wall_pals)
    pages = _norm_pages(page_walls, page_pals)
    slots = _norm_wall_list(_flatten_slots(set_page_walls, THEME_COUNT, PAGE_COUNT),
                            _flatten_slots(set_page_pals, THEME_COUNT, PAGE_COUNT),
                            PAGE_SLOTS, "每套每页的壁纸")
    have = [s[0] is not None for s in sets]
    have_p = [p[0] is not None for p in pages]
    have_s = [s[0] is not None for s in slots]

    if any(have_s):
        ver, size = VERSION_V4, FILE_SIZE_V4
        single = False
    elif any(have_p):
        ver, size = VERSION_V3, FILE_SIZE_V3
        single = False
    elif not any(have):
        ver, size = VERSION, ICONS_ONLY_SIZE
        single = True
    elif all(have) and all(sets[i][0] == sets[0][0] and sets[i][1] == sets[0][1]
                           for i in range(1, THEME_COUNT)):
        ver, size = VERSION, FILE_SIZE          # 6 套同一张 → 还是发 v1，省 175KB
        single = True
    else:
        ver, size = VERSION_V2, FILE_SIZE_V2
        single = False

    base_ver = ver

    # 扩展色：全部 None 就不写（保持原版本，老固件也能读）
    excols = None
    if extra_cols is not None:
        rows, any_set = [], False
        for i in range(THEME_COUNT):
            row = list(extra_cols[i]) if i < len(extra_cols) else []
            row = (row + [None] * EXCOL_N)[:EXCOL_N]
            if any(v is not None for v in row):
                any_set = True
            dflt = (pals[i][1], pals[i][0], pals[i][1])   # BG, FG, BG
            rows.append([v if v is not None else dflt[j] for j, v in enumerate(row)])
        if any_set:
            excols = rows

    # logo：给了就升 v6；v6 里那 36 字节扩展色一定会写（没设就填默认值）
    logo_img, logo_pal = None, None
    if logo is not None:
        logo_img = bytes(logo[0])
        logo_pal = list(logo[1])
        if len(logo_img) != LOGO_IMG_BYTES:
            raise ValueError("logo 索引应为 %d 字节，实际 %d"
                             % (LOGO_IMG_BYTES, len(logo_img)))
        if len(logo_pal) != LOGO_PAL_N:
            raise ValueError("logo 调色板应有 %d 个颜色，实际 %d"
                             % (LOGO_PAL_N, len(logo_pal)))
        if excols is None:
            excols = [[pals[i][1], pals[i][0], pals[i][1]] for i in range(THEME_COUNT)]
        size += V6_TAIL
    elif excols is not None:
        size += EXCOL_BYTES

    out = bytearray(size)
    out[0:8] = MAGIC
    flags = 0
    if any(have) or any(have_p) or any(have_s):
        flags |= FLAG_WALL
    if base_ver >= VERSION_V2:
        for i in range(THEME_COUNT):
            if have[i]:
                flags |= 1 << (1 + i)
    if base_ver >= VERSION_V3:
        for i in range(PAGE_COUNT):
            if have_p[i]:
                flags |= 1 << (FLAG_PAGE_BASE + i)
    ver_out = VERSION_V6 if logo_img is not None else (VERSION_V5 if excols else base_ver)
    struct.pack_into("<HHI", out, 8, ver_out, flags, size)

    for i, p in enumerate(pals):
        struct.pack_into("<6H", out, OFF_COLORS + i * 12,
                         *[color555(c) for c in p])

    for i, blk in enumerate(icons):
        o = OFF_ICONS + i * ICON_STRIDE
        out[o:o + ICON_STRIDE] = blk

    def put_wall(o, item):
        w, wp = item
        out[o:o + WALL_BYTES] = w
        struct.pack_into("<%dH" % WALLPAL_N, out, o + WALL_BYTES, *wp)

    if single and any(have):
        if base_ver >= VERSION_V2:
            put_wall(OFF_WALL, sets[0])
        else:
            w, wp = sets[0]
            out[OFF_WALL:OFF_WALL + WALL_BYTES] = w
            struct.pack_into("<%dH" % WALLPAL_N, out, OFF_WALLPAL, *wp)
    elif not single:
        for i, item in enumerate(sets):
            if item[0] is not None:
                put_wall(OFF_WALL + i * WALL_STRIDE, item)

    if base_ver >= VERSION_V3:
        for i, item in enumerate(pages):
            if item[0] is not None:
                put_wall(OFF_PAGES + i * WALL_STRIDE, item)

    if base_ver >= VERSION_V4:
        mask = 0
        for i, ok in enumerate(have_s):
            if ok:
                mask |= 1 << i
        struct.pack_into("<II", out, OFF_V4MASK,
                         mask & 0xFFFFFFFF, (mask >> 32) & 0xFFFFFFFF)
        for i, item in enumerate(slots):
            if item[0] is not None:
                put_wall(OFF_V4 + i * WALL_STRIDE, item)

    if excols is not None or logo_img is not None:
        tail = size - (V6_TAIL if logo_img is not None else EXCOL_BYTES)
        for i, row in enumerate(excols):
            struct.pack_into("<3H", out, tail + i * EXCOL_N * 2,
                             *[color555(c) for c in row])
        if logo_img is not None:
            o = tail + EXCOL_BYTES
            out[o:o + LOGO_IMG_BYTES] = logo_img
            struct.pack_into("<3H", out, o + LOGO_IMG_BYTES,
                             *[color555(c) for c in logo_pal])

    return bytes(out)


def parse(data):
    """拆开 theme.bin（自检 / 体检用）。v1、v2、v3 都认。"""
    data = bytes(data)
    if len(data) < ICONS_ONLY_SIZE:
        raise ValueError("文件只有 %d 字节，比最小长度 %d 还小"
                         % (len(data), ICONS_ONLY_SIZE))
    if data[0:8] != MAGIC:
        raise ValueError("魔数不是 %r（实际 %r）" % (MAGIC, data[0:8]))
    ver, flags, size = struct.unpack_from("<HHI", data, 8)
    if ver not in (VERSION, VERSION_V2, VERSION_V3, VERSION_V4, VERSION_V5, VERSION_V6):
        raise ValueError("版本是 %d，只支持 1 / 2 / 3 / 4 / 5 / 6" % ver)
    if size != len(data):
        raise ValueError("头部写长度 %d，文件实际 %d" % (size, len(data)))
    # v5 = 老布局 + 36 字节扩展色；v6 = 老布局 + 36 + 223 字节 logo。
    # 两种都按「总长 - 尾巴」反推基础版本。
    tail = {VERSION_V5: EXCOL_BYTES, VERSION_V6: V6_TAIL}.get(ver, 0)
    base = ver
    if tail:
        base = _BASE_BY_SIZE.get(len(data) - tail)
        if base is None:
            raise ValueError("v%d 文件长度 %d 对不上任何已知布局" % (ver, len(data)))

    has_wall = bool(flags & FLAG_WALL)
    want = {VERSION: FILE_SIZE, VERSION_V2: FILE_SIZE_V2,
            VERSION_V3: FILE_SIZE_V3, VERSION_V4: FILE_SIZE_V4}[base]
    want += tail
    if has_wall and len(data) != want:
        raise ValueError("标志位说带壁纸，但长度是 %d，应该是 %d"
                         % (len(data), want))

    pals = []
    for i in range(THEME_COUNT):
        vals = struct.unpack_from("<6H", data, OFF_COLORS + i * 12)
        pals.append([rgb555(v) for v in vals])

    icons = []
    for i in range(THEME_COUNT):
        o = OFF_ICONS + i * ICON_STRIDE
        sprite = data[o:o + ICON_BYTES]
        pal = list(struct.unpack_from("<%dH" % ICON_PAL_N, data, o + ICON_BYTES))
        icons.append((sprite, pal))

    def get_wall(o):
        return data[o:o + WALL_BYTES], list(struct.unpack_from(
            "<%dH" % WALLPAL_N, data, o + WALL_BYTES))

    walls = [None] * THEME_COUNT
    wall_pals = [None] * THEME_COUNT
    if has_wall:
        if base >= VERSION_V2:
            for i in range(THEME_COUNT):
                if not (flags >> (1 + i)) & 1:
                    continue
                walls[i], wall_pals[i] = get_wall(OFF_WALL + i * WALL_STRIDE)
        else:
            w, p = get_wall(OFF_WALL)
            walls = [w] * THEME_COUNT
            wall_pals = [p] * THEME_COUNT

    page_walls = [None] * PAGE_COUNT
    page_pals = [None] * PAGE_COUNT
    if base >= VERSION_V3:
        for i in range(PAGE_COUNT):
            if not (flags >> (FLAG_PAGE_BASE + i)) & 1:
                continue
            page_walls[i], page_pals[i] = get_wall(OFF_PAGES + i * WALL_STRIDE)

    # v4：每套每页的专属图，返回 6×7 嵌套（调用方按 [套][页] 取）
    sp_walls = [[None] * PAGE_COUNT for _ in range(THEME_COUNT)]
    sp_pals = [[None] * PAGE_COUNT for _ in range(THEME_COUNT)]
    sp_mask = 0
    if base >= VERSION_V4:
        lo, hi = struct.unpack_from("<II", data, OFF_V4MASK)
        sp_mask = lo | (hi << 32)
        for s in range(THEME_COUNT):
            for p in range(PAGE_COUNT):
                if (sp_mask >> slot_index(s, p)) & 1:
                    sp_walls[s][p], sp_pals[s][p] = get_wall(
                        OFF_V4 + slot_index(s, p) * WALL_STRIDE)

    # 尾部：6 套 ×（载入底, 进度条, 游戏内菜单底），v6 后面再跟一份 logo
    excols = None
    logo = None
    if tail:
        o = len(data) - tail
        excols = [[rgb555(v) for v in struct.unpack_from("<3H", data, o + i * EXCOL_N * 2)]
                  for i in range(THEME_COUNT)]
        if ver == VERSION_V6:
            lo = o + EXCOL_BYTES
            img = data[lo:lo + LOGO_IMG_BYTES]
            pal = [rgb555(v) for v in struct.unpack_from(
                "<%dH" % LOGO_PAL_N, data, lo + LOGO_IMG_BYTES)]
            logo = (img, pal)

    first = next((i for i in range(THEME_COUNT) if walls[i] is not None), None)
    return {"version": ver, "base_version": base, "flags": flags, "has_wall": has_wall,
            "extra_cols": excols, "logo": logo,
            "pals": pals, "icons": icons,
            "walls": walls, "wall_pals": wall_pals,
            "page_walls": page_walls, "page_pals": page_pals,
            "set_page_walls": sp_walls, "set_page_pals": sp_pals,
            "set_page_mask": sp_mask,
            # 老接口：有壁纸时给第一张，方便老代码少改
            "wall": walls[first] if first is not None else None,
            "wall_pal": wall_pals[first] if first is not None else None}
