#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按「任意字符集」造 FO v1 字体包（固件内置兜底包用）。

`fw/res/fonts/generator.py` 只能按**整段 Unicode 区间**造包（block 是 start..end 的
稠密表），而固件里的兜底包只需要「界面文案真正用到的那些字」，散落在各个区间里。
这个脚本把字集排序、按连续区间合并成 block，再照 FO v1 的格式写出来。

格式（和 font_render.c 完全一致）：

    header: 'F','O' | ver=1 | nblocks | u32 total_size
    blocks: {u32 start, u32 end, u32 flags, u32 data_off} × nblocks
    data  : 每个 block 的数据，后面 4 字节对齐

    flags & 1  : FLAG_FW16 —— 16 列定宽，每字 16 个 uint16（= 32 B）
    flags == 0 : 变宽 —— 先一张 n 个 uint16 的索引（高 3 位宽-1，低 13 位列偏移），
                 后面是列数据（每列一个 uint16，位 j = 第 j 行）

用法：

    # 从语言文件里抽字符，造界面兜底包
    python tools/make_font_pack.py --hex fw/res/fonts/unscii-16-full.hex \\
        fw/res/fonts/hangul-blocks.hex --chars-from ref/superfw-fix/res/lang \\
        --output ref/superfw-fix/res/fonts-mini.pack
