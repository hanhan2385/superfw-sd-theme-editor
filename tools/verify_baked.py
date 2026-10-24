"""验证烘焙结果：按真机布局从渲染点真跑钩子。

v0.14 起的钩子（也是唯一该存在的形态）：
  1. 把 CPLD 切到 Flash 模式（0x09FFFFFE 写 0xA55A 两遍 + 模式 4 两遍）；
  2. 直接从卡带 Flash（0x08xxxxxx = 0x08000000 + 固件文件偏移）读 220 色
     调色板和 240×144 位图，用 CPU 的 ldmia/stmia 铺进 **当前帧** 的
     frame+0xF00；
  3. 再把 CPLD 切回 SDRAM 模式（7）。

所以仿真时 0x08000000 起铺的就是整份 .fw 文件；不再有 assets→SDRAM 拷贝，
也不该出现任何 DMA 写（真机上 DMA 从 CPLD 后面读数据是读不到的）。

用法:
  python tools/verify_baked.py baked.fw
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fwbake as B   # noqa: E402

EWRAM = B.EWRAM_MIRROR
VRAM = B.VRAM
PAL = B.PALETTE
REGS = 0x04000000
FLASH = 0x08000000
FLASH_SIZE = 0x2000000
IWRAM = 0x03000000
FRAME = 0x06000000
SENTINEL = IWRAM + 0x7000


def run(rom):
    import unicorn as U
    from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_MEM_WRITE, UC_HOOK_CODE

    layout = B.find_layout(rom)
    meta = B.find_baked(rom)
    if meta is None:
        raise SystemExit("这个固件里没有烘焙标记")

    mu = U.Uc(UC_ARCH_ARM, UC_MODE_ARM)
    mu.mem_map(EWRAM, 0x40000)
    mu.mem_map(IWRAM, 0x8000)
    mu.mem_map(FLASH, FLASH_SIZE)          # 0x08 窗口（Flash 模式）+ 0x09FFFFFE
    mu.mem_map(VRAM, 0x18000)
    mu.mem_map(PAL, 0x400)
    mu.mem_map(REGS, 0x400)

    mu.mem_write(EWRAM, bytes(rom[layout.payload_src:
                                 layout.payload_src + layout.payload_len]))
    mu.mem_write(FLASH, bytes(rom))
    mu.mem_write(VRAM, b"\x11" * 0x18000)      # 内容区先填 BG 色
    mu.mem_write(PAL, b"\xff" * 0x400)

    state = {"dma": 0, "bad": []}

    def on_write(mu_, access, address, size, value, user):
        if REGS + 0xD4 <= address < REGS + 0xE0:
            state["dma"] += 1
            state["bad"].append(("钩子在写 DMA 寄存器", hex(address)))

    mu.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=REGS, end=REGS + 0x3FF)

    site_rt = EWRAM + (meta["patch_off"] - layout.payload_src)
    start = site_rt | 1
    patch_len = meta["patch_len"]
    resume = site_rt + patch_len          # 钩子返回后真正该落到的地方
    # frame 指针所在的寄存器随构建不同（r75 是 r4，chis 是 r0）——
    # 只写那一个，别顺手把 r4 也写了：那会把"钩子读错了寄存器"这种 bug 盖掉。
    fr = meta["frame_reg"]
    mu.reg_write(getattr(U.arm_const, "UC_ARM_REG_R%d" % fr), FRAME)
    mu.reg_write(U.arm_const.UC_ARM_REG_LR, SENTINEL | 1)
    mu.reg_write(U.arm_const.UC_ARM_REG_SP, IWRAM + 0x1000)

    trace = []
    hook_addr = EWRAM + meta["hook_off"]
    seen = {"hook": False}

    def on_code(mu_, address, size, user):
        if address == SENTINEL:
            mu_.emu_stop()
            return
        if hook_addr <= address < hook_addr + B.HOOK_RESERVE:
            seen["hook"] = True
        # 钩子跑完、落回 site+patch_len —— 就是"干净返回"
        if seen["hook"] and address == resume:
            mu_.emu_stop()
            return
        if len(trace) < 32:
            trace.append(address)

    mu.hook_add(UC_HOOK_CODE, on_code)

    err = None
    try:
        mu.emu_start(start, SENTINEL | 1, count=8000000)
    except Exception as e:                     # noqa: BLE001
        err = e

    vram = bytes(mu.mem_read(VRAM + 0xF00, B.IMG_BYTES))
    page1 = bytes(mu.mem_read(VRAM + 0xA000 + 0xF00, 0x100))
    pal = bytes(mu.mem_read(PAL, 0x400))
    stopped_ok = bool(seen["hook"]) and mu.reg_read(U.arm_const.UC_ARM_REG_PC) == resume
    return layout, meta, site_rt, vram, page1, pal, err, trace, state, stopped_ok


def main():
    path = sys.argv[1]
    rom = bytearray(open(path, "rb").read())
    layout, meta, site_rt, vram, page1, pal, err, trace, state, stopped = run(rom)
    hr = layout.payload_src + meta["hook_off"]
    tailb = bytes(rom[hr:hr + B.HOOK_RESERVE])
    hook_n = 0
    while hook_n + 2 <= len(tailb) and tailb[hook_n:hook_n + 2] != b"\x00\x00":
        hook_n += 2

    print("文件   %s  %d B  fw_size 0x%X" % (
        os.path.basename(path), len(rom),
        struct.unpack_from("<I", bytes(rom), 0xCC)[0]))
    print("站点   0x%06X -> 运行址 0x%08X（%d B，frame 在 r%d）" % (
        meta["patch_off"], site_rt, meta["patch_len"], meta["frame_reg"]))
    print("钩子   0x%08X（ROM 0x%06X，%d B；返回点 0x%08X）" % (
        EWRAM + meta["hook_off"], layout.payload_src + meta["hook_off"],
        hook_n, site_rt + meta["patch_len"]))
    print("位图   A 0x%06X(%d B)  B 0x%06X(%d B)  调色板 0x%06X" % (
        meta["a_rom"], meta["a_len"], meta["data_b_at"], meta["b_len"],
        meta["pal_at"]))
    print("DMA    钩子碰 DMA 寄存器 %d 次（应当是 0）%s" % (
        state["dma"], ("，%s" % state["bad"]) if state["bad"] else ""))
    print("执行   %s" % ("异常 %r" % err if err else
                        ("干净返回到 0x%08X" % (site_rt + meta["patch_len"])
                         if stopped else "停在了别处")))
    if trace:
        print("轨迹   " + " ".join("0x%08X" % a for a in trace[:6]) + " ...")

    exp_idx = (bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
               + bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]]))
    diff = sum(1 for i in range(B.IMG_BYTES) if vram[i] != exp_idx[i])
    print("位图   差异 %d / %d 字节 -> %s" % (
        diff, B.IMG_BYTES, "一致" if diff == 0 else "不一致"))

    exp_pal = bytes(rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
    got_pal = pal[B.PAL_BASE * 2:(B.PAL_BASE + meta["pal_n"]) * 2]
    pd = sum(1 for i in range(len(exp_pal)) if exp_pal[i] != got_pal[i])
    print("调色板 槽 %d..%d: 差异 %d 字节 -> %s" % (
        B.PAL_BASE, B.PAL_BASE + meta["pal_n"] - 1, pd,
        "一致" if pd == 0 else "不一致"))

    ico_at = B.ICON_PAL_DST - B.PALETTE
    guard = (pal[0:B.PAL_BASE * 2]
             + pal[(B.PAL_BASE + meta["pal_n"]) * 2:
                   (ico_at if meta.get("icon_flash") else 0x400)])
    if meta.get("icon_flash"):
        # 6 套图标版会写 256..293 这段图标调色板，那是设计如此，不算越界
        guard += pal[ico_at + B.ICON_PAL_BYTES:0x400]
    ok_guard = all(b == 0xFF for b in guard)
    print("保护槽 0..%d / %d..511%s: %s" % (
        B.PAL_BASE - 1, B.PAL_BASE + meta["pal_n"],
        "（256..293 归 6 套图标）" if meta.get("icon_flash") else "",
        "没被写" if ok_guard else "被写了!"))

    ok_page1 = page1 == b"\x11" * 0x100
    print("另一页   内容区没被写: %s（双页同写会让文字闪）" % ok_page1)

    ok = (diff == 0 and pd == 0 and err is None and state["dma"] == 0
          and ok_guard and ok_page1 and stopped)
    print("\n结论: %s" % ("通过 —— 调色板与位图都由 CPU 从 Flash 读入当前帧，"
                          "没碰 DMA，也不动另一页" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
