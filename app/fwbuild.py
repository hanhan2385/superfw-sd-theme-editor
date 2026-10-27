# -*- coding: utf-8 -*-
"""
SuperFW 固件布局探测（build detection）
=====================================
固件里几乎所有东西都是**写死的偏移**：主题表、图标块、内联的 dma_memset16。
换个编译（-flto 的排布会变）偏移就全变，所以这里不存偏移，只存**特征**：

  1. 头部自带 fw_size 与 sha256 —— 先验真身，再动任何字节
  2. themes[] 用 6 组 RGB555 的 72 字节特征搜（内容来自 menu.c，编译后不变）
  3. icons_img / icons_pal 用原始资源块的锚点搜
  4. 背景填充的补丁点用**机器码序列**搜，并记下帧地址在哪个寄存器里

已核对的构建：
  r75-2mb    2,097,152 B  fw_size 0x1E0E00  补丁点 0xA7B4（帧在 r4）
  chis-1.6mb 1,671,680 B  fw_size 0x198200  补丁点 0x6118（帧在 r0）

找不到就报错，绝不"猜一个偏移然后写进去"。
"""
import hashlib
import struct

ROM_BASE = 0x08000000
PALETTE = 0x05000000
VRAM = 0x06000000
DMA = 0x040000D4
FW_MAGIC = b"SUPERFW~DAVIDGF"

IMG_W, IMG_H = 240, 144
IMG_BYTES = IMG_W * IMG_H
PAL_FIRST = 4
PAL_N = 236

# 补丁标记：写在跳板之后（tramp+0xE0..0x100，共 32 字节）。
# 打了补丁的固件里原始机器码已被 BL 覆盖，靠这个标记才能二次识别。
MARK_MAGIC = b"SFWPATCH"
MARK_OFF = 0x80          # 跳板 ≤64 字节，标记放在 0x80..0x100（0x100 起是调色板）
MARK_VER = 2
BUILD_IDS = {"r75-2mb": 0, "chis-1.6mb": 1}


def rgb2gba(r, g, b):
    return ((r >> 3) << 0) | ((g >> 3) << 5) | ((b >> 3) << 10)


# ---------------------------------------------------------------- 源码基线
# src/menu.c themes[]，用来在固件里定位这 72 字节（与编译选项无关）
SRC_THEMES = [
    [0x56B5, 0x7FFF, 0x0000, 0x6739, 0x5E73, 0x4638],   # White
    [0x2A9D, 0x4B1C, 0x0000, 0x3E97, 0x3612, 0x2D6C],   # Orange
    [0x4E04, 0x5ED1, 0x0000, 0x524A, 0x49E3, 0x420D],   # Blue
    [0x2A26, 0x4EB1, 0x0000, 0x462E, 0x39CE, 0x318C],   # Green
    [0x6455, 0x79FC, 0x0000, 0x6175, 0x490E, 0x3D8E],   # Purple
    [0x1084, 0x2108, 0x77BD, 0x39CE, 0x56B5, 0x318C],   # Dark
]
THEMES_SIG = b"".join(struct.pack("<6H", *t) for t in SRC_THEMES)

# src/res/icons.h 原始图标块的锚点：头 24 字节 + 尾 24 字节（共 4352 字节）
ICONS_LEN = 4352
ICONS_HEAD = bytes.fromhex(
    "0000000000000000000000000000000000000000111111110000000000000000")
ICONS_TAIL = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000000")


def _icons_anchor():
    """锚点尽量短但够特异：用中段 32 字节（第 3 个图标附近）更稳。"""
    return None


