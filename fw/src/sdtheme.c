/*
 * SD 卡主题加载器 —— 把「EZ-Flash Omega DE 那种放张 SD 卡就能换肤」的思路
 * 搬到 SuperFW 上。
 *
 * 和 v0.19 的区别：不再开机把 6 套图标 + 6 张壁纸全搬进内存，而是
 * **只读当前这一套**（约 39.5 KB），切到别的套时再读那一套，剩下的一个
 * 字节都不碰。所以开机读取量和以前相当甚至更少，内存里永远只有一套。
 *
 * 文件格式（/.superfw/theme.bin，小端）
 *
 *   v1 = 全文件一张壁纸，v2 = 每套一张，v3 = v2 再加「每个标签页一张」：
 *
 *   0x0000  char[8]   "SFWTHM01"
 *   0x0008  u16       版本 = 1 / 2 / 3
 *   0x000A  u16       标志位
 *                       v1：bit0 = 带壁纸（一张，6 套共用）
 *                       v2：bit(1+i) = 第 i 套有自己的壁纸
 *                       v3：bit(1+i) = 第 i 套的默认壁纸；bit(7+p) = 第 p 页有自己的图
 *   0x000C  u32       整个文件长度
 *   0x0010  u16[36]  6 套配色，每套 6 个 RGB555（= menu.c 的 themes[] 原样）
 *   0x0058  u8[6*4432] 6 套图标：每套 4352 B 精灵 + 38 项调色板（76 B）+ 4 B 对齐
 *   0x6838  壁纸块，每套 35000 B = 34560 索引 + 440 调色板
 *                      v1：只有开头这一段（文件到 0xF0F0 = 61,680 结束）
 *                      v2/v3：6 段连排（到 0x39CC8 = 236,680）
 *   0x39CC8 页面图块（仅 v3），7 段连排，每段同上
 *                      文件到 0x75990 = 481,680 结束
 *
 * 页面取图规则：这一页有自己的图 → 用它；否则用当前套的默认图；都没有 → BG 纯色。
 *
 * 缓存布局（只放当前这一套 + 当前要显示的那张图）：
 *
 *   0x0000     88 B    文件头 + 6 套配色
 *   0x0058   4432 B    当前套图标（精灵 4352 + 调色板 76 + 4 填充）
 *   0x2000  35000 B    当前要显示的那张壁纸（索引 34560 + 调色板 440）
 *   一共 43,192 B，按 48 KB 预留。
 *
 * 缓存放 SDRAM：菜单模式下能直接读，比每次回卡带 Flash 里翻资源快得多。
 * 具体地址不写死 —— 不同批次/容量的卡带解码范围不一样（实测有的卡
 * 0x09800000 读回全 FF），上电时按候选表逐个「写-读」探测。
 *
 * 注意：SD 接口和 SDRAM 是互斥的（模式寄存器 bit1），读卡期间 SDRAM 整个
 * 不可见，所以只能 4KB 一块先读进 EWRAM 静态缓冲、再用 DMA 写进 SDRAM。
 */

#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#include "common.h"
#include "gbahw.h"
#include "fatfs/ff.h"
#include "nanoprintf.h"
#include "sdtheme.h"

#define SDT_PATH        "/.superfw/theme.bin"
#define SDT_MAGIC       "SFWTHM01"
#define SDT_VERSION     1
#define SDT_VERSION_V2  2       /* 每套一张壁纸 */
#define SDT_VERSION_V3  3       /* 再加：每个标签页可以有自己的图 */
#define SDT_VERSION_V4  4       /* 再加：每一套的每一页都能单独配图 */

#define SDT_NTHEME      6
#define SDT_NPAGE       7       /* 和 menu.c 的 MENUTAB_* 个数一致 */
#define SDT_ICON_BYTES  4352
#define SDT_ICON_PALN   38
#define SDT_ICON_PALB   (SDT_ICON_PALN * 2)
#define SDT_ICON_STRIDE 4432
#define SDT_WALL_BYTES  (240 * 144)
#define SDT_WALLPALN    220

#define SDT_OFF_COLORS  0x0010
#define SDT_OFF_ICONS   0x0058
#define SDT_OFF_WALL    (SDT_OFF_ICONS + SDT_NTHEME * SDT_ICON_STRIDE)  /* 0x6838 */
#define SDT_OFF_WALLPAL (SDT_OFF_WALL + SDT_WALL_BYTES)                 /* 0xEF38 */
#define SDT_WALL_STRIDE (SDT_WALL_BYTES + SDT_WALLPALN * 2)             /* 35000 */
#define SDT_FILE_SIZE   (SDT_OFF_WALLPAL + SDT_WALLPALN * 2)            /* 0xF0F0 = 61680 */
/* v2：6 套壁纸连排，每套 35000 字节（索引 + 调色板），都在 4 字节边界上 */
#define SDT_FILE_SIZE_V2 (SDT_OFF_WALL + SDT_NTHEME * SDT_WALL_STRIDE)  /* 0x39CC8 = 236680 */
/* v3：v2 之后再来 7 段页面图 */
#define SDT_OFF_PAGES   SDT_FILE_SIZE_V2                                /* 0x39CC8 */
#define SDT_FILE_SIZE_V3 (SDT_OFF_PAGES + SDT_NPAGE * SDT_WALL_STRIDE)  /* 0x75990 = 481680 */
#define SDT_FLAG_PAGE_BASE 7                                            /* bit(7+p) */
#define SDT_ICONS_ONLY_SIZE SDT_OFF_WALL                                /* 0x6838 = 26680 */

/* v4：v3 之后一层「每套 × 每页」。42 位掩码 + 42 段定长连排，
 * 空槽位照样占 35000 字节（内容全 0），所以文件是定长的。
 * 掩码位序 = set * SDT_NPAGE + page，和 app/sdtheme.py 的 slot_index() 必须一致。 */
