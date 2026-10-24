# -*- coding: utf-8 -*-
"""自检诊断钩子：SDRAM 有数据时应画出壁纸，SDRAM 全 0 时应整片洋红。

用法: python tools/test_diag_hook.py out/diag/xxx-诊断.fw
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import unicorn as U  # noqa: E402

EWRAM, VRAM, PAL, IWRAM = B.EWRAM_MIRROR, B.VRAM, B.PALETTE, 0x03000000
SDRAM = 0x08000000
SDRAM_SIZE = 0x2000000
SENTINEL = IWRAM + 0x7000


def run(rom, sdram_mode):
    layout = B.find_layout(rom)
    meta = B.find_baked(rom)

    sdram = bytearray(b"\x00" * SDRAM_SIZE)
    if sdram_mode == "copy":
        for c in layout.copies:
            sdram[c.dst - SDRAM:c.dst - SDRAM + c.size] = \
                bytes(rom[c.src:c.src + c.size])
    elif sdram_mode == "ff":
        sdram = bytearray(b"\xFF" * SDRAM_SIZE)

    mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_ARM)
    mu.mem_map(EWRAM, 0x40000)
    mu.mem_map(IWRAM, 0x8000)
    mu.mem_map(SDRAM, SDRAM_SIZE)
    mu.mem_map(VRAM, 0x18000)
    mu.mem_map(PAL, 0x400)
    mu.mem_map(0x04000000, 0x400)
    mu.mem_write(EWRAM, bytes(rom[layout.payload_src:
                                 layout.payload_src + layout.payload_len]))
    mu.mem_write(SDRAM, bytes(sdram))
    mu.mem_write(VRAM, b"\x11" * 0x18000)
    mu.mem_write(PAL, b"\xff" * 0x400)

    site_rt = EWRAM + (meta["patch_off"] - layout.payload_src)
    resume = site_rt + meta["patch_len"]
    mu.reg_write(getattr(U.arm_const, "UC_ARM_REG_R%d" % meta["frame_reg"]), VRAM)
    mu.reg_write(U.arm_const.UC_ARM_REG_SP, IWRAM + 0x1000)
    mu.reg_write(U.arm_const.UC_ARM_REG_LR, SENTINEL | 1)

    seen = {"hook": False}
    hook_addr = EWRAM + meta["hook_off"]

    def on_code(uc, addr, size, _):
        if hook_addr <= addr < hook_addr + B.HOOK_RESERVE:
            seen["hook"] = True
        if seen["hook"] and addr == resume:
            uc.emu_stop()

    mu.hook_add(U.UC_HOOK_CODE, on_code)
    err = None
    try:
        mu.emu_start(site_rt | 1, SENTINEL | 1, count=8000000)
    except Exception as e:                       # noqa: BLE001
        err = e
    vram = bytes(mu.mem_read(VRAM + 0xF00, B.IMG_BYTES))
    pal = bytes(mu.mem_read(PAL, 0x400))
    return dict(vram=vram, pal=pal, err=err, seen=seen["hook"],
                pc=mu.reg_read(U.arm_const.UC_ARM_REG_PC), resume=resume,
                meta=meta)


def main():
    path = sys.argv[1]
    rom = bytearray(open(path, "rb").read())
    meta = B.find_baked(rom)
    exp = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["meta" if False else "a_len"]]) + \
        bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
    ok = True
    for mode, want in (("copy", "wall"), ("zero", "magenta"), ("ff", "magenta")):
        r = run(rom, mode)
        v = r["vram"]
        magenta = v.count(0xFA)
        diff = sum(1 for i in range(B.IMG_BYTES) if v[i] != exp[i])
        good = bool(r["seen"]) and r["err"] is None and \
            r["pc"] == r["resume"] and \
            ((want == "wall" and diff == 0) or
             (want == "magenta" and magenta == B.IMG_BYTES))
        ok = ok and good
        print("%-5s : 钩子=%s 异常=%s 返回=%s 壁纸差异=%d 洋红像素=%d/%d  槽250=0x%04X -> %s"
              % (mode, r["seen"], r["err"], r["pc"] == r["resume"], diff,
                 magenta, B.IMG_BYTES,
                 struct.unpack_from("<H", r["pal"], 500)[0],
                 "OK" if good else "不对"))
    print("\n结论:", "诊断钩子行为正确" if ok else "诊断钩子有问题")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
