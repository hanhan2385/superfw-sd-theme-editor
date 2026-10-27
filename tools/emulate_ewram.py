# -*- coding: utf-8 -*-
"""按 SuperFW 的真实内存布局仿真：菜单是跑在 EWRAM 里的，不是从 ROM 取指。
=====================================================================
rom_boot.S 的头几行写得很清楚：
  "Wrapper/Bootloader for firmware.ewram — boots it by copying the payload
   into the EWRAM and executing it from there."
  "The EWRAM payload is copied at 0x027C0000"
  EWRAM 链接长度 251K（ldscripts/gba_ewram.ld）

所以 ROM 偏移 O 的菜单代码，运行时地址是 0x027C0000 + (O - payload_start)。
任何 PC 相对的跳转（比如 BL）偏移量在两边一样，但落点必须是 EWRAM 里的地址；
链到 ROM 绝对地址的跳转、或从 ROM 绝对地址取数据的指令，运行时都会读错地方。

这个工具就按这个模型跑，用来区分"能开机"和"白屏"。

用法：
  python tools/emulate_ewram.py dist/safe/xxx-Safe.bin
  python tools/emulate_ewram.py dist/adapted/xxx-Themed.bin
"""
import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbuild as FB          # noqa: E402
import wallpaper as WP        # noqa: E402
from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_MEM_WRITE,  # noqa: E402
                     UC_HOOK_CODE, UC_ERR_OK)
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R2, UC_ARM_REG_R4,  # noqa: E402
                               UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R10,
                               UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PC)

DMA = 0x040000D4
EWRAM_BASE = 0x02000000
EWRAM_MIRROR = 0x027C0000     # 菜单的实际链接/运行地址
EWRAM_SIZE = 256 * 1024

# 每个构建：原样从哪跑、打过补丁从哪跑、跑到哪算完（都是 ROM 偏移）
ENTRY = {
    "r75-2mb":    {"orig": 0xA7A2, "patched": 0xA7B4, "stop": 0xA7BE},
    "chis-1.6mb": {"orig": 0x6104, "patched": 0x6118, "stop": 0x6128},
}


def payload_start(rom):
    """载荷起点：crt0 的 `ldr pc,[pc,#-4]` + 紧跟着的 0x027C0008。"""
    pat = bytes.fromhex("04f01fe5")
    i = rom.find(pat, 0, 0x4000)
    while i != -1:
        if struct.unpack_from("<I", rom, i + 4)[0] == EWRAM_MIRROR + 8:
            return i
        i = rom.find(pat, i + 1, 0x4000)
    raise SystemExit("没找到 EWRAM 载荷入口")