#define SDT_PAGE_SLOTS   (SDT_NTHEME * SDT_NPAGE)                       /* 42 */
#define SDT_OFF_V4MASK   SDT_FILE_SIZE_V3                               /* 0x75990 */
#define SDT_OFF_V4       (SDT_OFF_V4MASK + 8)                           /* 0x75998 */
#define SDT_FILE_SIZE_V4 (SDT_OFF_V4 + SDT_PAGE_SLOTS * SDT_WALL_STRIDE)  /* 1,951,688 */

/* v5：在某个老布局后面追加 36 字节，给「整屏界面」三个独立颜色。
 * 不改动上面任何一个偏移，所以 v1..v4 的解析代码一行不用动；
 * 版本号写 5，基础布局靠「长度 - 36」反推。
 * 顺序：每套 3 个 u16 = 载入画面底、载入进度条、游戏内菜单底。 */
#define SDT_VERSION_V5      5
#define SDT_EXCOL_N         3
#define SDT_EXCOL_BYTES     (SDT_NTHEME * SDT_EXCOL_N * 2)              /* 36 */

/* v6：在 v5 的 36 字节后面再接一份 logo —— 31x7 索引（217）+ 3 色调色板（6）。
 * 同样不改任何已有偏移，版本号写 6，「长度 - 259」反推基础布局。 */
#define SDT_VERSION_V6      6
#define SDT_LOGO_W          31
#define SDT_LOGO_H          7
#define SDT_LOGO_IMG        (SDT_LOGO_W * SDT_LOGO_H)                   /* 217 */
#define SDT_LOGO_PALN       3
#define SDT_LOGO_BYTES      (SDT_LOGO_IMG + SDT_LOGO_PALN * 2)          /* 223 */
#define SDT_V6_TAIL         (SDT_EXCOL_BYTES + SDT_LOGO_BYTES)          /* 259 */

/* v7：再追加 6 套 × 2 字节的界面透明度。
 *   [0] 底栏透明度  [1] 选中条透明度，都是 0..16（0 = 实心，就是老行为）
 * 0 表示「不透明」，所以 v1..v6 的文件不缺字段、行为完全不变。
 * 同样不改任何已有偏移，版本号写 7，「长度 - 271」反推基础布局。 */
#define SDT_VERSION_V7      7
#define SDT_ALPHA_N         2
#define SDT_ALPHA_BAR       0
#define SDT_ALPHA_SEL       1
#define SDT_ALPHA_MAX       16
#define SDT_ALPHA_BYTES     (SDT_NTHEME * SDT_ALPHA_N)                  /* 12 */
#define SDT_V7_TAIL         (SDT_V6_TAIL + SDT_ALPHA_BYTES)             /* 271 */

/* v8：再追加「选中条纹理」——每套一块 16×16 的 8bpp 小图（256 B）+ 最多 15 色。
 * 每套定长 287 = 色数(1) + 15×RGB555(30) + tile(256)，固件按 set*287 直接寻址。
 * tile 像素值 0..15：**0 号恒等于本套的 hi_blend**（换套自动跟色），1..n 取那套的调色板。
 * 调色板写进 OBJ 调色板的 240..255（0..37 归图标，用不到这块）。
 * 六套都没有纹理时文件不会升到 v8，老行为一个字节都不变。 */
#define SDT_VERSION_V8      8
#define SDT_HIB_TILE_W      16
#define SDT_HIB_TILE_BYTES  256
#define SDT_HIB_PAL_MAX     15
#define SDT_HIB_SET_BYTES   (1 + SDT_HIB_PAL_MAX * 2 + SDT_HIB_TILE_BYTES)  /* 287 */
#define SDT_HIB_BYTES       (SDT_NTHEME * SDT_HIB_SET_BYTES)                /* 1722 */
#define SDT_V8_TAIL         (SDT_V7_TAIL + SDT_HIB_BYTES)                   /* 1993 */
#define SDT_HIB_PAL_FIRST   240
#define SDT_HIB_SLOT        63                  /* 选中条用的精灵号 */

/* 缓存内的相对偏移（只放当前套） */
#define SDT_C_HDR       0x0000
#define SDT_C_ICONS     0x0058
#define SDT_C_WALL      0x2000
#define SDT_C_SIZE      0xC000          /* 48 KB，留足余量 */

/* SDRAM 候选缓存地址（按优先级）。
 *
 * 布局（common.h / menu.c 的 t_sdram_state）：
 *   0x08000000..0x08200000  写 NOR / 升级用的 2MB scratch（会被整块覆盖）
 *   0x08200000..0x08E80000  文件浏览器的目录项缓存（静态数组，约 12.5MB）
 *   0x08E80000..0x09000000  字体（常驻）
 *   0x09000000..0x09800000  hiscratch —— 打补丁时最多要占满这 8MB
 *   0x09800000..0x09C00000  空档，**实测 chis 上写入会别名到目录项缓存，别用**
 *   0x09C00000 / 0x09D00000 / 0x09E00000  用户补丁库 / 补丁库 / 内置资源
 *
 * 首选 hiscratch 起点：8MB 空间，固件自己往里 DMA，一定是真实 RAM；
 * 只占前 48 KB。它唯一的占用者是 generate_patches_progress()，所以那边
 * 干完活会调 sdtheme_invalidate()，回菜单后 sdtheme_poll() 自动重读。
 * 最后那个退路在写入 scratch 尾部，写 NOR 时会被踩，同样靠 invalidate 兜住。
 */
#define SDT_NCAND       3
static const uint32_t sdt_cand[SDT_NCAND] = {
  0x09000000,   /* hiscratch 起点 —— 首选 */
  0x09400000,   /* 同一块 8MB 的后半，退路 */
  0x081E0000,   /* 2MB 写入 scratch 尾部，最后退路 */
};

#define SDT_NONE        0xFFFFu

