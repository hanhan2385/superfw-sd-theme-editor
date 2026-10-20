"""把背景图真正烘焙进 SuperFW 固件（bake 方案，替代运行时跳板）。

为什么旧方案会白屏
------------------
菜单代码不在 ROM 里跑：bootloader 把 ROM 的 250KB 载荷（0xFA00 字）
整块拷到 EWRAM 0x027C0000，再从那儿取指。旧方案把跳板写在 fw_size
之后（既不进入 EWRAM，也不在拷贝范围内），并且用 ROM 地址算 BL 偏移，
落到 EWRAM 之外 —— 取指异常，而 setup_video() 已经把 VRAM 刷成 0xFFFF，

本方案
------
1. 背景图按 8bpp（220 色，槽 20..239）分两段写进 fw_size 内部的
   「不被任何拷贝覆盖的全 0 区」；
2. 钩子代码写进载荷尾部 256 字节的填充区（随载荷一起进 EWRAM），
   渲染点的 5 条 DMA 填充指令改成 BL + NOP；
3. 钩子把 CPLD 切到 **Flash 模式**（bootloader 拷 assets 时用的同一条路），
   直接从卡带 Flash 读位图与调色板，用 CPU 的 ldmia/stmia 铺进 VRAM，
   读完切回固件常态的 SDRAM 模式。

为什么不再走 SDRAM（v0.14 的实机定论）
------------------------------------
SDRAM 的 0x09E00000（30MB）在这台卡上读回来全是 0xFF —— 也就是没映射；
而字体所在的 0x08E80000（14.5MB）是好的，说明 CPLD 的 SDRAM 窗口只到
16MB 左右。bootloader 照着 ASSETS_ADDR = 0x09E00000 拷贝只是白拷。
所以位图不再进 SDRAM，钩子自己开 Flash 窗口直读，也就不用改拷贝块数了。

只写当前帧（frame + 0xF00）：双缓冲两页一起写会把正在显示的那页也涂掉，
真机表现就是文字一直闪、偶尔整片消失。

不碰 fw_size，也不碰主题/图标数据。
"""

import struct

ROM_BASE = 0x08000000
EWRAM_MIRROR = 0x027C0000
EWRAM_END = 0x02800000
PALETTE = 0x05000000
VRAM = 0x06000000
DMA3 = 0x040000D4

IMG_W, IMG_H = 240, 144
IMG_BYTES = IMG_W * IMG_H

# 只用一段连续的可用槽：20..239（避开 0..3 背景+logo、16..19 FG/BG/FT/HI、
# 240..244 游戏内菜单、256+ 图标 bank）
PAL_BASE, PAL_N = 20, 220
SLOTS = list(range(PAL_BASE, PAL_BASE + PAL_N))

HOOK_RESERVE = 0x200             # 载荷里给钩子留的空位（6 套图标版约 320 字节）
STATE_OFF = 0x1F8                # 钩子块最后一个字里的「上次是哪套」状态字节
# DMAxCNT_H：bit15 = 使能（不是 bit14，bit14 是 IRQ 使能）；bit8-9 = 00 表示 16 位
DMA_CTRL = 0x8000
DMA_WAIT_MAX = 0x3000            # 等 DMA 的循环上界，防止 CNT_L 不递减时死等
MAGIC = b"SFWBAKE"               # 7 字节；v4 写的是 SFWBAKE4，v5 是 SFWBAKE5
BAKE_VER = 5
HDR_SIZE = 64                    # 头 64 字节 + 调色板，再往后才是 B 段位图
HDR_SIZE_V4 = 48                 # v4 及更早的烘焙头（只有 48 字节）

# ---- 6 套主题各自的图标 ----
# 固件里 17 个图标是 4352 字节的 8bpp 精灵块 + 38 项调色板（索引 0 透明）。
# 它们原本只在 menu_setup 里 dma_memcpy16 拷进 VRAM 一次（menu.c:2145），
# 所以 6 套主题共用一套。要做成「每套一套图标」，就把 6 块并排放在 Flash 里，
# 钩子在每帧开头比一下 menu_theme，变了就把对应那块重新拷进 VRAM。
ICON_BYTES = 4352
ICON_PAL_N = 38
ICON_PAL_BYTES = ICON_PAL_N * 2
ICON_STRIDE = 4432               # 4352 + 76 = 4428，补齐到 16 的倍数
ICON_DST = 0x06014000            # MEM_VRAM_OBJS（gbahw.h:95）
ICON_PAL_DST = PALETTE + 0x200   # &MEM_PALETTE[256]
THEME_COUNT = 6

# 图标表后面跟一条 32 字节的记录（"SFWICON"）。不塞进烘焙头是因为头是定长的：
# v4 的头只有 48 字节，往里加字段就得挪调色板，旧固件一换图就全错位。
# 单独一条记录，v4/v5 的固件都能原地升级成「6 套图标」。
ICON_MAGIC = b"SFWICON"
ICON_RECORD = 32

BLOCKS = 2048

# CPLD 模式寄存器：写 0xA55A 两遍解锁，再写模式值两遍。
# 取值见 superfw/src/supercard_driver.c：bit0 = SDRAM 映射、bit1 = 写使能、
# bit2 = 卡带(sd)，所以 4 = 固件(Flash)映射 + 写，7 = SDRAM 映射 + 写 + sd
# —— 7 就是菜单跑起来时的常态，钩子读完必须切回来。
CPLD_REG = 0x09FFFFFE
CPLD_MAGIC = 0xA55A
MODE_FLASH = 4
MODE_SDRAM = 7
NOP16 = 0x46C0                   # mov r8, r8 —— ARMv4T 的 NOP

FAIL_IDX = 0xFA                  # Flash 也读不到数据时整片涂这个索引（洋红）
FAIL_COLOR = 0x7C1F


def _cs():
    import capstone
    return capstone


def arm_scan(code, base=ROM_BASE):
    """逐 4 字节线性反汇编（capstone 遇到分支会停，不能直接 disasm 一段）。"""
    cs = _cs()
    md = cs.Cs(cs.CS_ARCH_ARM, cs.CS_MODE_ARM)
    out = []
    for off in range(0, len(code) - 3, 4):
        ins = list(md.disasm(bytes(code[off:off + 4]), base + off))
        if ins:
            out.append((off, ins[0]))
    return out


def _imm(ins):
    import re
    m = re.search(r"#(-?0x[0-9a-fA-F]+|-?\d+)", ins.op_str)
    return int(m.group(1), 0) if m else None


def _dst_reg(ins):
    """目标寄存器（op_str 的第一项）。"""
    if not ins.op_str:
        return None
    return ins.op_str.split(",")[0].strip()


def _lit_value(ins, code, base=ROM_BASE):
    import re
    if ins.mnemonic != "ldr" or "[pc" not in ins.op_str:
        return None
    m = re.search(r"#(-?0x[0-9a-fA-F]+|-?\d+)\]", ins.op_str)
    if not m:
        return None
    addr = ins.address + 8 + int(m.group(1), 0)
    off = addr - base
    if 0 <= off <= len(code) - 4:
        return struct.unpack_from("<I", code, off)[0]
    return None


