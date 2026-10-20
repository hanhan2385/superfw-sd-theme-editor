"""诊断：mGBA 里固件到底跑到哪了。"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app"))

from mgba_check import Gdb, MGBA, stop_reason, decode_hex  # noqa: E402
import fwbake as B                                          # noqa: E402

fw = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    "dist", "v0.01", "固件", "SuperFW-chis-r75-CN-v0.01.gba")

rom = bytearray(open(fw, "rb").read())
L = B.find_layout(rom)
meta = B.find_baked(rom)
site_rt = B.EWRAM_MIRROR + (meta["patch_off"] - L.payload_src)
hook = B.EWRAM_MIRROR + meta["hook_off"]
print("站点 0x%08X 钩子 0x%08X 返回 0x%08X" % (site_rt, hook, site_rt + meta["patch_len"]))

p = subprocess.Popen([MGBA, "-g", fw])
g = Gdb()
try:
    print("qSupported ->", g.cmd("qSupported:multiprocess+;swbreak+;hwbreak+")[:60])
    print("? ->", g.cmd("?"))
    r = g.regs()
    print("PC 0x%08X  SP 0x%08X" % (r[15], r[13]))
    print("Z0 hook -> %r" % g.cmd("Z0,%x,4" % hook))
    print("Z0 site -> %r" % g.cmd("Z0,%x,4" % site_rt))

    # 读一下 EWRAM 里钩子位置的内容，确认载荷真被拷进去了
    got = g.read(hook, 16)
    print("EWRAM 钩子处前16字节: %s" % got.hex())
    print("ROM  载荷尾部前16字节: %s" % bytes(rom[L.hook_rom:L.hook_rom + 16]).hex())

    for i in range(6):
        g.s.sendall(b"$c#63")
        try:
            g.s.settimeout(4.0)
            pkt = g._recv_pkt().decode("latin-1")
            sig, f = stop_reason(pkt)
            print("停 %-20s sig=%s PC=0x%08X" % (pkt[:20], sig, f.get(0x0f, 0) if f else 0))
            continue
        except Exception as e:                                  # noqa: BLE001
            print("4 秒没停（%s），发中断" % type(e).__name__)
        g.s.sendall(b"\x03")
        g.s.settimeout(5.0)
        try:
            pkt = g._recv_pkt().decode("latin-1")
        except Exception as e:                                  # noqa: BLE001
            print("  中断也没回: %s" % e)
            break
        sig, f = stop_reason(pkt)
        r = g.regs()
        print("  -> %s sig=%s PC=0x%08X LR=0x%08X R4=0x%08X" % (
            pkt[:20], sig, r[15], r[14], r[4]))
        # 看看 PC 附近还合不合法
        if i == 0:
            for name, addr in (("VRAM", 0x06000000), ("PAL", 0x05000000),
                               ("EWRAM载荷首", 0x027C0000)):
                try:
                    b = g.read(addr, 8)
                    print("  %s: %s" % (name, b.hex()))
                except Exception as e:                          # noqa: BLE001
                    print("  %s 读失败 %s" % (name, e))
finally:
    try:
        g.close()
    except Exception:                                           # noqa: BLE001
        pass
    p.terminate()
