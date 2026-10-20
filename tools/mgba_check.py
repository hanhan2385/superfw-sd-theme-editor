"""用 mGBA 的 GDB 调试口验证：真实跑一遍固件，把菜单第一帧的 VRAM 取回来渲染成图。

mGBA 前端都支持 `-g`（GDB stub，端口 2345），启动后处于暂停态等连接。
我们：在「钩子入口」和「钩子返回点」各下一个断点 → 连续运行 →
命中返回点时两块 DMA 都已完成、调色板也写完了 → 把 VRAM + PALETTE 整个读回来。

这样拿到的就是 mGBA 真正画出来的那一帧，不是我们自己的模拟。
"""
import os
import socket
import struct
import subprocess
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

import fwbake as B          # noqa: E402
import fwbuild as F         # noqa: E402

HOST, PORT = "127.0.0.1", 2345
MGBA = r"C:\Users\HAN\Downloads\mGBA-0.10.5-win32\mGBA.exe"

VRAM = 0x06000000
PAL = 0x05000000
FRAME_W, FRAME_H = 240, 160


# ---------------------------------------------------------------- GDB RSP
class Gdb:
    def __init__(self, host=HOST, port=PORT, timeout=20.0):
        t0 = time.time()
        while True:
            try:
                self.s = socket.create_connection((host, port), timeout=5)
                break
            except OSError:
                if time.time() - t0 > timeout:
                    raise
                time.sleep(0.3)
        self.s.settimeout(20.0)
        self.buf = b""
        self.log = []

    def _recv_pkt(self):
        # 找 $...#
        while True:
            i = self.buf.find(b"$")
            if i < 0:
                self.buf += self.s.recv(4096)
                continue
            j = self.buf.find(b"#", i)
            if j < 0:
                self.buf += self.s.recv(4096)
                continue
            payload = self.buf[i + 1:j]
            self.buf = self.buf[j + 3:]
            self.s.sendall(b"+")
            return payload

    def cmd(self, text, quiet=False):
        data = text.encode()
        csum = sum(data) & 0xFF
        self.s.sendall(b"$" + data + b"#%02x" % csum)
        pkt = self._recv_pkt().decode("latin-1")
        if not quiet:
            self.log.append((text, pkt[:80]))
        return pkt

    def read(self, addr, n, chunk=0x100):   # mGBA 的 GDB 单包最大 256 字节
        out = bytearray()
        while n > 0:
            k = min(chunk, n)
            for attempt in range(6):
                pkt = self.cmd("m%x,%x" % (addr, k))
                if pkt and pkt[0] in "0123456789abcdefABCDEF":
                    break
                # 前面可能堆着 T/S 停止包，或者刚停下时 mGBA 暂时拒绝访问
                time.sleep(0.05)
            else:
                raise RuntimeError("读 0x%08X 失败: %r" % (addr, pkt))
            out += decode_hex(pkt)
            addr += k
            n -= k
        return bytes(out)

    def regs(self):
        for _ in range(6):
            pkt = self.cmd("g", quiet=True)
            if pkt and pkt[0] in "0123456789abcdefABCDEF" and len(pkt) >= 64:
                raw = decode_hex(pkt)
                vals = list(struct.unpack("<16I", raw[:64]))
                return vals                # r0..r15
            # 前面可能还堆着一个 T/S 停止包，再要一次
            time.sleep(0.02)
        raise RuntimeError("取寄存器失败: %r" % pkt)

    def bp(self, addr, kind=4):
        return self.cmd("Z0,%x,%x" % (addr, kind))

    def cont(self, wait=30.0):
        """c 之后一直读到 T 包，返回 (signal, {reg:val})。"""
        self.s.sendall(b"$" + b"c" + b"#%02x" % (ord("c") & 0xFF))
        deadline = time.time() + wait
        self.s.settimeout(max(1.0, wait))
        while time.time() < deadline:
            pkt = self._recv_pkt().decode("latin-1")
            if pkt.startswith("T") or pkt.startswith("S") or pkt.startswith("W"):
                return pkt
        return "timeout"

    def close(self):
        try:
            self.s.sendall(b"$D#44")
        except OSError:
            pass
        self.s.close()


def decode_hex(s):
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == "*":
            n = int(s[i + 1:i + 2], 16) - 29
            out += out[-1:] * n
            i += 2
        else:
            out.append(int(s[i:i + 2], 16))
            i += 2
    return bytes(out)


def stop_reason(pkt):
    if not pkt or not pkt.startswith("T"):
        return pkt, None
    sig = int(pkt[1:3], 16)
    fields = {}
    for part in pkt[3:].split(";"):
        if len(part) >= 3 and part[0] in "0123456789abcdefABCDEF":
            fields[int(part[:2], 16)] = int(part[3:], 16)
    return sig, fields


