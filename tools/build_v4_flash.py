# -*- coding: utf-8 -*-
"""V4：直接从卡带 Flash 读位图与调色板，只写当前帧。

为什么换掉 SDRAM 方案
--------------------
V3 探针上机显示**整片蓝** = 从 0x09E25800 读到 0xFFFFFFFF，说明这块 30MB 的
地址在当前卡上根本没映射（字体在 0x08E80000 = 14.5MB，是好的 → SDRAM 只到
16MB 左右）。位图被 bootloader 拷到 0x09E00000 之后就读不到了。

新做法：钩子自己把 CPLD 切到 Flash 模式（就是 bootloader 拷 assets 时用的
mode 4），直接从 ROM 0x0817A1F8 / 0x08191074 读位图，读完切回 SDRAM 模式
（固件常态是 set_supercard_mode(SDRAM, write, sd) = 0x7）。完全不碰 SDRAM
的高位区域。

另一个修复：**只写当前帧（r0 + 0xF00）**，不再两页一起写。
V1（单页）不闪、V2/V3（双页）闪，说明闪字就是"把已显示的那一页也涂掉"造成的。

用法:
  python tools/build_v4_flash.py out/user2/superfw-chis-修复背景.fw
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import fwbuild as F  # noqa: E402

REG_MODE = 0x09FFFFFE
MAGIC = 0xA55A
M_FLASH = 4          # MAPPED_FIRMWARE | write
M_SDRAM = 7          # MAPPED_SDRAM | write | sdcard（固件常态）
NOP = 0x46C0         # mov r8, r8


MAGENTA = 0x7C1F
FAIL_IDX = 0xFA


class T(B.Thumb):
    def ldr(self, rd, rn, imm=0):
        assert imm % 4 == 0 and 0 <= imm < 128
        self.h(0x6800 | ((imm >> 2) << 6) | (rn << 3) | rd)

    def jump(self, target):
        """无条件长跳（±2KB），target 可为 None 占位，之后用 here() 回填。"""
        self.h(0xE000)
        self.fix.append(("rel", len(self.b) - 2, target))
        return len(self.fix) - 1


def build(pal_flash, a_flash, a_len, b_flash, b_len, base_addr):
    t = T()
    t.push_all()
    t.mov_hi(6, 0)                       # r6 = frame（进入时 r0）

    # ① CPLD 切到 Flash 模式
    t.ldr_lit(0, REG_MODE)
    t.ldr_lit(1, MAGIC)
    t.strh(1, 0)
    t.strh(1, 0)
    t.movs(1, M_FLASH)
    t.strh(1, 0)
    t.strh(1, 0)
    for _ in range(4):
        t.h(NOP)

    # ② 读一下 A 段首字，Flash 若是空的（0 / 0xFF）就走失败分支
    t.ldr_lit(0, a_flash)
    t.ldr(3, 0)
    t.cmp_imm(3, 0)
    bad1 = t.bcond_f(0x0)
    t.adds_imm(3, 1)
    bad2 = t.bcond_f(0x0)

    # ③ 调色板 220 色：Flash -> 0x05000028
    t.ldr_lit(0, pal_flash)
    t.ldr_lit(1, B.PALETTE + B.PAL_BASE * 2)
    t.movs(2, B.PAL_N)
    L = t.mark()
    t.ldrh(3, 0)
    t.strh(3, 1)
    t.adds_imm(0, 2)
    t.adds_imm(1, 2)
    t.subs_imm(2, 1)
    t.bcond(0x1, L)

    # ④ dst = frame + 0xF00
    t.ldr_lit(0, 0x0F00)
    t.adds_rr(0, 6, 0)

    # ⑤ 位图 A + B：Flash -> VRAM（16 字节/次）
    for src, n in ((a_flash, a_len), (b_flash, b_len)):
        assert n % 16 == 0, n
        t.ldr_lit(1, src)
        t.ldr_lit(2, n // 16)
        M = t.mark()
        t.ldmia(1, (3, 4, 5, 7))
        t.stmia(0, (3, 4, 5, 7))
        t.subs_imm(2, 1)
        t.bcond(0x1, M)
    done = t.jump(None)

    # ⑥ 失败分支：整片洋红（说明 Flash 也没读到数据）
    t.here(bad1)
    t.here(bad2)
    t.ldr_lit(0, B.PALETTE + FAIL_IDX * 2)
    t.ldr_lit(1, MAGENTA)
    t.strh(1, 0)
    t.ldr_lit(0, FAIL_IDX * 0x01010101)
    t.mov_hi(1, 0)
    t.mov_hi(2, 0)
    t.mov_hi(3, 0)
    t.ldr_lit(7, 0x0F00)
    t.adds_rr(7, 6, 7)
    t.ldr_lit(4, (a_len + b_len) // 16)
    M = t.mark()
    t.stmia(7, (0, 1, 2, 3))
    t.subs_imm(4, 1)
    t.bcond(0x1, M)

    # ⑦ 恢复 SDRAM 模式
    t.here(done)
    t.ldr_lit(0, REG_MODE)
    t.ldr_lit(1, MAGIC)
    t.strh(1, 0)
    t.strh(1, 0)
    t.movs(1, M_SDRAM)
    t.strh(1, 0)
    t.strh(1, 0)
    for _ in range(4):
        t.h(NOP)

    t.pop_all()
    return t.finish(base_addr)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "out/user2/superfw-chis-修复背景.fw"
    rom = bytearray(open(src, "rb").read())
    meta = B.find_baked(rom)
    if not meta:
        raise SystemExit("没有烘焙头")
    L = B.find_layout(rom)
    hook_off = meta["hook_off"]
    hook_addr = B.EWRAM_MIRROR + hook_off
    hook_rom = L.payload_src + hook_off

    ROM_BASE = 0x08000000
    pal_flash = ROM_BASE + meta["pal_at"]
    a_flash = ROM_BASE + meta["a_rom"]
    b_flash = ROM_BASE + meta["data_b_at"]

    hook = build(pal_flash, a_flash, meta["a_len"], b_flash, meta["b_len"], hook_addr)
    if len(hook) > B.HOOK_RESERVE:
        raise SystemExit("V4 钩子太长: %d > %d" % (len(hook), B.HOOK_RESERVE))
    rom[hook_rom:hook_rom + len(hook)] = hook
    rom[hook_rom + len(hook):hook_rom + B.HOOK_RESERVE] = b"\0" * (
        B.HOOK_RESERVE - len(hook))
    if bytes(rom[meta["patch_off"]:meta["patch_off"] + 2]) != b"\x0c\xf0":
        raise SystemExit("补丁点不是 BL")
    F.reseal(rom)

    out = os.path.join("out", "v4", "superfw-chis-背景Flash版.fw")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(rom)
    print("写出 %s" % out)
    print("钩子 %d B @ 0x%08X（只写当前帧）" % (len(hook), hook_addr))
    print("  PAL 0x%08X  A 0x%08X(%d)  B 0x%08X(%d)" % (
        pal_flash, a_flash, meta["a_len"], b_flash, meta["b_len"]))
    ok, why = F.validate(rom)
    print("头部校验:", "通过" if ok else why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