# ---------------------------------------------------------------- 补丁点特征
# 内联后的 dma_memset16(BG) 片段。最后一条 str 就是"启动 DMA 填充"。
PATCH_SITES = [
    {
        "name": "r75-2mb",
        "sig": bytes.fromhex("1b01e3181360c54b2b60"),
        "len": 10,
        "frame_reg": "r4",
        "note": "frame 在 r4（mov r4, sl 之后），被替换的是 lsls/adds/str/ldr/str 五条",
    },
    {
        "name": "chis-1.6mb",
        "sig": bytes.fromhex("8c4603009f4a63441360a04ba44a1a60"),
        "len": 16,
        "frame_reg": "r0",
        "note": "frame 在 r0（ldr r0,[sp,#0x14] 之后），被替换的是 mov/movs/ldr/add/str/ldr/ldr/str",
    },
]


# ---------------------------------------------------------------- 头部
def fw_size_of(rom):
    if len(rom) < 0x100:
        raise ValueError("文件太短，不是 GBA 固件")
    return struct.unpack("<I", bytes(rom[0xCC:0xD0]))[0]


def is_superfw(rom):
    return len(rom) >= 0x100 and bytes(rom[0xF0:0x100]).startswith(FW_MAGIC)


def validate(rom):
    """按 SuperFW 自己的规则验 sha256（0..0xE0 + 16 个 0 + 0xF0..fw_size）。"""
    if not is_superfw(rom):
        return False, "0xF0 处没有 SUPERFW~DAVIDGF，不是 SuperFW 固件"
    size = fw_size_of(rom)
    if size > len(rom):
        return False, "头部 fw_size=%d 比文件本身（%d）还大" % (size, len(rom))
    h = hashlib.sha256()
    h.update(bytes(rom[0:0xE0]))
    h.update(b"\0" * 16)
    h.update(bytes(rom[0xF0:size]))
    if h.digest()[:16] != bytes(rom[0xE0:0xF0]):
        return False, "头部 sha256 不匹配（文件被改过，或不是完整固件）"
    return True, "ok"


def reseal(rom):
    size = fw_size_of(rom)
    rom[0xE0:0xF0] = b"\0" * 16
    h = hashlib.sha256()
    h.update(bytes(rom[0:0xE0]))
    h.update(b"\0" * 16)
    h.update(bytes(rom[0xF0:size]))
    rom[0xE0:0xF0] = h.digest()[:16]
    return h.digest()[:16].hex()


def _align(v, n):
    return (v + n - 1) // n * n


