# -*- coding: utf-8 -*-
"""
SuperFW —— 背景图片补丁（wallpaper patch）
=========================================
固件原本没有图片层：内容区就是一条 `dma_memset16(&frame[16*240], dup8(BG), 240*144/2)`。
`-flto -flto-partition=none` 把它内联成直接写 DMA3 寄存器的一段指令，所以我们
可以只换掉其中几条，跳进自己写的小跳板，把「纯色填充」换成「位图搬运」。

两个构建的内联块不一样，偏移也不一样，因此坐标全部来自 fwbuild.detect()：

  r75-2mb     补丁点 0xA7B4（10 字节，帧地址在 r4）
  chis-1.6mb  补丁点 0x6118（16 字节，帧地址在 r0）

跳板（纯 Thumb-1，只碰 r0-r3/r12，进出各一条 push/pop）：
  1) 用 CPU 把 236 项调色板搬到 BG 调色板 4..239
  2) 把 DMA3 改成 SRC_INC，从 ROM 把 240x144 索引位图搬到 frame+0xF00

保留原样的槽位：0(backdrop) / 1-3(logo) / 16-19(FG,BG,FT,HI) / 240-244(IGM)。
载荷写在 fw_size 之后的空白，不参与头部 sha256，所以 fw_size 不变、校验和照常通过。
"""
import hashlib
import struct

import fwbuild

ROM_BASE = fwbuild.ROM_BASE
W, H = fwbuild.IMG_W, fwbuild.IMG_H
PAL_FIRST = fwbuild.PAL_FIRST
PAL_N = fwbuild.PAL_N
IMG_BYTES = fwbuild.IMG_BYTES
DMA_CTL = 0x80004380        # DMA3: 使能 | 源地址递增 | 16 位 | 17280 个半字
PAL_ADDR = fwbuild.PALETTE + PAL_FIRST * 2
RESERVED_PAL = "0(backdrop) 1-3(logo) 16(FG) 17(BG) 18(FT) 19(HI) 240-244(游戏内菜单)"


