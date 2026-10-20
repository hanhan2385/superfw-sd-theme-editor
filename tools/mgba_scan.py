"""先让 mGBA 跑一会儿，再检查 EWRAM 载荷/钩子是否就位，并看菜单跑到哪。"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA, stop_reason   # noqa: E402
import fwbake as B                              # noqa: E402
import fwbuild as F                             # noqa: E402

fw = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "dist", "v0.01", "固件", "SuperFW-chis-r75-CN-v0.01.gba")
rom = bytearray(open(fw, "rb").read())
L = B.find_layout(rom)
meta = B.find_baked(rom)
hook = B.EWRAM_MIRROR + meta["hook_off"]
site_rt = B.EWRAM_MIRROR + (meta["patch_off"] - L.payload_src)

p = subprocess.Popen([MGBA, "-g", fw])
g = Gdb()
try:
    g.cmd("?")
    g.s.sendall(b"$c#63")
    g.s.settimeout(6.0)
    try:
        while True:
            pkt = g._recv_pkt()
            print("运行中收到:", pkt[:20])
    except Exception:                                   # noqa: BLE001
        pass
    g.s.sendall(b"\x03")
    g.s.settimeout(5.0)
    pkt = g._recv_pkt().decode("latin-1")
    r = g.regs()
    print("中断 %s  PC 0x%08X LR 0x%08X" % (pkt, r[15], r[14]))

    print("\n== 载荷头部 ==")
    e = g.read(B.EWRAM_MIRROR, 16)
    rr = bytes(rom[L.payload_src:L.payload_src + 16])
    print("  EWRAM %s" % e.hex())
    print("  ROM   %s" % rr.hex())
    print("  一致  %s" % (e == rr))

    print("\n== 钩子处（on 0x%08X）==" % hook)
    e = g.read(hook, 32)
    rr = bytes(rom[L.hook_rom:L.hook_rom + 32])
    print("  EWRAM %s" % e.hex())
    print("  ROM   %s" % rr.hex())
    print("  一致  %s" % (e == rr))

    print("\n== 补丁站点（on 0x%08X）==" % site_rt)
    e = g.read(site_rt, 16)
    rr = bytes(rom[meta["patch_off"]:meta["patch_off"] + 16])
    print("  EWRAM %s" % e.hex())
    print("  ROM   %s" % rr.hex())

    print("\n== 显存/调色板 ==")
    print("  VRAM[0x00:16] %s" % g.read(0x06000000, 16).hex())
    print("  VRAM[0xF00:0xF10] %s" % g.read(0x06000F00, 16).hex())
    print("  PAL[0:8] %s" % g.read(0x05000000, 8).hex())
    print("  PAL[20*2:20*2+8] %s" % g.read(0x05000000 + 40, 8).hex())

    dis = 0x08000000
    print("\n== 当前 PC 附近指令（读 EWRAM）==")
    b = g.read(r[15] & ~1, 16)
    print("  %s" % b.hex())
finally:
    try:
        g.close()
    except Exception:                                   # noqa: BLE001
        pass
    p.terminate()
