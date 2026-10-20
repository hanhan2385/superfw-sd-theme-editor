# -*- coding: utf-8 -*-
"""自检「6 套主题各自一套图标」的钩子（v5 烘焙格式）。

仿真按真机布局：
  - 0x08000000 起 32MB = 卡带 Flash，内容就是 .fw 文件本身（mode 4 下就是这个映射）
  - 0x03000000 起 IWRAM，`menu_theme` 就在里面（烘焙头里记了地址）
  - VRAM 两页都预填 0x11，钩子只该动 page0 的内容区 + OBJ 图块
  - 调色板预填 0xFF

检查四件事：
  1. 第 N 套：OBJ 图块 == 该套的 4352 字节精灵块，调色板 256..293 == 该套调色板
  2. 壁纸：page0 内容区 == A+B 位图，page1 不动
  3. 同一套再跑一次：OBJ 区不该被再次写（预填的 0x77 应该还在）
  4. 换到下一套再跑：必须换过来

用法: python tools/test_theme_icons.py out/v16/xxx.fw
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import fwbake as B   # noqa: E402
import unicorn as U  # noqa: E402

EWRAM, VRAM, PAL, IWRAM = B.EWRAM_MIRROR, B.VRAM, B.PALETTE, 0x03000000
ROM = 0x08000000
ROM_SIZE = 0x2000000
SENTINEL = IWRAM + 0x7000


class Rig(object):
    def __init__(self, rom):
        self.rom = bytearray(rom)
        self.layout = B.find_layout(rom)
        self.meta = B.find_baked(rom)
        assert self.meta and self.meta.get("icon_flash"), "这个固件没有 6 套图标表"
        self.mu = U.Uc(U.UC_ARCH_ARM, U.UC_MODE_ARM)
        mu = self.mu
        mu.mem_map(EWRAM, 0x40000)
        mu.mem_map(IWRAM, 0x8000)
        mu.mem_map(ROM, ROM_SIZE)
        mu.mem_map(VRAM, 0x18000)
        mu.mem_map(PAL, 0x400)
        mu.mem_map(0x04000000, 0x400)
        mu.mem_write(EWRAM, bytes(rom[self.layout.payload_src:
                                     self.layout.payload_src + self.layout.payload_len]))
        mu.mem_write(ROM, bytes(rom))
        self.site_rt = EWRAM + (self.meta["patch_off"] - self.layout.payload_src)
        self.resume = self.site_rt + self.meta["patch_len"]
        self.hook_addr = EWRAM + self.meta["hook_off"]
        self.seen = False

        def on_code(uc, addr, size, _):
            if self.hook_addr <= addr < self.hook_addr + B.HOOK_RESERVE:
                self.seen = True
            if self.seen and addr == self.resume:
                uc.emu_stop()

        mu.hook_add(U.UC_HOOK_CODE, on_code)

    def blank(self, obj_fill=0x11):
        mu = self.mu
        mu.mem_write(VRAM, bytes([obj_fill]) * 0x18000)
        mu.mem_write(PAL, b"\xff" * 0x400)
        mu.mem_write(PAL + B.ICON_PAL_DST - PAL, b"\xff" * B.ICON_PAL_BYTES)

    def set_theme(self, n):
        self.mu.mem_write(self.meta["theme_addr"], bytes([n]))

    def call(self):
        mu = self.mu
        self.seen = False
        mu.reg_write(U.arm_const.UC_ARM_REG_SP, IWRAM + 0x1000)
        mu.reg_write(U.arm_const.UC_ARM_REG_LR, SENTINEL | 1)
        mu.reg_write(U.arm_const.UC_ARM_REG_R0, VRAM)     # frame = page0
        err = None
        try:
            mu.emu_start(self.site_rt | 1, SENTINEL | 1, count=20000000)
        except Exception as e:                            # noqa: BLE001
            err = e
        return dict(err=err, seen=self.seen, resume=self.resume,
                    pc=mu.reg_read(U.arm_const.UC_ARM_REG_PC),
                    vram=bytes(mu.mem_read(VRAM, 0x18000)),
                    pal=bytes(mu.mem_read(PAL, 0x400)))


def main():
    path = sys.argv[1]
    rom = bytearray(open(path, "rb").read())
    meta = B.find_baked(rom)
    exp_wall = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]]) + \
        bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
    exp_pal = bytes(rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
    stride = meta["icon_stride"]
    base = meta["icon_flash"] - ROM

    def icons_of(n):
        off = base + n * stride
        return (bytes(rom[off:off + B.ICON_BYTES]),
                bytes(rom[off + B.ICON_BYTES:off + B.ICON_BYTES + B.ICON_PAL_BYTES]))

    rig = Rig(rom)
    problems = []
    print("固件 %s  ·  menu_theme @ 0x%08X  ·  图标表 @ 0x%06X（6×%d）"
          % (os.path.basename(path), meta["theme_addr"], base, stride))

    # 1) 每套都试一遍（每次全新预填，状态字节故意保留 -> 每套都必须真的换过来）
    for n in range(B.THEME_COUNT):
        rig.blank()
        rig.set_theme(n)
        r = rig.call()
        sprite, ipal = icons_of(n)
        obj = r["vram"][B.ICON_DST - VRAM:B.ICON_DST - VRAM + B.ICON_BYTES]
        got = r["pal"][0x200:0x200 + B.ICON_PAL_BYTES]
        dw = sum(1 for i in range(B.IMG_BYTES)
                 if r["vram"][0x0F00 + i] != exp_wall[i])
        di = sum(1 for i in range(B.ICON_BYTES) if obj[i] != sprite[i])
        dp = sum(1 for i in range(B.ICON_PAL_BYTES) if got[i] != ipal[i])
        dpal = sum(1 for i in range(len(exp_pal))
                   if r["pal"][B.PAL_BASE * 2 + i] != exp_pal[i])
        page1 = r["vram"][0xA000:0xA100] == b"\x11" * 0x100
        ok = (r["seen"] and r["err"] is None and r["pc"] == r["resume"]
              and dw == 0 and di == 0 and dp == 0 and dpal == 0 and page1)
        print("  第 %d 套: 图标差异=%d 图标调色板差异=%d 壁纸差异=%d 壁纸调色板=%d "
              "page1干净=%s -> %s" % (n + 1, di, dp // 2, dw, dpal // 2, page1,
                                     "OK" if ok else "不对"))
        if not ok:
            problems.append("第 %d 套不对" % (n + 1))

    # 2) 同一套连跑两次：第二次不该再写 OBJ（预填 0x77 应保留）
    rig.blank(obj_fill=0x77)
    rig.set_theme(2)
    rig.call()
    rig.blank(obj_fill=0x77)
    rig.set_theme(2)
    r = rig.call()
    untouched = r["vram"][B.ICON_DST - VRAM:B.ICON_DST - VRAM + 64] == b"\x77" * 64
    print("  同套重跑不再搬图标: %s" % ("是" if untouched else "否（每帧都搬）"))
    if not untouched:
        problems.append("同套重跑又搬了一次图标")

    # 3) 换一套：必须搬
    rig.set_theme(4)
    r = rig.call()
    sprite, _ = icons_of(4)
    obj = r["vram"][B.ICON_DST - VRAM:B.ICON_DST - VRAM + B.ICON_BYTES]
    di = sum(1 for i in range(B.ICON_BYTES) if obj[i] != sprite[i])
    print("  切到第 5 套: 图标差异=%d -> %s" % (di, "OK" if di == 0 else "没换过来"))
    if di:
        problems.append("切主题没换图标")

    print("\n结论:", "6 套图标行为正确" if not problems else "；".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
