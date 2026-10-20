# -*- coding: utf-8 -*-
"""回归测试：换图（rebake）必须把钩子重写成 CPU 拷贝版，并且真的能跑。

背景：v0.12 及更早装进固件的钩子是用 DMA3 从卡带 SDRAM 读位图的，实测在真机上
读不到数据（调色板是 CPU 搬的所以正常，位图一直是黑的）。rebake() 以前只覆盖
位图和调色板、完全不动钩子，于是"换图"永远换不出正确的画面。

这个测试做四件事：
  1. 拿一个已经烘焙过的固件（默认 G:/superfw-chis.fw）当输入
  2. 用一张能跟原图区分开的索引图调 rebake()
  3. 检查钩子机器码：开头是 push {r0-r7,lr}、有 ldmia/stmia、没有任何 DMA 写
  4. 用 unicorn 按真实内存布局跑一遍钩子（SDRAM 在 0x09Exxxxx），
     逐字节比对 VRAM 内容区和 220 个调色板槽

用法：
  python tools/test_rebake_hook.py [固件] [--out 输出.fw]
"""
import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import fwbake as FB          # noqa: E402
import fwbuild               # noqa: E402

from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB)                    # noqa: E402
import unicorn.arm_const as armreg                                     # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_SP,            # noqa: E402
                               UC_ARM_REG_LR)

SDRAM_BASE = 0x08000000
SDRAM_SIZE = 32 * 1024 * 1024


def new_picture(rom, info):
    """从固件里已有那张图派生一张明显不同、但色数合法的图。"""
    idx = bytearray(rom[info["a_rom"]:info["a_rom"] + info["a_len"]])
    idx += rom[info["data_b_at"]:info["data_b_at"] + info["b_len"]]
    pal = list(struct.unpack("<%dH" % FB.PAL_N,
                             bytes(rom[info["pal_at"]:info["pal_at"] + FB.PAL_N * 2])))
    # 索引错开一格（都在 PAL_BASE..PAL_BASE+PAL_N-1 内循环），调色板整体左移
    lo, hi = FB.PAL_BASE, FB.PAL_BASE + FB.PAL_N
    idx = bytes(lo + ((v - lo + 1) % FB.PAL_N) if lo <= v < hi else v for v in idx)
    pal = pal[1:] + pal[:1]
    return idx, pal


def check_code(hook):
    """钩子机器码体检。"""
    problems = []
    if len(hook) < 8:
        problems.append("钩子太短")
    if hook[:2] != b"\xFF\xB5":
        problems.append("开头不是 push {r0-r7,lr}（0xB5FF）")
    # 返回指令后面还跟着字面量池，所以不能只看最后两个字节
    words = [struct.unpack_from("<H", hook, i)[0] for i in range(0, len(hook), 2)]
    if 0xBDFF not in words:
        problems.append("里面没有 pop {r0-r7,pc}（0xBDFF）")
    if not any((w & 0xF800) == 0xC800 for w in words):
        problems.append("里面没有 ldmia（0xC8xx），说明位图不是 CPU 搬的")
    if not any((w & 0xF800) == 0xC000 for w in words):
        problems.append("里面没有 stmia（0xC0xx）")
    # 钩子不许再碰 DMA：没有 ldr rX,=0x040000D4 这样的字面量
    for i in range(0, len(hook) - 3, 2):
        if struct.unpack_from("<I", hook, i)[0] == 0x040000D4:
            problems.append("钩子里还有指向 0x040000D4 的字面量（又在用 DMA）")
            break
    # v0.14 起必须自己切 CPLD 到 Flash 模式再读（读 SDRAM 那条路真机不通）
    if not any(struct.unpack_from("<I", hook, i)[0] == FB.CPLD_REG
               for i in range(0, len(hook) - 3, 2)):
        problems.append("钩子里没有 CPLD 模式寄存器字面量（0x09FFFFFE），"
                        "不是 Flash 直读版")
    return problems


