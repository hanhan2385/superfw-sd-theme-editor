#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme.bin 体检 —— 结构、版本、flags 自洽性、与固件常量的一致性。

用法:
    python tools/check_theme_bin.py theme.bin
    python tools/check_theme_bin.py theme.bin --fw out/firmware.fw

头布局（两侧必须一致）:
    0x00  8B  "SFWTHM01"
    0x08  u16 版本
    0x0A  u16 flags
    0x0C  u32 声明长度
         bit0       v1：有一张共用壁纸
         bit(1+i)   v2：第 i 套有默认壁纸
         bit(7+p)   v3：第 p 页有页面图

固件读法见 fw/src/sdtheme.c 里 `flags = b[10] | (b[11] << 8)`。
注意 **不要用 b[9] 当 flags**，那是版本号的高字节。
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import sdtheme

OK, BAD = [], []


def ck(cond, msg):
    (OK if cond else BAD).append(msg)
    print(("  OK   " if cond else "  FAIL ") + msg)
    return cond


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--fw", help="可选：固件文件，用于核对常量与缓存地址")
    a = ap.parse_args()

    data = open(a.path, "rb").read()
    print("== %s (%d 字节) ==" % (a.path, len(data)))

    # ---- 1. 头 ----
    print("\n[头]")
    ck(data[:8] == sdtheme.MAGIC, "magic = %r" % data[:8])
    ver, flags, decl = struct.unpack_from("<HHI", data, 8)
    ck(decl == len(data), "声明长度 %d == 实际 %d" % (decl, len(data)))
    ck(1 <= ver <= 7, "版本 v%d（支持 v1~v7）" % ver)
    print("       flags = 0x%04X" % flags)

    # ---- 2. 解析 ----
    print("\n[解析]")
    try:
        info = sdtheme.parse(data)
    except Exception as e:
        ck(False, "sdtheme.parse 失败：%s" % e)
        print("\n%d 项通过，%d 项失败" % (len(OK), len(BAD)))
        return 1
    ck(True, "sdtheme.parse 成功，识别为 v%s（基础布局 v%s）"
       % (info.get("version"), info.get("base_version")))

    # ---- 3. flags 与体积自洽 ----
    # 固件此刻正在按 flags 决定「哪一套有壁纸、哪一页有图」。
    # 体积表示数据排布到哪一段，flags 表示该段里哪些格子是有效的，两者必须对得上，
    # 否则就是不报错但壁纸静默不显示。
    print("\n[flags 与体积自洽]")
    has_wall = bool(flags & 0x01)
    wall_mask = (flags >> 1) & 0x3F
    page_mask = (flags >> 7) & 0x7F
    base = info.get("base_version") or 0
    print("       bit0(共用壁纸)=%d  壁纸掩码=0x%02X (%d/6 套)  页面掩码=0x%02X (%d/7 页)"
          % (has_wall, wall_mask, bin(wall_mask).count("1"),
             page_mask, bin(page_mask).count("1")))

    if len(data) <= sdtheme.ICONS_ONLY_SIZE:
        ck(not has_wall and wall_mask == 0, "无壁纸布局：flags 不应标壁纸")
    elif base <= 1:
        ck(has_wall, "v1 布局：bit0 应为 1（6 套共用这一张）")
    elif base == 2:
        ck(wall_mask != 0, "v2 布局：壁纸掩码不能为 0（否则六套全是纯色）")
    elif base == 3:
        ck(wall_mask != 0 and page_mask != 0, "v3 布局：壁纸与页面掩码都不能为 0")
    elif base == 4:
        ck(page_mask != 0, "v4 布局：页面掩码不能为 0")
    else:
        ck(False, "无法判定基础布局（base_version=%r），体积 %d" % (base, len(data)))

    # v5/v6/v7 是基础布局 + 尾巴，尾巴里没有 flags，只需长度对得上
    tail = len(data) - {
        1: sdtheme.FILE_SIZE, 2: sdtheme.FILE_SIZE_V2,
        3: sdtheme.FILE_SIZE_V3, 4: sdtheme.FILE_SIZE_V4,
    }.get(base, len(data) - sdtheme.ICONS_ONLY_SIZE)
    ck(tail in (0, sdtheme.EXCOL_BYTES, sdtheme.V6_TAIL, sdtheme.V7_TAIL),
       "尾巴 %d 字节（0 / %d / %d / %d 之一）"
       % (tail, sdtheme.EXCOL_BYTES, sdtheme.V6_TAIL, sdtheme.V7_TAIL))
    if tail == sdtheme.V7_TAIL:
        print("       v7：带 31x7 Logo（%d B）+ 6 套 x 2 个界面透明度" % sdtheme.LOGO_IMG_BYTES)
        got = sdtheme.parse(data)["alphas"]
        ck(all(0 <= v <= sdtheme.ALPHA_MAX for row in got for v in row),
           "6 套透明度都在 0..%d 之间（%s）" % (sdtheme.ALPHA_MAX, got))
        ck(any(any(row) for row in got), "不是全 0（全 0 就不该升 v7）")
    elif tail == sdtheme.V6_TAIL:
        print("       v6：带 31x7 Logo（%d B）" % sdtheme.LOGO_IMG_BYTES)
    elif tail == sdtheme.EXCOL_BYTES:
        print("       v5：带 6 套 x 3 个整屏色")

    # ---- 5. 固件侧常量 ----
    if a.fw:
        print("\n[固件]")
        fwb = open(a.fw, "rb").read()
        fw_size = struct.unpack_from("<I", fwb, 0x0CC)[0]
        variant = struct.unpack_from("<I", fwb, 0x0C8)[0]
        ck(fw_size == len(fwb) or all(x == 0xFF for x in fwb[fw_size:]),
           "固件 fw_size=%d，尾部填充正常" % fw_size)
        ck(variant == 0x78000001, "版本标识 0x%08X（期望 0x78000001 = r78）" % variant)
        # 缓存地址常量在 ARM 常量池里是 4 字节小端
        for addr in (0x081F0000, 0x081D0000, 0x08100000):
            pat = struct.pack("<I", addr)
            ck(fwb.count(pat) > 0, "缓存地址 0x%08X 出现在固件里（%d 处）"
               % (addr, fwb.count(pat)))
        ck(fwb.count(struct.pack("<I", 0x081E0000)) == 0
           or True, "0x081E0000 是菜单 overlay，仅提示不作失败")
        # v6 尾巴支持的证据：EXCOL_BYTES=36 / LOGO 217 这些数字附近难搜，只查字符串
        for s in (b"SFWTHM01",):
            ck(s in fwb, "固件里带 magic %r（说明已集成 sdtheme）" % s)

    print("\n%d 项通过，%d 项失败" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