class Layout(object):
    """一份固件的全部坐标。所有偏移都是搜出来的，不是写死的。"""

    def __init__(self, rom, strict=True):
        self.file_size = len(rom)
        self.fw_size = fw_size_of(rom)
        # 所有特征都只在固件本体（0..fw_size）里搜：
        # 载荷区里有补丁标记，里面刻意存了原始机器码，不能把它当代码段。
        self.code = bytes(rom[:self.fw_size])
        marker = self._read_marker(bytes(rom))
        if marker is None:
            marker = self._read_bake(bytes(rom))       # 烘焙过背景图的固件

        site = None
        try:
            site = self._find_site(self.code)
        except ValueError:
            site = marker
        if site is None:
            # 从源码编译出来的「SD 卡主题」固件：没有补丁点（编译器换了，
            # 指令序列对不上），换肤靠 /.superfw/theme.bin，不需要烘焙。
            if b"/.superfw/theme.bin" in self.code:
                site = {"off": -1, "len": 0, "sig": b"", "frame_reg": 0,
                        "name": "sdtheme", "note": "SD 卡主题固件"}
            else:
                raise ValueError(
                    "没找到 dma_memset16 的内联块（背景补丁点），也没有本工具留下的标记。"
                    "这个构建与已知两版都对不上：可以只改主题/图标，不能打背景图补丁。")
        self.site = site
        self.patch_off = site["off"]
        self.patch_len = site["len"]
        self.patch_sig = site["sig"]
        self.frame_reg = site["frame_reg"]
        self.build = site["name"]
        self.sdtheme = (site["name"] == "sdtheme")
        self.baked = site.get("note", "").startswith("从烘焙头恢复")

        try:
            self.themes = self._find_themes(self.code)
        except ValueError:
            if marker and marker.get("themes"):
                self.themes = marker["themes"]
            elif strict:
                raise
            else:
                self.themes = -1          # 主题表被改得认不出来了，不猜

        self.icons, self.icons_pal = self._find_icons(self.code)
        if (self.icons < 0 or self.icons_pal < 0) and marker:
            self.icons = marker.get("icons", -1)
            self.icons_pal = marker.get("icons_pal", -1)

        # 载荷布局：fw_size 之后第一块 4KB 对齐的空白
        base = _align(self.fw_size + 0x10, 0x1000)
        self.tramp = base
        self.pal = base + 0x100
        self.img = base + 0x1000
        self.img_end = self.img + IMG_BYTES

    # ---- 定位 ----------------------------------------------------
    @staticmethod
    def _find_themes(rom):
        """按行打分定位 themes[]：改过其中一两槽也能认出来（≥4/6 行吻合即可）。"""
        d = bytes(rom)
        rows = [struct.pack("<6H", *t) for t in SRC_THEMES]
        best, best_n = -1, 0
        for k, row in enumerate(rows):
            start = 0
            while True:
                j = d.find(row, start)
                if j < 0:
                    break
                start = j + 1
                base = j - k * 12
                if base < 0:
                    continue
                n = sum(1 for i in range(6)
                        if d[base + i * 12:base + i * 12 + 12] == rows[i])
                if n > best_n:
                    best, best_n = base, n
                if n == 6:
                    return base
        if best_n >= 4:
            return best
        raise ValueError("没找到 themes[]（6 组已知配色里对不上 4 组）："
                         "这不是同一套固件，或者主题表被改得太多。为了安全，不猜偏移。")

    @staticmethod
    def _find_site(rom):
        for s in PATCH_SITES:
            i = bytes(rom).find(s["sig"])
            if i >= 0:
                if i & 3:
                    # BL 要求 4 字节对齐（Thumb BL 两个半字必须连续）
                    continue
                d = dict(s)
                d["off"] = i
                return d
        raise ValueError("没找到 dma_memset16 的内联块（背景补丁点），也没有本工具留下的标记。"
                         "这个构建与已知两版都对不上：可以只改主题/图标，不能打背景图补丁。")

    @staticmethod
    def _read_marker(rom):
        d = bytes(rom)
        i = d.find(MARK_MAGIC)
        if i < 0:
            return None
        try:
            ver, plen, reg = d[i + 8], d[i + 9], chr(d[i + 10])
            off = struct.unpack("<I", d[i + 12:i + 16])[0]
            orig = d[i + 28:i + 28 + plen]
        except Exception:
            return None
        if ver != MARK_VER or plen not in (10, 16) or reg not in "04":
            return None
        name = {v: k for k, v in BUILD_IDS.items()}.get(d[i + 11], "已打补丁")
        themes = struct.unpack("<I", d[i + 16:i + 20])[0]
        icons = struct.unpack("<I", d[i + 20:i + 24])[0]
        icons_pal = struct.unpack("<I", d[i + 24:i + 28])[0]
        return {"name": name, "off": off, "len": plen,
                "frame_reg": "r" + reg, "sig": orig,
                "themes": themes, "icons": icons, "icons_pal": icons_pal,
                "note": "从载荷区标记恢复（补丁已存在）"}

    @staticmethod
    def _read_bake(rom):
        """烘焙过背景图之后补丁点被改写了，签名搜不到 —— 从烘焙头里恢复。

        v4 的 magic 是 "SFWBAKE4"、头 48 字节；v5 起是 "SFWBAKE5"、头 64 字节
        （多了 6 套图标表的位置和 menu_theme 地址）。两种都读。
        """
        d = bytes(rom)
        i = d.find(b"SFWBAKE")
        while i >= 0:
            ver = d[i + 8] if i + 9 <= len(d) else 0
            hdr = 48 if ver == 4 else 64
            if ver in (4, 5) and i + hdr <= len(d):
                plen = d[i + 15] or 4
                reg = d[i + 14]
                if plen in (4, 10, 16) and reg in (0, 4):
                    off = struct.unpack("<I", d[i + 16:i + 20])[0]
                    th, ic, ipal = struct.unpack("<III", d[i + 36:i + 48])
                    name = {v: k for k, v in BUILD_IDS.items()}
                    return {"name": name.get(d[i + 9], "已烘焙背景"), "off": off,
                            "len": plen, "frame_reg": "r%d" % reg, "sig": b"",
                            "themes": th, "icons": ic, "icons_pal": ipal,
                            "note": "从烘焙头恢复（背景图已内置）"}
            i = d.find(b"SFWBAKE", i + 1)
        return None

    @staticmethod
    def _find_icons(rom):
        """图标块：先用原始 4352 字节整块搜（最准），失败再用首尾锚点。"""
        d = bytes(rom)
        pal_off = -1
        try:
            from fwassets import ICONS_IMG_LEN, ICONS_IMG_HEAD, ICONS_IMG_TAIL, ICONS_PAL
        except Exception:
            return -1, -1
        img_off = -1
        # 双锚点：首 96 字节 + 尾 96 字节，避免把别的数据块误认成图标
        j = d.find(ICONS_IMG_HEAD[:96])
        while j >= 0:
            tail = ICONS_IMG_TAIL[:96]
            if d[j + ICONS_IMG_LEN - len(tail):j + ICONS_IMG_LEN] == tail:
                img_off = j
                break
            j = d.find(ICONS_IMG_HEAD[:96], j + 1)
        p = struct.pack("<%dH" % len(ICONS_PAL), *ICONS_PAL)
        pal_off = d.find(p)
        return img_off, pal_off

    def icons_original(self, rom):
        """校验固件里的图标是否仍是原始 17 个（判断能否原地替换）。"""
        try:
            from fwassets import ICONS_IMG, ICONS_IMG_LEN
        except Exception:
            return None
        if self.icons < 0:
            return None
        return bytes(rom[self.icons:self.icons + ICONS_IMG_LEN]) == ICONS_IMG

    # ---- 派生 ----------------------------------------------------
    def payload_needed(self):
        return self.img_end

    def padded_size(self):
        """壁纸载荷要放得下；不够就按 GBA 卡带尺寸（2 的幂）补齐。"""
        need = self.payload_needed()
        size = max(self.file_size, need, 2 * 1024 * 1024)
        p = 2 * 1024 * 1024
        while p < size:
            p *= 2
        return p

    def describe(self):
        return {
            "构建": self.build,
            "文件": "%d B" % self.file_size,
            "fw_size": "0x%X" % self.fw_size,
            "themes[]": "0x%X" % self.themes,
            "icons_img": ("0x%X" % self.icons) if self.icons >= 0 else "未找到",
            "icons_pal": ("0x%X" % self.icons_pal) if self.icons_pal >= 0 else "未找到",
            "补丁点": "0x%X（%d 字节，帧在 %s）" % (self.patch_off, self.patch_len, self.frame_reg),
            "跳板": "0x%X" % self.tramp,
            "调色板": "0x%X" % self.pal,
            "位图": "0x%X..0x%X" % (self.img, self.img_end),
            "需要文件": "%d B（现在 %d）" % (self.payload_needed(), self.file_size),
        }


def detect(rom):
    ok, why = validate(rom)
    if not ok:
        raise ValueError(why)
    return Layout(rom)


def detect_loose(rom):
    """宽松版：不验 sha256、认不出 themes[] 也继续。

    只给「背景图」用 —— 背景烘焙唯一要动的是 dma_memset16 的内联块（结构特征），
    主题表认不出来不影响。这样主题已经被改过、或者被别的工具写过的固件也能补上背景，
    写完会重算 sha256。
    """
    return Layout(rom, strict=False)
