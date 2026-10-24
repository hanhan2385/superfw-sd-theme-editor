# -*- coding: utf-8 -*-
"""把烘焙进固件的钩子按运行地址反汇编（Thumb），并解出字面量。

用法: hook_dump.py out/xxx.fw
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import capstone
import fwbake

path = sys.argv[1]
fw = open(path, "rb").read()
info = fwbake.find_baked(fw)
if not info:
    sys.exit("没有烘焙头")
L = fwbake.find_layout(fw)
hook_rom = L.payload_src + info["hook_off"]
hook_addr = fwbake.EWRAM_MIRROR + info["hook_off"]
# 钩子长度：找从 hook_rom 起、到下一个全零长段之前的非零尾巴
seg = fw[hook_rom:hook_rom + fwbake.HOOK_RESERVE]
end = len(seg)
while end and seg[end - 1] == 0:
    end -= 1
code = seg[:end + 4]

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
print("hook ROM 0x%06X -> EWRAM 0x%08X, %d 字节" % (hook_rom, hook_addr, end))
for ins in md.disasm(code, hook_addr):
    ex = ""
    if ins.mnemonic == "ldr" and "[pc" in ins.op_str:
        imm = int(ins.op_str.split("#")[-1].rstrip("]"), 0)
        pc = (ins.address + 4) & ~3
        off = pc - hook_addr + imm
        if 0 <= off <= len(code) - 4:
            import struct
            ex = "      ; =0x%08X" % struct.unpack_from("<I", code, off)[0]
    print("  0x%08X  %-8s %-8s %-16s%s" % (ins.address, ins.bytes.hex(),
                                           ins.mnemonic, ins.op_str, ex))
print()
print("patched site: 0x%X  bytes=%s" % (info["patch_off"],
      fw[info["patch_off"]:info["patch_off"] + info["patch_len"]].hex()))