static bool     sdt_loaded = false;
static bool     sdt_missing = false;    /* 三个候选路径都没有这个文件 */
static bool     sdt_v2     = false;     /* 文件是 v2/v3（每套一段壁纸） */
static bool     sdt_v3     = false;     /* 文件是 v3（还有每页一段） */
static bool     sdt_v4     = false;     /* 文件是 v4（每套每页各一段） */
static bool     sdt_v8     = false;     /* 文件是 v8（多了选中条纹理段） */
static uint32_t sdt_fsize  = 0;         /* 文件总长，v8 的纹理段从末尾倒着算 */
static bool     sdt_extra  = false;     /* 文件带尾部（v5 的 36 / v6 的 259 / v7 的 271） */
/* 尾部原样缓存：v5 = 36 字节扩展色；v6 = 36 + 223 logo；v7 = 36 + 223 + 12 透明度。
 * 4 字节对齐：sdt_read_all 走 32 位 DMA，地址不对会读飞。 */
static uint8_t  sdt_tail[SDT_V7_TAIL] __attribute__((aligned(4)));
/* v7 的界面透明度：6 套 × 2 项，0 = 实心（老文件的默认行为） */
static uint8_t  sdt_alpha[SDT_NTHEME][SDT_ALPHA_N];
static bool     sdt_wall_ready = false; /* 缓存里的壁纸 == 现在该显示的那张 */
static bool     sdt_dirty  = false;     /* 缓存被别的功能踩了，需要重读 */
/* 载入 / 写 NOR 那一屏的壁纸已经铺进两个帧缓冲（见 sdtheme_arm_loadbg） */
static bool     sdt_loadbg_armed = false;
static uint8_t  sdt_wallmask = 0;       /* bit i = 第 i 套有默认壁纸 */
static uint8_t  sdt_pagemask = 0;       /* bit p = 第 p 页有自己的图（仅 v3） */
static uint32_t sdt_pmask_lo = 0;       /* v4：42 位掩码的低 32 位 */
static uint32_t sdt_pmask_hi = 0;       /* v4：高 10 位（bit 32..41） */
static unsigned sdt_cur    = SDT_NONE;  /* 已经装进 EWRAM 的图标套 */
static unsigned sdt_page   = SDT_NONE;  /* 当前标签页；SDT_NONE = 还不知道 */
static uint32_t sdt_wall_src = 0;       /* 缓存里那张图来自文件哪个偏移（0 = 没有） */
static uint32_t sdt_cache  = 0;         /* 探测通过的缓存地址，0 = 没有 */
static const char *sdt_path;            /* 打开成功的那个路径（换套重读要用） */

/* FIL 有 550 字节，静态放：用户栈只有 16KB，这条链（menu_init -> load /
 * reload_theme -> apply）上不能再压。这几个操作不会重入，共用一个就够。 */
static FIL sdt_fil;

/* menu.c 提供的资源覆盖入口（资源本身是 const，但落在 EWRAM，运行时可写） */
void menu_override_themes(const uint16_t *colors);
void menu_override_icons(const uint8_t *img, const uint16_t *pal);
void menu_override_logo(const uint8_t *img, const uint16_t *pal);
void menu_refresh_icons(void);
/* 反过来：把当前常驻的（= 内置）资源交出来，供「首次开机自动生成 theme.bin」用 */
void  menu_copy_themes(void *dst);
const void *menu_icons_img_ptr(void);
const void *menu_icons_pal_ptr(void);

/* ---- 诊断文本（只有 SDTHEME_DIAG 构建才画到屏幕上） ----------------------
 * 正式版里 sdtheme_diag_text() 没人调用，缓冲区留 1 字节就够（只写个 '\0'）。
 * 别加回 192：IWRAM 只剩两百来字节，诊断版曾因为这 192 字节撞上「没有栈空间」
 * 的链接断言（详见 menu.c 里 menu_boot_timing 的注释）。
 * 诊断版 96 字节够用：最长的一条是
 *   "OK t4294967295 v4294967295 mff.ff.ffff cffffffff 4294967295+4294967295ms" = 72。 */
#ifdef SDTHEME_DIAG
static char sdt_diag[96] = "";
#else
static char sdt_diag[1] = "";
#endif
#ifdef SDTHEME_DIAG
static unsigned sdt_probe_res = 0;      /* 每位对应 sdt_cand[] 的一项 */
static int      sdt_fo = -1, sdt_fr = -1;
static unsigned sdt_ms = 0, sdt_set_ms = 0;
#endif

const char *sdtheme_diag_text(void) {
  return sdt_diag;
}

/* 写-读探测：地址不存在/不解码时读回的不是我们写的值。 */
static bool sdt_probe(uint32_t addr) {
  volatile uint32_t *p = (volatile uint32_t*)addr;
  p[0] = 0xA5A5F00Du;
  p[1] = 0x1234ABCDu;
  return p[0] == 0xA5A5F00Du && p[1] == 0x1234ABCDu;
}

static uint32_t sdt_pick_cache(void) {
  for (unsigned i = 0; i < SDT_NCAND; i++) {
    bool ok = sdt_probe(sdt_cand[i]);
    #ifdef SDTHEME_DIAG
    if (ok) sdt_probe_res |= (1u << i);
    #endif
    if (ok)
      return sdt_cand[i];
  }
  return 0;
}

/* 分块读：先落到 4KB 静态缓冲，再用 DMA 写进 SDRAM。
 * 读卡期间 SD 接口占着 0x08 窗口，碰不到 SDRAM，所以必须中转。
 * 缓冲放静态区而不是栈上：用户栈只有 16KB，menu_init -> sdtheme_load 这条链上
 * 再摆一个 4KB 数组太贴边，溢出会踩坏 FatFs 的全局对象（= 之后所有文件都读不出来）。 */
static uint32_t sdt_buf[1024];          /* 4 KiB */

