"""把 mGBA 停在任意时刻的画面按显示模式渲染成 PNG。"""
import os
import struct
import subprocess
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from mgba_check import Gdb, MGBA    # noqa: E402

W, H = 240, 160


def bgr555(v):
    r = (v & 0x1F) << 3
    g = ((v >> 5) & 0x1F) << 3
    b = ((v >> 10) & 0x1F) << 3
    return (r | r >> 5, g | g >> 5, b | b >> 5)


def pal_lut(raw, base=0):
    return [bgr555(struct.unpack_from("<H", raw, i * 2)[0]) for i in range(256)]


def render_mode4(vram, pal):
    im = Image.new("RGB", (W, H))
    im.putdata([pal[v] for v in vram[:W * H]])
    return im


def render_mode3(vram, pal):
    im = Image.new("RGB", (W, H))
    px = []
    for i in range(W * H):
        px.append(bgr555(struct.unpack_from("<H", vram, i * 2)[0]))
    im.putdata(px)
    return im


def tile_pixels(vram, tiledata, mapbase, charbase, pal, bpp):
    """mode 0 的 BG 层（只用 1 张 8x8 字符 + 32x32 地图，够看清菜单了）。"""
    out = [(0, 0, 0)] * (W * H)
    for ty in range(20):
        for tx in range(30):
            e = struct.unpack_from("<H", vram, mapbase + (ty * 32 + tx) * 2)[0]
            tid = e & 0x3FF
            pbank = (e >> 12) & 0xF
            for y in range(8):
                for x in range(8):
                    if bpp == 4:
                        byte = vram[charbase + tid * 32 + y * 4 + (x >> 1)]
                        idx = (byte & 0xF) if x & 1 else (byte >> 4)
                        col = pal[pbank * 16 + idx] if idx else (0, 0, 0)
                    else:
                        idx = vram[charbase + tid * 64 + y * 8 + x]
                        col = pal[idx] if idx else (0, 0, 0)
                    out[(ty * 8 + y) * W + tx * 8 + x] = col
    im = Image.new("RGB", (W, H))
    im.putdata(out)
    return im


def main():
    fw = sys.argv[1]
    out = sys.argv[2]
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
        r = g.regs()

        dispcnt = struct.unpack("<H", g.read(0x04000000, 2))[0]
        mode = dispcnt & 7
        frame = 0xA000 if dispcnt & 0x10 else 0
        vram = g.read(0x06000000, 0x18000)
        palraw = g.read(0x05000000, 512)
        pal = pal_lut(palraw)
        print("PC 0x%08X  DISPCNT 0x%04X  mode %d  frame %d"
              % (r[15], dispcnt, mode, 1 if dispcnt & 0x10 else 0))

        if mode == 4:
            img = render_mode4(vram[frame:], pal)
        elif mode == 3:
            img = render_mode3(vram[frame:], pal)
        elif mode == 5:
            img = render_mode3(vram[0xA000:], pal)      # 160x128 小图
        else:
            bg0 = struct.unpack("<H", g.read(0x04000008, 2))[0]
            print("BG0CNT 0x%04X" % bg0)
            prio, cbase, mbase, bpp = bg0 & 3, (bg0 >> 2) & 3, (bg0 >> 8) & 0x1F, 8 if bg0 & 0x80 else 4
            img = tile_pixels(vram,
                              vram, mbase * 0x800, cbase * 0x4000,
                              pal, bpp)
        img = img.resize((W * 3, H * 3), Image.NEAREST)
        img.save(out)
        print("已存 %s" % out)
        # 面板统计
        cols = {}
        for px in img.getdata():
            cols[px] = cols.get(px, 0) + 1
        top = sorted(cols.items(), key=lambda kv: -kv[1])[:6]
        print("主要颜色: " + ", ".join("#%02X%02X%02X x%d" % (c[0], c[1], c[2], n)
                                   for c, n in top))
    finally:
        try:
            g.close()
        except Exception:                       # noqa: BLE001
            pass
        p.terminate()


if __name__ == "__main__":
    main()
