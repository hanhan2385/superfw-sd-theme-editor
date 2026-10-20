"""单步跟踪 mGBA 里卡住的那段代码，打印每条指令 + 关键寄存器。"""
import os
import struct
import subprocess
import sys
import time

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA    # noqa: E402

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
NAMES = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
         "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]


def main():
    fw = sys.argv[1]
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    p = subprocess.Popen([MGBA, "-g", fw])
    g = Gdb()
    try:
        g.cmd("?")
        g.s.sendall(b"$c#63")
        time.sleep(wait)
        g.s.settimeout(4.0)
        g.s.sendall(b"\x03")
        g._recv_pkt()

        seen = {}
        for i in range(steps):
            r = g.regs()
            pc = r[15] & ~1
            code = g.read(pc, 4)
            ins = next(md.disasm(code, pc), None)
            txt = ("%-6s %s" % (ins.mnemonic, ins.op_str)) if ins else code.hex()
            seen[pc] = seen.get(pc, 0) + 1
            print("%2d  0x%08X  %-28s r0=%08X r1=%08X r2=%08X r3=%08X r4=%08X"
                  % (i, pc, txt, r[0], r[1], r[2], r[3], r[4]))
            g.cmd("s", quiet=True)
        print("\n热点:")
        for a, n in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
            print("  0x%08X  %d 次" % (a, n))
    finally:
        try:
            g.close()
        except Exception:                       # noqa: BLE001
            pass
        p.terminate()


if __name__ == "__main__":
    main()