static bool sdt_read_all(FIL *f, uint32_t dst, unsigned len) {
  unsigned total = 0;
  while (total < len) {
    unsigned chunk = len - total;
    if (chunk > sizeof(sdt_buf))
      chunk = sizeof(sdt_buf);
    UINT br = 0;
    if (f_read(f, sdt_buf, chunk, &br) != FR_OK || br != chunk)
      return false;
    dma_memcpy32((volatile void*)(dst + total), sdt_buf, chunk / 4);
    total += chunk;
  }
  return true;
}

bool sdtheme_active(void) {
  return sdt_loaded;
}

static inline bool sdt_has_wall(unsigned set) {
  return (sdt_wallmask >> set) & 1;
}

static inline bool sdt_has_page(unsigned page) {
  return sdt_v3 && page < SDT_NPAGE && ((sdt_pagemask >> page) & 1);
}

/* v4：第 set 套的第 page 页有没有专属图。掩码位序必须和 app/sdtheme.py 对齐。 */
static inline bool sdt_has_page_set(unsigned set, unsigned page) {
  if (!sdt_v4 || set >= SDT_NTHEME || page >= SDT_NPAGE)
    return false;
  unsigned bit = set * SDT_NPAGE + page;
  return (bit < 32) ? ((sdt_pmask_lo >> bit) & 1)
                    : ((sdt_pmask_hi >> (bit - 32)) & 1);
}

/* 第 set 套的默认壁纸在文件里的偏移。v1 只有一张，6 套共用，不看 set。 */
static uint32_t sdt_wall_off(unsigned set) {
  return sdt_v2 ? (SDT_OFF_WALL + (uint32_t)set * SDT_WALL_STRIDE) : SDT_OFF_WALL;
}

/* 现在这个「第 thnum 套 + 第 page 页」到底该显示哪一段；0 = 纯色。
 * 页面图优先，其次这套的默认图。 */
static uint32_t sdt_wall_off_for(unsigned thnum, unsigned page) {
  if (sdt_has_page_set(thnum, page))
    return SDT_OFF_V4 + ((uint32_t)thnum * SDT_NPAGE + page) * SDT_WALL_STRIDE;
  if (sdt_has_page(page))
    return SDT_OFF_PAGES + (uint32_t)page * SDT_WALL_STRIDE;
  if (thnum < SDT_NTHEME && sdt_has_wall(thnum))
    return sdt_wall_off(thnum);
  return 0;
}

/* 当前这一套/这一页到底有没有壁纸可画。sdt_wall_ready 保证缓存里那份
 * 真的是现在该显示的图，否则会画出上一页的壁纸。 */
bool sdtheme_wall_enabled(void) {
  return sdt_loaded && sdt_wall_ready;
}

/* 读第 set 套的图标段到缓存（4432 字节，很快）。 */
static bool sdt_fetch_icons(unsigned set) {
  FIL *f = &sdt_fil;
  if (!sdt_path || f_open(f, sdt_path, FA_READ) != FR_OK)
    return false;

  bool ok = (f_lseek(f, SDT_OFF_ICONS + (uint32_t)set * SDT_ICON_STRIDE) == FR_OK) &&
            sdt_read_all(f, sdt_cache + SDT_C_ICONS, SDT_ICON_STRIDE);
  f_close(f);
  return ok;
}

/* 从文件偏移 off 读一段壁纸（35000 字节）进缓存，同时把调色板灌进 20..239。
 * reload_theme() 只动 0..15 和 256+SEL_COLOR，不会把它们刷掉。 */
static bool sdt_load_wall_at(uint32_t off) {
  FIL *f = &sdt_fil;
  if (!sdt_path || f_open(f, sdt_path, FA_READ) != FR_OK)
    return false;

  bool ok = (f_lseek(f, off) == FR_OK) &&
            sdt_read_all(f, sdt_cache + SDT_C_WALL, SDT_WALL_STRIDE);
  f_close(f);
  if (!ok)
    return false;

  const uint16_t *wp = (const uint16_t*)(sdt_cache + SDT_C_WALL + SDT_WALL_BYTES);
  for (unsigned i = 0; i < SDT_WALLPALN; i++)
    MEM_PALETTE[20 + i] = wp[i];
  return true;
}

/* 让缓存里的壁纸跟上「当前套 + 当前页」。
 * 只有目标段真的变了才读卡 —— 所以同一页内操作、或者这页用页面图时切主题，
 * 都不会产生任何读取。 */
static void sdt_sync_wall(void) {
  uint32_t off = sdt_wall_off_for(sdt_cur, sdt_page);
  if (off == sdt_wall_src && (off == 0 || sdt_wall_ready))
    return;

  sdt_wall_ready = false;
  sdt_wall_src = 0;
  if (!off)
    return;                       // 这套/这页没图 → 内容区用 BG 纯色

  if (sdt_load_wall_at(off)) {
    sdt_wall_ready = true;
    sdt_wall_src = off;
  }
}

/* 装上第 set 套：图标进 EWRAM 副本 + VRAM，壁纸交给 sdt_sync_wall()。
 * 图标读失败时一个字节都不改。 */
static bool sdt_install(unsigned set) {
  if (!sdt_fetch_icons(set))
    return false;

  const uint8_t *icon = (const uint8_t*)(sdt_cache + SDT_C_ICONS);
  menu_override_icons(icon, (const uint16_t*)(icon + SDT_ICON_BYTES));
  menu_refresh_icons();

  sdt_cur = set;
  sdt_loadbg_armed = false;     /* 换套了，载入画面那份快照作废 */
  sdt_sync_wall();
  return true;
}

/* v8：把当前套的选中条纹理铺进 63 号精灵的显存，调色板写到 OBJ 的 240..255。
 *   auto_color = 本套的 hi_blend —— tile 里像素值 0 全用它，所以纹理能跟着换套
 *   自动变色（用户只要把主体涂 0 号，6 套都不会串色）。
 * 返回 false = 这套没有纹理 / 文件不是 v8 / 读卡失败，调用方退回「实心一块」。
 *
 * 287 字节读缓冲区放**栈**上：静态再加 287 会挤爆 IWRAM（那边只剩两百来字节），
 * 而这条链上栈还剩得多。 */
