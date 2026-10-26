# -*- coding: utf-8 -*-
"""体检「背景补丁点」：签名命中几处、四周是什么代码、有几个内容区填充。

用法: python tools/inspect_bg_site.py in/chis.fw [--rom]
"""
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import capstone
import fwbuild as F

MIRROR = 0x027C0000


def scan(fw):
    code = bytes(fw[:F.fw_size_of(fw)])
    for s in F.PATCH_SITES:
        sig = s["sig"]
        hits = []
        i = code.find(sig)
        while i >= 0:
            hits.append(i)
            i = code.find(sig, i + 1)
        print("%-10s sig=%s -> %d 处: %s" % (
            s["name"], sig.hex(), len(hits), ", ".join("0x%X" % h for h in hits)))


def dump(fw, off, n=64):
    """以 Thumb 反汇编 off 附近 n 条。"""
    code = bytes(fw[:F.fw_size_of(fw)])
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    start = off - 24
    print("---- Thumb @0x%X (ROM) ----" % off)
    for ins in md.disasm(code[start:off + 40], 0x08000000 + start):
        mark = " <<<" if ins.address == 0x08000000 + off else ""
        print("  0x%08X  %-28s %s %s%s" % (ins.address, ins.bytes.hex(),
                                           ins.mnemonic, ins.op_str, mark))


def find_bg_fills(fw):
    """搜所有 DMA3 立即启动 + 目标/字节数像内容区填充的点。"""
    code = bytes(fw[:F.fw_size_of(fw)])
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    insns = list(md.disasm(code[0:0x2000], 0x08000000))
    # 找 ldr rX, [pc, #imm] 的值
    lit = {}
    for ins in insns:
        if ins.mnemonic == "ldr" and "[pc" in ins.op_str:
            try:
                imm = int(ins.op_str.split("#")[-1].rstrip("]"), 0)
            except ValueError:
                continue
            a = ins.address + 4 + imm
            if a & 1:
                continue
            o = a - 0x08000000
            if 0 <= o <= len(code) - 4:
                lit[ins.address] = struct.unpack_from("<I", code, o)[0]
    print("\n---- 前 0x1200 里的字面量地址 ----")
    for a, v in sorted(lit.items()):
        if 0x08000000 <= v < 0x08000000 + F.fw_size_of(fw) or v >= 0x027C0000:
            print("  0x%08X -> 0x%08X" % (a, v))


def main():
    path = sys.argv[1]
    fw = bytearray(open(path, "rb").read())
    print("file=%s size=%d fw_size=0x%X" % (path, len(fw), F.fw_size_of(fw)))
    scan(fw)
    L = F.detect_loose(fw)
    print("site: off=0x%X len=%d frame=%s build=%s" % (
        L.patch_off, L.patch_len, L.frame_reg, L.build))
    dump(fw, L.patch_off)
    find_bg_fills(fw)


if __name__ == "__main__":
    main()
