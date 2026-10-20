# -*- coding: utf-8 -*-
"""生成「诊断用」背景钩子：把内容区先涂洋红，只有真从 SDRAM 读到像素才盖上去。

为什么需要它
------------
背景图在实机上一直不显示，而代码逐条核过没有逻辑错。可能的失败只剩三种，
现象各不相同，这次让钩子自己把它们区分开：

  1. 位图真在 SDRAM      -> 内容区 = 正常壁纸
  2. 钩子跑了但 SDRAM 空  -> 内容区 = 一片洋红（0xFA，调色板槽 250）
  3. 钩子根本没被调用     -> 内容区 = 原样（主题底色），一个像素都不变
  4. 地址算错/写到别处    -> 画面花屏（洋红出现在别的地方）

判据：每个字节只有「非 0 且非 0xFF」才写进 VRAM —— 空 SDRAM 读回 0x00、
没映射读回 0xFF，两种都盖不上洋红，所以红色=没数据是确定的。

用法:
  python tools/build_diag_wall.py out/user2/superfw-chis-修复背景.fw
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import fwbuild as F  # noqa: E402

MARK_IDX = 0xFA                    # 洋红标记用的调色板索引（240..254 固件不用）
MARK_COLOR = 0x7C1F                # BGR555 洋红
PAL_MARK = F.PALETTE + MARK_IDX * 2


class DThumb(B.Thumb):
    def ldrb(self, rd, rn, imm=0):
        assert 0 <= imm < 32
        self.h(0x7800 | (imm << 6) | (rn << 3) | rd)

    def strb(self, rd, rn, imm=0):
        assert 0 <= imm < 32
        self.h(0x7000 | (imm << 6) | (rn << 3) | rd)


def build_diag_hook(pal_sdram, segs, frame_reg, code_addr):
    """segs = [(frame_off, sdram_src, nbytes), ...]"""
    t = DThumb()
    t.push_all()
    t.mov_hi(7, frame_reg)                       # r7 = frame

    # ① 调色板照常从 SDRAM 读（这样第 1 种情况能看到真实颜色）
    t.ldr_lit(0, pal_sdram)
    t.ldr_lit(1, F.PALETTE + B.PAL_BASE * 2)
    t.movs(2, B.PAL_N)
    L = t.mark()
    t.ldrh(3, 0)
    t.strh(3, 1)
    t.adds_imm(0, 2)
    t.adds_imm(1, 2)
    t.subs_imm(2, 1)
    t.bcond(0x1, L)

    # ② 槽 250 = 洋红（常量，不依赖 SDRAM）
    t.ldr_lit(0, PAL_MARK)
    t.ldr_lit(1, MARK_COLOR)
    t.strh(1, 0)

    # ③ 内容区整片涂成 0xFA
    t.ldr_lit(3, 0xFAFAFAFA)
    t.mov_hi(4, 3)
    t.mov_hi(5, 3)
    t.mov_hi(6, 3)
    t.ldr_lit(0, 0x0F00)
    t.adds_rr(0, 0, 7)
    t.ldr_lit(2, B.IMG_BYTES // 16)
    L = t.mark()
    t.stmia(0, (3, 4, 5, 6))
    t.subs_imm(2, 1)
    t.bcond(0x1, L)

    # ④ 逐字节从 SDRAM 盖上去；0x00 / 0xFF 当作「没有数据」，保留洋红
    for off, src, n in segs:
        t.ldr_lit(0, off)
        t.adds_rr(0, 0, 7)
        t.ldr_lit(1, src)
        t.ldr_lit(2, n)
        L = t.mark()
        t.ldrb(3, 1)
        t.cmp_imm(3, 0x00)
        z = t.bcond_f(0x0)                      # beq keep
        t.cmp_imm(3, 0xFF)
        f = t.bcond_f(0x0)                      # beq keep
        t.strb(3, 0)
        t.here(z)
        t.here(f)
        t.adds_imm(0, 1)
        t.adds_imm(1, 1)
        t.subs_imm(2, 1)
        t.bcond(0x1, L)

    t.pop_all()
    return t.finish(code_addr)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else \
        "out/user2/superfw-chis-修复背景.fw"
    rom = bytearray(open(src, "rb").read())
    meta = B.find_baked(rom)
    if not meta:
        raise SystemExit("这个固件里没有烘焙头，先正常烘焙一次")
    L = B.find_layout(rom)
    hook_off = meta["hook_off"]
    hook_addr = B.EWRAM_MIRROR + hook_off
    hook_rom = L.payload_src + hook_off

    a_rom, b_rom = meta["a_rom"], meta["b_rom"]
    a_sdram = L.assets.dst + (a_rom - L.assets.src)
    b_sdram = L.assets.dst + (b_rom - L.assets.src)
    pal_sdram = b_sdram + B.HDR_SIZE
    data_b_sdram = b_sdram + B.HDR_SIZE + meta["pal_n"] * 2
    segs = [(0x0F00, a_sdram, meta["a_len"]),
            (0x0F00 + meta["a_len"], data_b_sdram, meta["b_len"])]

    hook = build_diag_hook(pal_sdram, segs, meta["frame_reg"], hook_addr)
    if len(hook) > B.HOOK_RESERVE:
        raise SystemExit("诊断钩子太长: %d > %d" % (len(hook), B.HOOK_RESERVE))
    rom[hook_rom:hook_rom + len(hook)] = hook
    rom[hook_rom + len(hook):hook_rom + B.HOOK_RESERVE] = b"\0" * (
        B.HOOK_RESERVE - len(hook))
    F.reseal(rom)

    out = os.path.join("out", "diag", os.path.basename(src).replace(".fw", "-诊断.fw"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(rom)
    print("写出 %s（%d 字节）" % (out, len(rom)))
    print("钩子 %d B @ EWRAM 0x%08X（ROM 0x%06X），frame=r%d" % (
        len(hook), hook_addr, hook_rom, meta["frame_reg"]))
    for off, s, n in segs:
        print("  段 frame+0x%04X <- 0x%08X  %d B" % (off, s, n))
    ok, why = F.validate(rom)
    print("头部校验:", "通过" if ok else why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
