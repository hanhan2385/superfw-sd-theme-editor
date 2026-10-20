"""生成「仅供 mGBA 验证」的测试版固件。

真机固件依赖烧录卡上的 CPLD 和 SDRAM，mGBA 里这两样都不存在，所以：
  1) 把 `wait_cpld()` 里的 `ldrh r2,[r1]` 改成 `movs r2,#1`，让握手立刻成功；
  2) 把钩子里指向 SDRAM 的 DMA 源地址改成指向 ROM（数据本来就存在 ROM 里）。

**这个产物只是给模拟器看的，绝对不能刷进卡带。**
"""
import os
import shutil
import struct
import sys

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B      # noqa: E402

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)


def rt2rom(off_rt, payload_src):
    return payload_src + (off_rt - B.EWRAM_MIRROR)


def patch_cpld(rom, payload_src, verbose=True):
    """把所有 `ldrh rX,[rY]` + `lsls rX,rX,#31` + `bne` 的轮询改成必然成功。"""
    seg_from = payload_src
    seg_to = payload_src + 0x40000
    hits = []
    i = seg_from
    while i < min(seg_to, len(rom)) - 8:
        w0, w1 = struct.unpack_from("<HH", rom, i)
        # ldrh rD,[rB] = 0x8800 | (imm5<<6) | (rB<<3) | rD
        if (w0 & 0xF800) == 0x8800:
            rd = w0 & 7
            # lsls rD,rD,#31
            if w1 == (0x0000 | (31 << 6) | (rd << 3) | rd):
                nxt = struct.unpack_from("<H", rom, i + 4)[0]
                if (nxt & 0xFF00) == 0xD100:      # bne
                    rom[i:i + 2] = struct.pack("<H", 0x2000 | (1 << 8) | rd)  # movs rD,#1
                    hits.append(i)
                    i += 4
                    continue
        i += 2
    if verbose:
        for h in hits:
            print("   绕过 CPLD 轮询 @ ROM 0x%06X (运行 0x%08X)"
                  % (h, B.EWRAM_MIRROR + h - payload_src))
    return hits


def retarget_dma(rom, meta, L, payload_src, verbose=True):
    """钩子的字面量池里，把 SDRAM 地址换成同内容的 ROM 地址。"""
    hook_rom = payload_src + meta["hook_off"]
    a_sd = L.assets.dst + (meta["a_rom"] - L.assets.src)
    b_sd = L.assets.dst + (meta["b_rom"] - L.assets.src)
    pal_sd = b_sd + B.HDR_SIZE
    data_b_sd = b_sd + B.HDR_SIZE + meta["pal_n"] * 2
    if verbose:
        print("   SDRAM 侧: A 0x%08X  B 0x%08X  调色板 0x%08X" % (a_sd, b_sd, pal_sd))
    subs = {
        a_sd: 0x08000000 + meta["a_rom"],
        data_b_sd: 0x08000000 + meta["data_b_at"],
        pal_sd: 0x08000000 + meta["pal_at"],
    }
    n = 0
    for i in range(0, 0x100 - 4, 2):
        v = struct.unpack_from("<I", rom, hook_rom + i)[0]
        if v in subs:
            struct.pack_into("<I", rom, hook_rom + i, subs[v])
            n += 1
            if verbose:
                print("   DMA 源 0x%08X -> 0x%08X" % (v, subs[v]))
    if verbose:
        print("   共改写 %d 处字面量" % n)
    return n


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "dist", "v0.01", "固件", "SuperFW-chis-r75-CN-v0.01.gba")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        ROOT, "out", "mgba", "test-mgba.gba")
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    rom = bytearray(open(src, "rb").read())
    L = B.find_layout(rom)
    meta = B.find_baked(rom)
    if not meta:
        print("源固件没有烘焙背景图")
        return 1
    print("源 %s  payload_src 0x%06X" % (os.path.basename(src), L.payload_src))
    print("  钩子 0x%08X  A@0x%08X(%d)  B@0x%08X  调色板@0x%08X"
          % (meta["hook_off"], meta["a_rom"], meta["a_len"],
             meta["b_rom"], meta["pal_at"]))
    patch_cpld(rom, L.payload_src)
    retarget_dma(rom, meta, L, L.payload_src)
    open(dst, "wb").write(rom)
    print("已写出 %s（仅模拟器用）" % dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
