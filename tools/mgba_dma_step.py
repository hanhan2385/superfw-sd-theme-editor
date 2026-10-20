"""在钩子内部下断点，看两段 DMA 各自的落点。断点位置由反汇编自动定位。"""
import os
import struct
import subprocess
import sys
import time

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA      # noqa: E402
import fwbake as B                    # noqa: E402

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
STUB = 0x03007FF0


def find_starts(rom, hook_off):
    """找出两处 `strh r1,[r0,#0xa]`（写 CNT_H 触发 DMA）的地址。"""
    L = B.find_layout(bytearray(rom))
    base = B.EWRAM_MIRROR + hook_off
    code = rom[L.payload_src + hook_off:L.payload_src + hook_off + 0x100]
    out = []
    for i in md.disasm(code, base):
        if i.mnemonic == "strh" and i.op_str.replace(" ", "") == "r1,[r0,#0xa]":
            out.append(i.address)
    return out


def main():
    fw = sys.argv[1]
    rom = bytearray(open(fw, "rb").read())
    meta = B.find_baked(rom)
    hook = B.EWRAM_MIRROR + meta["hook_off"]
    starts = find_starts(rom, meta["hook_off"])
    print("钩子 0x%08X  触发点 %s" % (hook, ["0x%08X" % a for a in starts]))

    p = subprocess.Popen([MGBA, "-g", fw])
    g = Gdb()
    try:
        g.cmd("?")
        g.s.sendall(b"$c#63")
        time.sleep(1.5)
        g.s.settimeout(4.0)
        g.s.sendall(b"\x03")
        g._recv_pkt()

        g.cmd("M4000208,2:0000")          # IME = 0，VBlank 服务程序不再重画
        g.cmd("M4000200,2:0000")
        for off in range(0, 0x9600, 0x100):
            g.cmd("M%x,100:%s" % (0x06000000 + off, "00" * 0x100))
        for off in range(0, 0x200, 0x100):
            g.cmd("M%x,100:%s" % (0x05000000 + off, "00" * 0x100))

        g.cmd("M%x,4:fee70000" % STUB)
        g.cmd("P04=%s" % struct.pack("<I", 0x06000000).hex())
        g.cmd("P0d=%s" % struct.pack("<I", 0x03007F00).hex())
        g.cmd("P0e=%s" % struct.pack("<I", STUB | 1).hex())
        g.cmd("P0f=%s" % struct.pack("<I", hook).hex())

        for k, a in enumerate(starts):
            g.bp(a)
        g.bp(STUB)
        g.s.sendall(b"$c#63")

        for k in range(len(starts) + 1):
            g.s.settimeout(10.0)
            pkt = g._recv_pkt().decode("latin-1")
            r = g.regs()
            print("\n停 %s  PC=0x%08X  r1(即将写的值)=0x%08X"
                  % (pkt[:12], r[15], r[1]))
            print("  VRAM 非零 %d / 38400   VRAM[0xF00]=%s"
                  % (sum(1 for b in g.read(0x06000000, 240 * 160) if b),
                     g.read(0x06000F00, 8).hex()))
            if r[15] == STUB:
                break
            g.cmd("s", quiet=True)        # 执行这次 CNT_H 写入
            g.regs()
            print("  执行该写之后: VRAM[0xF00]=%s VRAM[0xF00+0x5900]=%s"
                  % (g.read(0x06000F00, 8).hex(),
                     g.read(0x06006800, 8).hex()))
            g.s.sendall(b"$c#63")

        vram = g.read(0x06000000, 240 * 160)
        pal = g.read(0x05000000, 512)
        exp = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
        exp += bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
        got = vram[0xF00:0xF00 + B.IMG_BYTES]
        print("\n位图差异 %d / %d" % (sum(1 for a, b in zip(exp, got) if a != b),
                                     B.IMG_BYTES))
        ep = bytes(rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
        gp = pal[40:40 + meta["pal_n"] * 2]
        print("调色板差异 %d" % sum(1 for a, b in zip(ep, gp) if a != b))
    finally:
        try:
            g.close()
        except Exception:                   # noqa: BLE001
            pass
        p.terminate()


if __name__ == "__main__":
    main()
