# -*- coding: utf-8 -*-
"""自检 V2 钩子（绝对地址、双页）。

期望：
  copy  -> 两页内容区都等于 A+B 位图，洋红 0
  zero  -> 两页整片洋红（SDRAM 读回 0）
  ff    -> 两页整片洋红（SDRAM 读回 0xFF）

用法: python tools/test_v2_hook.py out/diag/xxx-V2.fw
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
PAGES = (0x0000, 0xA000)


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
        mu.emu_start(site_rt | 1, SENTINEL | 1, count=20000000)
    except Exception as e:                       # noqa: BLE001
        err = e
    vram = bytes(mu.mem_read(VRAM, 0x18000))
    pal = bytes(mu.mem_read(PAL, 0x400))
    return dict(vram=vram, pal=pal, err=err, seen=seen["hook"],
                pc=mu.reg_read(U.arm_const.UC_ARM_REG_PC), resume=resume)


def main():
    path = sys.argv[1]
    rom = bytearray(open(path, "rb").read())
    meta = B.find_baked(rom)
    exp = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]]) + \
        bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
    assert len(exp) == B.IMG_BYTES, len(exp)

    ok = True
    for mode, want in (("copy", "wall"), ("zero", "magenta"), ("ff", "magenta")):
        r = run(rom, mode)
        v = r["vram"]
        parts = []
        good = bool(r["seen"]) and r["err"] is None and r["pc"] == r["resume"]
        for off in PAGES:
            page = v[off + 0xF00:off + 0xF00 + B.IMG_BYTES]
            magenta = page.count(0xFA)
            diff = sum(1 for i in range(B.IMG_BYTES) if page[i] != exp[i])
            if want == "wall":
                good = good and diff == 0
            else:
                good = good and magenta == B.IMG_BYTES
            parts.append("页@0x%04X 差异=%d 洋红=%d" % (off, diff, magenta))
        ok = ok and good
        print("%-4s : 钩子=%s 异常=%s 返回=%s 槽250=0x%04X | %s -> %s"
              % (mode, r["seen"], r["err"], r["pc"] == r["resume"],
                 struct.unpack_from("<H", r["pal"], 500)[0],
                 "  ".join(parts), "OK" if good else "不对"))
    print("\n结论:", "V2 钩子行为正确（双页）" if ok else "V2 钩子有问题")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
