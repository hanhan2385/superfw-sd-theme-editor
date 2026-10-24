"""自检：6 套主题各写各自配色，互不串味。

用法: python tools/test_theme_slots.py [固件]

检查项：
1. 逐槽写入后，每个槽的 12 字节正好等于该槽配色（RGB→BGR555 位复制）；
2. 主题表之外（前后各 16 字节）一个字节都没动；
3. reseal 之后 validate 通过、文件长度不变；
4. 反着写一遍（先写 6 套，再单独改第 3 套）只影响第 3 套。
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fwbuild as F          # noqa: E402
import wallpaper as WP      # noqa: E402

FW = sys.argv[1] if len(sys.argv) > 1 else "in/cn.fw"

PALS = [
    ["AAAAAA", "FFFFFF", "000000", "CCCCCC", "9999BB", "C08888"],   # White
    ["ECA551", "E7C092", "000000", "BDA27B", "90816E", "615D58"],   # Orange
    ["26879C", "8FB1B8", "000000", "5296A5", "1D7F95", "6F8185"],   # Blue
    ["308855", "88AA99", "000000", "778888", "777777", "606060"],   # Green
    ["AD11C8", "E47AF6", "000000", "AD5DC6", "724095", "72667A"],   # Purple
    ["222222", "444444", "EEEEEE", "737573", "AAAAAA", "606060"],   # Dark
]


def rgb2gba(c):
    return F.rgb2gba(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def main():
    rom0 = bytearray(open(FW, "rb").read())
    lay = F.detect(rom0)
    off = lay.themes
    n = len(rom0)
    print("固件 %s（%s，%d B）主题表 0x%06X" % (FW, lay.build, n, off))

    # 先把 6 套都改掉（改坏原值，确保后面的比对不是"本来就这样"）
    rom = bytearray(rom0)
    for s in range(6):
        WP.patch_theme(rom, ["010203", "040506", "070809", "0A0B0C", "0D0E0F", "101112"],
                       slot=s, layout=lay)
    changed = sum(1 for i in range(72) if rom[off + i] != rom0[off + i])
    print("6 套全改后主题表变了 %d / 72 字节" % changed)

    # 再逐槽写回各自配色
    for s, pal in enumerate(PALS):
        WP.patch_theme(rom, pal, slot=s, layout=lay)

    bad = 0
    for s, pal in enumerate(PALS):
        got = struct.unpack_from("<6H", rom, off + s * 12)
        want = tuple(rgb2gba(c) for c in pal)
        ok = got == want
        bad += 0 if ok else 1
        print("  槽%d %-7s %s  %s" % (
            s + 1, ["White", "Orange", "Blue", "Green", "Purple", "Dark"][s],
            " ".join("%04X" % v for v in got), "OK" if ok else "不对 期望 %s" % (want,)))

    # 主题表前后不能被碰
    guard_ok = (rom[off - 16:off] == rom0[off - 16:off]
                and rom[off + 72:off + 88] == rom0[off + 72:off + 88])
    print("表外 16 字节未改动：%s" % ("是" if guard_ok else "否"))

    # 只写第 3 套时，别的槽必须保持上一轮的值
    before = bytes(rom[off:off + 72])
    WP.patch_theme(rom, ["FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF"],
                   slot=2, layout=lay)
    after = bytes(rom[off:off + 72])
    diff_slots = [s for s in range(6) if before[s * 12:(s + 1) * 12] != after[s * 12:(s + 1) * 12]]
    print("单写第 3 套时被改的槽：%s（应只有 3）" % (diff_slots and [s + 1 for s in diff_slots]))

    digest = F.reseal(rom)
    okv, why = F.validate(rom)
    print("reseal %s / validate %s %s / 长度不变 %s"
          % (digest[:12] + "…", okv, why, len(rom) == n))

    good = (bad == 0 and guard_ok and diff_slots == [2] and okv and len(rom) == n)
    print("\n结论: %s" % ("6 套主题可以各自注入，互不影响" if good else "有问题"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
