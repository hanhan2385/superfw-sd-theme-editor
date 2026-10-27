# -*- coding: utf-8 -*-
"""
SuperFW 固件主题/图标注入器。

不重新编译，直接在已有的 .bin 里改数据，然后重算头部 SHA-256 校验和。
适用：主题表与图标块与源码一致的构建（r75 这类）。

用法：
  python tools/patch_firmware.py in.bin out.bin --theme 5 --colors 0E1A21,16272F,E6F5F7,1E3B44,6FF2C0,0A1419
  python tools/patch_firmware.py in.bin out.bin --icons out/icons.h
"""
import argparse
import hashlib
import os
import re
import struct
import sys

# 源码 src/menu.c themes[] 的 RGB555 值（用于定位主题表）
THEMES = [
    [0x56B5, 0x7FFF, 0x0000, 0x6739, 0x5E73, 0x4638],
    [0x2A9D, 0x4B1C, 0x0000, 0x3E97, 0x3612, 0x2D6C],
    [0x4E04, 0x5ED1, 0x0000, 0x524A, 0x49E3, 0x420D],
    [0x2A26, 0x4EB1, 0x0000, 0x462E, 0x39CE, 0x318C],
    [0x6455, 0x79FC, 0x0000, 0x6175, 0x490E, 0x3D8E],
    [0x1084, 0x2108, 0x77BD, 0x39CE, 0x56B5, 0x318C],
]


def rgb2gba(h):
    h = h.replace("#", "")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return ((r >> 3) << 0) | ((g >> 3) << 5) | ((b >> 3) << 10)


def find_table(img):
    pat = b"".join(struct.pack("<6H", *t) for t in THEMES)
    pos = img.find(pat)
    if pos < 0:
        raise SystemExit("没找到主题表：这个固件不是这套 themes[]（可能改过源码或换了版本）")
    if img.find(pat, pos + 1) >= 0:
        print("警告：主题表出现多次，使用第一处")
    return pos


REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "ref", "superfw", "src", "res")