class Copy(object):
    def __init__(self, call_addr, src, dst, blocks, cnt_addr):
        self.call_addr = call_addr
        self.src = src
        self.dst = dst
        self.blocks = blocks
        self.cnt_addr = cnt_addr

    @property
    def size(self):
        return self.blocks * BLOCKS

    @property
    def src_end(self):
        return self.src + self.size

    def __repr__(self):
        return "Copy(0x%08X->0x%08X, %d blk = %d B, cnt@0x%X)" % (
            self.src, self.dst, self.blocks, self.size, self.cnt_addr)


class Layout(object):
    def __init__(self):
        self.payload_src = 0
        self.payload_len = 0
        self.payload_cnt_addr = None
        self.copies = []
        self.assets = None
        self.free = []

    @property
    def payload_end(self):
        return EWRAM_MIRROR + self.payload_len

    @property
    def hook_addr(self):
        return self.payload_end - HOOK_RESERVE

    @property
    def hook_rom(self):
        return self.payload_src + self.payload_len - HOOK_RESERVE

    def describe(self):
        s = "载荷 ROM 0x%06X..0x%06X (%d B) -> EWRAM 0x%08X..0x%08X\n" % (
            self.payload_src, self.payload_src + self.payload_len,
            self.payload_len, EWRAM_MIRROR, self.payload_end)
        for c in self.copies:
            s += "  %r\n" % c
        s += "钩子 EWRAM 0x%08X（ROM 0x%06X 起 %d 字节）\n" % (
            self.hook_addr, self.hook_rom, HOOK_RESERVE)
        s += "空闲区: " + ", ".join("0x%06X..0x%06X(%d)" % (a, b, b - a)
                                 for a, b in self.free)
        return s


def find_layout(rom):
    fw_size = struct.unpack_from("<I", bytes(rom[0:0x100]), 0xCC)[0]
    code = bytes(rom[:fw_size])
    insns = arm_scan(code[:0x1200])
    L = Layout()

    # ---- 载荷拷贝：mov r1, #0x027c0000
    for i, (off, ins) in enumerate(insns):
        if ins.mnemonic == "mov" and _imm(ins) == EWRAM_MIRROR:
            for j in range(i - 1, max(-1, i - 6), -1):
                v = _lit_value(insns[j][1], code)
                if v and ROM_BASE <= v < ROM_BASE + fw_size:
                    L.payload_src = v - ROM_BASE
                    break
            for j in range(i + 1, min(len(insns), i + 4)):
                if insns[j][1].mnemonic == "mov" and _imm(insns[j][1]):
                    L.payload_len = _imm(insns[j][1]) * 4
                    L.payload_cnt_addr = insns[j][0]
                    break
            break
    if not L.payload_src or not L.payload_len:
        raise ValueError("没找到 EWRAM 载荷拷贝代码，构建不认识")

    # ---- 所有 sdram_copy 调用（按调用点前面的 r0/r1/r2 装载来识别，
    #      不靠"哪个函数被调用最多"—— set_sd_mode 可能被调用得更多）
    bls = [(off, ins) for off, ins in insns if ins.mnemonic == "bl"]
    for off, ins in bls:
        if off >= L.payload_src:
            continue
        idx = [k for k, (o, _) in enumerate(insns) if o == off][0]
        src = dst = blocks = None
        cnt = None
        for j in range(idx - 1, max(-1, idx - 12), -1):
            o, i2 = insns[j]
            rd = _dst_reg(i2)
            if blocks is None and i2.mnemonic == "mov" and rd == "r2":
                v = _imm(i2)
                if v and 0 < v < 0x10000:
                    blocks, cnt = v, o
            if dst is None and rd == "r1":
                v = _lit_value(i2, code) if i2.mnemonic == "ldr" else (
                    _imm(i2) if i2.mnemonic == "mov" else None)
                if v and v >= 0x08000000:
                    dst = v
            if src is None and rd == "r0" and i2.mnemonic == "ldr":
                v = _lit_value(i2, code)
                if v and ROM_BASE <= v < ROM_BASE + fw_size:
                    src = v - ROM_BASE
        if src is not None and dst is not None and blocks:
            c = Copy(off + ROM_BASE, src, dst, blocks, cnt)
            # 同一个 sdram_copy 调用会被多条 bl 识别到（跳转表/重复调用点），
            # 源、目标、块数三者全同就是同一份拷贝
            if not any((c.src, c.dst, c.blocks) == (x.src, x.dst, x.blocks)
                       for x in L.copies):
                L.copies.append(c)
    L.copies.sort(key=lambda c: c.call_addr)

    for c in L.copies:
        if c.dst == 0x09E00000:
            L.assets = c
    if L.assets is None:
        raise ValueError("没找到 assets 的 sdram_copy（目标 0x09E00000）")

    # ---- 空闲区
    used = [(0, L.payload_src + L.payload_len)]
    for c in L.copies:
        used.append((c.src, c.src_end))

    def clip(a, b):
        """把零区 [a,b) 按「有东西在用」的区间切开，返回剩下的段。

        以前是「零区只要碰到任何一段用到的东西就整段丢掉」，于是资源块
        尾部那一大片零（资产只用到前 153 KB，后面 89 KB 全是 0）会被
        一起扔掉，chis 构建就只剩 29 KB 空位、塞不下背景图。
        正确做法是把用到的区间从零区里减掉，剩下的还是能用的。
        """
        parts = [(a, b)]
        for ua, ub in used:
            nxt = []
            for pa, pb in parts:
                if ub <= pa or pb <= ua:
                    nxt.append((pa, pb))
                    continue
                if pa < ua:
                    nxt.append((pa, ua))
                if ub < pb:
                    nxt.append((ub, pb))
            parts = nxt
        return parts

    i = 0
    while i < fw_size:
        if code[i] == 0:
            j = i
            while j < fw_size and code[j] == 0:
                j += 1
            for a, b in clip(i, j):
                if b - a >= 8192:
                    L.free.append((a, b))
            i = j
        else:
            i += 1
    L.free.sort()
    return L


def free_regions(rom, L=None):
    """真正能用来放背景图的空位（assets 那一整块要拷进 SDRAM，从后面开始算）。"""
    L = L or find_layout(rom)
    out = []
    for a, b in L.free:
        a = max(a, L.assets.src_end)
        if b - a >= 1024:
            out.append((a, b))
    return out


def room_for(rom):
    """背景图能不能塞进去：返回 (能塞?, 需要的字节, 可用空位列表)。"""
    need_a = 95 * IMG_W
    need_b = HDR_SIZE + PAL_N * 2 + (IMG_H - 95) * IMG_W
    regs = free_regions(rom)
    ra = next((r for r in regs if r[1] - r[0] >= need_a), None)
    rb = next((r for r in regs if r is not ra and r[1] - r[0] >= need_b), None)
    return (ra is not None and rb is not None, need_a + need_b, regs)


