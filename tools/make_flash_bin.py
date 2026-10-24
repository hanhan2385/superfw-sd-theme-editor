#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 .fw 固件补成外部烧录器能写的整段镜像（默认 2 MiB，0xFF 填充）。

为什么需要它：
  .fw 是给「内置升级器」用的，校验要求 header->fw_size == 文件大小；
  外部烧录器写的是 NOR 裸片，通常要按扇区/整片对齐，所以要补 FF。
  补 FF 并不改任何头部字段 —— 这和内置升级器写完擦除态尾部的结果一致。

用法：
  # 只生成固件区镜像（2 MiB，适合 2 MiB 的 NOR，或烧录器支持「只写前 2MB」）
  python tools/make_flash_bin.py dist/superfw-fix/SuperFW-Fix-Chis-SDTheme.fw

  # 已经用烧录器读回整片备份，要在备份上只覆盖固件区（推荐，保住元数据区）
  python tools/make_flash_bin.py <fw> --base dump.bin --size 0x400000
"""
import argparse
import hashlib
import os
import struct
import sys

FW_REGION = 0x00200000      # 固件区大小：2 MiB（ROM_OFF_FLASHMETA 就是这里）
META_OFF = 0x00200000
HDR_FW_SIZE = 0xCC
HDR_SHA = 0xE0


def sha16_at16(data):
    """头部 0xE0 起 16 字节 = sha256(0x00..0xDF + 16 个 0 + 0xF0..) 的前 16 字节。"""
    h = hashlib.sha256(data[:HDR_SHA] + b"\0" * 16 + data[HDR_SHA + 16:]).digest()
    return h[:16]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("fw", help="输入 .fw")
    ap.add_argument("-o", "--out", help="输出 .bin（默认同目录同名 -2m.bin）")
    ap.add_argument("--size", default="0x200000",
                    help="镜像总大小，默认 0x200000（2 MiB）；整片备份场景给 0x400000")
    ap.add_argument("--base", help="整片备份 .bin，作为底；只覆盖固件区")
    args = ap.parse_args(argv)

    size = int(args.size, 0)
    fw = open(args.fw, "rb").read()
    out = args.out or os.path.splitext(args.fw)[0] + "-%dm.bin" % (size >> 20)

    bad = []
    fw_size = struct.unpack_from("<I", fw, HDR_FW_SIZE)[0]
    if fw_size != len(fw):
        bad.append("header->fw_size(0x%X) != 文件大小(%d)：这个文件本身就不完整" %
                   (fw_size, len(fw)))
    if fw[0xA0:0xAC].rstrip(b"\0") != b"SUPERFW":
        bad.append("0xA0 的游戏名不是 SUPERFW：%r" % fw[0xA0:0xAC])
    if sha16_at16(fw) != fw[HDR_SHA:HDR_SHA + 16]:
        bad.append("头部 sha256 校验不过：文件被改过，或者不是最新构建")
    if len(fw) > FW_REGION:
        bad.append("固件 %d 字节超过 2 MiB 固件区，不能放进前 2MB" % len(fw))
    if bad:
        for b in bad:
            print("  FAIL " + b)
        return 1

    img = bytearray(b"\xFF" * size)
    if args.base:
        base = open(args.base, "rb").read()
        if len(base) < size:
            print("  FAIL 备份只有 %d 字节，小于目标 %d" % (len(base), size))
            return 1
        img[:] = base[:size]
        keep = "元数据区 0x%06X..0x%06X 沿用备份" % (META_OFF, size) if size > META_OFF else ""
    else:
        keep = "整片 0xFF 底"

    # 固件区：固件本体 + 到 2 MiB 全是 FF（把旧尾巴清干净）
    img[:FW_REGION] = b"\xFF" * FW_REGION
    img[:len(fw)] = fw
    open(out, "wb").write(img)

    # 回读校验
    chk = open(out, "rb").read()
    ok = True
    if len(chk) != size:
        ok = False
    if chk[:len(fw)] != fw:
        ok = False
    if any(b != 0xFF for b in chk[len(fw):FW_REGION]):
        ok = False
    if args.base and size > META_OFF and chk[META_OFF:] != open(args.base, "rb").read()[META_OFF:size]:
        ok = False

    print("  输入  %s  %s 字节" % (os.path.basename(args.fw), format(len(fw), ",")))
    print("  输出  %s  0x%X（%s）" % (out, size, keep))
    print("  固件区 0x000000..0x%06X：固件 %s 字节 + FF 填充 %s 字节"
          % (FW_REGION, format(len(fw), ","), format(FW_REGION - len(fw), ",")))
    if size > META_OFF:
        print("  元数据区 0x%06X..0x%06X：%s" %
              (META_OFF, size, "沿用备份" if args.base else "FF（固件会清零重建）"))
    print("  sha256 %s" % hashlib.sha256(chk).hexdigest())
    print("  回读校验 %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
