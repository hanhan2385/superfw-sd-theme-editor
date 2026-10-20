"""在 mGBA 里真跑「测试卡带」，把钩子铺进 VRAM 的画面取回来。

和 mgba_check.py 的区别：这里跑的是一张普通游戏卡带（tools/gba_testrom.py 造的），
不依赖烧录卡的 CPLD/SDRAM，所以 mGBA 能正常开机一路跑到底。

断点设在「钩子入口」和「引导程序里 BL 的下一条」：
  命中后者时两块 DMA 已经搬完、220 个调色板槽也写完了。
读出 0x06000000 起的一整帧 + 调色板，跟固件里内置的位图逐字节比。
"""
import os
import struct
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B          # noqa: E402
from mgba_check import Gdb, decode_hex, render, stop_reason   # noqa: E402

MGBA = r"C:\Users\HAN\Downloads\mGBA-0.10.5-win32\mGBA.exe"
ROM = os.environ.get("TESTROM") or os.path.join(ROOT, "out", "mgba", "hooktest-v006.gba")
FW = os.environ.get("TESTFW") or os.path.join(ROOT, "build", "_rel", "v006-theme6-wall.gba")
OUT = os.path.join(ROOT, "out", "mgba")

VRAM = 0x06000000
PAL = 0x05000000
W, H = 240, 160
HOOK_AT = 0x08000400
STOP_AT = 0x08000200        # 引导程序里那两条 `b .` 的位置（见 gba_testrom.py）
BOOT_LEN = 0x20             # 引导程序本体长度上限，用来算 BL 的返回点


def main():
    os.makedirs(OUT, exist_ok=True)
    fw = bytearray(open(FW, "rb").read())
    meta = B.find_baked(fw)
    rom = bytearray(open(ROM, "rb").read())

    # 引导程序里 BL 后面那条 `b .` 的地址就是返回点
    ret = None
    for off in range(0x200, 0x200 + 0x60, 2):
        hw = struct.unpack_from("<H", rom, off)[0]
        if hw == 0xE7FE:                        # b .
            ret = 0x08000000 + off
            break
    if ret is None:
        raise SystemExit("测试卡带里没找到 `b .` 收尾")
    print("测试卡带 %s  %d B" % (os.path.basename(ROM), len(rom)))
    print("钩子入口 0x%08X  返回点 0x%08X" % (HOOK_AT, ret))

    proc = subprocess.Popen([MGBA, "-g", ROM])
    gdb = None
    try:
        gdb = Gdb()
        gdb.cmd("?", quiet=True)
        pc0 = gdb.regs()[15]
        print("入口 PC 0x%08X" % pc0)

        gdb.bp(HOOK_AT)
        gdb.bp(ret)

        hit = []
        frame = None
        for _ in range(8):
            pkt = gdb.cont(wait=20.0)
            if pkt == "timeout":
                print("继续运行超时")
                break
            sig, fields = stop_reason(pkt)
            if isinstance(sig, str):                 # "S05" 这种短停止包
                try:
                    sig = int(sig[1:3], 16)
                except ValueError:
                    sig = None
                fields = fields or {}
            pc = (fields or {}).get(0x0F)
            if pc is None:
                pc = gdb.regs()[15]
            print("  停 PC 0x%08X（信号 %s）" % (pc, sig))
            hit.append(pc)
            if pc == ret:
                frame = (gdb.read(VRAM, W * H), gdb.read(PAL, 512), gdb.regs())
                break
            if sig not in (5, 0):
                print("  非断点异常，停止")
                break

        if frame is None:
            print("没停在返回点，已命中：%s" % ["0x%08X" % a for a in hit])
            r = gdb.regs()
            print("  PC 0x%08X LR 0x%08X SP 0x%08X" % (r[15], r[14], r[13]))
            try:
                vram = gdb.read(VRAM, W * H)
                pal = gdb.read(PAL, 512)
                render(vram, pal, os.path.join(OUT, "hooktest-v006-now.png"))
                print("  已存当前画面 out/mgba/hooktest-v006-now.png")
            except Exception as e:              # noqa: BLE001
                print("  读显存失败: %s" % e)
            return 2

        vram_idx, pal_raw, regs = frame
        img = render(vram_idx, pal_raw, os.path.join(OUT, "hooktest-v006-frame.png"))

        exp = bytes(fw[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
        exp += bytes(fw[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
        got = vram_idx[0xF00:0xF00 + B.IMG_BYTES]
        diff = sum(1 for a, b in zip(exp, got) if a != b)

        exp_pal = bytes(fw[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
        got_pal = pal_raw[20 * 2:20 * 2 + meta["pal_n"] * 2]
        pd = sum(1 for a, b in zip(exp_pal, got_pal) if a != b)

        print("寄存器 R4 0x%08X  R7 0x%08X" % (regs[4], regs[7]))
        print("内容区 %d 字节：与固件内置位图差异 %d" % (B.IMG_BYTES, diff))
        print("调色板 槽 20..%d：差异 %d 字节" % (20 + meta["pal_n"] - 1, pd))
        print("\n结论: %s" % ("通过 —— mGBA 真机执行钩子后，显存里就是内置的那张图"
                             if diff == 0 and pd == 0 else "失败 —— 画面与预期不符"))
        print("画面: out/mgba/hooktest-v006-frame.png")
        return 0 if (diff == 0 and pd == 0) else 3
    finally:
        if gdb is not None:
            gdb.close()
        try:
            proc.terminate()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