# --------------------------------------------------------------- Thumb 编码
class Thumb(object):
    def __init__(self):
        self.b = bytearray()
        self.fix = []

    def h(self, v):
        self.b += struct.pack("<H", v & 0xFFFF)

    def mark(self):
        return len(self.b)

    def push_all(self):
        self.h(0xB5FF)

    def pop_all(self):
        self.h(0xBDFF)

    def mov_hi(self, rd, rm):
        self.h(0x4600 | ((rd >> 3) << 7) | (rm << 3) | (rd & 7))

    def movs(self, rd, imm):
        assert 0 <= imm < 256, imm
        self.h(0x2000 | (rd << 8) | imm)

    def lsls_imm(self, rd, rm, imm):
        assert 0 <= imm < 32
        self.h(0x0000 | (imm << 6) | (rm << 3) | rd)

    def adds_rr(self, rd, rn, rm):
        self.h(0x1800 | (rm << 6) | (rn << 3) | rd)

    def adds_imm(self, rd, imm):
        assert 0 <= imm < 256
        self.h(0x3000 | (rd << 8) | imm)

    def subs_imm(self, rd, imm):
        assert 0 <= imm < 256
        self.h(0x3800 | (rd << 8) | imm)

    def ldmia(self, rn, regs):
        """ldmia rN!, {regs} —— regs 是寄存器号元组。"""
        mask = 0
        for r in regs:
            mask |= 1 << r
        self.h(0xC800 | (rn << 8) | mask)

    def stmia(self, rn, regs):
        """stmia rN!, {regs}"""
        mask = 0
        for r in regs:
            mask |= 1 << r
        self.h(0xC000 | (rn << 8) | mask)

    def ldrh(self, rd, rn, imm=0):
        assert imm % 2 == 0 and 0 <= imm < 64
        self.h(0x8800 | ((imm >> 1) << 6) | (rn << 3) | rd)

    def strh(self, rd, rn, imm=0):
        assert imm % 2 == 0 and 0 <= imm < 64
        self.h(0x8000 | ((imm >> 1) << 6) | (rn << 3) | rd)

    def str(self, rd, rn, imm=0):
        assert imm % 4 == 0 and 0 <= imm < 128
        self.h(0x6000 | ((imm >> 2) << 6) | (rn << 3) | rd)

    def ldr(self, rd, rn, imm=0):
        assert imm % 4 == 0 and 0 <= imm < 128
        self.h(0x6800 | ((imm >> 2) << 6) | (rn << 3) | rd)

    def cmp_imm(self, rn, imm):
        assert 0 <= imm < 256
        self.h(0x2800 | (rn << 8) | imm)

    def cmp_rr(self, rn, rm):
        """CMP rn, rm（两个低寄存器，0x4280）。"""
        assert 0 <= rn < 8 and 0 <= rm < 8
        self.h(0x4280 | (rm << 3) | rn)

    def ldrb(self, rd, rn, imm=0):
        assert 0 <= imm < 32
        self.h(0x7800 | (imm << 6) | (rn << 3) | rd)

    def strb(self, rd, rn, imm=0):
        assert 0 <= imm < 32
        self.h(0x7000 | (imm << 6) | (rn << 3) | rd)

    def bcond(self, cond, target):
        self.h(0xD000 | (cond << 8))
        self.fix.append(("rel", len(self.b) - 2, target))

    def bcond_f(self, cond):
        """向前跳：先占位，由 here() 稍后填目标。返回 fix 下标。"""
        self.h(0xD000 | (cond << 8))
        self.fix.append(("rel", len(self.b) - 2, None))
        return len(self.fix) - 1

    def here(self, idx):
        """把 bcond_f 的目标定在当前位置。"""
        kind, pos, _ = self.fix[idx]
        self.fix[idx] = (kind, pos, len(self.b))

    def jump(self, target):
        """无条件 B（±2KB）。target 给 None 就先占位，稍后用 here() 回填。"""
        self.h(0xE000)
        self.fix.append(("b", len(self.b) - 2, target))
        return len(self.fix) - 1

    def ldr_lit(self, rd, value):
        self.fix.append(("lit", len(self.b), rd, value))
        self.h(0x4800 | (rd << 8))

    def finish(self, base_addr):
        while len(self.b) % 4:
            self.h(0x46C0)                    # mov r8,r8 —— ARMv4T 的 NOP
        lit_pos = len(self.b)
        pool = bytearray()
        for item in self.fix:
            if item[0] != "lit":
                continue
            _, pos, rd, value = item
            off = lit_pos + len(pool)
            pc = (base_addr + pos + 4) & ~3
            imm = off - (pc - base_addr)
            assert 0 <= imm <= 1020 and imm % 4 == 0, "字面量太远: %d" % imm
            self.b[pos:pos + 2] = struct.pack("<H", 0x4800 | (rd << 8) | (imm >> 2))
            pool += struct.pack("<I", value & 0xFFFFFFFF)
        self.b += pool
        for item in self.fix:
            if item[0] != "rel":
                continue
            _, pos, target = item
            assert target is not None, "bcond_f 占位没被 here() 填上"
            off = target - (pos + 4)
            assert off % 2 == 0
            assert -256 <= off < 256, "短分支超范围: %d" % off
            old = struct.unpack_from("<H", self.b, pos)[0]
            self.b[pos:pos + 2] = struct.pack("<H", (old & 0xFF00) | ((off >> 1) & 0xFF))
        for item in self.fix:
            if item[0] != "b":
                continue
            _, pos, target = item
            assert target is not None, "jump() 占位没被 here() 填上"
            off = target - (pos + 4)
            assert off % 2 == 0
            assert -2048 <= off < 2048, "长跳超范围: %d" % off
            self.b[pos:pos + 2] = struct.pack("<H", 0xE000 | ((off >> 1) & 0x7FF))
        while len(self.b) % 4:
            self.h(0x46C0)
        return bytes(self.b)


def thumb_bl(site_addr, target):
    """ARMv4T Thumb BL（两个半字，23 位有符号位移，±4MB）。

    不是 Thumb-2 的 J1/J2 形式 —— ARM7TDMI 不认那个。
    """
    off = target - (site_addr + 4)
    if off % 2:
        raise ValueError("BL 目标必须半字对齐")
    if not (-0x400000 <= off < 0x400000):
        raise ValueError("BL 超出 ±4MB: %d" % off)
    off &= 0x7FFFFF
    s = (off >> 22) & 1
    imm10 = (off >> 12) & 0x3FF
    imm11 = (off >> 1) & 0x7FF
    return struct.pack("<HH", 0xF000 | (s << 10) | imm10, 0xF800 | imm11)


def _dma_sync(t):
    """等 DMA3 把上一步搬完。

    GBA 的 DMAxCNT_L 是「剩余个数」，搬完读回 0 —— 它不是什么忙碌标志位，
    所以不能拿 bit15 当 busy 判断（之前的写法就是这个错）。
    再配一个计数器上界：万一 CNT_L 不递减也不至于卡死在这儿。
    """
    t.ldr_lit(6, DMA_WAIT_MAX)
    w = t.mark()
    t.ldrh(2, 0, 8)                           # CNT_L = 还剩几个
    t.cmp_imm(2, 0)
    done = t.bcond_f(0x0)                     # beq: 0 就是搬完了
    t.subs_imm(6, 1)
    t.bcond(0x1, w)                           # bne: 还在搬，继续等
    t.here(done)


def _cpld_mode(t, mode):
    """写 CPLD 模式寄存器：0xA55A 两遍解锁 + 模式值两遍。"""
    t.ldr_lit(0, CPLD_REG)
    t.ldr_lit(1, CPLD_MAGIC)
    t.strh(1, 0)
    t.strh(1, 0)
    t.movs(1, mode)
    t.strh(1, 0)
    t.strh(1, 0)
    for _ in range(4):
        t.h(NOP16)