# ---------------------------------------------------------------- 跳板汇编
class _Asm(object):
    """够用的 Thumb-1 编码器：只实现跳板需要的那几条指令。"""

    def __init__(self):
        self.b = bytearray()
        self.fix = []           # (insn_pos, rd, value)

    def h(self, v):
        self.b += struct.pack("<H", v & 0xFFFF)

    def push_lr(self):
        self.h(0xB500)

    def pop_pc(self):
        self.h(0xBD00)

    def mov_hi(self, rd, rm):
        assert 0 <= rd < 16 and 0 <= rm < 16
        self.h(0x4600 | ((rd >> 3) << 7) | (rm << 3) | (rd & 7))

    def add_hi(self, rd, rm):
        self.h(0x4400 | ((rd >> 3) << 7) | (rm << 3) | (rd & 7))

    def movs(self, rd, imm):
        assert 0 <= imm < 256
        self.h(0x2000 | (rd << 8) | imm)

    def lsls(self, rd, rm, imm):
        self.h(0x0000 | (imm << 6) | (rm << 3) | rd)

    def ldrh(self, rd, rn):
        self.h(0x8800 | (rn << 3) | rd)

    def strh(self, rd, rn):
        self.h(0x8000 | (rn << 3) | rd)

    def adds_imm(self, rd, imm):
        self.h(0x3000 | (rd << 8) | imm)

    def subs_imm(self, rd, imm):
        self.h(0x3800 | (rd << 8) | imm)

    def bne(self, off):
        assert -256 <= off < 256
        self.h(0xD100 | (off & 0xFF))

    def str(self, rd, rn, imm=0):
        assert imm % 4 == 0 and 0 <= imm < 128
        self.h(0x6000 | ((imm >> 2) << 6) | (rn << 3) | rd)

    def ldr_lit(self, rd, value):
        self.fix.append([len(self.b), rd, value])
        self.h(0x4800 | (rd << 8))          # 占位，finish() 里补偏移

    def finish(self):
        if len(self.b) % 4:
            self.h(0)
        lit_base = len(self.b)
        for item in self.fix:
            insn_pos, rd, value = item
            lit_pos = len(self.b)
            self.b += struct.pack("<I", value & 0xFFFFFFFF)
            pc = ((insn_pos + 4) // 4) * 4
            imm = lit_pos - pc
            assert 0 <= imm <= 1020 and imm % 4 == 0, "字面量太远: %d" % imm
            self.b[insn_pos:insn_pos + 2] = struct.pack("<H", 0x4800 | (rd << 8) | (imm >> 2))
        return bytes(self.b)


def _bl(addr, target):
    """Thumb-1 BL（T1），±16MB。"""
    off = (target - (addr + 4)) & 0xFFFFFFFF
    if off & 1:
        raise ValueError("BL 目标必须是半字对齐")
    if off >= 0x80000000:
        off -= 0x100000000
    if not (-0x1000000 <= off < 0x1000000):
        raise ValueError("BL 超出 ±16MB: %d" % off)
    off &= 0x01FFFFFF
    s = (off >> 24) & 1
    i1 = (off >> 23) & 1
    i2 = (off >> 22) & 1
    imm10 = (off >> 12) & 0x3FF
    imm11 = (off >> 1) & 0x7FF
    j1 = (~(i1 ^ s)) & 1
    j2 = (~(i2 ^ s)) & 1
    return struct.pack("<HH", 0xF000 | (s << 10) | imm10,
                       0xD000 | (j1 << 13) | (1 << 12) | (j2 << 11) | imm11)


def build_tramp(pal_addr, img_addr, frame_reg="r4"):
    """生成跳板。frame_reg 是补丁点上"帧地址在哪个寄存器"（由 fwbuild 探测）。

    **已被 fwbake.build_hook() 取代，只留给老的 patch_wallpaper.py / selftest 用。**
    这里位图是拿 DMA3 搬的，而 DMA 从卡带 SDRAM 读在真机上取不到数据
    （调色板 CPU 搬所以正常、位图全黑）—— 新代码一律走 CPU 的 ldmia/stmia。
    """
    a = _Asm()
    a.push_lr()
    a.mov_hi(12, 4 if frame_reg == "r4" else 0)   # r12 = frame
    a.ldr_lit(0, pal_addr + PAL_FIRST * 2)        # 调色板源（跳过 4 个保留槽）
    a.movs(1, PAL_N)                              # 236 项
    a.ldr_lit(2, fwbuild.PALETTE + PAL_FIRST * 2)  # 目标：调色板 4 号
    loop = len(a.b)
    a.ldrh(3, 0)
    a.strh(3, 2)
    a.adds_imm(0, 2)
    a.adds_imm(2, 2)
    a.subs_imm(1, 1)
    a.bne((loop - (len(a.b) + 4)) // 2)
    a.ldr_lit(0, img_addr)
    a.ldr_lit(1, fwbuild.DMA)
    a.movs(3, 0xF0)
    a.lsls(3, 3, 4)                               # 0xF00 = 16*240
    a.add_hi(3, 12)                               # 目标 = frame + 0xF00
    a.str(0, 1, 0)                                # DMA3SAD
    a.str(3, 1, 4)                                # DMA3DAD
    a.ldr_lit(0, DMA_CTL)
    a.str(0, 1, 8)                                # DMA3CNT -> 启动
    a.pop_pc()
    return a.finish()


def verify_tramp(code, frame_reg="r4"):
    """把跳板反汇编回来，确认每条都是我们想写的那条（防止编码错）。"""
    try:
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    except Exception:
        return None
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    return ["%04x %s %s" % (i.address, i.mnemonic, i.op_str)
            for i in md.disasm(code, 0)]


# ---------------------------------------------------------------- 图片
def crop_metrics(src_size, anchor=(0.5, 0.5)):
    """算「铺满 240×144 再裁」到底裁掉了源图的哪一块。

    返回：
      win        源图上会被保留的矩形 (l, t, r, b)，浮点
      keep       保留的宽高比是否正好 240:144（永远是的）
      cut_x/cut_y 左右 / 上下各被裁掉多少像素（两侧合计）
      pct_x/pct_y 裁掉的占原图比例
      zoom       放大倍数（>1 表示源图比成品小，是被拉大的）
    """
    sw, sh = max(1, src_size[0]), max(1, src_size[1])
    ax, ay = anchor
    r = float(W) / H
    if sw / float(sh) > r:                  # 太宽 → 裁左右
        ww, wh = sh * r, float(sh)
    else:                                   # 太高（或正好）→ 裁上下
        ww, wh = float(sw), sw / r
    left = (sw - ww) * ax
    top = (sh - wh) * ay
    return {
        "win": (left, top, left + ww, top + wh),
        "cut_x": sw - ww,
        "cut_y": sh - wh,
        "pct_x": (sw - ww) / sw,
        "pct_y": (sh - wh) / sh,
        "zoom": W / max(1e-6, ww),
    }


def fit_exact(im, anchor=(0.5, 0.5)):
    """把任意图变成**正好** 240×144：铺满、按 anchor 取景、不拉伸变形。"""
    im = im.convert("RGB")
    m = crop_metrics(im.size, anchor)
    l, t, r, b = m["win"]
    box = (int(round(l)), int(round(t)),
           max(int(round(l)) + 1, int(round(r))),
           max(int(round(t)) + 1, int(round(b))))
    return im.crop(box).resize((W, H), _LANCZOS)


def fit_image(im, mode="cover", bg=(0, 0, 0), anchor=(0.5, 0.5)):
    """把任意图片摆到 240x144。返回新的 RGB 图。

    mode: crop / cover（同一件事：铺满再裁，成品正好 240×144）
          contain（整张塞进去，空的地方填 bg）
          stretch（硬拉，会变形）
    """
    im = im.convert("RGB")
    tw, th = W, H
    if mode == "crop" or mode == "cover":
        return fit_exact(im, anchor)
    if mode == "stretch":
        return im.resize((tw, th), _LANCZOS)
    sw, sh = im.size
    scale = min(tw / sw, th / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    rs = im.resize((nw, nh), _LANCZOS)
    canvas = _new_rgb((tw, th), bg)
    canvas.paste(rs, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def quantize(im, dim=0.0, bg=(0, 0, 0), colors=PAL_N, dither=True):
    """返回 (索引字节 240*144, 256 项 RGB555 调色板, 实际色数)。"""
    if dim > 0:
        im = _blend(im, bg, dim)
    colors = max(2, min(colors, PAL_N))
    q = im.quantize(colors=colors, method=_MEDIANCUT,
                    dither=_FS if dither else _NODITHER)
    # 纯色图量化后调色板可能只有 3 项，必须补齐再用，否则下面会越界
    p = list(q.getpalette() or [])
    if len(p) < colors * 3:
        p += [0] * (colors * 3 - len(p))
    pal = [0] * 256
    for i in range(colors):
        r, g, b = p[i * 3], p[i * 3 + 1], p[i * 3 + 2]
        pal[PAL_FIRST + i] = fwbuild.rgb2gba(r, g, b)
    last = pal[PAL_FIRST + colors - 1]
    for i in range(PAL_FIRST + colors, PAL_FIRST + PAL_N):
        pal[i] = last
    idx = bytes((v + PAL_FIRST) & 0xFF for v in q.tobytes())
    return idx, pal, colors


# ---------------------------------------------------------------- 补丁
def prepare(rom, layout=None):
    """文件放不下载荷时按卡带尺寸补齐（0xFF）。返回 (rom, 是否补齐)。"""
    layout = layout or fwbuild.detect(rom)
    if len(rom) >= layout.payload_needed():
        return rom, False
    need = layout.padded_size()
    rom += b"\xFF" * (need - len(rom))
    return rom, True


def is_patched(rom, layout=None):
    layout = layout or fwbuild.detect(rom)
    if getattr(layout, "patch_off", 0) < 0:
        return False          # SD 卡主题固件：没有补丁点，壁纸走 theme.bin
    want = _bl(ROM_BASE + layout.patch_off, ROM_BASE + layout.tramp)
    return bytes(rom[layout.patch_off:layout.patch_off + 4]) == want


def check_rom(rom, layout=None):
    ok, why = fwbuild.validate(rom)
    if not ok:
        return False, why
    try:
        layout = layout or fwbuild.detect(rom)
    except ValueError as e:
        return False, str(e)
    if is_patched(rom, layout):
        return True, "已经打过补丁"
    return True, "ok"


def apply(rom, idx, pal256, layout=None):
    """把墙纸写进固件。rom 是 bytearray，原地修改（必要时会变长）。"""
    layout = layout or fwbuild.detect(rom)
    if len(idx) != IMG_BYTES:
        raise ValueError("位图必须是 %d 字节，收到 %d" % (IMG_BYTES, len(idx)))
    if len(pal256) != 256:
        raise ValueError("调色板必须是 256 项")
    if len(rom) < layout.payload_needed():
        need = layout.padded_size()
        rom += b"\xFF" * (need - len(rom))
    code = build_tramp(ROM_BASE + layout.pal, ROM_BASE + layout.img, layout.frame_reg)
    if len(code) > fwbuild.MARK_OFF:
        raise ValueError("跳板 %d 字节，超过 %d 字节的保留空间" % (len(code), fwbuild.MARK_OFF))
    rom[layout.tramp:layout.tramp + len(code)] = code
    # 标记：二次识别用（补丁点上已经是 BL，原来的机器码搜不到了）
    mark = bytearray(0x80)
    mark[0:len(fwbuild.MARK_MAGIC)] = fwbuild.MARK_MAGIC
    mark[8] = fwbuild.MARK_VER
    mark[9] = layout.patch_len
    mark[10] = ord(layout.frame_reg[1])
    mark[11] = fwbuild.BUILD_IDS.get(layout.build, 0)
    mark[12:16] = struct.pack("<I", layout.patch_off)
    mark[16:20] = struct.pack("<I", layout.themes)
    mark[20:24] = struct.pack("<I", max(0, layout.icons))
    mark[24:28] = struct.pack("<I", max(0, layout.icons_pal))
    mark[28:28 + layout.patch_len] = layout.patch_sig
    rom[layout.tramp + fwbuild.MARK_OFF:layout.tramp + 0x100] = mark

    rom[layout.pal:layout.pal + 512] = struct.pack("<256H", *pal256)
    rom[layout.img:layout.img_end] = idx
    rom[layout.patch_off:layout.patch_off + layout.patch_len] = (
        _bl(ROM_BASE + layout.patch_off, ROM_BASE + layout.tramp)
        + b"\xC0\x46" * ((layout.patch_len - 4) // 2))   # mov r8,r8：ARMv4T 真 NOP
    digest = fwbuild.reseal(rom)
    ok, why = fwbuild.validate(rom)
    return {
        "build": layout.build,
        "patch_at": hex(ROM_BASE + layout.patch_off),
        "tramp": hex(ROM_BASE + layout.tramp),
        "palette": hex(ROM_BASE + layout.pal),
        "image": hex(ROM_BASE + layout.img),
        "sha256": digest,
        "valid": ok,
        "why": why,
        "size": len(rom),
    }


def revert(rom, layout=None):
    """还原成纯色填充，并清掉载荷区。"""
    layout = layout or fwbuild.detect(rom)
    if not is_patched(rom, layout):
        return False
    rom[layout.patch_off:layout.patch_off + layout.patch_len] = layout.patch_sig
    rom[layout.tramp:layout.img_end] = b"\xFF" * (layout.img_end - layout.tramp)
    fwbuild.reseal(rom)
    return True


def patch_theme(rom, colors, slot=5, layout=None):
    """把 6 个颜色写进指定主题槽（不重算校验和）。colors 为 RRGGBB 或 (r,g,b)。"""
    layout = layout or fwbuild.detect(rom)
    off = layout.themes + slot * 12
    vals = []
    for c in colors:
        if isinstance(c, str):
            v = c.lstrip("#")
            vals.append(fwbuild.rgb2gba(int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)))
        else:
            vals.append(fwbuild.rgb2gba(*c[:3]))
    rom[off:off + 12] = struct.pack("<6H", *vals)
    return off, vals


ICON_PAL_N = 38


def patch_icons(rom, img_bytes, pal, layout=None):
    """整块替换 17 个图标 + 调色板。img_bytes 必须正好 4352 字节。

    图标是 8bpp 精灵，索引 0 是**透明**，真正的颜色从索引 1 开始。
    所以调色板第 0 项必须留给透明，pal[0] 要写到第 1 项去——
    以前从第 0 项开始写，整体错位一格，上机就是"颜色全是红的"。
    """
    layout = layout or fwbuild.detect(rom)
    if layout.icons < 0 or layout.icons_pal < 0:
        raise ValueError("这个固件里没找到图标块，不能原地替换")
    if len(img_bytes) != fwbuild.ICONS_LEN:
        raise ValueError("图标块必须是 %d 字节，收到 %d" % (fwbuild.ICONS_LEN, len(img_bytes)))
    if len(pal) > ICON_PAL_N - 1:
        raise ValueError("图标调色板最多 %d 项，收到 %d" % (ICON_PAL_N - 1, len(pal)))
    entries = [0] + [int(v) & 0xFFFF for v in pal]      # 第 0 项 = 透明
    entries += [0] * (ICON_PAL_N - len(entries))
    rom[layout.icons:layout.icons + fwbuild.ICONS_LEN] = img_bytes
    rom[layout.icons_pal:layout.icons_pal + ICON_PAL_N * 2] = struct.pack(
        "<%dH" % ICON_PAL_N, *entries)
    return layout.icons, layout.icons_pal


# Pillow 常量在模块级解析一次，避免顶层 import 失败时整个模块不可用
try:
    from PIL import Image
    _LANCZOS = Image.LANCZOS
    _MEDIANCUT = Image.MEDIANCUT
    _FS = Image.FLOYDSTEINBERG
    _NODITHER = Image.NONE
    _new_rgb = lambda size, c: Image.new("RGB", size, c)
except Exception:  # pragma: no cover
    Image = None
    _LANCZOS = _MEDIANCUT = _FS = _NODITHER = None
    _new_rgb = None


def _blend(im, bg, a):
    from PIL import Image
    a = max(0.0, min(1.0, a))
    if a <= 0:
        return im
    layer = Image.new("RGB", im.size, tuple(int(v) for v in bg))
    return Image.blend(im, layer, a)
