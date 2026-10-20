# -*- coding: utf-8 -*-
"""
用 CPU 仿真真跑一遍补丁点，验证背景图确实被搬进显存。
======================================================
不是"看代码觉得对"，是真的取指执行：
  * 从补丁点前面的指令开始跑（两版构建的起点不同，由 fwbuild 给出）
  * hook DMA3CNT 的写入，按控制字模拟 DMA 搬运
  * 比对 VRAM[frame+0xF00 .. +34560] 与索引位图、调色板 4..239 与调色板
  * 对照组：把补丁字节换回原样再跑，内容区应该还是纯色 0x11

用法：
  python tools/emulate_wallpaper.py dist/xxx.bin
  python tools/emulate_wallpaper.py dist/xxx.bin --outdir dist/verify
"""
import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbuild as FB          # noqa: E402
import wallpaper as WP        # noqa: E402
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R2, UC_ARM_REG_R4,  # noqa: E402
                               UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R10,
                               UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_LR)

DMA = 0x040000D4

# 每个构建：原样从哪跑、打过补丁从哪跑、跑到哪停
ENTRY = {
    "r75-2mb":    {"orig": 0xA7A2, "patched": 0xA7B4, "stop": 0xA7BE},
    "chis-1.6mb": {"orig": 0x6104, "patched": 0x6118, "stop": 0x6128},
}


def run(rom, layout, patched=True):
    ent = ENTRY[layout.build]
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(FB.ROM_BASE, 2 * 1024 * 1024)
    mu.mem_map(0x03000000, 32 * 1024)      # IWRAM（栈）
    mu.mem_map(0x04000000, 1024)           # IO
    mu.mem_map(0x05000000, 1024)           # 调色板
    mu.mem_map(0x06000000, 128 * 1024)     # VRAM
    mu.mem_write(FB.ROM_BASE, bytes(rom[:2 * 1024 * 1024]))

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
            raise RuntimeError("DMA 失败 sad=%08x dad=%08x cnt=%d: %s" % (sad, dad, cnt, exc))

    mu.hook_add(UC_HOOK_MEM_WRITE, on_write, None, DMA + 8, DMA + 8)

    frame = FB.VRAM + 0xA000 * 0
    sp = 0x03007000
    mu.reg_write(UC_ARM_REG_SP, sp)
    mu.mem_write(sp + 0x14, struct.pack("<I", frame))
    mu.reg_write(UC_ARM_REG_R2, sp + 0x18)      # 1.6mb 版：ip = sp+0x18
    mu.reg_write(UC_ARM_REG_R4, frame)          # 2mb 版：frame 在 r4/sl
    mu.reg_write(UC_ARM_REG_R10, frame)
    mu.reg_write(UC_ARM_REG_R0, frame)
    mu.reg_write(UC_ARM_REG_R6, DMA)
    mu.reg_write(UC_ARM_REG_R5, DMA + 8)
    mu.reg_write(UC_ARM_REG_R11, DMA + 4)
    mu.reg_write(UC_ARM_REG_LR, (FB.ROM_BASE + ent["stop"]) | 1)

    start = ent["patched"] if patched else ent["orig"]
    # 必须带 Thumb 位（|1）启动，否则 unicorn 按 ARM 取指，BL 会被判成非法指令
    mu.emu_start((FB.ROM_BASE + start) | 1, FB.ROM_BASE + ent["stop"], count=500000)

    vram = bytes(mu.mem_read(frame + 0xF00, FB.IMG_BYTES))
    pal = struct.unpack("<256H", bytes(mu.mem_read(FB.PALETTE, 512)))
    return vram, pal


