"""用 mGBA 判定 GBA DMA3 的使能位到底在 CNT_H 的哪一位。

不查文档、不猜：造三张只有「一位之差」的探针卡带，各跑一次 DMA3，
读 VRAM 看有没有搬过去。哪张搬成功，哪张的 CNT_H 就是对的。
"""
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B                              # noqa: E402
from mgba_check import Gdb                      # noqa: E402

MGBA = r"C:\Users\HAN\Downloads\mGBA-0.10.5-win32\mGBA.exe"
OUT = os.path.join(ROOT, "out", "mgba")
BOOT = 0x08000200
DATA = 0x08000800
DST = 0x06000000
SRC_BYTES = bytes(range(0x10))
HDR = bytes(open(os.path.join(ROOT, "dist", "v0.02", "固件",
                             "SuperFW-chis-r75-CN-v0.02.gba"), "rb").read(0xC0))


def build(cnt_l, cnt_h, name):
    t = B.Thumb()
    t.ldr_lit(0, 0x040000D4)                    # r0 = DMA3
    t.ldr_lit(1, DATA)
    t.str(1, 0, 0)                              # SAD
    t.ldr_lit(2, DST)
    t.str(2, 0, 4)                              # DAD
    t.ldr_lit(1, cnt_l)
    t.strh(1, 0, 8)                             # CNT_L
    t.ldr_lit(1, cnt_h)
    t.strh(1, 0, 0x0A)                          # CNT_H
    for _ in range(40):                         # 等 DMA 跑完
        t.h(0x46C0)                             # mov r8,r8
    t.h(0xE7FE)                                 # b .
    code = t.finish(BOOT)

    rom = bytearray(b"\x00" * 0x200000)
    rom[0:0xC0] = HDR                            # 合法卡带头（含 logo），mGBA 才认
    struct.pack_into("<I", rom, 0x00, 0xEA00002E)   # b 0xC0
    struct.pack_into("<I", rom, 0xC0, 0xE59F0004)
    struct.pack_into("<I", rom, 0xC4, 0xE12FFF10)
    struct.pack_into("<I", rom, 0xC8, 0xE1A00000)
    struct.pack_into("<I", rom, 0xCC, BOOT | 1)
    rom[BOOT - 0x08000000:BOOT - 0x08000000 + len(code)] = code
    rom[DATA - 0x08000000:DATA - 0x08000000 + len(SRC_BYTES)] = SRC_BYTES
    path = os.path.join(OUT, name)
    open(path, "wb").write(rom)
    return path


def run(path):
    import time
    last = None
    for _ in range(3):                          # 端口有时被上一轮残留进程占着，重试
        proc = subprocess.Popen([MGBA, "-g", path])
        g = None
        try:
            time.sleep(0.4)
            g = Gdb()
            g.cmd("?", quiet=True)
            g.regs()
            g.s.sendall(b"$c#63")
            time.sleep(1.2)
            g.s.sendall(b"\x03")
            g._recv_pkt()
            pc = g.regs()[15]
            got = g.read(DST, 16)
            cnt = struct.unpack("<H", g.read(0x040000DC, 2))[0]
            return pc, got, cnt
        except Exception as e:                  # noqa: BLE001
            last = e
            time.sleep(0.6)
        finally:
            if g:
                try:
                    g.close()
                except Exception:               # noqa: BLE001
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(0.4)
    raise last


def main():
    os.makedirs(OUT, exist_ok=True)
    cases = [
        ("CNT_H=0x4000（v0.02 现在的写法）", 8, 0x4000, "dma-4000.gba"),
        ("CNT_H=0x8000（只置 bit15）      ", 8, 0x8000, "dma-8000.gba"),
        ("CNT_H=0x8400（bit15+32位）      ", 4, 0x8400, "dma-8400.gba"),
        ("CNT_H=0x8000 且 CNT_L=0x8008    ", 0x8008, 0x8000, "dma-cntl-hi.gba"),
    ]
    for title, cnt_l, cnt_h, name in cases:
        path = build(cnt_l, cnt_h, name)
        pc, got, cnt = run(path)
        ok = got == SRC_BYTES
        print("%s  ->  %s   VRAM=%s  CNT_L读回=0x%04X  PC=0x%08X"
              % (title, "搬成功" if ok else "没搬  ", got.hex(), cnt, pc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
