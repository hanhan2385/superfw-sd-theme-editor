"""反汇编 SuperFW 载荷中的任意运行地址。用法: dis_runtime.py <start_rt> [n] [--raw off]"""
import sys
sys.path.insert(0, "app")
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_ARCH_ARM, CS_MODE_THUMB
import fwbake as F

ROM = sys.argv[1] if len(sys.argv) > 1 else "out/cn-baked.bin"
rom = open(ROM, "rb").read()
L = F.find_layout(rom)
BASE = L.payload_src
RT = F.EWRAM_MIRROR


def rt2rom(a):
    return a - RT + BASE


def rom2rt(o):
    return RT + o - BASE


def show_arm(start_rt, n=40, stop_rt=None):
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    off = rt2rom(start_rt)
    b = rom[off: off + n * 4]
    for i in md.disasm(b, start_rt):
        print("0x%08X  rom 0x%06X  %-8s %s" % (i.address, rt2rom(i.address), i.mnemonic, i.op_str))
        if stop_rt and i.address >= stop_rt:
            break


def show_thumb(start_rt, n=60):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    off = rt2rom(start_rt)
    b = rom[off: off + n * 2]
    for i in md.disasm(b, start_rt):
        print("0x%08X  rom 0x%06X  %-8s %s" % (i.address, rt2rom(i.address), i.mnemonic, i.op_str))


if __name__ == "__main__":
    mode = "arm"
    args = sys.argv[1:]
    if args and args[0] == "rom":
        args.pop(0)
    if args and args[0].startswith("0x") and len(args[0]) > 10:
        start = int(args[0], 16)
    else:
        start = 0x027C9BF8
    n = 40
    for a in args:
        if a in ("thumb", "arm"):
            mode = a
        elif a.startswith("0x") and int(a, 16) < 0x1000000:
            n = int(a, 16)
    print("== %s  start=0x%08X  n=%d" % (mode, start, n))
    if mode == "thumb":
        show_thumb(start, n)
    else:
        show_arm(start, n)
