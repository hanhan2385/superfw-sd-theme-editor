#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把新的字体包打进一个现成的 .fw，**不用重新编译**。

为什么能做到：字体段在镜像里是明文、位置固定（FO 头 + 983,948 B），后面才是
IGM payload 那些东西。新字体不超过原长度时，就地覆盖 + 补零就行 —— 运行时的
`font_block_size()` 读的是 FO 头里的 data_size，后面多出来的零没人看。

唯一躲不开的是**头部校验**：升级时 `validate_superfw_checksum()` 会算

    sha256( fw[0:224] + zeros(16) + fw[240:fw_size] )

取前 16 字节跟头里的 checksum 比。所以改完字节必须重算，否则机器直接拒刷。

头部布局（`t_superfw_header`，flash.c）：
    0..191    rom_head[192]
    192..195  branch_op
    196..199  version
    200..203  git_version
    204..207  fw_size          ← 必须等于文件长度
    208..211  hw_variant       ("Chis")
    212..215  fw_variant
    216..223  pad[8]
    224..239  checksum[16]     ← 要重算的
    240..255  magic            ("SUPERFW~DAVIDGF")

用法：

    python tools/patch_font_in_fw.py --fw dist/fonttest/SuperFW-wqy12-2226.fw \\
        --font in/wqy/fonts-ext-wqy.pack -o dist/fonttest/SuperFW-myfont.fw

    # 只检查能不能打，不写文件
    python tools/patch_font_in_fw.py --fw ... --font ... --check
"""
import argparse
import hashlib
import struct
import sys

# 16 字节，末尾带 NUL —— 结构体里是 magic[16]，memcmp 也比 16 个字节
MAGIC = b"SUPERFW~DAVIDGF\x00"
OFF_FW_SIZE = 204
OFF_CHECKSUM = 224
OFF_MAGIC = 240
HDR_END = 256


def find_font_segment(fw):
    """扫出主字体段：FO 头 + 块数 >= 10 + 体积够大。

    内嵌的 ASCII 表也是 FO 格式（3 块、1748 B），所以不能只认 magic。
    """
    best = None
    for i in range(HDR_END, len(fw) - 16):
        if fw[i] != 0x46 or fw[i + 1] != 0x4F:
            continue
        nblk = fw[i + 3]
        total = struct.unpack_from("<I", fw, i + 4)[0]
        if not (1 <= nblk <= 255 and 500_000 <= total <= 2_000_000):
            continue
        if i + total > len(fw):
            continue
        best = (i, total)
        break
    return best


def checksum(fw):
    h = hashlib.sha256()
    h.update(fw[0:OFF_CHECKSUM])
    h.update(b"\0" * 16)
    h.update(fw[OFF_MAGIC:])
    return h.digest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fw", required=True, help="现有的 .fw")
    ap.add_argument("--font", required=True, help="新的字体包（FO 格式）")
    ap.add_argument("-o", "--output", help="输出的 .fw")
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = ap.parse_args()

    fw = bytearray(open(args.fw, "rb").read())

    if len(fw) < HDR_END or bytes(fw[OFF_MAGIC:OFF_MAGIC + 16]) != MAGIC:
        sys.exit("不是 SuperFW 固件（找不到 magic SUPERFW~DAVIDGF）")

    fwsize = struct.unpack_from("<I", fw, OFF_FW_SIZE)[0]
    if fwsize != len(fw):
        sys.exit("头部 fw_size=%d 跟文件长度 %d 不一致，这份固件本身有问题"
                 % (fwsize, len(fw)))

    seg = find_font_segment(fw)
    if not seg:
        sys.exit("固件里没找到字体段（应该是 17 块、约 983 KB 的 FO 包）")
    seg_off, seg_len = seg

    font = open(args.font, "rb").read()
    if len(font) > seg_len:
        sys.exit("新字体 %d B 超过原字体段 %d B —— 这种情况只能重新编译固件"
                 % (len(font), seg_len))

    print("固件          %s (%d B)" % (args.fw, len(fw)))
    print("字体段        off=0x%06X, %d B" % (seg_off, seg_len))
    print("新字体        %s (%d B, 占 %.0f%%)"
          % (args.font, len(font), 100.0 * len(font) / seg_len))

    if args.check:
        print("检查通过：可以就地打补丁")
        return

    if not args.output:
        sys.exit("要写文件就加 -o，或者用 --check 只检查")

    old_sum = bytes(fw[OFF_CHECKSUM:OFF_CHECKSUM + 16])
    fw[seg_off:seg_off + seg_len] = font + b"\0" * (seg_len - len(font))
    fw[OFF_CHECKSUM:OFF_CHECKSUM + 16] = checksum(fw)
    new_sum = bytes(fw[OFF_CHECKSUM:OFF_CHECKSUM + 16])

    open(args.output, "wb").write(fw)
    print("校验和        %s → %s" % (old_sum.hex()[:16], new_sum.hex()[:16]))
    print("已写出        %s (%d B)" % (args.output, len(fw)))


if __name__ == "__main__":
    main()
