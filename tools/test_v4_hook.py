# -*- coding: utf-8 -*-
"""自检 V4 钩子（Flash 直读 + 只写当前帧）。

仿真按真机布局：
  - 0x08000000 起 32MB = 卡带 Flash，内容就是 .fw 文件本身（mode 4 下就是这个映射）
  - 0x09FFFFFE = CPLD 模式寄存器（写进去不报错即可）
  - VRAM 两页都预填 0x11，钩子只应该动 page0 的内容区

期望：
  page0[0xF00:0xF00+34560] == A+B 位图（差异 0）
  page1 原封不动（差异 0，仍是 0x11）
  调色板槽 20..239 == 烘焙头里那 220 色

用法: python tools/test_v4_hook.py out/v4/xxx.fw
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import unicorn as U  # noqa: E402

EWRAM, VRAM, PAL, IWRAM = B.EWRAM_MIRROR, B.VRAM, B.PALETTE, 0x03000000
ROM = 0x08000000
ROM_SIZE = 0x2000000
SENTINEL = IWRAM + 0x7000


def run(rom):
    layout = B.find_layout(rom)
    meta = B.find_baked(rom)

    mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_ARM)
    mu.mem_map(EWRAM, 0x40000)
    mu.mem_map(IWRAM, 0x8000)
    mu.mem_map(ROM, ROM_SIZE)
    mu.mem_map(VRAM, 0x18000)
    mu.mem_map(PAL, 0x400)
    mu.mem_map(0x04000000, 0x400)
    mu.mem_write(EWRAM, bytes(rom[layout.payload_src:
                                 layout.payload_src + layout.payload_len]))
    mu.mem_write(ROM, bytes(rom))
    mu.mem_write(VRAM, b"\x11" * 0x18000)
    mu.mem_write(PAL, b"\xff" * 0x400)

    site_rt = EWRAM + (meta["patch_off"] - layout.payload_src)
    resume = site_rt + meta["patch_len"]
    mu.reg_write(U.arm_const.UC_ARM_REG_SP, IWRAM + 0x1000)
    mu.reg_write(U.arm_const.UC_ARM_REG_LR, SENTINEL | 1)
    mu.reg_write(U.arm_const.UC_ARM_REG_R0, VRAM)        # frame = page0

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
    exp_pal = bytes(rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
    assert len(exp) == B.IMG_BYTES, len(exp)

    r = run(rom)
    v, p = r["vram"], r["pal"]
    page0 = v[0x0F00:0x0F00 + B.IMG_BYTES]
    page1 = v[0xA000:0xA100]
    got_pal = p[B.PAL_BASE * 2:(B.PAL_BASE + 220) * 2]

    d0 = sum(1 for i in range(B.IMG_BYTES) if page0[i] != exp[i])
    dpal = sum(1 for i in range(len(exp_pal)) if got_pal[i] != exp_pal[i])
    page1_ok = page1 == b"\x11" * 0x100

    good = (bool(r["seen"]) and r["err"] is None and r["pc"] == r["resume"]
            and d0 == 0 and dpal == 0 and page1_ok)
    print("钩子=%s 异常=%s 返回=%s" % (r["seen"], r["err"], r["pc"] == r["resume"]))
    print("page0 位图差异=%d / %d" % (d0, B.IMG_BYTES))
    print("调色板 220 色差异=%d / %d" % (dpal // 2, 220))
    print("page1 未被改动=%s" % page1_ok)
    print("\n结论:", "V4 钩子行为正确（只写当前帧）" if good else "V4 钩子有问题")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
