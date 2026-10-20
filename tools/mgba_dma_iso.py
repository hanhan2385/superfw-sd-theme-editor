"""最小桩：在 IWRAM 里手写一段只用 DMA3 的代码，验证 mGBA 的 DMA 是否工作。"""
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA      # noqa: E402
import fwbake as B                    # noqa: E402

CODE = 0x03007000
SRC = 0x0818A68C
DST = 0x06000F00
N = 11400


def build_stub(base):
    t = B.Thumb()
    t.ldr_lit(0, 0x040000D4)
    t.ldr_lit(1, SRC)
    t.str(1, 0, 0)
    t.ldr_lit(1, DST)
    t.str(1, 0, 4)
    t.ldr_lit(1, N)
    t.strh(1, 0, 8)
    t.ldr_lit(1, B.DMA_CTRL)
    t.strh(1, 0, 0x0A)
    loop = t.mark()
    t.ldrh(3, 0, 8)
    t.lsls_imm(3, 3, 16)
    t.bcond(0x4, loop)
    here = t.mark()
    t.bcond(0xE, here)                 # b .  (自锁)
    return t.finish(base)


def main():
    rom = bytearray(open(os.path.join(ROOT, "out", "mgba", "test-v002.gba"), "rb").read())
    stub = build_stub(CODE)
    print("桩 %d 字节: %s" % (len(stub), stub.hex()))

    p = subprocess.Popen([MGBA, "-g", os.path.join(ROOT, "out", "mgba", "test-v002.gba")])
    g = Gdb()
    try:
        g.cmd("?")
        g.s.sendall(b"$c#63")
        time.sleep(1.5)
        g.s.settimeout(4.0)
        g.s.sendall(b"\x03")
        g._recv_pkt()

        g.cmd("M4000208,2:0000")
        g.cmd("M4000200,2:0000")
        for off in range(0, 0x9600, 0x100):
            g.cmd("M%x,100:%s" % (0x06000000 + off, "00" * 0x100))
        print("清零后 VRAM[0xF00] %s" % g.read(DST, 16).hex())

        for i in range(0, len(stub), 0x100):
            chunk = stub[i:i + 0x100]
            g.cmd("M%x,%x:%s" % (CODE + i, len(chunk), chunk.hex()))
        back = g.read(CODE, len(stub))
        print("写入校验 %s" % ("一致" if back == stub else "不一致!"))

        g.cmd("P0d=%s" % struct.pack("<I", 0x03007F00).hex())
        g.cmd("P0e=%s" % struct.pack("<I", 0x03007F00).hex())
        g.cmd("P0f=%s" % struct.pack("<I", CODE | 1).hex())
        g.s.sendall(b"$c#63")
        time.sleep(1.5)
        g.s.settimeout(4.0)
        g.s.sendall(b"\x03")
        g._recv_pkt()
        r = g.regs()
        print("桩执行后 PC=0x%08X r3=0x%08X r0=0x%08X" % (r[15], r[3], r[0]))
        d = g.read(DST, 32)
        print("VRAM[0xF00] %s" % d.hex())
        print("VRAM 非零 %d / 38400" % sum(1 for b in g.read(0x06000000, 240 * 160) if b))
        print("期望前 8 字节 %s" % bytes(rom[SRC - 0x08000000:SRC - 0x08000000 + 8]).hex())
        print("结论: %s" % ("mGBA 的 DMA3 正常" if d.hex() ==
                          bytes(rom[SRC - 0x08000000:SRC - 0x08000000 + 32]).hex()
                          else "mGBA 没搬"))
    finally:
        try:
            g.close()
        except Exception:                # noqa: BLE001
            pass
        p.terminate()


if __name__ == "__main__":
    main()
