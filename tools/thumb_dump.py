# -*- coding: utf-8 -*-
"""把 chis 固件 0x6000..0x6200 这段按 Thumb 打印，并把 pc 相对字面量解出来。"""
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import capstone
import fwbuild as F

path = sys.argv[1] if len(sys.argv) > 1 else "in/chis.fw"
lo = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x6000
hi = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x6200

fw = open(path, "rb").read()
code = fw  # 便于按文件偏移读字面量


def lit(addr):
    o = addr - 0x08000000
    if 0 <= o <= len(code) - 4:
        return struct.unpack_from("<I", code, o)[0]
    return None


md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
md.detail = False
for ins in md.disasm(fw[lo:hi], 0x08000000 + lo):
    extra = ""
    if ins.mnemonic == "ldr" and "[pc" in ins.op_str:
        try:
            imm = int(ins.op_str.split("#")[-1].rstrip("]"), 0)
        except ValueError:
            imm = 0
        a = (ins.address + 4) & ~3
        v = lit(a + imm)
        if v is not None:
            extra = "      ; =0x%08X" % v
    print("0x%08X  %-10s %-8s %-22s%s" % (ins.address, ins.bytes.hex(),
                                          ins.mnemonic, ins.op_str, extra))
