# -*- coding: utf-8 -*-
"""V2 背景钩子：**完全不依赖 frame 寄存器**，直接按 VRAM 绝对地址写两页。

为什么放弃 r0
-------------
V1（v0.13 与 -诊断.fw）把 `r0` 当作 frame 指针（`r0 + 0xF00` 才是内容区）。
实机两次都没出现任何写入（内容区停在 setup_video 的 0xFFFF = 白），
而原填充确实被 BL 删掉了（底色 0x1111 深灰 -> 白），说明补丁生效、钩子没写对地方。
最省事的定案方式：不管 r0 是什么，双缓冲的两页都按绝对地址写一遍。

判据（互不重叠）
----------------
  图正常      -> 钩子执行、SDRAM 有位图、绝对地址有效
  整片洋红    -> 钩子执行了，但 SDRAM 读回全 0 或全 0xFF（位图没进 SDRAM）
  整片纯白    -> 钩子根本没被调用（原填充已被删，所以是 0xFFFF）
  花屏        -> 地址算错

写法要点
--------
* 洋红底用 **32 位 stmia**；真机 VRAM 不吃 8 位写（strb），一个字节都不单写。
* 盖图用 **32 位 ldr/str**，遇到 0x00000000 或 0xFFFFFFFF 视为"没有数据"，保留洋红。
* 内容区起始 = 页 + 0xF00（16*240），B 段接在 A 段后面 +0x5910。
* 两页 = 0x06000000 / 0x0600A000，不关心当前显示的是哪一页。

用法:
  python tools/build_v2_wall.py out/user2/superfw-chis-修复背景.fw
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import fwbuild as F  # noqa: E402

MARK_IDX = 0xFA
MARK_COLOR = 0x7C1F
PAL_MARK = F.PALETTE + MARK_IDX * 2
VRAM0 = 0x06000000
PAGE = 0xA000


class T2(B.Thumb):
    def ldr(self, rd, rn, imm=0):
        assert imm % 4 == 0 and 0 <= imm < 128
        self.h(0x6800 | ((imm >> 2) << 6) | (rn << 3) | rd)

    def finish(self, base_addr):
        """跟父类一样，但**相同字面量只放进池一次** —— 两页/两段会重复引用
        同样的 SDRAM 源地址与长度，不去重就要多花 20 多字节。"""
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
            if item[0] != "rel":
                continue
            _, pos, target = item
            assert target is not None, "bcond_f 占位没被 here() 填上"
            off = target - (pos + 4)
            assert off % 2 == 0
            assert -256 <= off < 256, "短分支超范围: %d" % off
            old = struct.unpack_from("<H", self.b, pos)[0]
            self.b[pos:pos + 2] = struct.pack("<H", (old & 0xFF00) | ((off >> 1) & 0xFF))
        while len(self.b) % 4:
            self.h(0x46C0)
        return bytes(self.b)


def build(pal_sdram, a_sdram, a_len, b_sdram, b_len, base_addr):
    t = T2()
    t.push_all()

    # ① 调色板 220 色 -> 0x05000028
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

    # ② 槽 250 = 洋红（常量，不读 SDRAM）
    t.ldr_lit(0, PAL_MARK)
    t.ldr_lit(1, MARK_COLOR)
    t.strh(1, 0)

    # ③ 两页：整片洋红底 + 有数据才盖
    for base in (VRAM0, VRAM0 + PAGE):
        t.ldr_lit(3, 0xFAFAFAFA)
        t.mov_hi(4, 3)
        t.mov_hi(5, 3)
        t.mov_hi(6, 3)
        t.ldr_lit(0, base + 0x0F00)
        t.ldr_lit(2, B.IMG_BYTES // 16)
        L = t.mark()
        t.stmia(0, (3, 4, 5, 6))
        t.subs_imm(2, 1)
        t.bcond(0x1, L)

        for dst, src, n in ((base + 0x0F00, a_sdram, a_len),
                            (base + 0x0F00 + a_len, b_sdram, b_len)):
            assert n % 4 == 0, n
            t.ldr_lit(0, dst)
            t.ldr_lit(1, src)
            t.ldr_lit(2, n // 4)
            L = t.mark()
            t.ldr(3, 1)
            t.cmp_imm(3, 0x00)          # == 0 ?
            z = t.bcond_f(0x0)
            t.adds_imm(3, 1)            # 0xFFFFFFFF + 1 == 0
            f = t.bcond_f(0x0)
            t.subs_imm(3, 1)
            t.str(3, 0)
            t.here(z)
            t.here(f)
            t.adds_imm(0, 4)
            t.adds_imm(1, 4)
            t.subs_imm(2, 1)
            t.bcond(0x1, L)

    t.pop_all()
    return t.finish(base_addr)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "out/user2/superfw-chis-修复背景.fw"
    rom = bytearray(open(src, "rb").read())
    meta = B.find_baked(rom)
    if not meta:
        raise SystemExit("没有烘焙头，先正常烘焙一次")
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
        raise SystemExit("V2 钩子太长: %d > %d" % (len(hook), B.HOOK_RESERVE))
    rom[hook_rom:hook_rom + len(hook)] = hook
    rom[hook_rom + len(hook):hook_rom + B.HOOK_RESERVE] = b"\0" * (
        B.HOOK_RESERVE - len(hook))

    site = bytes(rom[meta["patch_off"]:meta["patch_off"] + 2])
    if site != b"\x0c\xf0":
        raise SystemExit("补丁点已经不是 BL（%s），请从原始烘焙件重来" % site.hex())
    F.reseal(rom)

    out = os.path.join("out", "diag",
                       os.path.basename(src).replace(".fw", "-V2.fw"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(rom)
    print("写出 %s（%d 字节）" % (out, len(rom)))
    print("钩子 %d B @ EWRAM 0x%08X（ROM 0x%06X）" % (len(hook), hook_addr, hook_rom))
    print("  A 段 0x%08X %d B   B 段 0x%08X %d B" % (
        a_sdram, meta["a_len"], data_b_sdram, meta["b_len"]))
    print("  调色板 0x%08X 220 色" % pal_sdram)
    ok, why = F.validate(rom)
    print("头部校验:", "通过" if ok else why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