"""
import argparse
import glob
import json
import os
import struct
import sys

FLAG_FW16 = 1
MAX_BLOCKS = 255           # 固件里 block_count 是 uint8
GAP_MERGE = 8              # 相邻区间空隙 <= 这么多字就并成一个 block（多带几个字更省表项）


def parse_hex(paths):
    """读出 {码点: (宽度, [每列一个 uint16])}，和 generator.py 的转换方式一致。"""
    raw = {}
    for p in paths:
        with open(p, "r") as f:
            for line in f.read().strip().split("\n"):
                if not line.strip():
                    continue
                cp, hexc = line.split(":")
                raw[int(cp, 16)] = bytes.fromhex(hexc)

    out = {}
    for cp, data in raw.items():
        if len(data) == 32:                       # 16 列宽
            words = [data[i + 1] | (data[i] << 8) for i in range(0, 32, 2)]
            width = 16
            cols = words                            # 每项 = 一列 16 行
        elif len(data) == 16:                      # 8 列宽
            width = 8
            cols = [0] * width
            for r, d in enumerate(data):
                for i in range(width):
                    if d & (1 << (width - 1 - i)):
                        cols[i] |= (1 << r)
        else:
            continue
        out[cp] = (width, cols)

    # 8 宽字形去掉左右全空的列（和固件变宽块的做法一致），宽度 1..8
    trimmed = {}
    for cp, (width, cols) in out.items():
        if width == 16:
            trimmed[cp] = (16, cols)
            continue
        lo, hi = 0, len(cols) - 1
        while lo <= hi and cols[lo] == 0:
            lo += 1
        while hi >= lo and cols[hi] == 0:
            hi -= 1
        if lo > hi:
            trimmed[cp] = (1, [0])
        else:
            trimmed[cp] = (hi - lo + 1, cols[lo:hi + 1])
    return trimmed


def chars_from_json(dirpath):
    """目录 → 该目录下所有 *.json；也可以直接给单个 .json 文件。"""
    cps = set()
    if os.path.isfile(dirpath):
        paths = [dirpath]
    else:
        paths = sorted(glob.glob(os.path.join(dirpath, "*.json")))
    for p in paths:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)

        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
            elif isinstance(o, str):
                cps.update(ord(c) for c in o)

        walk(doc)
    return cps


def gb2312_cps():
    """GB2312 全集（6763 汉字 + 682 符号）+ ASCII 可打印区。

    GBA 上的中文文件名基本都是简体 GB2312 编码的，全量 CJK（20902 字）里
    绝大多数永远用不到 —— 一个 690 KB 的包从 SD 卡读要好几秒，砍到 GB2312
    只要 1/3，覆盖面上几乎看不出差别。
    """
    cps = set(range(0x20, 0x7F))
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                cps.add(ord(bytes([hi, lo]).decode("gb2312")))
            except UnicodeDecodeError:
                pass
    return cps


def make_runs(cps):
    """把码点排序、合并成连续区间（空隙 <= GAP_MERGE 也并进去）。"""
    runs = []
    for cp in sorted(cps):
        if runs and cp - runs[-1][1] <= GAP_MERGE:
            runs[-1][1] = cp
        else:
            runs.append([cp, cp])
    return runs


def grab_composite(path, start=0xAC00, end=0xD7A3):
    """从现成的包里抠一个「合成块」原样搬过来。

    韩文 11,172 个音节如果按字形逐个存要 357 KB；upstream 的做法是存 205 个
    字母（jamo）、运行时用 hangul.h 组合，只占 6.7 KB。那块数据的格式和普通
    block 不同（flags 带 0x80000000），自己造容易错，直接搬上游那份。
    """
    b = open(path, "rb").read()
    if b[:2] != b"FO":
        return None
    n = b[3]
    base = 8 + 16 * n
    dsize = struct.unpack_from("<I", b, 4)[0]
    for i in range(n):
        s, e, fl, off = struct.unpack_from("<IIII", b, 8 + 16 * i)
        if fl & 0x80000000:
            nxt = struct.unpack_from("<I", b, 8 + 16 * (i + 1) + 12)[0] if i + 1 < n else dsize
            return (start, end, fl, b[base + off:base + nxt])
    return None


def split_by_width(run, font):
    """一个区间里可能 8 宽 16 宽混着，按**类别**切成子区间。

    类别只有两种：16 宽定宽（FLAG_FW16，每字 32 B）和非 16 宽（变宽块，
    每字自带宽度 1..8）。变宽块内部允许逐字不同宽度，所以不能按精确宽度切 ——
    以前按精确宽度切，ASCII 那 95 个字符被切成 46 段，一上来就把 block 数
    顶穿了（固件里 block_count 是 uint8，而 fontload 的头只读 256 B → 实际
    只能装 15 块）。
    """
    out = []
    cur = None
    for cp in range(run[0], run[1] + 1):
        width = font[cp][0] if cp in font else 16
        cls = 16 if width == 16 else 0
        if cur and cur[2] == cls and cp == cur[1] + 1:
            cur[1] = cp
        else:
            cur = [cp, cp, cls]
            out.append(cur)
    return out


def build(font, cps, out_path, hangul_from=None):
    comp = None
    if hangul_from:
        comp = grab_composite(hangul_from)
        if comp is None:
            sys.exit("%s 里没找到合成块（flags & 0x80000000）" % hangul_from)
        cps = {c for c in cps if not (0xAC00 <= c <= 0xD7A3)}

    runs = []
    for r in make_runs(cps):
        runs.extend(split_by_width(r, font))
    # block 太散就加大合并粒度没用，直接报错让人看
    if len(runs) + (1 if comp else 0) > MAX_BLOCKS:
        sys.exit("block 数 %d 超过固件上限 %d，把 GAP_MERGE 调大或减少字集"
                 % (len(runs) + (1 if comp else 0), MAX_BLOCKS))

    blocks = []      # (start, end, flags, data)
    missing = []
    for start, end, cls in runs:
        if cls == 16:
            data = b""
            for cp in range(start, end + 1):
                w, cols = font.get(cp, (16, [0] * 16))
                data += b"".join(struct.pack("<H", c) for c in cols)
            blocks.append((start, end, FLAG_FW16, data))
        else:
            n = end - start + 1
            index = []
            coldata = []
            off = 0
            for cp in range(start, end + 1):
                w, cols = font.get(cp, (1, [0]))
                if w > 8:
                    w, cols = 8, cols[:8]
                index.append(((w - 1) << 13) | off)
                coldata.extend(cols)
                off += w
            if off > 0x1FFF:
                sys.exit("变宽块 U+%04X..U+%04X 列偏移超 13 位上限" % (start, end))
            data = b"".join(struct.pack("<H", v) for v in index + coldata)
            blocks.append((start, end, 0, data))

    if comp:
        blocks.append(comp)

    # 组装
    head = 8 + 16 * len(blocks)
    off = 0
    table = b""
    body = b""
    for start, end, flags, data in blocks:
        table += struct.pack("<IIII", start, end, flags, off)
        body += data
        off += len(data)
        while len(data) % 4:                      # 每个 block 4 字节对齐
            body += b"\0"
            data += b"\0"
            off += 1
    total = head + len(body)
    out = b"FO" + bytes([1, len(blocks)]) + struct.pack("<I", total) + table + body
    assert len(out) == total
    with open(out_path, "wb") as f:
        f.write(out)

    for cp in sorted(cps):
        if cp not in font:
            missing.append(cp)
    print("%s: %d 字 / %d block / %d B" % (out_path, len(cps), len(blocks), total))
    if missing:
        print("  %d 个字在字库里没有（会画成空白）：%s"
              % (len(missing), "".join(chr(c) for c in missing[:40])))
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hex", nargs="+", required=True)
    ap.add_argument("--chars-from", help="语言 JSON 目录（抽里面出现过的字）")
    ap.add_argument("--chars", help="直接给一串字符")
    ap.add_argument("--gb2312", action="store_true",
                    help="并上 GB2312 全集 + ASCII（中文精简包用这个）")
    ap.add_argument("--gap", type=int, default=None, help="相邻区间合并的空隙上限")
    ap.add_argument("--hangul-from", help="从这个包里搬韩文合成块（省 350 KB）")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    global GAP_MERGE
    if args.gap is not None:
        GAP_MERGE = args.gap
    font = parse_hex(args.hex)
    cps = set()
    if args.chars_from:
        cps |= chars_from_json(args.chars_from)
    if args.chars:
        cps |= {ord(c) for c in args.chars}
    if args.gb2312:
        cps |= gb2312_cps()
    if not cps:
        sys.exit("没给字集：--chars-from 或 --chars 至少一个")
    build(font, cps, args.output, hangul_from=args.hangul_from)


if __name__ == "__main__":
    main()
