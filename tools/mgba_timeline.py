"""时间轴采样：跑 rom，每 0.4 秒中断一次，记录 PC / VRAM 指纹 / 调色板指纹。

用来做 A/B 对照 —— 原版 vs 烘焙版在 mGBA 里的启动轨迹是否一致。
"""
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA, stop_reason   # noqa: E402
import fwbake as B                              # noqa: E402


def brief(b):
    return hashlib.sha1(b).hexdigest()[:8]


def main():
    fw = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    label = os.path.basename(fw)
    rom = bytearray(open(fw, "rb").read())
    meta = B.find_baked(rom)
    if meta:
        L = B.find_layout(rom)
        hook = B.EWRAM_MIRROR + meta["hook_off"]
        site = B.EWRAM_MIRROR + (meta["patch_off"] - L.payload_src)
        print("%s  烘焙版：站点 0x%08X 钩子 0x%08X" % (label, site, hook))
    else:
        hook = site = -1
        print("%s  原始固件（未烘焙）" % label)

    p = subprocess.Popen([MGBA, "-g", fw])
    g = Gdb()
    pcs = []
    try:
        g.cmd("?")
        g.s.sendall(b"$c#63")
        t0 = time.time()
        n = int(secs / 0.4)
        for i in range(n):
            time.sleep(0.4)
            g.s.settimeout(3.0)
            g.s.sendall(b"\x03")
            try:
                pkt = g._recv_pkt().decode("latin-1")
            except Exception:                          # noqa: BLE001
                print("  %5.1fs  中断无响应" % (time.time() - t0))
                continue
            r = g.regs()
            pc = r[15]
            vram = g.read(0x06000000, 240 * 160)
            pal = g.read(0x05000000, 512)
            mark = ""
            if pc == hook:
                mark = "  <= 钩子入口！"
            elif pc == site:
                mark = "  <= 渲染站点！"
            print("  %5.1fs  PC 0x%08X  VRAM %s(%d 非零)  PAL %s%s" % (
                time.time() - t0, pc, brief(vram), sum(1 for b in vram if b),
                brief(pal), mark))
            pcs.append(pc)
            if pc in (hook, site):
                print("     R4=0x%08X R0=0x%08X" % (r[4], r[0]))
            g.s.sendall(b"$c#63")
        return pcs
    finally:
        try:
            g.close()
        except Exception:                              # noqa: BLE001
            pass
        p.terminate()


if __name__ == "__main__":
    main()