bool sdtheme_hib_apply(uint16_t auto_color) {
  if (!sdt_v8 || !sdt_loaded || sdt_cur >= SDT_NTHEME)
    return false;

  FIL *f = &sdt_fil;
  if (!sdt_path || f_open(f, sdt_path, FA_READ) != FR_OK)
    return false;

  uint8_t buf[SDT_HIB_SET_BYTES];
  uint32_t off = sdt_fsize - SDT_V8_TAIL + SDT_V7_TAIL +
                 (uint32_t)sdt_cur * SDT_HIB_SET_BYTES;
  UINT br = 0;
  bool ok = (f_lseek(f, off) == FR_OK) &&
            f_read(f, buf, SDT_HIB_SET_BYTES, &br) == FR_OK &&
            br == SDT_HIB_SET_BYTES;
  f_close(f);
  if (!ok)
    return false;

  unsigned n = buf[0];
  if (!n || n > SDT_HIB_PAL_MAX)
    return false;                   /* 这一套没有纹理 */

  /* 240 号固定给「自动色」，241.. 是这套自己的调色板（文件里已经是 RGB555） */
  MEM_PALETTE[256 + SDT_HIB_PAL_FIRST] = auto_color;
  for (unsigned i = 0; i < n; i++) {
    const uint8_t *p = buf + 1 + i * 2;
    MEM_PALETTE[256 + SDT_HIB_PAL_FIRST + 1 + i] = (uint16_t)(p[0] | (p[1] << 8));
  }

  /* tile 的 256 个像素 → 128 个半字写进 VRAM。真机不认 8 位写，只能按 16 位写；
   * 像素值 0 是「自动色」，映射到 240 号槽。 */
  const uint8_t *tile = buf + 1 + SDT_HIB_PAL_MAX * 2;
  volatile uint16_t *dst =
      (volatile uint16_t*)(MEM_VRAM_OBJS + SDT_HIB_SLOT * SDT_HIB_TILE_BYTES);
  for (unsigned i = 0; i < SDT_HIB_TILE_BYTES / 2; i++) {
    uint8_t a = tile[i * 2], b = tile[i * 2 + 1];
    dst[i] = (uint16_t)((SDT_HIB_PAL_FIRST + a) |
                        ((uint16_t)(SDT_HIB_PAL_FIRST + b) << 8));
  }
  return true;
}