def emulate(rom, L, code_addr, frame_reg, frame_addr, idx, pal):
    """按真机布局跑钩子。

    钩子会先把 CPLD 切到 Flash 模式，那时 0x08 窗口 = 卡带 Flash，
    内容就是 .fw 文件本身；这里直接把整份固件铺在 0x08000000 上，
    模式寄存器 0x09FFFFFE 也落在同一段映射里，写进去不报错即可。
    """
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(FB.EWRAM_MIRROR, 256 * 1024)
    mu.mem_write(FB.EWRAM_MIRROR,
                 bytes(rom[L.payload_src:L.payload_src + 256 * 1024]))
    mu.mem_map(0x03000000, 32 * 1024)          # IWRAM（栈）
    mu.mem_map(0x04000000, 0x400)              # IO
    mu.mem_map(FB.PALETTE, 0x400)
    mu.mem_map(FB.VRAM, 128 * 1024)
    mu.mem_map(SDRAM_BASE, SDRAM_SIZE)         # 0x08 窗口（Flash 模式）
    mu.mem_write(SDRAM_BASE, bytes(rom))

    sp = 0x03007000
    ret = FB.EWRAM_MIRROR + 0x3F000             # 随便一个能停的 EWRAM 地址
    mu.mem_write(ret, b"\xFE\xE7")              # b .
    mu.reg_write(UC_ARM_REG_SP, sp)
    mu.reg_write(UC_ARM_REG_LR, ret)
    # 帧基址要放进钩子约定的那个寄存器（补丁点原来用的 r0/r4）
    mu.reg_write(getattr(armreg, "UC_ARM_REG_R%d" % frame_reg), frame_addr)

    err = None
    try:
        mu.emu_start(code_addr | 1, ret, count=400000)
    except Exception as exc:                    # noqa: BLE001
        err = "%s @ PC=%08X" % (type(exc).__name__, mu.reg_read(UC_ARM_REG_PC))

    vram = bytes(mu.mem_read(frame_addr + 0xF00, FB.IMG_BYTES))
    got_pal = struct.unpack("<256H", bytes(mu.mem_read(FB.PALETTE, 512)))
    return {"vram": vram, "pal": got_pal, "err": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("firmware", nargs="?", default="G:/superfw-chis.fw")
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "regress",
                                                  "rebake-hook.fw"))
    a = ap.parse_args()

    if not os.path.exists(a.firmware):
        print("找不到固件：%s" % a.firmware)
        return 2
    rom = bytearray(open(a.firmware, "rb").read())
    print("输入：%s（%d 字节）" % (os.path.basename(a.firmware), len(rom)))

    info = FB.find_baked(rom)
    if not info:
        print("这个固件里没有背景图，测试需要「已经烘焙过」的文件")
        return 2
    L = FB.find_layout(rom)
    hook_rom = L.payload_src + info["hook_off"]
    old_hook = bytes(rom[hook_rom:hook_rom + FB.HOOK_RESERVE])
    print("旧钩子（前 16 字节）：%s" % old_hook[:16].hex(" "))
    old_problems = check_code(old_hook)
    print("旧钩子体检：%s" % ("、".join(old_problems) or "没问题"))

    idx, pal = new_picture(rom, info)
    rep = FB.rebake(rom, indexed=idx, palette=pal)
    rom = rep["rom"]

    print("-" * 60)
    print("新钩子 EWRAM 0x%08X / ROM 0x%06X，%d 字节（预留 %d）"
          % (rep["hook_addr"], rep["hook_rom"], len(rep["hook"]), FB.HOOK_RESERVE))
    print("新钩子（前 16 字节）：%s" % rep["hook"][:16].hex(" "))
    problems = check_code(rep["hook"])
    print("新钩子体检：%s" % ("、".join(problems) or "没问题"))
    # 第二条指令必须是 mov r6, rX（r6 存 frame），X = 烘焙头里记的帧寄存器
    want_mov = 0x4600 | ((info["frame_reg"] & 7) << 3) | 6
    got_mov = struct.unpack_from("<H", rep["hook"], 2)[0]
    print("帧寄存器：头里记的是 r%d，钩子里 %s"
          % (info["frame_reg"],
             "一致（0x%04X）" % got_mov if got_mov == want_mov
             else "不一致（0x%04X，应为 0x%04X）" % (got_mov, want_mov)))
    if got_mov != want_mov:
        problems.append("钩子读的帧寄存器不是 r%d" % info["frame_reg"])
    print("钩子内容有变化：%s" % ("是" if rep["hook_changed"] else
                                  "没变（旧钩子本来就是 Flash 直读版，正常）"))
    # 旧钩子若是旧一代（读 SDRAM），换图必须把它换掉；本来就对的话不变才是对的
    ok = not problems and (rep["hook_changed"] or not old_problems)

    # 位图/调色板落点是否写对
    same_a = bytes(rom[rep["a_rom"]:rep["a_rom"] + rep["a_len"]]) == idx[:rep["a_len"]]
    same_b = bytes(rom[rep["data_b_at"]:rep["data_b_at"] + rep["b_len"]]) == idx[rep["a_len"]:]
    print("位图 A/B 写入：%s / %s" % (same_a, same_b))
    ok = ok and same_a and same_b

    # ---- 真跑一遍
    mu = emulate(rom, L, rep["hook_addr"], info["frame_reg"],
                 FB.VRAM, idx, pal)
    diff = sum(1 for x, y in zip(mu["vram"], idx) if x != y)
    pdiff = sum(1 for i in range(FB.PAL_N)
                if mu["pal"][FB.PAL_BASE + i] != pal[i])
    print("unicorn 跑钩子：%s" % (mu["err"] or "正常返回"))
    print("VRAM 内容区：差异 %d / %d 字节" % (diff, FB.IMG_BYTES))
    print("调色板槽 %d..%d：差异 %d / %d"
          % (FB.PAL_BASE, FB.PAL_BASE + FB.PAL_N - 1, pdiff, FB.PAL_N))
    ok = ok and mu["err"] is None and diff == 0 and pdiff == 0

    # ---- 写盘 + 校验
    out = a.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fwbuild.reseal(rom)
    open(out, "wb").write(bytes(rom))
    back = bytearray(open(out, "rb").read())
    vok, why = fwbuild.validate(back)
    print("写盘复验：%s（%s，%d 字节）" % (vok, why, len(back)))
    print("输出：%s" % out)
    ok = ok and vok and len(back) == len(rom)

    print("=" * 60)
    print("结论：", "换图路径已经会把钩子升级成 CPU 拷贝版" if ok else "还有问题")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