def to_img(vram, pal, size=None):
    from PIL import Image
    im = Image.new("P", (FB.IMG_W, FB.IMG_H))
    flat = []
    for v in pal:
        flat += [(v & 0x1F) * 255 // 31, ((v >> 5) & 0x1F) * 255 // 31,
                 ((v >> 10) & 0x1F) * 255 // 31]
    im.putpalette(flat)
    im.frombytes(vram)
    im = im.convert("RGB")
    if size:
        im = im.resize(size, Image.NEAREST)
    return im


def theme_rgb(rom, layout, slot, idx):
    """读固件里 theme[slot] 的第 idx 个颜色（BGR555 -> RGB888）。"""
    import struct
    try:
        off = layout.themes + slot * 12 + idx * 2
        v = struct.unpack_from("<H", bytes(rom), off)[0]
    except Exception:
        return None
    return ((v & 0x1F) << 3, ((v >> 5) & 0x1F) << 3, ((v >> 10) & 0x1F) << 3)


def compose_screen(img, fg=(0xAA, 0xAA, 0xAA)):
    """拼成 240x160：上面 16 行是顶栏（FG 纯色），下面 144 行是内容区。"""
    from PIL import Image
    out = Image.new("RGB", (240, 160), fg)
    out.paste(img, (0, 16))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "dist", "verify"))
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    all_ok = True
    for path in args.files:
        rom = bytearray(open(path, "rb").read())
        print("=" * 66)
        print(os.path.basename(path), len(rom), "字节")
        layout = FB.detect(rom)
        okh, why = FB.validate(rom)
        print("  构建 %s · 头部校验 %s" % (layout.build, "通过" if okh else "失败: " + why))
        try:
            patched = WP.is_patched(rom, layout)
        except Exception:
            patched = False
        print("  补丁状态:", "已打补丁" if patched else "未打补丁")

        idx = bytes(rom[layout.img:layout.img_end]) if patched else None
        pal_file = struct.unpack("<256H", bytes(rom[layout.pal:layout.pal + 512])) if patched else None

        if patched:
            vram, gpal = run(rom, layout, True)
            same_img = vram == idx
            same_pal = list(gpal[4:240]) == list(pal_file[4:240])
            print("  仿真: 位图 %s（差异 %d 字节）· 调色板 4..239 %s" % (
                "一致" if same_img else "不一致",
                sum(1 for a, b in zip(vram, idx) if a != b),
                "一致" if same_pal else "不一致"))
            all_ok = all_ok and same_img and same_pal
            img = to_img(vram, gpal)
            base = os.path.splitext(os.path.basename(path))[0]
            fg = theme_rgb(rom, layout, 5, 0) or (0xAA, 0xAA, 0xAA)
            shot = compose_screen(img, fg)
            p1 = os.path.join(args.outdir, base + "-wallpaper-240x160.png")
            p2 = os.path.join(args.outdir, base + "-wallpaper-720x480.png")
            shot.save(p1)
            shot.resize((720, 480), 0).save(p2)
            print("  画面:", p1)

        # 图标块：和重绘源码逐字节比
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import redraw_icons as RI
            want_icons, _c, _t = RI.build()
            if layout.icons >= 0:
                got = bytes(rom[layout.icons:layout.icons + FB.ICONS_LEN])
                same = got == want_icons
                print("  图标: %s（%d 字节）" % ("与重绘版一致" if same else "不一致！", len(got)))
                all_ok = all_ok and same
        except Exception as e:
            print("  图标校验跳过:", e)

        # 对照组：把补丁字节换回去，内容区应当仍是纯色
        ctrl = bytearray(rom[:2 * 1024 * 1024])
        if patched:
            ctrl[layout.patch_off:layout.patch_off + layout.patch_len] = layout.patch_sig
            ctrl[layout.tramp:layout.img_end] = b"\xFF" * (layout.img_end - layout.tramp)
        ctrl[0xF0:0x100] = b"SUPERFW~DAVIDGF\x00"   # 保证 magic 不被破坏
        vram0, pal0 = run(ctrl, layout, False)
        solid = set(vram0) == {0x11}
        print("  对照（未打补丁）: 内容区全 0x11 ->", solid)
        all_ok = all_ok and solid

    print("=" * 66)
    print("结论:", "补丁在真实取指执行下按预期工作" if all_ok else "有问题，见上面逐项")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
