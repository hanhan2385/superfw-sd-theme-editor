# -*- coding: utf-8 -*-
"""按 ARM 打印引导区，并解 pc 相对字面量。用法: arm_dump.py rom lo hi"""
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import capstone

path, lo, hi = sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0)
fw = open(path, "rb").read()


def lit(addr):
    o = addr - 0x08000000
    if 0 <= o <= len(fw) - 4:
        return struct.unpack_from("<I", fw, o)[0]
    return None


md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
for ins in md.disasm(fw[lo:hi], 0x08000000 + lo):
    ex = ""
    if ins.mnemonic in ("ldr", "ldrb") and "[pc" in ins.op_str:
        try:
            imm = int(ins.op_str.split("#")[-1].strip("]"), 0)
            sub = ins.op_str.split("#")[-2].strip("], ").lstrip("+")
        except (ValueError, IndexError):
            imm, sub = 0, ""
        a = ins.address + 8 + (imm if sub else -imm)
        v = lit(a)
        if v is not None:
            ex = "      ; =0x%08X" % v
    print("0x%08X  %-14s %-9s %-24s%s" % (ins.address, ins.bytes.hex(),
                                          ins.mnemonic, ins.op_str, ex))