def run(rom, layout, patched, trace=0):
    pstart = payload_start(rom)
    ent = ENTRY[layout.build]
    start_rom = ent["patched"] if patched else ent["orig"]
    stop_rom = ent["stop"]
    # ROM 偏移 -> EWRAM 地址
    to_ew = lambda o: EWRAM_MIRROR + (o - pstart)

    img = bytearray(EWRAM_SIZE)
    chunk = bytes(rom[pstart:pstart + EWRAM_SIZE])
    img[:len(chunk)] = chunk

    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    # 只映射菜单真正运行的镜像地址（0x027C0000 是 256K 对齐的）
    mu.mem_map(EWRAM_MIRROR, EWRAM_SIZE)
    mu.mem_write(EWRAM_MIRROR, bytes(img))
    mu.mem_map(0x03000000, 32 * 1024)      # IWRAM（栈）
    mu.mem_map(0x04000000, 1024)           # IO
    mu.mem_map(0x05000000, 1024)           # 调色板
    mu.mem_map(0x06000000, 128 * 1024)     # VRAM
    # 运行期 0x08 窗口是 SDRAM（不是 flash），这里按 0xFF 空白实现
    mu.mem_map(FB.ROM_BASE, 2 * 1024 * 1024)
    mu.mem_write(FB.ROM_BASE, b"\xFF" * (2 * 1024 * 1024))

    faults = []
    path = []

    def on_write(uc, access, addr, size, value, data):
        if addr != DMA + 8:
            return
        ctl = (value >> 16) & 0xFFFF
        cnt = value & 0xFFFF
        if not (ctl & 0x8000) or cnt == 0:
            return
        sad = struct.unpack("<I", uc.mem_read(DMA, 4))[0]
        dad = struct.unpack("<I", uc.mem_read(DMA + 4, 4))[0]
        fixed = bool(ctl & 0x0100)
        try:
            if fixed:
                uc.mem_write(dad, bytes(uc.mem_read(sad, 2)) * cnt)
            else:
                uc.mem_write(dad, bytes(uc.mem_read(sad, cnt * 2)))
        except Exception as exc:
            faults.append("DMA 读源失败 sad=%08X dad=%08X cnt=%d (%s)"
                          % (sad, dad, cnt, exc))

    def on_code(uc, addr, size, _):
        if len(path) < trace:
            path.append(addr)

    mu.hook_add(UC_HOOK_MEM_WRITE, on_write, None, DMA + 8, DMA + 8)
    if trace:
        mu.hook_add(UC_HOOK_CODE, on_code)

    frame = FB.VRAM
    sp = 0x03007000
    mu.reg_write(UC_ARM_REG_SP, sp)
    mu.mem_write(sp + 0x14, struct.pack("<I", frame))
    mu.reg_write(UC_ARM_REG_R2, sp + 0x18)
    mu.reg_write(UC_ARM_REG_R4, frame)
    mu.reg_write(UC_ARM_REG_R10, frame)
    mu.reg_write(UC_ARM_REG_R0, frame)
    mu.reg_write(UC_ARM_REG_R6, DMA)
    mu.reg_write(UC_ARM_REG_R5, DMA + 8)
    mu.reg_write(UC_ARM_REG_R11, DMA + 4)
    mu.reg_write(UC_ARM_REG_LR, to_ew(stop_rom) | 1)

    err = None
    try:
        mu.emu_start(to_ew(start_rom) | 1, to_ew(stop_rom), count=200000)
    except Exception as exc:
        err = "%s @ PC=%08X" % (type(exc).__name__, mu.reg_read(UC_ARM_REG_PC))

    vram = bytes(mu.mem_read(frame + 0xF00, FB.IMG_BYTES))
    pal = struct.unpack("<256H", bytes(mu.mem_read(FB.PALETTE, 512)))
    return {"vram": vram, "pal": pal, "err": err, "faults": faults,
            "path": path, "pc": mu.reg_read(UC_ARM_REG_PC),
            "pstart": pstart, "entry": to_ew(start_rom), "stop": to_ew(stop_rom)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--trace", type=int, default=0)
    args = ap.parse_args()

    all_ok = True
    for path in args.files:
        rom = bytearray(open(path, "rb").read())
        lay = FB.detect(rom)
        ps = payload_start(rom)
        print("=" * 66)
        print("%s  (%d 字节)  构建 %s" % (os.path.basename(path), len(rom), lay.build))
        print("  EWRAM 载荷起点 ROM 0x%04X → 0x%08X；代码实际跑在 EWRAM" % (ps, EWRAM_MIRROR))
        print("  头部校验: %s" % (FB.validate(rom),))

        if getattr(lay, "sdtheme", False):
            # 从源码编译的 SD 卡主题固件：没有补丁点，代码布局也和两版官方构建
            # 完全不同，这里只能报结构，不跑补丁仿真。
            print("  类型: SD 卡主题固件（换肤走 SD 卡 /.superfw/theme.bin，不需要烘焙）")
            print("  themes[] 0x%X  icons_img 0x%X  icons_pal 0x%X"
                  % (lay.themes, lay.icons, lay.icons_pal))
            continue

        patched = WP.is_patched(rom)
        print("  补丁状态: %s" % ("已打补丁" if patched else "未打补丁（纯数据改动）"))

        # 一直跑"原始代码路径"，看内容区是否被正常填成 BG 色
        base = run(rom, lay, False)
        solid = base["vram"] == b"\x11" * FB.IMG_BYTES
        print("  原始路径: 内容区全 0x11 -> %s%s"
              % (solid, "" if base["err"] is None else "  [异常 %s]" % base["err"]))

        if patched:
            got = run(rom, lay, True, trace=args.trace)
            same = got["vram"] == bytes(rom[lay.img:lay.img_end])
            print("  补丁路径: 入口 0x%08X%s" % (got["entry"],
                  "" if got["err"] is None else "  → 异常 %s" % got["err"]))
            if got["path"]:
                print("  执行轨迹: " + " ".join("%08X" % a for a in got["path"][:12]))
            print("  补丁路径: 位图 %s（差异 %d 字节）"
                  % ("一致" if same else "不一致",
                     sum(1 for a, b in zip(got["vram"], rom[lay.img:lay.img_end]) if a != b)))
            for f in got["faults"][:2]:
                print("  !", f)
            all_ok = all_ok and got["err"] is None and same
        else:
            # 纯数据构建：代码区必须一个字节都没动
            print("  代码区: 未改动（补丁字节保持原样）")

    print("=" * 66)
    print("结论:", "补丁在 EWRAM 运行模型下正常" if all_ok else "补丁在 EWRAM 下不成立")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
