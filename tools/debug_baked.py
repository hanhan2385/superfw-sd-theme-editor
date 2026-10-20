"""调试：跑烘焙钩子，崩溃时打印 PC 与最后若干条指令。

用法: python tools/debug_baked.py out/cn-baked.bin
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))
import fwbake as B            # noqa: E402
import verify_baked as V       # noqa: E402
import unicorn as U            # noqa: E402
from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ_UNMAPPED


def main():
    path = sys.argv[1]
    rom = bytearray(open(path, "rb").read())
    layout = B.find_layout(rom)
    meta = B.find_baked(rom)
    sdram = V.bootload_sdram(rom, layout)

    mu = U.Uc(UC_ARCH_ARM, UC_MODE_ARM)
    mu.mem_map(V.EWRAM, V.EWRAM_SIZE)
    mu.mem_map(V.IWRAM, 0x8000)
    mu.mem_map(V.SDRAM, V.SDRAM_SIZE)
    mu.mem_map(V.VRAM, 0x18000)
    mu.mem_map(V.PAL, 0x400)
    mu.mem_map(V.REGS, 0x400)
    mu.mem_write(V.EWRAM, bytes(rom[layout.payload_src:layout.payload_src + layout.payload_len]))
    mu.mem_write(V.SDRAM, bytes(sdram))
    mu.mem_write(V.VRAM, b"\x11" * 0x18000)
    mu.mem_write(V.PAL, b"\xff" * 0x400)

    ring = []
    dma = [0]

    def on_write(mu_, access, address, size, value, user):
        if address != V.REGS + 0xDC:
            return
        cnt = value & 0xFFFFFFFF
        if not (cnt & 0x8000):
            return
        sad = struct.unpack("<I", mu_.mem_read(V.REGS + 0xD4, 4))[0]
        dad = struct.unpack("<I", mu_.mem_read(V.REGS + 0xD8, 4))[0]
        count = (cnt >> 16) & 0xFFFF
        data = bytes(mu_.mem_read(sad, count * 4))
        mu_.mem_write(dad, data)
        dma[0] += 1
        mu_.mem_write(V.REGS + 0xDC, struct.pack("<I", cnt & 0xFFFF7FFF))

    mu.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=V.REGS, end=V.REGS + 0x3FF)

    def on_code(mu_, address, size, user):
        if address == V.SENTINEL:
            mu_.emu_stop()
            return
        ring.append((address, size, mu_.reg_read(U.arm_const.UC_ARM_REG_SP)))
        if len(ring) > 4000:
            del ring[:2000]

    mu.hook_add(UC_HOOK_CODE, on_code)

    def on_rd(mu_, access, address, size, value, user):
        print("!! 非法读 0x%08X (size %d) pc=0x%08X" % (address, size, mu_.reg_read(13)))

    mu.hook_add(UC_HOOK_MEM_READ_UNMAPPED, on_rd)

    site_rt = V.EWRAM + (meta["patch_off"] - layout.payload_src)
    mu.reg_write(U.arm_const.UC_ARM_REG_R4, V.FRAME)
    mu.reg_write(U.arm_const.UC_ARM_REG_LR, V.SENTINEL | 1)
    mu.reg_write(U.arm_const.UC_ARM_REG_SP, V.IWRAM + 0x1000)

    err = None
    try:
        mu.emu_start(site_rt | 1, V.SENTINEL | 1, count=200000)
    except U.UcError as e:
        err = e

    regs = ["r%d" % i for i in range(13)] + ["sp", "lr", "pc"]
    names = [getattr(U.arm_const, "UC_ARM_REG_" + r.upper()) for r in regs]
    print("错误: %r" % err)
    print("PC = 0x%08X" % mu.reg_read(28))
    print(" ".join("%s=0x%08X" % (r, mu.reg_read(n)) for r, n in zip(regs, names)))
    print("DMA 次数 %d" % dma[0])
    print("最后指令:")
    for a, s, sp in ring[-40:]:
        print("  0x%08X  size %d  sp 0x%08X" % (a, s, sp))


if __name__ == "__main__":
    main()
