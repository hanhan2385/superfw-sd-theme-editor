"""在 mGBA 里执行固件自身的内置钩子，把结果渲染成图。

真机固件依赖烧录卡 CPLD + SDRAM，mGBA 没有这两样东西，菜单初始化跑不完
（原版固件在 mGBA 里同样卡死，与本补丁无关）。所以这里不等菜单自己画：

  1. 正常启动到载荷进入 EWRAM（验证钩子/补丁站点运行时与 ROM 完全一致）
  2. 把 CPU 直接放到钩子入口，r4 = 帧基址，SP/LR 指向 IWRAM 里的一个自锁桩
  3. 让 mGBA 真实执行钩子：调色板循环 + 两次 DMA3 块传输
  4. 钩子返回后读回 VRAM / PALETTE，和固件里烘焙的那份逐字节比对并渲染

数据源用 ROM 地址（测试版里已把 3 处字面量改成 ROM 地址），因为 SDRAM 在
mGBA 里不存在；钩子的**代码本身**和出货版逐字节一致。
"""
import os
import struct
import subprocess
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA     # noqa: E402
import fwbake as B                   # noqa: E402

FRAME = 0x06000000
W, H = 240, 160
STUB = 0x03007FF0
SP_TOP = 0x03007F00


def bgr555(v):
    r = (v & 0x1F) << 3
    g = ((v >> 5) & 0x1F) << 3
    b = ((v >> 10) & 0x1F) << 3
    return (r | r >> 5, g | g >> 5, b | b >> 5)


def render(idx, palraw, path, scale=3):
    lut = [bgr555(struct.unpack_from("<H", palraw, i * 2)[0]) for i in range(256)]
    im = Image.new("RGB", (W, H))
    im.putdata([lut[v] for v in idx])
    big = im.resize((W * scale, H * scale), Image.NEAREST)
    big.save(path)
    return im


def main():
    test_rom = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "out", "mgba", "test-mgba.gba")
    outdir = os.path.join(ROOT, "out", "mgba")
    os.makedirs(outdir, exist_ok=True)

    rom = bytearray(open(test_rom, "rb").read())
    L = B.find_layout(rom)
    meta = B.find_baked(rom)
    hook_addr = B.EWRAM_MIRROR + meta["hook_off"]
    site_rt = B.EWRAM_MIRROR + (meta["patch_off"] - L.payload_src)

    print("固件   %s" % os.path.basename(test_rom))
    print("钩子   0x%08X（%d B）  站点 0x%08X" % (hook_addr, B.HOOK_RESERVE, site_rt))

    p = subprocess.Popen([MGBA, "-g", test_rom])
    g = Gdb()
    try:
        g.cmd("?")
        # ---- 1. 跑到载荷进 EWRAM
        g.s.sendall(b"$c#63")
        time.sleep(1.6)
        g.s.settimeout(4.0)
        g.s.sendall(b"\x03")
        g._recv_pkt()

        hook_rt = g.read(hook_addr, 32)
        hook_rom = bytes(rom[L.hook_rom:L.hook_rom + 32])
        site_rt_b = g.read(site_rt, 8)
        site_rom = bytes(rom[meta["patch_off"]:meta["patch_off"] + 8])
        print("\n== 运行期核对 ==")
        print("  载荷里的钩子   %s" % ("与 ROM 一致" if hook_rt == hook_rom else "不一致!"))
        print("  补丁站点       %s  (%s)" % (
            "与 ROM 一致" if site_rt_b == site_rom else "不一致!", site_rt_b.hex()))

        # ---- 2. 摆好 CPU，直接进钩子
        orig = g.read(STUB, 4)
        g.cmd("M%x,4:%s" % (STUB, b"\xfe\xe7\x00\x00".hex()))
        # 显存清 0（一包最多 256 字节）
        for off in range(0, 0x9600, 0x100):
            g.cmd("M%x,100:%s" % (0x06000000 + off, "00" * 0x100))
        for off in range(0, 0x200, 0x100):
            g.cmd("M%x,100:%s" % (0x05000000 + off, "00" * 0x100))

        g.bp(STUB)
        g.cmd("P04=%s" % struct.pack("<I", FRAME).hex())      # r4 = 帧基址
        g.cmd("P0d=%s" % struct.pack("<I", SP_TOP).hex())     # sp
        g.cmd("P0e=%s" % struct.pack("<I", STUB | 1).hex())   # lr
        g.cmd("P0f=%s" % struct.pack("<I", hook_addr).hex())  # pc

        # ---- 3. 执行钩子
        g.s.sendall(b"$c#63")
        g.s.settimeout(10.0)
        pkt = g._recv_pkt().decode("latin-1")
        r = g.regs()
        print("\n== 钩子执行 ==")
        print("  停止包 %s   PC 0x%08X（0x%08X = 干净返回）"
              % (pkt[:20], r[15], STUB))

        # ---- 4. 取画面
        vram = g.read(FRAME, W * H)
        palraw = g.read(0x05000000, 512)
        img = render(vram, palraw, os.path.join(outdir, "hook-frame.png"))
        only = Image.new("RGB", (240, 144))
        only.putdata([img.getpixel((x, y + 15)) for y in range(144) for x in range(240)])
        only.resize((720, 432), Image.NEAREST).save(
            os.path.join(outdir, "hook-content.png"))

        exp = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
        exp += bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
        got = vram[0xF00:0xF00 + B.IMG_BYTES]
        diff = sum(1 for a, b in zip(exp, got) if a != b)
        exp_pal = bytes(rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
        got_pal = palraw[20 * 2:20 * 2 + meta["pal_n"] * 2]
        pd = sum(1 for a, b in zip(exp_pal, got_pal) if a != b)
        guard = sum(1 for i in list(range(0, 20)) + list(range(240, 256))
                    if got_pal_ok(palraw, i))

        print("\n== 结果 ==")
        print("  内容区位图   差异 %d / %d 字节" % (diff, B.IMG_BYTES))
        print("  调色板 20..%d 差异 %d 字节" % (20 + meta["pal_n"] - 1, pd))
        print("  保护槽 0-19 / 240-255 被写: %s" % ("否" if guard == 0 else "是!"))
        print("  画面非零像素 %d / %d" % (sum(1 for v in vram if v), W * H))
        ok = (diff == 0 and pd == 0 and guard == 0 and r[15] == STUB)
        print("\n结论: %s" % (
            "通过 —— mGBA 真实执行钩子，背景图与调色板逐字节落在预期位置"
            if ok else "失败"))
        return 0 if ok else 1
    finally:
        try:
            g.close()
        except Exception:                       # noqa: BLE001
            pass
        p.terminate()


def got_pal_ok(palraw, i):
    return struct.unpack_from("<H", palraw, i * 2)[0] != 0


if __name__ == "__main__":
    sys.exit(main())
