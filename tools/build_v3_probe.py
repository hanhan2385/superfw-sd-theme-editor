# -*- coding: utf-8 -*-
"""V3 探针：一次刷机分清「SDRAM 是全 0」「全 0xFF」还是「真有数据」。

为什么还需要这一版
------------------
诊断件实机显示**整片洋红** —— 钩子确实被调用了、地址也对（洋红正落在内容区），
但逐字节读 SDRAM 时每个字节都是 0x00 或 0xFF，全被当成"没有数据"。
这两种情况的修法完全相反，必须分开：

  全 0x00   -> bootloader 那次扩充拷贝没写到这里（块数 / 模式）
  全 0xFF   -> 这个地址在当前 CPLD 模式下根本没映射（要换存放地址）
  有真实数据 -> 数据在，直接出图（这一版就是能用的正式钩子）

判读
----
  整片**绿** -> A 段首字是 0x00000000
  整片**蓝** -> A 段首字是 0xFFFFFFFF
  **出图**   -> 读到真数据
  花屏/局部 -> 有数据但地址或长度对不上

用法:
  python tools/build_v3_probe.py out/user2/superfw-chis-修复背景.fw
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import fwbuild as F  # noqa: E402

GREEN = 0x03E0          # BGR555 纯绿
BLUE = 0x7C00           # BGR555 纯蓝
GREEN_IDX, BLUE_IDX = 0xFA, 0xFB
VRAM0 = 0x06000000
PAGE = 0xA000


class T3(B.Thumb):
    def ldr(self, rd, rn, imm=0):
        assert imm % 4 == 0 and 0 <= imm < 128
        self.h(0x6800 | ((imm >> 2) << 6) | (rn << 3) | rd)

    def jump(self, target):
        """target 可为 None（占位），之后用 here_b() 回填。"""
        self.h(0xE000)
        self.fix.append(("b", len(self.b) - 2, target))
        return len(self.fix) - 1

    def here_b(self, idx):
        kind, pos, _ = self.fix[idx]
        self.fix[idx] = (kind, pos, len(self.b))

    def finish(self, base_addr):
        while len(self.b) % 4:
            self.h(0x46C0)
        lit_pos = len(self.b)
        pool = bytearray()
        seen = {}
        for item in self.fix:
            if item[0] != "lit":
                continue
            _, pos, rd, value = item
            v = value & 0xFFFFFFFF
            if v in seen:
                off = lit_pos + seen[v]
            else:
                off = lit_pos + len(pool)
                seen[v] = len(pool)
                pool += struct.pack("<I", v)
            pc = (base_addr + pos + 4) & ~3
            imm = off - (pc - base_addr)
            assert 0 <= imm <= 1020 and imm % 4 == 0, "字面量太远: %d" % imm
            self.b[pos:pos + 2] = struct.pack("<H", 0x4800 | (rd << 8) | (imm >> 2))
        self.b += pool
        for item in self.fix:
            if item[0] == "rel":
                _, pos, target = item
                assert target is not None
                off = target - (pos + 4)
                assert off % 2 == 0 and -256 <= off < 256, "短分支超范围: %d" % off
                old = struct.unpack_from("<H", self.b, pos)[0]
                self.b[pos:pos + 2] = struct.pack("<H", (old & 0xFF00) | ((off >> 1) & 0xFF))
            elif item[0] == "b":
                _, pos, target = item
                assert target is not None, "b 占位没回填"
                off = target - (pos + 4)
                assert off % 2 == 0 and -2048 <= off < 2048, "B 超范围: %d" % off
                self.b[pos:pos + 2] = struct.pack("<H", 0xE000 | ((off >> 1) & 0x7FF))
        while len(self.b) % 4:
            self.h(0x46C0)
        return bytes(self.b)


def _fill_const(t, color_word):
    """把 0xNNNNNNNN 铺满两页内容区：r3..r6 都装同一个字，16 字节/次。"""
    t.ldr_lit(3, color_word)
    t.mov_hi(4, 3)
    t.mov_hi(5, 3)
    t.mov_hi(6, 3)
    for base in (VRAM0, VRAM0 + PAGE):
        t.ldr_lit(0, base + 0x0F00)
        t.ldr_lit(2, B.IMG_BYTES // 16)
        L = t.mark()
        t.stmia(0, (3, 4, 5, 6))
        t.subs_imm(2, 1)
        t.bcond(0x1, L)


def build(pal_sdram, a_sdram, a_len, b_sdram, b_len, base_addr):
    t = T3()
    t.push_all()

    # ① 调色板 220 色
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

    # ② 两个诊断色（常量，不依赖 SDRAM）
    t.ldr_lit(0, F.PALETTE + GREEN_IDX * 2)
    t.ldr_lit(1, GREEN)
    t.strh(1, 0)
    t.ldr_lit(0, F.PALETTE + BLUE_IDX * 2)
    t.ldr_lit(1, BLUE)
    t.strh(1, 0)

    # ③ 采样 A 段首字
    t.ldr_lit(1, a_sdram)
    t.ldr(3, 1)
    t.cmp_imm(3, 0x00)
    green = t.bcond_f(0x0)
    t.adds_imm(3, 1)                 # 0xFFFFFFFF + 1 == 0
    blue = t.bcond_f(0x0)

    # ④ NORMAL：有数据，直接两页盖图
    for base in (VRAM0, VRAM0 + PAGE):
        for dst, src, n in ((base + 0x0F00, a_sdram, a_len),
                            (base + 0x0F00 + a_len, b_sdram, b_len)):
            t.ldr_lit(0, dst)
            t.ldr_lit(1, src)
            assert n % 16 == 0, n
            t.ldr_lit(2, n // 16)
            L = t.mark()
            t.ldmia(1, (3, 4, 5, 6))
            t.stmia(0, (3, 4, 5, 6))
            t.subs_imm(2, 1)
            t.bcond(0x1, L)
    j1 = t.jump(None)

    # ⑤ GREEN
    t.here(green)
    _fill_const(t, GREEN_IDX * 0x01010101)
    j2 = t.jump(None)

    # ⑥ BLUE
    t.here(blue)
    _fill_const(t, BLUE_IDX * 0x01010101)

    # ⑦ DONE
    t.here_b(j1)
    t.here_b(j2)
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

    a_sdram = L.assets.dst + (meta["a_rom"] - L.assets.src)
    b_sdram = L.assets.dst + (meta["b_rom"] - L.assets.src)
    pal_sdram = b_sdram + B.HDR_SIZE
    data_b_sdram = b_sdram + B.HDR_SIZE + meta["pal_n"] * 2

    hook = build(pal_sdram, a_sdram, meta["a_len"], data_b_sdram,
                 meta["b_len"], hook_addr)
    if len(hook) > B.HOOK_RESERVE:
        raise SystemExit("V3 钩子太长: %d > %d" % (len(hook), B.HOOK_RESERVE))
    rom[hook_rom:hook_rom + len(hook)] = hook
    rom[hook_rom + len(hook):hook_rom + B.HOOK_RESERVE] = b"\0" * (
        B.HOOK_RESERVE - len(hook))
    if bytes(rom[meta["patch_off"]:meta["patch_off"] + 2]) != b"\x0c\xf0":
        raise SystemExit("补丁点不是 BL")
    F.reseal(rom)

    out = os.path.join("out", "diag",
                       os.path.basename(src).replace(".fw", "-V3探针.fw"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(rom)
    print("写出 %s" % out)
    print("钩子 %d B @ 0x%08X" % (len(hook), hook_addr))
    print("  A 0x%08X %d  B 0x%08X %d  PAL 0x%08X" % (
        a_sdram, meta["a_len"], data_b_sdram, meta["b_len"], pal_sdram))
    ok, why = F.validate(rom)
    print("头部校验:", "通过" if ok else why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