bool sdtheme_load(unsigned thnum) {
  FIL *fil = &sdt_fil;
  sdt_diag[0] = 0;

  sdt_loaded = false;
  sdt_wall_ready = false;
  sdt_wallmask = 0;
  sdt_pagemask = 0;
  sdt_pmask_lo = 0;
  sdt_pmask_hi = 0;
  sdt_v2 = false;
  sdt_v3 = false;
  sdt_v4 = false;
  sdt_extra = false;
  for (unsigned i = 0; i < SDT_V7_TAIL; i++)
    sdt_tail[i] = 0;
  sdt_missing = false;
  sdt_dirty = false;
  sdt_cur = SDT_NONE;
  sdt_page = SDT_NONE;
  sdt_wall_src = 0;
  sdt_cache = 0;
  sdt_path = 0;
  #ifdef SDTHEME_DIAG
  sdt_probe_res = 0;
  sdt_fo = sdt_fr = -1;
  sdt_ms = sdt_set_ms = 0;
  #endif

  if (thnum >= SDT_NTHEME)
    thnum = 0;

  // 三个候选路径依次试：标准位置 / 不带点 / 根目录。
  // 少一层目录就少一个出错点（FatFs 建不出的点开头目录、用户手滑放错位置都能兜住）。
  static const char *const sdt_paths[] = {
    SDT_PATH, "/superfw/theme.bin", "/theme.bin",
  };
  FRESULT res = FR_NO_FILE;
  unsigned pi = 0;
  for (; pi < sizeof(sdt_paths) / sizeof(sdt_paths[0]); pi++) {
    res = f_open(fil, sdt_paths[pi], FA_READ);
    if (res == FR_OK)
      break;
  }
  #ifdef SDTHEME_DIAG
  sdt_fo = (int)res;
  #endif
  if (res != FR_OK) {
    // 「文件不存在」和「文件在但打开失败」要分开：前者可以自动生成一份，
    // 后者（被占用 / 介质错误）绝不能覆盖用户的东西。
    sdt_missing = (res == FR_NO_FILE || res == FR_NO_PATH);
    #ifdef SDTHEME_DIAG
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "open=%d (no theme.bin?)", (int)res);
    #endif
    return false;
  }

  // 找一块能存住数据的 SDRAM。
  uint32_t cache = sdt_pick_cache();
  if (!cache) {
    f_close(fil);
    #ifdef SDTHEME_DIAG
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "no sdram cache (probe=0x%x)", sdt_probe_res);
    #endif
    return false;
  }

  uint8_t *b = (uint8_t*)cache;

  // 先只读文件头（含 6 套配色）。剩下当前套的图标和壁纸由 sdt_install() 去拿，
  // 别的套不读 —— 这就是「开机只读一套」。
  #ifdef SDTHEME_DIAG
  uint32_t t0 = systime();
  #endif
  res = sdt_read_all(fil, cache, SDT_OFF_ICONS) ? FR_OK : FR_INT_ERR;
  #ifdef SDTHEME_DIAG
  sdt_ms = systime() - t0;
  #endif

  #ifdef SDTHEME_DIAG
  sdt_fr = (int)res;
  #endif
  if (res != FR_OK || memcmp(b, SDT_MAGIC, 8) != 0 ||
      (b[8] != SDT_VERSION && b[8] != SDT_VERSION_V2 &&
       b[8] != SDT_VERSION_V3 && b[8] != SDT_VERSION_V4 &&
       b[8] != SDT_VERSION_V5 && b[8] != SDT_VERSION_V6 &&
       b[8] != SDT_VERSION_V7 && b[8] != SDT_VERSION_V8)) {
    f_close(fil);
    #ifdef SDTHEME_DIAG
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "rd=%d mg=%02x%02x%02x%02x c=%08x",
                 (int)res, b[0], b[1], b[2], b[3], (unsigned)cache);
    #endif
    return false;
  }
  /* 版本 5 = 某个老布局 + 末尾 36 字节扩展色。先把「基础版本」按长度反推出来，
   * 之后所有偏移和长度判断一律用 base，绝不能再用 b[8]（那会误判成 v4）。 */
  uint32_t fsz = (uint32_t)b[12] | ((uint32_t)b[13] << 8) |
                 ((uint32_t)b[14] << 16) | ((uint32_t)b[15] << 24);
  uint32_t tail = (b[8] == SDT_VERSION_V8) ? SDT_V8_TAIL
                : (b[8] == SDT_VERSION_V7) ? SDT_V7_TAIL
                : (b[8] == SDT_VERSION_V6) ? SDT_V6_TAIL
                : (b[8] == SDT_VERSION_V5) ? SDT_EXCOL_BYTES : 0;
  bool extra = (tail != 0);
  uint8_t base = b[8];
  if (extra) {
    uint32_t core = (fsz >= tail) ? (fsz - tail) : 0;
    if (core == SDT_FILE_SIZE_V4)          base = SDT_VERSION_V4;
    else if (core == SDT_FILE_SIZE_V3)     base = SDT_VERSION_V3;
    else if (core == SDT_FILE_SIZE_V2)     base = SDT_VERSION_V2;
    else if (core == SDT_FILE_SIZE ||
             core == SDT_ICONS_ONLY_SIZE)  base = SDT_VERSION;
    else {
      f_close(fil);
      #ifdef SDTHEME_DIAG
      npf_snprintf(sdt_diag, sizeof(sdt_diag), "v5 bad len %u", (unsigned)fsz);
      #endif
      return false;
    }
  }

  /* v4 的 42 位掩码在 0x75990，离文件头很远，单独 seek 读 8 字节。
   * 文件这会儿还开着，省一次 open。 */
  uint32_t v4mask[2] = {0, 0};    /* 必须 4 字节对齐：sdt_read_all 走 32 位 DMA */
  if (base >= SDT_VERSION_V4) {
    if (f_lseek(fil, SDT_OFF_V4MASK) != FR_OK ||
        !sdt_read_all(fil, (uint32_t)v4mask, sizeof(v4mask))) {
      f_close(fil);
      return false;
    }
  }
  if (extra) {
    /* v8 的尾巴 = v7 的 271 + 纹理段 1722。sdt_tail 只缓存前面那 271（纹理段
     * 按需单独读，省 IWRAM），所以这里按 v7 的长度读，偏移还是从末尾倒着算。 */
    uint32_t tlen = (tail > SDT_V7_TAIL) ? SDT_V7_TAIL : tail;
    if (f_lseek(fil, fsz - tail) != FR_OK ||
        !sdt_read_all(fil, (uint32_t)sdt_tail, tlen)) {
      f_close(fil);
      return false;
    }
  }
  f_close(fil);

  uint16_t flags = (uint16_t)(b[10] | ((uint16_t)b[11] << 8));
  sdt_v2 = (base >= SDT_VERSION_V2);
  sdt_v3 = (base >= SDT_VERSION_V3);
  sdt_v4 = (base >= SDT_VERSION_V4);
  sdt_v8 = (b[8] >= SDT_VERSION_V8);
  sdt_fsize = fsz;
  sdt_extra = extra;

  /* 长度自检：头部写的长度必须和这个版本该有的长度完全一致。
   * 宁可整份不要（回落内置皮肤），也不要拿错位的偏移去画花屏。 */
  uint32_t want = sdt_v4 ? SDT_FILE_SIZE_V4
                : sdt_v3 ? SDT_FILE_SIZE_V3
                : sdt_v2 ? SDT_FILE_SIZE_V2
                : ((flags & 0x01) ? SDT_FILE_SIZE : SDT_ICONS_ONLY_SIZE);
  if (extra)
    want += tail;
  if (fsz != want) {
    /* 文件已经在上面 f_close 过了，这里只报错、不再关一次 */
    #ifdef SDTHEME_DIAG
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "size=%u want=%u (v%u)",
                 (unsigned)fsz, (unsigned)want, b[8]);
    #endif
    return false;
  }
  if (sdt_v2)
    sdt_wallmask = (uint8_t)((flags >> 1) & 0x3F);   // bit(1+i) = 第 i 套有默认壁纸
  else
    sdt_wallmask = (flags & 0x01) ? 0x3F : 0x00;     // v1：一张图，6 套共用
  if (sdt_v3)
    sdt_pagemask = (uint8_t)((flags >> SDT_FLAG_PAGE_BASE) & 0x7F);  // bit(7+p) = 该页有图
  if (sdt_v4) {
    sdt_pmask_lo = v4mask[0];
    sdt_pmask_hi = v4mask[1];
  }

  sdt_cache = cache;
  sdt_path = sdt_paths[pi];
  sdt_loaded = true;

  // 6 套配色（72 字节）全读进来了，直接覆盖 themes[]——切主题时颜色立刻能变，
  // 不用等读卡。
  menu_override_themes((const uint16_t*)(b + SDT_OFF_COLORS));

  // v6 还带了 logo：主菜单关于页和游戏内菜单都用它（v7 保留这一段，位置不变）
  if (b[8] >= SDT_VERSION_V6) {
    menu_override_logo(sdt_tail + SDT_EXCOL_BYTES,
                       (const uint16_t*)(sdt_tail + SDT_EXCOL_BYTES + SDT_LOGO_IMG));
  }

  // v7 的界面透明度。老文件没有这一段 → 全 0 = 实心，和以前完全一样。
  memset(sdt_alpha, 0, sizeof(sdt_alpha));
  if (b[8] >= SDT_VERSION_V7) {
    const uint8_t *a = sdt_tail + SDT_V6_TAIL;
    for (unsigned i = 0; i < SDT_NTHEME; i++)
      for (unsigned k = 0; k < SDT_ALPHA_N; k++) {
        uint8_t v = a[i * SDT_ALPHA_N + k];
        sdt_alpha[i][k] = (uint8_t)(v > SDT_ALPHA_MAX ? SDT_ALPHA_MAX : v);
      }
  }

  // 再把当前这套的图标 + 壁纸拉下来。
  #ifdef SDTHEME_DIAG
  t0 = systime();
  #endif
  sdt_install(thnum);
  #ifdef SDTHEME_DIAG
  sdt_set_ms = systime() - t0;
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "OK t%d v%d m%02x.%02x.%04x c%x %u+%ums",
                 thnum, sdt_v4 ? 4 : (sdt_v3 ? 3 : (sdt_v2 ? 2 : 1)),
                 sdt_wallmask, sdt_pagemask, (unsigned)(sdt_pmask_lo & 0xFFFF),
                 (unsigned)cache, sdt_ms, sdt_set_ms);
  #endif
  return true;
}