def _parse_icon_h(text):
    body = text[text.index("icons_img"):text.index("icons_pal")]
    rows = []
    for m in re.finditer(r"\{([^{}]*)\}", body):
        r = [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", m.group(1))]
        if len(r) == 8:
            rows.append(bytes(r))
    pal = [int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{4})", text[text.index("icons_pal"):])]
    return b"".join(rows), pal


def locate_original_assets(img):
    """用仓库里的原始 icons.h / logo.h 定位固件中的数据块。"""
    orig_icons = open(os.path.join(REF, "icons.h"), encoding="utf-8").read()
    data, pal = _parse_icon_h(orig_icons)
    img_off = img.find(data)
    pal_off = img.find(struct.pack("<%dH" % len(pal), *pal))
    if img_off < 0 or pal_off < 0:
        raise SystemExit("没找到固件里的图标块（可能不是同一套资源）")
    orig_logo = open(os.path.join(REF, "logo.h"), encoding="utf-8").read()
    lrows = [int(v) for v in re.findall(r"-?\d+", orig_logo[orig_logo.index("logo_img"):orig_logo.index("logo_pal")])]
    ldata = bytes(lrows)
    lpal = [int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{4})", orig_logo[orig_logo.index("logo_pal"):])]
    l_off = img.find(ldata)
    lp_off = img.find(struct.pack("<3H", *lpal))
    return {
        "icons_off": img_off, "icons_len": len(data),
        "pal_off": pal_off, "pal_n": len(pal),
        "logo_off": l_off, "logo_len": len(ldata),
        "logo_pal_off": lp_off,
    }


def validate(img):
    size = struct.unpack("<I", img[0xCC:0xD0])[0]
    if size > len(img):
        return False
    h = hashlib.sha256()
    h.update(img[0:0xE0])
    h.update(b"\0" * 16)
    h.update(img[0xF0:size])
    return h.digest()[:16] == bytes(img[0xE0:0xF0])


def reseal(img):
    size = struct.unpack("<I", img[0xCC:0xD0])[0]
    img[0xE0:0xF0] = b"\0" * 16
    h = hashlib.sha256()
    h.update(img[0:0xE0])
    h.update(b"\0" * 16)
    h.update(img[0xF0:size])
    img[0xE0:0xF0] = h.digest()[:16]
    return h.digest()[:16].hex()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--theme", type=int, help="要覆盖的主题槽位 0-5")
    ap.add_argument("--colors", help="6 个 RRGGBB，逗号分隔（顺序 fg,bg,ft,hi,hib,sh）")
    ap.add_argument("--icons", help="用某个 icons.h 替换整套图标")
    ap.add_argument("--logo", help="用某个 logo.h 替换 Logo")
    args = ap.parse_args()

    img = bytearray(open(args.src, "rb").read())
    print(f"输入 {len(img)} 字节，校验和 {'有效' if validate(img) else '无效'}")

    changed = []

    if args.theme is not None:
        if not 0 <= args.theme < 6:
            raise SystemExit("槽位只能是 0-5")
        if not args.colors:
            raise SystemExit("--theme 需要 --colors")
        cols = [rgb2gba(c) for c in args.colors.split(",")]
        if len(cols) != 6:
            raise SystemExit("需要正好 6 个颜色")
        off = find_table(img) + args.theme * 12
        old = struct.unpack("<6H", img[off:off + 12])
        img[off:off + 12] = struct.pack("<6H", *cols)
        changed.append((off, 12))
        print(f"主题槽 {args.theme + 1}: {[hex(v) for v in old]} → {[hex(v) for v in cols]} @ {hex(off)}")

    loc = locate_original_assets(img) if (args.icons or args.logo) else None

    if args.icons:
        text = open(args.icons, encoding="utf-8").read()
        newdata, pal = _parse_icon_h(text)
        off, n, pal_off, pn = loc["icons_off"], loc["icons_len"], loc["pal_off"], loc["pal_n"]
        body = text[text.index("icons_img"):text.index("icons_pal")]
        newdata = b"".join(
            bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", m.group(1)))
            for m in re.finditer(r"\{([^{}]*)\}", body)
            if len(re.findall(r"0x([0-9a-fA-F]{2})", m.group(1))) == 8
        )
        if len(newdata) != n:
            raise SystemExit(f"图标数据长度不符 {len(newdata)} != {n}")
        if len(pal) > pn:
            raise SystemExit(f"新调色板 {len(pal)} 色，超过固件里的 {pn} 色，无法原地替换")
        if len(pal) < pn:
            # 用最后一个颜色补齐，索引不受影响
            pal = pal + [pal[-1]] * (pn - len(pal))
        img[off:off + n] = newdata
        img[pal_off:pal_off + pn * 2] = struct.pack("<%dH" % pn, *pal)
        changed.append((off, n))
        changed.append((pal_off, pn * 2))
        print(f"图标块 {n} 字节 + 调色板 {pn} 色 @ {hex(off)}")

    if args.logo:
        text = open(args.logo, encoding="utf-8").read()
        rows = [int(v) for v in re.findall(r"-?\d+", text[text.index("logo_img"):text.index("logo_pal")])]
        data = bytes(rows) if len(rows) == 217 else None
        pal = [int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{4})", text[text.index("logo_pal"):])]
        if not data or len(pal) != 3:
            raise SystemExit("logo.h 解析失败")
        start, palpos = loc["logo_off"], loc["logo_pal_off"]
        if start < 0 or palpos < 0:
            raise SystemExit("没找到 logo 数据块")
        img[start:start + 217] = data
        img[palpos:palpos + 6] = struct.pack("<3H", *pal)
        changed.append((start, 217))
        changed.append((palpos, 6))
        print(f"Logo 217 字节 + 调色板 @ {hex(start)}")

    if not changed:
        raise SystemExit("什么都没改，用 --theme / --icons / --logo")

    digest = reseal(img)
    open(args.dst, "wb").write(img)
    print(f"写出 {args.dst}")
    print(f"重算校验和 {digest}")
    print("校验结果:", "通过" if validate(img) else "失败")


if __name__ == "__main__":
    main()