# ---------------------------------------------------------------- 渲染
def render(vram_idx, pal_raw, path, scale=3):
    lut = []
    for i in range(256):
        v = struct.unpack_from("<H", pal_raw, i * 2)[0]
        r = (v & 0x1F) << 3
        g = ((v >> 5) & 0x1F) << 3
        b = ((v >> 10) & 0x1F) << 3
        lut.append((r | r >> 5, g | g >> 5, b | b >> 5))
    im = Image.new("RGB", (FRAME_W, FRAME_H))
    im.putdata([lut[v] for v in vram_idx])
    if scale != 1:
        im = im.resize((FRAME_W * scale, FRAME_H * scale), Image.NEAREST)
    im.save(path)
    return im


def main():
    fw = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "dist", "v0.01", "固件", "SuperFW-chis-r75-CN-v0.01.gba")
    outdir = os.path.join(ROOT, "out", "mgba")
    os.makedirs(outdir, exist_ok=True)

    rom = bytearray(open(fw, "rb").read())
    L = B.find_layout(rom)
    meta = B.find_baked(rom)
    if not meta:
        print("这个固件没有烘焙背景图")
        return 1
    site_rt = B.EWRAM_MIRROR + (meta["patch_off"] - L.payload_src)
    hook_addr = B.EWRAM_MIRROR + meta["hook_off"]
    resume = site_rt + meta["patch_len"]

    print("固件   %s  %d B" % (os.path.basename(fw), len(rom)))
    print("站点   0x%08X  钩子 0x%08X  返回 0x%08X" % (site_rt, hook_addr, resume))

    proc = subprocess.Popen([MGBA, "-g", fw])
    gdb = None
    try:
        gdb = Gdb()
        print("已连上 GDB stub")
        gdb.cmd("?", quiet=True)
        pc = gdb.regs()[15]
        print("入口 PC 0x%08X" % pc)

        gdb.bp(hook_addr)
        gdb.bp(resume)

        hit_hook = False
        frame = None
        pc = None
        for i in range(12):
            pkt = gdb.cont(wait=25.0)
            sig, fields = stop_reason(pkt)
            if pkt == "timeout":
                print("继续运行超时（可能已经在跑菜单循环，没到断点）")
                break
            # 软断点命中 T05，PC 在 0x0d / 0x0f 字段
            pc = fields.get(0x0F) if fields else None
            if pc is None:
                r = gdb.regs()
                pc = r[15]
            print("  停 %s  信号 %s  PC 0x%08X" % (pkt[:24], sig, pc))

            if pc == hook_addr:
                hit_hook = True
                continue
            if pc == resume:
                # 两块 DMA 已完成、调色板已写 —— 这就是要的那一帧
                frame = (gdb.read(VRAM, FRAME_W * FRAME_H),
                         gdb.read(PAL, 512),
                         gdb.regs())
                break
            # 撞到别的异常就别再继续了
            if sig not in (5, 0x05):
                break

        if frame is None:
            print("没停在返回点，当前状态：")
            r = gdb.regs()
            print("  PC 0x%08X LR 0x%08X SP 0x%08X" % (r[15], r[14], r[13]))
            pkt = gdb.cmd("?")
            print("  停止原因 %s" % pkt)
            try:
                vram = gdb.read(VRAM, FRAME_W * FRAME_H)
                pal = gdb.read(PAL, 512)
                render(vram, pal, os.path.join(outdir, "frame-now.png"))
                print("  已存当前画面 out/mgba/frame-now.png")
            except Exception as e:                       # noqa: BLE001
                print("  读显存失败: %s" % e)
            return 2

        vram_idx, pal_raw, r = frame
        img = render(vram_idx, pal_raw, os.path.join(outdir, "frame.png"))
        print("钩子命中: %s" % ("是" if hit_hook else "否（直接落在返回点）"))
        print("寄存器 R4(帧基址) 0x%08X  R0 0x%08X  R1 0x%08X" % (r[4], r[0], r[1]))

        # 内容区应当就是烘焙进去的那张图
        exp = bytes(rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
        exp += bytes(rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]])
        got = vram_idx[0xF00:0xF00 + B.IMG_BYTES]
        diff = sum(1 for a, b in zip(exp, got) if a != b)
        print("内容区 (%d 字节) 与固件里的位图差异: %d" % (B.IMG_BYTES, diff))

        exp_pal = bytes(rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2])
        got_pal = pal_raw[20 * 2:20 * 2 + meta["pal_n"] * 2]
        pd = sum(1 for a, b in zip(exp_pal, got_pal) if a != b)
        print("调色板 20..%d 差异: %d 字节" % (20 + meta["pal_n"] - 1, pd))

        ok = (diff == 0 and pd == 0)
        print("\n结论: %s" % ("通过 —— mGBA 真跑出来的这一帧就是内置的背景图"
                             if ok else "失败 —— 画面与预期不符"))
        return 0 if ok else 3
    finally:
        if gdb is not None:
            gdb.close()
        try:
            proc.terminate()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