bool sdtheme_missing(void) {
  return sdt_missing;
}

/* 第一次开机时在 SD 卡上铺一份默认 theme.bin。
 *
 * 内容 = 当前常驻资源（就是固件内置皮肤）→ 26,680 字节、不带壁纸。
 * 只在「三个候选路径都找不到文件」时由 menu_init 调用，绝不覆盖已有文件；
 * 写坏了会把自己删掉，下次开机重来。
 * 写完立刻按正常流程装载一遍，既是自检，也让内存状态和卡上文件保持一致。
 */
bool sdtheme_save_default(unsigned thnum) {
  uint8_t hdr[SDT_OFF_ICONS];      /* 88 字节：文件头 + 6 套配色 */
  UINT bw = 0;
  bool ok;

  memset(hdr, 0, sizeof(hdr));
  memcpy(hdr, SDT_MAGIC, 8);
  hdr[8]  = SDT_VERSION;           /* u16 版本，小端 */
  hdr[10] = 0;                     /* flags：bit0=0 → 不带壁纸 */
  hdr[12] = (uint8_t)(SDT_ICONS_ONLY_SIZE & 0xFF);
  hdr[13] = (uint8_t)((SDT_ICONS_ONLY_SIZE >> 8) & 0xFF);
  hdr[14] = (uint8_t)((SDT_ICONS_ONLY_SIZE >> 16) & 0xFF);
  hdr[15] = (uint8_t)((SDT_ICONS_ONLY_SIZE >> 24) & 0xFF);
  menu_copy_themes(hdr + SDT_OFF_COLORS);

  (void)f_mkdir("/.superfw");      /* 已存在返回 FR_EXIST，忽略 */

  FRESULT res = f_open(&sdt_fil, SDT_PATH, FA_WRITE | FA_CREATE_ALWAYS);
  if (res != FR_OK) {
    #ifdef SDTHEME_DIAG
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "create=%d (ro card?)", (int)res);
    #endif
    return false;
  }

  ok = (f_write(&sdt_fil, hdr, sizeof(hdr), &bw) == FR_OK && bw == sizeof(hdr));
  // 每套占 SDT_ICON_STRIDE(4432) = 精灵 4352 + 调色板 76 + 4 字节对齐填充，
  // 和 sdt_fetch_set() 里 set * SDT_ICON_STRIDE 的寻址必须对上。
  static const uint8_t sdt_pad[SDT_ICON_STRIDE - SDT_ICON_BYTES - SDT_ICON_PALB] = {0};
  for (unsigned i = 0; ok && i < SDT_NTHEME; i++) {
    ok = (f_write(&sdt_fil, menu_icons_img_ptr(), SDT_ICON_BYTES, &bw) == FR_OK &&
          bw == SDT_ICON_BYTES);
    if (ok)
      ok = (f_write(&sdt_fil, menu_icons_pal_ptr(), SDT_ICON_PALB, &bw) == FR_OK &&
            bw == SDT_ICON_PALB);
    if (ok)
      ok = (f_write(&sdt_fil, sdt_pad, sizeof(sdt_pad), &bw) == FR_OK &&
            bw == sizeof(sdt_pad));
  }
  if (f_close(&sdt_fil) != FR_OK)
    ok = false;

  if (!ok) {
    (void)f_unlink(SDT_PATH);      /* 半截文件下次会变成「打不开」，删掉重来 */
    #ifdef SDTHEME_DIAG
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "write failed, file removed");
    #endif
    return false;
  }

  // 立刻按正常流程再读一遍：既确认文件合法，也让 sdt_loaded 等状态跟上。
  bool got = sdtheme_load(thnum);
  #ifdef SDTHEME_DIAG
  if (got)
    npf_snprintf(sdt_diag, sizeof(sdt_diag), "created 26680B\nOK c=%08x",
                 (unsigned)sdt_cache);
  #endif
  return got;
}

// 切到第 thnum 套：图标 + 壁纸一起换。
// 切到「已经装好的那一套」时什么都不做（设置菜单每按一个键都会调这里）。
// 切到别的套才去卡上读那块图标；壁纸由 sdt_sync_wall() 决定读不读
// （这页有专属图的话，换套不影响壁纸，一次读取都不会发生）。
// 把第 thnum 套的整屏界面三色写进调色板。没有 v5 扩展色就什么都不做 ——
// 那三格在 reload_theme() 里已经按 BG/FG 填好了，行为和以前完全一样。
static void sdt_apply_excolors(unsigned thnum) {
  if (!sdt_loaded || !sdt_extra || thnum >= SDT_NTHEME)
    return;
  const uint16_t *c = (const uint16_t*)(sdt_tail + thnum * SDT_EXCOL_N * 2);
  MEM_PALETTE[SDT_EXP_LOADBG] = c[0];
  MEM_PALETTE[SDT_EXP_LOADFG] = c[1];
  MEM_PALETTE[SDT_EXP_IGMBG]  = c[2];
}