def find_theme_addr(rom, L=None):
    """固件运行时 `menu_theme`（uint8_t）的地址，找不到返回 None。

    settings.c 里把设置项做成一张 {名字, 变量指针} 表：
        { "theme", &menu_theme },
    所以先在载荷里找字符串 "theme\\0"，再找指向它的指针；表项是 8 字节，
    紧跟指针的 4 字节就是变量地址。chis 构建解出来是 0x0300004C（IWRAM），
    r75 构建是另一个地址 —— 一律现算，不写死。
    """
    L = L or find_layout(rom)
    seg = bytes(rom[L.payload_src:L.payload_src + L.payload_len])
    i = seg.find(b"theme\x00")
    if i < 0:
        return None
    ref = struct.pack("<I", EWRAM_MIRROR + i)
    j = seg.find(ref)
    while j >= 0:
        if j + 8 <= len(seg):
            val = struct.unpack_from("<I", seg, j + 4)[0]
            if (0x03000000 <= val < 0x03008000 or
                    EWRAM_MIRROR <= val < EWRAM_MIRROR + 0x40000):
                return val
        j = seg.find(ref, j + 1)
    return None


def build_hook(pal_flash, data_a, a_len, data_b, b_len,
               frame_reg=0, code_addr=0x027FE700, icons=None):
    """生成钩子：从卡带 Flash 读调色板和位图，铺进当前帧的 VRAM。

    pal_flash / data_a / data_b 是 **Flash 里的绝对地址**（0x08xxxxxx =
    0x08000000 + 固件文件偏移）。钩子进来先把 CPLD 切到 Flash 模式
    （bootloader 拷 assets 用的就是这条路），读完切回 SDRAM 模式。

    为什么不用 SDRAM（v0.14 实机定论）：这块卡上 0x09E00000 读回全 0xFF
    （未映射），只有 0x08E80000（字体，14.5MB）以内可读 —— CPLD 的 SDRAM
    窗口只到 16MB 左右，bootloader 那 260KB 拷到 30MB 全是白拷。

    为什么只写当前帧：双缓冲两页一起写会把正在显示的那页也涂掉，真机上
    文字会一直闪、偶尔整片消失（V2/V3 的现场）。

    失败保护：Flash 里 A 段首字若是 0x00000000 / 0xFFFFFFFF，说明没读到
    数据，整片涂洋红（索引 0xFA），一眼就能和"正常出图/没跑"区分开。

    icons 给了就顺带做「每套主题一套图标」：每帧比一下 menu_theme，变了就把
    那一套的 4352 字节精灵块 + 38 项调色板重新拷进 VRAM。只在切主题那一帧
    干一次活，平时就两条 ldrb + 一次比较。
    """
    t = Thumb()
    t.push_all()
    t.mov_hi(6, frame_reg)                    # r6 = frame（当前显示的那一页）

    # ① 切到 Flash 模式
    _cpld_mode(t, MODE_FLASH)

    # ①½ 6 套主题各自的图标（切主题后重新装一次）
    if icons:
        t.ldr_lit(0, icons["theme_addr"])
        t.ldrb(1, 0)                          # r1 = menu_theme
        t.ldr_lit(2, icons["state_addr"])
        t.ldrb(3, 2)                          # r3 = 上次装的是第几套
        t.cmp_rr(1, 3)
        same = t.bcond_f(0x0)                 # beq：没换主题，跳过
        t.strb(1, 2)                          # 记下这一套
        t.ldr_lit(0, icons["base"])           # r0 = 第 1 套的地址
        t.cmp_imm(1, 0)
        first = t.bcond_f(0x0)                # 第 1 套不用加偏移
        adv = t.mark()
        t.ldr_lit(3, ICON_STRIDE)
        t.adds_rr(0, 0, 3)
        t.subs_imm(1, 1)
        t.bcond(0x1, adv)
        t.here(first)
        t.ldr_lit(2, ICON_DST)
        t.ldr_lit(3, ICON_BYTES // 16)
        cp = t.mark()
        t.ldmia(0, (1, 4, 5, 7))
        t.stmia(2, (1, 4, 5, 7))
        t.subs_imm(3, 1)
        t.bcond(0x1, cp)
        # ldmia 已经把 r0 推到精灵块之后，正好是这一套的调色板
        t.ldr_lit(1, ICON_PAL_DST)
        t.movs(2, ICON_PAL_N)
        pl = t.mark()
        t.ldrh(3, 0)
        t.strh(3, 1)
        t.adds_imm(0, 2)
        t.adds_imm(1, 2)
        t.subs_imm(2, 1)
        t.bcond(0x1, pl)
        t.here(same)

    # ② A 段首字自检，空数据就走失败分支
    t.ldr_lit(0, data_a)
    t.ldr(3, 0)
    t.cmp_imm(3, 0)
    bad1 = t.bcond_f(0x0)                     # beq: 读回 0
    t.adds_imm(3, 1)
    bad2 = t.bcond_f(0x0)                     # beq: 读回 0xFFFFFFFF

    # ③ 调色板：220 个半字 -> 槽 20..239
    t.ldr_lit(0, pal_flash)
    t.ldr_lit(1, PALETTE + PAL_BASE * 2)
    t.movs(2, PAL_N)
    loop = t.mark()
    t.ldrh(3, 0)
    t.strh(3, 1)
    t.adds_imm(0, 2)
    t.adds_imm(1, 2)
    t.subs_imm(2, 1)
    t.bcond(0x1, loop)                        # bne

    # ④ dst = frame + 0xF00（内容区起点），A、B 两段连着写
    t.ldr_lit(0, 0x0F00)
    t.adds_rr(0, 6, 0)

    # ⑤ 位图：ldmia/stmia 一次 16 字节（两段在 ROM 里不连续）
    for src, n in ((data_a, a_len), (data_b, b_len)):
        assert n % 16 == 0, "位图分段必须 16 字节对齐: %d" % n
        t.ldr_lit(1, src)
        t.ldr_lit(2, n // 16)
        cp = t.mark()
        t.ldmia(1, (3, 4, 5, 7))
        t.stmia(0, (3, 4, 5, 7))
        t.subs_imm(2, 1)
        t.bcond(0x1, cp)                      # bne
    done = t.jump(None)

    # ⑥ 失败分支：整片洋红（Flash 也没读到数据）
    t.here(bad1)
    t.here(bad2)
    t.ldr_lit(0, PALETTE + FAIL_IDX * 2)
    t.ldr_lit(1, FAIL_COLOR)
    t.strh(1, 0)
    t.ldr_lit(0, FAIL_IDX * 0x01010101)
    t.mov_hi(1, 0)
    t.mov_hi(2, 0)
    t.mov_hi(3, 0)
    t.ldr_lit(7, 0x0F00)
    t.adds_rr(7, 6, 7)
    t.ldr_lit(4, (a_len + b_len) // 16)
    fill = t.mark()
    t.stmia(7, (0, 1, 2, 3))
    t.subs_imm(4, 1)
    t.bcond(0x1, fill)

    # ⑦ 切回 SDRAM 模式（固件常态 7）
    t.here(done)
    _cpld_mode(t, MODE_SDRAM)

    t.pop_all()
    return t.finish(code_addr)


# --------------------------------------------------------------- 图像
def prepare_image(img, dim=0.0):
    from PIL import Image, ImageEnhance
    import wallpaper as WP

    im = WP.fit_image(img.convert("RGB"), "cover")
    if im.size != (IMG_W, IMG_H):
        im = im.resize((IMG_W, IMG_H), Image.LANCZOS)
    if dim > 0:
        im = ImageEnhance.Brightness(im).enhance(max(0.0, 1.0 - dim))

    q = im.quantize(colors=PAL_N, method=Image.MEDIANCUT,
                    dither=Image.FLOYDSTEINBERG)
    raw = list(q.getdata())
    used = len(set(raw))
    idx = bytes(SLOTS[v] if v < PAL_N else PAL_BASE for v in raw)
    p = q.getpalette() or []
    pal = []
    for i in range(PAL_N):
        r, g, b = (list(p[i * 3:i * 3 + 3]) + [0, 0, 0])[:3]
        pal.append(((r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)))
    return idx, pal, used, im


_WALL_FIRST = 4                  # = wallpaper.PAL_FIRST（fwbuild.PAL_FIRST），WP.quantize 自加的偏移


def map_wall_indices(idxs):
    """WP.quantize 的输出 → SLOTS 槽位。

    WP.quantize 返回的像素值已经 +PAL_FIRST(4)，调色板表里颜色 i 在槽 PAL_FIRST+i。
    直接把它的像素值当 raw 再映射会整体错 4 格：前 216 个颜色因为像素和调色板
    两边偏移互相抵消碰巧正确，但最后 4 个颜色（raw 216..219）会被挤到槽
    PAL_BASE（黑色）—— 图片用满 220 色时真机上就是一片错色的黑点。
    这里先剥掉 +4 再映射，编辑器/一键写入/打包都必须走这个函数。
    """
    pf = _WALL_FIRST
    return bytes(SLOTS[v - pf] if 0 <= v - pf < PAL_N else PAL_BASE for v in idxs)


def slice_wall_palette(pal, n=PAL_N):
    """WP.quantize 的调色板表（颜色 i 在槽 PAL_FIRST+i）→ 按 0..n-1 排列。"""
    pf = _WALL_FIRST
    out = list(pal)[pf:pf + n]
    return out + [0] * (n - len(out))


def bgr555_to_rgb(v):
    r = (v & 0x1F) << 3
    g = ((v >> 5) & 0x1F) << 3
    b = ((v >> 10) & 0x1F) << 3
    return (r | r >> 5, g | g >> 5, b | b >> 5)


def _literals_into(seg, lo, hi):
    """载荷里有没有 32 位字面量指向 [lo,hi)（Thumb 的 ldr rX,=v 就是这种）。"""
    for i in range(0, len(seg) - 4, 2):
        v = struct.unpack_from("<I", seg, i)[0]
        if lo <= v < hi:
            return (i, v)
    return None


def find_hook_slot(rom, L, need=HOOK_RESERVE):
    """在载荷里找一段可以放钩子的全零区，返回载荷内偏移。

    优先用载荷末尾的填充区（编译器对齐留下的尾巴），其次才在载荷内部找
    足够长的零区——那种通常是节之间的对齐填充，但必须确认没有字面量指向它。
    """
    seg = bytes(rom[L.payload_src:L.payload_src + L.payload_len])

    # 1) 载荷尾部
    tail = seg[-need:]
    if not any(tail) and not _literals_into(seg, L.hook_addr, L.hook_addr + need):
        return L.payload_len - need

    # 2) 载荷内部足够长的零区
    best = None
    i = 0
    while i < len(seg):
        if seg[i] == 0:
            j = i
            while j < len(seg) and seg[j] == 0:
                j += 1
            start = (i + 3) & ~3
            if j - start >= need:
                if not _literals_into(seg, EWRAM_MIRROR + start,
                                      EWRAM_MIRROR + start + need):
                    if best is None:
                        best = start
            i = j
        else:
            i += 1
    return best


def check_rom(rom):
    """确认这个固件满足烘焙前提，返回 (Layout, site, hook_off)。"""
    import fwbuild as F
    if find_baked(rom):
        raise ValueError("这个固件里已经有背景图，换图应该走替换（rebake），"
                         "不能再烘焙一次")
    L = find_layout(rom)
    hook_off = find_hook_slot(rom, L)
    if hook_off is None:
        raise ValueError("载荷里找不到 256 字节的空位放钩子")
    site = F.detect(rom)
    if site.patch_len < 4:
        raise ValueError("补丁点太短")
    return L, site, hook_off


def _icon_record(base_flash, theme_addr):
    """图标表尾部的记录：基址 + menu_theme 地址 + 步长 + 套数（定长 ICON_RECORD）。"""
    body = ICON_MAGIC + b"\0" + struct.pack(
        "<IIII", base_flash, theme_addr, ICON_STRIDE, THEME_COUNT)
    assert len(body) <= ICON_RECORD
    return body + b"\0" * (ICON_RECORD - len(body))


def find_icons(rom):
    """找「6 套图标」表，返回 {table_rom, base_flash, theme_addr, ...}；没有返回 None。"""
    d = bytes(rom)
    i = d.find(ICON_MAGIC)
    while i >= 0:
        if i + ICON_RECORD <= len(d):
            base, theme, stride, count = struct.unpack_from("<IIII", d, i + 8)
            ok_base = ROM_BASE <= base < ROM_BASE + 0x2000000
            ok_theme = (0x03000000 <= theme < 0x03008000 or
                        EWRAM_MIRROR <= theme < EWRAM_MIRROR + 0x40000)
            if ok_base and ok_theme and stride and count == THEME_COUNT:
                table = base - ROM_BASE
                if table >= 0 and table + stride * count <= len(d):
                    return {"record_at": i, "table_rom": table,
                            "base_flash": base, "theme_addr": theme,
                            "stride": stride, "count": count,
                            "end": i + ICON_RECORD}
        i = d.find(ICON_MAGIC, i + 1)
    return None


def find_zero_slot(rom, need, avoid=(), start=0, end=None):
    """找一段连续全 0 的空位（16 字节对齐），避开 avoid 里的区间。

    「6 套图标」要 26KB 连续空间，而这时候固件里已经烘焙过背景图 ——
    free_regions()（按 vf 目录算的）就不准了，只能直接看字节是不是全 0。
    """
    end = len(rom) if end is None else min(end, len(rom))
    i = max(0, start)
    while i < end:
        if rom[i] == 0:
            j = i
            while j < end and rom[j] == 0:
                j += 1
            pos = (i + 15) & ~15
            while pos + need <= j:
                hit = next(((a, b) for a, b in avoid if pos < b and a < pos + need), None)
                if hit is None:
                    return pos
                pos = (max(pos, hit[1]) + 15) & ~15      # 跳过这段被占的，接着往后找
            i = j
        else:
            i += 1
    return None


def _icon_table_bytes(icon_sets, force=False):
    """6 套图标 → 一整块（每套 ICON_STRIDE：4352 精灵 + 76 调色板）。

    每套的第 0 项固定为 0（透明），颜色从索引 1 起 —— 和 patch_icons 一致。
    6 套完全相同时返回 None（force=True 则照样写 6 份）：省下 26KB，
    钩子也不用每帧比 menu_theme。
    """
    if not icon_sets:
        return None
    if len(icon_sets) != THEME_COUNT:
        raise ValueError("图标要给 %d 套，收到 %d 套" % (THEME_COUNT, len(icon_sets)))
    sets = []
    for i, (img, pal) in enumerate(icon_sets):
        img = bytes(img)
        if len(img) != ICON_BYTES:
            raise ValueError("第 %d 套图标块必须是 %d 字节，收到 %d"
                             % (i + 1, ICON_BYTES, len(img)))
        pal = [int(v) & 0xFFFF for v in list(pal)]
        # 两种调用约定都收：37 色（自己补透明槽）或 38 项（第 0 项已经是透明槽）。
        # 编辑器 _icon_set_of 现在给的是后者，和 theme.bin 里存的格式一致。
        if len(pal) < ICON_PAL_N:
            pal = [0] + pal[:ICON_PAL_N - 1]
        entries = pal[:ICON_PAL_N]
        entries += [0] * (ICON_PAL_N - len(entries))
        sets.append(img + struct.pack("<%dH" % ICON_PAL_N, *entries))
    if len(set(sets)) == 1 and not force:
        return None
    return b"".join(s + b"\0" * (ICON_STRIDE - len(s)) for s in sets)


def bake(rom, img, dim=0.0, indexed=None, palette=None, site=None,
         icon_sets=None):
    """就地修改 rom（bytearray），返回清单。

    indexed / palette 给了就直接用（编辑器已经量化过，保证所见即所得）；
    否则从 img 现场量化。

    site 给了就用它当补丁点，不再重新 `detect()`。`detect()` 会校验头部
    sha256，而主题/图标一写进去校验就必然失败 —— 换图时务必先烘焙背景，
    或者把已经探测好的 site 传进来。
    """
    import fwbuild as F

    rom = bytearray(rom)
    if site is None:
        L, site, hook_off = check_rom(rom)
    else:
        if find_baked(rom):
            raise ValueError("这个固件里已经有背景图，换图应该走替换（rebake）")
        L = find_layout(rom)
        hook_off = find_hook_slot(rom, L)
        if hook_off is None:
            raise ValueError("载荷里找不到 256 字节的空位放钩子")
        if site.patch_len < 4:
            raise ValueError("补丁点太短")
    hook_addr = EWRAM_MIRROR + hook_off
    hook_rom = L.payload_src + hook_off
    if indexed is not None and palette is not None:
        idx = bytes(indexed)
        if len(idx) != IMG_BYTES:
            raise ValueError("位图长度不对: %d != %d" % (len(idx), IMG_BYTES))
        pal = [int(v) & 0xFFFF for v in list(palette)[:PAL_N]]
        pal += [0] * (PAL_N - len(pal))     # 不足就补黑，多出来的丢掉
        used, fitted = len(set(idx)), img
    else:
        idx, pal, used, fitted = prepare_image(img, dim)
    rom_len = len(rom)

    a_rows = 95
    a_len = a_rows * IMG_W
    b_len = (IMG_H - a_rows) * IMG_W
    b_hdr = HDR_SIZE
    b_pal = PAL_N * 2
    b_data = b_hdr + b_pal
    need_b = b_data + b_len

    # 钩子用 ldmia/stmia 搬位图，起点必须 4 字节对齐（这里统一按 16 对齐）
    free = [((lo + 15) & ~15, hi) for lo, hi in free_regions(rom, L)]
    free = [r for r in free if r[1] - r[0] > 0]

    # 6 套图标先占一大块（26.6KB + 32 字节记录），剩下的空位再分给位图 A/B。
    # 放不下就只跳过图标（背景图和 6 套配色照写），不能整个注入失败。
    icon_tab = _icon_table_bytes(icon_sets)
    icon_rom = None
    icon_skip = ""
    theme_addr = None
    if icon_tab:
        try:
            theme_addr = find_theme_addr(rom, L)
            if not theme_addr:
                raise ValueError("找不到 menu_theme 变量（切主题时没法判断用哪套）")
            need_icon = len(icon_tab) + ICON_RECORD
            best = None
            for ri in free:
                if ri[1] - ri[0] < need_icon:
                    continue
                k = free.index(ri)
                rem = ri[0] + need_icon
                rest = (free[:k] + ([(rem, ri[1])] if ri[1] > rem else [])
                        + free[k + 1:])
                # 图标占完这块之后，A、B 还得各自有地方放
                ra2 = next((r for r in rest if r[1] - r[0] >= a_len), None)
                rb2 = next((r for r in rest
                            if r is not ra2 and r[1] - r[0] >= need_b), None)
                if ra2 and rb2:
                    best = (ri, rest)
                    break
            if best is None:
                raise ValueError("空位不够：6 套图标要 %d 字节，背景图还要 A %d + B %d"
                                 % (need_icon, a_len, need_b))
            icon_rom = best[0][0]
            free = best[1]
        except ValueError as e:
            icon_skip = str(e)
            icon_tab = None
            icon_rom = None
            theme_addr = None

    if len(free) < 2:
        have = sum(b - a for a, b in free)
        raise ValueError(
            "这个固件里只找到 %d 段能用的空位（共 %d 字节），背景图要 2 段、"
            "约 %d 字节。换个没改过的卡带原厂固件再试。" % (len(free), have,
                                                    a_len + need_b))

    ra = next((r for r in free if r[1] - r[0] >= a_len), None)
    rb = next((r for r in free if r is not ra and r[1] - r[0] >= need_b), None)
    if ra is None:
        raise ValueError("找不到放位图 A 的 %d 字节空位（最大一段只有 %d 字节）"
                         % (a_len, max(b - a for a, b in free)))
    if rb is None:
        raise ValueError("找不到放位图 B 的 %d 字节空位（最大一段只有 %d 字节）"
                         % (need_b, max(b - a for a, b in free)))
    assert b_data % 4 == 0

    a_rom, b_rom = ra[0], rb[0]
    rom[a_rom:a_rom + a_len] = idx[:a_len]
    if icon_tab:
        rom[icon_rom:icon_rom + len(icon_tab)] = icon_tab
        rom[icon_rom + len(icon_tab):
            icon_rom + len(icon_tab) + ICON_RECORD] = _icon_record(
                ROM_BASE + icon_rom, theme_addr)
    payload_len = L.payload_len

    hdr = bytearray(b_hdr)
    hdr[0:7] = MAGIC                              # 7 字节，第 8 字节留 0
    hdr[8] = BAKE_VER                             # 版本
    bname = site["name"] if isinstance(site, dict) else site.build
    hdr[9] = F.BUILD_IDS.get(bname, 0) & 0xFF     # 构建代号
    struct.pack_into("<HH", hdr, 10, PAL_N, a_rows)
    hdr[14] = int(str(site.frame_reg).lstrip("r"))
    hdr[15] = site.patch_len
    struct.pack_into("<III", hdr, 16, site.patch_off, a_rom, b_rom)
    struct.pack_into("<I", hdr, 28, payload_len)
    struct.pack_into("<I", hdr, 32, hook_off)
    # 把 themes[]/图标坐标也记下来：6 套主题被改写成同色后，签名就搜不到了
    st_themes = site["themes"] if isinstance(site, dict) else site.themes
    st_icons = (site["icons"] if isinstance(site, dict) else site.icons)
    st_ipal = (site["icons_pal"] if isinstance(site, dict) else site.icons_pal)
    struct.pack_into("<III", hdr, 36, max(0, int(st_themes or 0)),
                     max(0, int(st_icons or 0)), max(0, int(st_ipal or 0)))
    # v5：6 套图标表的位置 + 运行时 menu_theme 地址（换图时要原样重装钩子）
    theme_addr = find_theme_addr(rom, L) if icon_tab else None
    struct.pack_into("<IIII", hdr, 48,
                     (ROM_BASE + icon_rom) if icon_rom is not None else 0,
                     theme_addr or 0, ICON_STRIDE, THEME_COUNT)
    rom[b_rom:b_rom + b_hdr] = bytes(hdr)
    rom[b_rom + b_hdr:b_rom + b_hdr + b_pal] = b"".join(
        struct.pack("<H", v) for v in pal)
    rom[b_rom + b_data:b_rom + b_data + b_len] = idx[a_len:]

    # 位图/调色板不再搬进 SDRAM（30MB 那块在这台卡上读不到，见模块头注释），
    # 钩子直接用 Flash 绝对地址读，所以拷贝块数保持原样，一个字都不用改。
    a_flash = ROM_BASE + a_rom
    b_flash = ROM_BASE + b_rom
    pal_flash = b_flash + b_hdr
    data_b_flash = b_flash + b_data

    icons_info = None
    if icon_tab:
        if not theme_addr:
            raise ValueError(
                "在这个固件里找不到 menu_theme 变量（6 套图标要靠它判断当前是哪套），"
                "只能改回「6 套共用一套图标」")
        icons_info = {"base": ROM_BASE + icon_rom,
                      "theme_addr": theme_addr,
                      "state_addr": hook_addr + STATE_OFF,
                      "count": THEME_COUNT}

    hook = build_hook(pal_flash, a_flash, a_len, data_b_flash, b_len,
                      frame_reg=int(str(site.frame_reg).lstrip("r")),
                      code_addr=hook_addr, icons=icons_info)
    if len(hook) > HOOK_RESERVE - 8:
        raise ValueError("钩子太长: %d > %d" % (len(hook), HOOK_RESERVE - 8))
    rom[hook_rom:hook_rom + HOOK_RESERVE] = b"\0" * HOOK_RESERVE
    rom[hook_rom:hook_rom + len(hook)] = hook
    # 状态字节从 0xFF 起：第一帧一定和 menu_theme(0..5) 不同 → 必定装一次图标
    rom[hook_rom + STATE_OFF] = 0xFF

    site_rt = EWRAM_MIRROR + (site.patch_off - L.payload_src)
    bl = thumb_bl(site_rt, hook_addr)
    filler = bl + b"\xC0\x46" * ((site.patch_len - 4) // 2)
    assert len(filler) == site.patch_len
    rom[site.patch_off:site.patch_off + site.patch_len] = filler

    if len(rom) != rom_len:
        raise ValueError("烘焙把固件长度改了: %d -> %d" % (rom_len, len(rom)))

    return {
        "layout": L, "site": site, "site_runtime": site_rt,
        "hook": hook, "hook_addr": hook_addr, "hook_rom": hook_rom,
        "a_rom": a_rom, "b_rom": b_rom,
        "a_len": a_len, "b_len": b_len,
        "a_flash": a_flash, "b_flash": b_flash,
        "pal_flash": pal_flash, "data_b_flash": data_b_flash,
        "icon_rom": icon_rom, "icon_flash": (ROM_BASE + icon_rom) if icon_rom is not None else 0,
        "icon_sets": bool(icon_tab), "theme_addr": theme_addr or 0,
        "icon_skip": icon_skip,
        "colors": used, "pal": pal,
        "idx": idx, "fitted": fitted, "rom": rom,
    }


def rebake(rom, img=None, indexed=None, palette=None, dim=0.0, icon_sets=None):
    """在「已经烘焙过背景图」的固件上换一张图。

    位图尺寸恒为 240×144、调色板恒为 220 色，所以换图不用重新找空位、
    也不用再改补丁点（也不改拷贝块数了 —— 现在钩子直接从 Flash 读）。

    但**钩子必须跟着重写**：v0.13 及更早烘焙进去的钩子要么用 DMA3 从卡带
    SDRAM 读位图，要么读了 30MB 那块不可映射的地址（细节见 build_hook 的
    说明），只换图不换钩子的话，换多少次背景都还是黑的。所以这里每次都
    按当前 build_hook() 重新生成一遍钩子写回，等于顺便把旧固件升级掉。
    返回的结构和 bake() 基本一致。
    """
    info = find_baked(rom)
    if not info:
        raise ValueError("这个固件里没有烘焙记录，应该走正常烘焙流程")
    rom = bytearray(rom)

    if indexed is not None and palette is not None:
        idx = bytes(indexed)
        pal = [int(v) & 0xFFFF for v in list(palette)[:PAL_N]]
        pal += [0] * (PAL_N - len(pal))
        used = len(set(idx))
    else:
        idx, pal, used, _fitted = prepare_image(img, dim)
    if len(idx) != IMG_BYTES:
        raise ValueError("位图长度不对: %d != %d" % (len(idx), IMG_BYTES))

    rows = info["rows"] or 95
    a_len = rows * IMG_W
    b_len = IMG_BYTES - a_len
    a_rom, b_rom = info["a_rom"], info["b_rom"]
    hdr = info.get("hdr_size") or HDR_SIZE
    pal_at = b_rom + hdr
    data_b = pal_at + PAL_N * 2
    if a_rom + a_len > len(rom) or data_b + b_len > len(rom):
        raise ValueError("烘焙记录里的偏移超出文件范围，这个固件可能被改坏了")
    if a_rom < 0x100:
        raise ValueError("烘焙记录里的位图偏移不合理: 0x%X" % a_rom)

    rom[a_rom:a_rom + a_len] = idx[:a_len]
    rom[pal_at:pal_at + PAL_N * 2] = b"".join(struct.pack("<H", v) for v in pal)
    rom[data_b:data_b + b_len] = idx[a_len:]

    L = find_layout(rom)

    # 钩子直接读 Flash：ROM 偏移 O 的运行时地址就是 0x08000000 + O
    # （卡带里固件就在 0 偏移处，v0.14 已在真机上验证过这条路）。
    a_flash = ROM_BASE + a_rom
    b_flash = ROM_BASE + b_rom
    pal_flash = b_flash + hdr
    data_b_flash = b_flash + hdr + PAL_N * 2

    hook_off = info["hook_off"]
    hook_addr = EWRAM_MIRROR + hook_off
    hook_rom = L.payload_src + hook_off
    if hook_rom < 0 or hook_rom + HOOK_RESERVE > L.payload_src + L.payload_len:
        raise ValueError("烘焙记录里的钩子偏移 0x%X 不在载荷里，固件可能被改坏了"
                         % hook_off)
    old_hook = bytes(rom[hook_rom:hook_rom + HOOK_RESERVE])
    # 帧基址寄存器必须照烘焙头里记的那个来。chis 构建是 r0，`info["frame_reg"]
    # 就是 0 —— 以前写成 `or 4`，0 被当成"没记"换成 r4，钩子就去读了个空寄存器，
    # 位图一个字节都搬不进去（真机表现：还是黑背景）。
    frame_reg = info["frame_reg"]
    if not isinstance(frame_reg, int) or not (0 <= frame_reg <= 7):
        raise ValueError("烘焙记录里的帧寄存器不合法: %r" % (frame_reg,))
    # v4 的钩子只占了 0x100，要放「6 套图标」版（288 字节）得往后多吃 0x100。
    # 状态字节（0x1F8）是我们自己写的 0xFF —— 见到它就说明这块 0x200 已经是
    # 我们的地盘（本文件的钩子已经从 0x100 扩过一次），不用再查。
    if rom[hook_rom + STATE_OFF] != 0xFF:
        tail = bytes(rom[hook_rom + 0x100:hook_rom + HOOK_RESERVE])
        if any(tail):
            raise ValueError("钩子后面那 %d 字节不是空的，装不下「6 套图标」版钩子"
                             % (HOOK_RESERVE - 0x100))
    # 6 套图标：头里记了表的位置就沿用；这次给了新图标就整表覆盖
    ico = find_icons(rom)
    icon_skip = ""
    if icon_sets:
        try:
            tab = _icon_table_bytes(icon_sets, force=True)
            theme_addr = (ico["theme_addr"] if ico else find_theme_addr(rom, L))
            if not theme_addr:
                raise ValueError("找不到 menu_theme 变量")
            if ico:
                icon_rom = ico["table_rom"]
            else:
                # 旧固件（v4）还没有图标表：在背景图之外找一段连续全 0 的地方
                b_end = b_rom + hdr + PAL_N * 2 + b_len
                # 从两张位图里更靠前的那个开始找（v4 的 assets 目录把
                # 位图 A 也算进了「资源区」，从 assets.src_end 起就找不到空位了）
                icon_rom = find_zero_slot(
                    rom, len(tab) + ICON_RECORD,
                    avoid=[(a_rom, a_rom + a_len), (b_rom, b_end),
                           (hook_rom, hook_rom + HOOK_RESERVE),
                           (0x100, L.payload_src + L.payload_len)],
                    start=max(0x100, min(a_rom, b_rom)), end=len(rom))
                if icon_rom is None:
                    raise ValueError("空位不够：要 %d 字节连续空间" % (len(tab) + ICON_RECORD))
            if icon_rom < 0 or icon_rom + len(tab) + ICON_RECORD > len(rom):
                raise ValueError("图标表位置超出文件范围")
            rom[icon_rom:icon_rom + len(tab)] = tab
            rom[icon_rom + len(tab):icon_rom + len(tab) + ICON_RECORD] = _icon_record(
                ROM_BASE + icon_rom, theme_addr)
            ico = {"table_rom": icon_rom, "base_flash": ROM_BASE + icon_rom,
                   "theme_addr": theme_addr, "stride": ICON_STRIDE,
                   "count": THEME_COUNT}
        except ValueError as e:
            icon_skip = str(e)
    icons_info = None
    if ico:
        icons_info = {"base": ico["base_flash"], "theme_addr": ico["theme_addr"],
                      "state_addr": hook_addr + STATE_OFF,
                      "count": ico["count"]}

    hook = build_hook(pal_flash, a_flash, a_len, data_b_flash, b_len,
                      frame_reg=frame_reg, code_addr=hook_addr, icons=icons_info)
    if len(hook) > HOOK_RESERVE - 8:
        raise ValueError("钩子太长: %d > %d" % (len(hook), HOOK_RESERVE - 8))
    rom[hook_rom:hook_rom + HOOK_RESERVE] = b"\0" * HOOK_RESERVE
    rom[hook_rom:hook_rom + len(hook)] = hook
    rom[hook_rom + STATE_OFF] = 0xFF        # 逼它下一帧把图标装一遍

    return {
        "layout": L, "replaced": True, "colors": used, "pal": pal, "idx": idx,
        "a_rom": a_rom, "b_rom": b_rom, "a_len": a_len, "b_len": b_len,
        "pal_at": pal_at, "data_b_at": data_b,
        "a_flash": a_flash, "b_flash": b_flash,
        "pal_flash": pal_flash, "data_b_flash": data_b_flash,
        "icon_skip": icon_skip,
        "icon_flash": ico["base_flash"] if ico else 0,
        "icon_sets": bool(ico),
        "icon_rom": ico["table_rom"] if ico else None,
        "theme_addr": ico["theme_addr"] if ico else 0,
        "hook": hook, "hook_addr": hook_addr, "hook_rom": hook_rom,
        "hook_changed": old_hook[:len(hook)] != hook,
        "hook_off": hook_off, "rom": rom,
    }


def _patch_mov_imm(rom, insn_off, value):
    """改一条 ARM `mov rX, #imm` 的立即数（保持 cond/opcode/rd）。"""
    if value >= 0x100 or value < 0:
        raise ValueError("立即数超 8 位: %d" % value)
    word = struct.unpack_from("<I", bytes(rom), insn_off)[0]
    if (word & 0x0FFF0000) != 0x03A00000:
        raise ValueError("0x%X 不是 mov 立即数: 0x%08X" % (insn_off, word))
    new = (word & 0xFFFFFF00) | value
    rom[insn_off:insn_off + 4] = struct.pack("<I", new)
    return new


def find_baked(rom):
    """在固件里找烘焙标记，返回元信息；没有就返回 None。

    v4 的头部是 48 字节（调色板紧跟在 48），v5 起是 64 字节（多出 6 套图标的
    位置和 menu_theme 地址）。两种都要能读，否则旧固件换图会读错调色板位置。
    """
    fw_size = struct.unpack_from("<I", bytes(rom[0:0x100]), 0xCC)[0]
    i = bytes(rom[:fw_size]).find(MAGIC)
    while i != -1:
        ver = rom[i + 8] if i + 9 <= len(rom) else 0
        hdr = HDR_SIZE_V4 if ver == 4 else HDR_SIZE
        if ver in (4, 5) and i + hdr <= len(rom):
            pal_n, rows = struct.unpack_from("<HH", bytes(rom), i + 10)
            frame_reg = rom[i + 14]
            patch_len = rom[i + 15] or 4
            patch_off, a_rom, b_rom = struct.unpack_from("<III", bytes(rom), i + 16)
            payload_len = struct.unpack_from("<I", bytes(rom), i + 28)[0]
            hook_off = struct.unpack_from("<I", bytes(rom), i + 32)[0]
            th, ic, ipal = struct.unpack_from("<III", bytes(rom), i + 36)
            icon_flash = theme_addr = icon_stride = icon_count = 0
            if ver >= 5:
                icon_flash, theme_addr, icon_stride, icon_count = \
                    struct.unpack_from("<IIII", bytes(rom), i + 48)
            res = {
                "ver": ver, "hdr_size": hdr, "build_id": rom[i + 9],
                "themes": th, "icons": ic, "icons_pal": ipal,
                "header_at": i, "pal_n": pal_n, "rows": rows,
                "frame_reg": frame_reg, "patch_off": patch_off,
                "patch_len": patch_len, "hook_off": hook_off,
                "a_rom": a_rom, "b_rom": b_rom,
                "payload_len": payload_len,
                "icon_flash": icon_flash, "theme_addr": theme_addr,
                "icon_stride": icon_stride or ICON_STRIDE,
                "icon_count": icon_count or THEME_COUNT,
                "a_len": rows * IMG_W,
                "b_len": (IMG_H - rows) * IMG_W,
                "pal_at": i + hdr,
                "data_b_at": i + hdr + pal_n * 2,
            }
            if not res["icon_flash"]:
                ico = find_icons(rom)
                if ico:
                    res.update(icon_flash=ico["base_flash"],
                               theme_addr=ico["theme_addr"],
                               icon_stride=ico["stride"],
                               icon_count=ico["count"])
            return res
        i = bytes(rom[:fw_size]).find(MAGIC, i + 1)
    return None
