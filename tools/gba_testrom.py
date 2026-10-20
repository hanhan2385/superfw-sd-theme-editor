"""造一个独立测试卡带：mGBA 里正常开机，跑固件里原封不动的那段钩子。

为什么要这么绕：SuperFW 是烧录卡固件，正常开机要跟卡上的 CPLD/SDRAM 握手，
mGBA 没有这些硬件，菜单初始化跑不完。而用 GDB 强行注入执行时，mGBA 的调试口
对 VRAM 的写入/时序又不完全等价于真实运行，结论不可信。

这个测试卡带只做一件事：像普通游戏一样开机 → 设 mode 4 → 把 220 色调色板
写进槽 20..239 → 跳到钩子 → 钩子跑完就停。钩子字节直接从成品固件里搬，一个
字节都不改；数据也放在和固件里完全相同的 ROM 偏移上，所以钩子的字面量不用动。
"""
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B      # noqa: E402

HOOK_AT = 0x08000400
BOOT_AT = 0x08000200
ARM_AT = 0x080000C0


def thumb_bl(site, target):
    return B.thumb_bl(site, target)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "out", "bocchi-v002.bin")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        ROOT, "out", "mgba", "hooktest.gba")

    fw = bytearray(open(src, "rb").read())
    L = B.find_layout(fw)
    meta = B.find_baked(fw)
    hook_off, hook_addr = meta["hook_off"], L.payload_src + meta["hook_off"]
    n_hook = B.HOOK_RESERVE                   # 整个填充区都搬，别按钩子长度截断

    # 2MB 空白卡带，数据放到和固件里一模一样的偏移上
    rom = bytearray(b"\x00" * 0x200000)
    rom[0:0xC0] = fw[0:0xC0]                    # 复用原固件的 logo 和头
    a_off, b_off, pal_off = meta["a_rom"], meta["data_b_at"], meta["pal_at"]
    a_len, b_len = meta["a_len"], meta["b_len"]

    # mGBA 会把这张 2MB 卡当 1MB 镜像（实测 0x0818A68C 读不到），所以数据统一
    # 放在 1MB 以下；钩子的字面量跟着改，代码本身不动。
    LOW = os.environ.get("LOWDATA", "1") == "1"
    src_a, src_pal, src_b = a_off, pal_off, b_off
    if LOW:
        src_a, src_pal, src_b = 0x040000, 0x038000, 0x070000
    rom[src_a:src_a + a_len] = fw[a_off:a_off + a_len]
    rom[src_pal:src_pal + meta["pal_n"] * 2] = \
        fw[pal_off:pal_off + meta["pal_n"] * 2]
    rom[src_b:src_b + b_len] = fw[b_off:b_off + b_len]
    print("数据放到 A@0x%06X 调色板@0x%06X B@0x%06X%s"
          % (src_a, src_pal, src_b, "（1MB 以下）" if LOW else ""))

    hook = fw[hook_addr: hook_addr + n_hook]     # 钩子整段（含字面量池）
    hook_blob = bytearray(hook)
    # 字面量指向 SDRAM，测试卡带里改成 ROM 里的同一份数据
    a_sd = L.assets.dst + (a_off - L.assets.src)
    b_sd = L.assets.dst + (meta["b_rom"] - L.assets.src)   # 用 B 段头的位置算，不是位图位置
    subs = {a_sd: 0x08000000 + src_a,
            b_sd + B.HDR_SIZE: 0x08000000 + src_pal,
            b_sd + B.HDR_SIZE + meta["pal_n"] * 2: 0x08000000 + src_b}
    n = 0
    for i in range(0, n_hook - 4, 2):
        v = struct.unpack_from("<I", hook_blob, i)[0]
        if v in subs:
            struct.pack_into("<I", hook_blob, i, subs[v])
            n += 1
    print("钩子 %d 字节，改写 %d 处字面量" % (n_hook, n))

    t = B.Thumb()
    t.ldr_lit(0, 0x04000000)                    # DISPCNT
    t.ldr_lit(1, 0x0404)                        # mode 4 + BG2
    t.str(1, 0, 0)
    t.ldr_lit(0, 0x08000000 + src_pal)          # 调色板源
    t.ldr_lit(1, 0x05000028)                    # 槽 20
    t.movs(2, meta["pal_n"])
    lp = t.mark()
    t.ldrh(3, 0)
    t.strh(3, 1)
    t.adds_imm(0, 2)
    t.adds_imm(1, 2)
    t.subs_imm(2, 1)
    t.bcond(1, lp)
    t.ldr_lit(0, 0x03007F00)                    # 栈
    t.h(0x4685)                                 # mov sp, r0
    # 钩子从哪个寄存器拿帧基址，随构建而不同（chis 是 r0、有些构建是 r4），
    # 一定要照烘焙头里记的那个来，写死 r4 的话 chis 会用 r0 当帧基址
    # ——r0 这时是栈顶，DMA 会把 IWRAM 连同返回地址一起冲掉，钩子再也回不来。
    fr = meta["frame_reg"]
    if fr is None:
        fr = 4
    t.ldr_lit(fr, 0x06000000)                   # rX = 帧基址（钩子要的）
    bl_at = BOOT_AT + len(t.b)
    t.b += thumb_bl(bl_at, HOOK_AT)
    # 收尾自跳必须是 0xE7FE：bcond(0xE) 编出来是 0xDEFE，ARM7TDMI 当未定义指令
    t.h(0xE7FE)                                 # b .
    boot = t.finish(BOOT_AT)

    rom[HOOK_AT - 0x08000000:HOOK_AT - 0x08000000 + n_hook] = hook_blob
    rom[BOOT_AT - 0x08000000:BOOT_AT - 0x08000000 + len(boot)] = boot

    # ARM 入口：b 0xC0 → ldr r0,=0x08000201 ; bx r0
    struct.pack_into("<I", rom, 0x00, 0xEA00002E)          # b 0x080000C0
    struct.pack_into("<I", rom, 0xC0, 0xE59F0004)          # ldr r0,[pc,#4]
    struct.pack_into("<I", rom, 0xC4, 0xE12FFF10)          # bx r0
    struct.pack_into("<I", rom, 0xC8, 0xE1A00000)          # nop
    struct.pack_into("<I", rom, 0xCC, BOOT_AT | 1)

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, "wb").write(rom)
    print("已写出 %s  %d B" % (dst, len(rom)))
    print("  数据 A@0x%06X B@0x%06X 调色板@0x%06X" % (src_a, src_b, src_pal))
    print("  引导 @0x%08X  钩子 @0x%08X" % (BOOT_AT, HOOK_AT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