bool sdtheme_apply_theme(unsigned thnum) {
  if (!sdt_loaded || thnum >= SDT_NTHEME)
    return false;

  // 颜色每次都要套（哪怕图标/壁纸没换）—— 切套后这两个界面也得跟着变
  sdt_apply_excolors(thnum);

  if (thnum == sdt_cur) {
    // 套没变：只有壁纸还没对上（比如上次读失败、或者页面刚变过）才补一次
    uint32_t want = sdt_wall_off_for(thnum, sdt_page);
    if (want == sdt_wall_src && (want == 0 || sdt_wall_ready))
      return false;
    sdt_sync_wall();
    return true;
  }

  return sdt_install(thnum);
}

// 切到第 page 个标签页。只有「该显示的图真的变了」才读卡。
void sdtheme_set_page(unsigned page) {
  if (!sdt_loaded || page == sdt_page)
    return;
  sdt_page = page;
  sdt_sync_wall();
}

// 第 thnum 套某项的界面透明度（0..16，0 = 实心）。which = SDT_ALPHA_BAR / SDT_ALPHA_SEL。
// 没装主题、或者是 v7 之前的文件，一律返回 0 —— 也就是老行为。
uint8_t sdtheme_alpha(unsigned thnum, unsigned which) {
  if (!sdt_loaded || thnum >= SDT_NTHEME || which >= SDT_ALPHA_N)
    return 0;
  return sdt_alpha[thnum][which];
}


// 内容区（第 16 行起，240x144）铺壁纸。用 CPU 拷：DMA 从卡带 SDRAM 读不到数据。
static void sdt_blit_wall(volatile uint8_t *dst) {
  volatile uint32_t *out = (volatile uint32_t*)dst;
  const uint32_t *src = (const uint32_t*)(sdt_cache + SDT_C_WALL);

  for (unsigned i = 0; i < SDT_WALL_BYTES / 4; i += 4) {
    uint32_t w0 = src[i + 0], w1 = src[i + 1];
    uint32_t w2 = src[i + 2], w3 = src[i + 3];
    out[i + 0] = w0; out[i + 1] = w1;
    out[i + 2] = w2; out[i + 3] = w3;
  }
}

void sdtheme_render_wall(volatile uint8_t *frame) {
  sdt_blit_wall(&frame[16 * 240]);
}

// 铺到任意目标（240x144，从 dst 第 0 行开始），**不读卡**。
// 载入进度画面用它：那时 SD 卡正在读游戏 ROM，绝不能再去读主题文件。
//
// 2026-10-26 修：这里以前还有一道 `sdt_cache < 0x09000000 → return false` 的守卫，
// 而这条线上的缓存候选表最高只有 0x081F0000，判据恒成立 —— 于是 loadrom_progress()
// 永远走纯色分支，编辑器预览里画得出来的「载入壁纸」在真机上从来不会出现
// （主题底色是黑的就直接黑屏）。这是死代码，不是配色问题。
//
// 守卫的本意没错：缓存落在 ROM 载入区（0x08000000 起），载入过半就会被 ROM 数据
// 整块盖掉，每帧都回缓存取图必然花屏。但载入**第一帧**缓存一定完好（首次回调时
// 最多才写了开头几 KB，离缓存还差着 2MB）——
// 所以改成「只铺一次」：调用方在第一帧把它铺进两个帧缓冲，之后只管进度条，
// 再也不回缓存取图，后面被覆盖也就无所谓了。
// 画不了就返回 false，让调用方退回纯色（生成补丁踩过缓存 → 里面是垃圾）。
bool sdtheme_blit_wall_early(volatile uint8_t *dst) {
  if (!sdt_loaded || !sdt_wall_ready || sdt_dirty)
    return false;
  sdt_blit_wall(dst);
  return true;
}

/* 载入 / 写 NOR 那一屏共用同一份画面：两个帧缓冲各铺一次，之后就不再碰缓存。
 * 写 NOR 会把整块 2MB scratch 当 ROM 中转区写满，缓存必然被盖 ——
 * 所以铺图必须在标脏之前做，那时 ROM 还一个字节都没写，缓存是完好的。 */
bool sdtheme_loadbg_armed(void) {
  return sdt_loadbg_armed;
}

bool sdtheme_arm_loadbg(void) {
  if (sdt_loadbg_armed)
    return true;
  if (!sdtheme_blit_wall_early(&MEM_VRAM_U8[0]))
    return false;
  sdtheme_blit_wall_early(&MEM_VRAM_U8[0xA000]);
  sdt_loadbg_armed = true;
  return true;
}

// 缓存被踩（写 NOR / 写固件占满那两块 scratch）。
// 这里只打标记，真正的重读放到 sdtheme_poll()，免得在写 NOR 的收尾路径上多花时间。
void sdtheme_invalidate(void) {
  if (sdt_loaded)
    sdt_dirty = true;
}

/* 扫描窗（hiscratch 前 1MB + 尾表）和「作弊码工具」都只占 0x09000000 起那段。
 * 缓存落在 0x081F0000 时根本碰不到 —— 无条件标脏等于每次扫补丁都白丢一次壁纸。 */
void sdtheme_invalidate_scan(void) {
  if (sdt_loaded && sdt_cache >= 0x09000000)
    sdt_dirty = true;
}

// 每帧调一次。只有标记过才动，平时就是一个 if。
void sdtheme_poll(void) {
  if (!sdt_dirty)
    return;
  sdt_dirty = false;
  if (!sdt_loaded)
    return;

  // 缓冲里现在是垃圾：图标和壁纸都得重来，装不上就让壁纸退回纯色。
  unsigned cur = sdt_cur;
  sdt_cur = SDT_NONE;
  sdt_wall_src = 0;
  sdt_wall_ready = false;
  (void)sdt_install(cur);
}
