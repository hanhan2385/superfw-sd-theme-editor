/*
 * SD 卡字体包加载（v0.47+ / r79）
 *
 * 为什么要有这个文件：以前字体是直接 .incbin 进固件镜像的（res/fonts-ext.pack，
 * 1,047,800 B，占 2 MiB 的 53%）。现在改成按语言分包、开机从 SD 卡读进 SDRAM：
 *
 *     /.superfw/font/<langcode>.pack   →   SDRAM 0x08E80000（= ROM_FONTBASE_U8）
 *
 * 落点不变是有意的 —— rom_boot.S 开机已经把内置兜底包拷到那个地址，loader.c 起
 * 游戏内菜单时也是从那个地址把字体拷进游戏 RAM，所以读字的代码一行都不用改。
 *
 * 三条约束（都踩过）：
 *   1. SD 接口和 SDRAM 互斥（模式寄存器 bit1），读卡期间 SDRAM 整个不可见，
 *      只能先读进 EWRAM 的静态/栈缓冲，读完再 DMA 写过去。
 *   2. 缓冲用 4 KB：f_read 每调一次都有固定开销，512 B 一块要读 1348 次，
 *      4 KB 一块只读 169 次，实测差别很明显。栈 16 KB，这条链很浅，放得下。
 *   3. 写 SDRAM 必须按 16 位/32 位，不能按字节。
 */

#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#include "common.h"
#include "gbahw.h"
#include "supercard_driver.h"
#include "fatfs/ff.h"
#include "nanoprintf.h"
#include "fontload.h"

/* lang_codes[] 在 messages_data.h 里（menu.c 用它算 LANG_COUNT），这里直接用 */
extern const uint16_t lang_codes[];

#define FL_MAX_BYTES   (1536u * 1024u)      /* SDRAM 字体区正好 1.5 MB */
#define FL_BUFSZ       4096u

/* 定时器寄存器（gbahw.h 里没有，menu.c 也是就地定义的） */
#define FL_TM2_L  (*((volatile uint16_t*)0x04000108))
#define FL_TM2_H  (*((volatile uint16_t*)0x0400010A))
#define FL_TM3_L  (*((volatile uint16_t*)0x0400010C))
#define FL_TM3_H  (*((volatile uint16_t*)0x0400010E))

/* 计时用 TM2+TM3 级联成 32 位、prescaler=1（1 tick = 1/16.78MHz ≈ 59.6ns）。
 * 不能用 systime()：它数的是 VBlank 帧数，而开机加载字体时 IRQ 还没开，
 * 读出来永远是 0（真机上就显示成 "font zh 674KB 0ms"）。 */
/* 使能位在 CNT_H（+2）。往 CNT_L 写 1<<7 只会把计数器设成 128，定时器根本不动。 */
#ifdef SDTHEME_DIAG
static void fl_tm_start(void) {
  FL_TM2_L = 0;
  FL_TM3_L = 0;
  FL_TM2_H = 1 << 7;
  FL_TM3_H = (1 << 7) | (1 << 2);       /* enable + cascade */
}

static uint32_t fl_tm_ticks(void) {
  uint16_t lo = FL_TM2_L, hi = FL_TM3_L;
  return ((uint32_t)hi << 16) | lo;
}

static void fl_tm_stop(void) {
  FL_TM2_H = 0;
  FL_TM3_H = 0;
}

static unsigned fl_ticks_to_ms(uint32_t t) {
  return (unsigned)((t * 1000u) / 16777216u);
}
#endif

#ifndef FL_BUILD_TAG
#define FL_BUILD_TAG "?"
#endif

#ifdef SDTHEME_DIAG
static char fl_diag[64] = "";
#else
static char fl_diag[1] = "";
#endif

const char *fontload_diag(void) {
  return fl_diag;
}

static void fl_seterr(const char *what, int code) {
  #ifdef SDTHEME_DIAG
  npf_snprintf(fl_diag, sizeof(fl_diag), "font %s err%d", what, code);
  #else
  (void)what; (void)code;
  #endif
}

/* 找包的候选位置，按顺序试。第一项是文档里写的，后面几项是「用户放错地方」
 * 的常见情况 —— 与其让人对着 err4 猜，不如自己多找几个地方。 */
static const char *fl_fmt[] = {
  "/.superfw/font/%c%c.pack",
  "/.superfw/fonts/%c%c.pack",
  "/.superfw/%c%c.pack",
  "/font/%c%c.pack",
  "/%c%c.pack",
};

/* 中转缓冲放在**栈上**，不是不想静态化，是静态化会顶穿 IWRAM：
 * 这个 fork 的 .bss 落 IWRAM（32 KB，要跟代码和栈共用），4 KB 一进去链接器
 * 直接报 "Not enough free IWRAM for stack"（2026-10-23 实测）。
 * 所以只能借栈 —— 好在调用链很浅（main_gba → fontload_load_lang → 这里），
 * 栈峰值约 4.6 KB / 16 KB，安全。真正的解法是把它挪到 EWRAM，得动链接脚本。 */
static bool fl_read_to_sdram(FIL *f, uint32_t dst, uint32_t len) {
  uint8_t buf[FL_BUFSZ] __attribute__((aligned(4)));
  uint32_t total = 0;

  while (total < len) {
    uint32_t chunk = len - total;
    if (chunk > FL_BUFSZ)
      chunk = FL_BUFSZ;

    UINT br = 0;
    if (f_read(f, buf, chunk, &br) != FR_OK || br != chunk)
      return false;

    /* 尾块不是 4 的倍数时补零到 4 字节再写（SDRAM 不吃 8 位写） */
    uint32_t words = (chunk + 3) / 4;
    for (uint32_t i = chunk; i < words * 4; i++)
      buf[i] = 0;

    /* 读完这一块后把 0x08 窗口切回 SDRAM 才能 DMA 过去 */
    set_supercard_mode(MAPPED_SDRAM, true, true);
    dma_memcpy32((volatile void*)(dst + total), buf, words);
    total += chunk;
  }
  return true;
}

/* 头 1 KB 的合法性检查：magic / 版本 / block 数 / 各块偏移都在文件内。
 * 完整校验（CRC）留到格式 v2，现在这样能挡住“读错文件”（比如把 theme.bin
 * 改名成 zh.pack）和大部分截断错误。 */
static bool fl_check_header(const uint8_t *h, uint32_t hlen, uint32_t fsz) {
  if (hlen < 8 || h[0] != 'F' || h[1] != 'O')
    return false;
  if (h[2] != 1 && h[2] != 2)               /* 1 = 8/16 列老格式，2 = 12 列新格式 */
    return false;
  unsigned nblk = h[3];
  if (nblk == 0 || nblk > 255 || 8u + 16u * nblk > hlen)
    return false;
  uint32_t dsize = (uint32_t)h[4] | ((uint32_t)h[5] << 8) |
                   ((uint32_t)h[6] << 16) | ((uint32_t)h[7] << 24);
  if (dsize < 8u + 16u * nblk || dsize > fsz || dsize > FL_MAX_BYTES)
    return false;
  for (unsigned i = 0; i < nblk; i++) {
    const uint8_t *e = h + 8 + 16 * i;
    uint32_t start = (uint32_t)e[0] | ((uint32_t)e[1] << 8) | ((uint32_t)e[2] << 16) | ((uint32_t)e[3] << 24);
    uint32_t end   = (uint32_t)e[4] | ((uint32_t)e[5] << 8) | ((uint32_t)e[6] << 16) | ((uint32_t)e[7] << 24);
    uint32_t off   = (uint32_t)e[12] | ((uint32_t)e[13] << 8) | ((uint32_t)e[14] << 16) | ((uint32_t)e[15] << 24);
    if (end < start || start > 0x2FFFF)
      return false;
    if (off >= dsize)
      return false;
  }
  return true;
}

/* 按候选表开文件，成功时把实际路径写进 pathout。 */
static FRESULT fl_open_any(uint16_t code, FIL *f, char *pathout, unsigned pathsz) {
  if (!code)
    return FR_INVALID_NAME;
  for (unsigned i = 0; i < sizeof(fl_fmt) / sizeof(fl_fmt[0]); i++) {
    npf_snprintf(pathout, pathsz, fl_fmt[i], code & 0xFF, code >> 8);
    FRESULT res = f_open(f, pathout, FA_READ);
    if (res == FR_OK)
      return FR_OK;
  }
  return FR_NO_FILE;
}

bool fontload_present(uint16_t code) {
  FIL f;
  char path[40];
  if (fl_open_any(code, &f, path, sizeof(path)) != FR_OK)
    return false;
  uint32_t sz = (uint32_t)f_size(&f);
  f_close(&f);
  return sz >= 64 && sz <= FL_MAX_BYTES;
}

bool fontload_load_code(uint16_t code) {
  char path[40];
  FIL f;
  FRESULT res = fl_open_any(code, &f, path, sizeof(path));
  if (res != FR_OK) {
    /* 诊断版多说一句：/.superfw/font 这个目录到底在不在（d1 = 在，说明文件名/
     * 扩展名不对；d0 = 连目录都没有，多半是放错地方了）。 */
    #ifdef SDTHEME_DIAG
    {
      DIR d;
      bool have = (f_opendir(&d, "/.superfw/font") == FR_OK);
      if (have) f_closedir(&d);
      npf_snprintf(fl_diag, sizeof(fl_diag), "#%s %c%c err%d d%d",
                   FL_BUILD_TAG, code & 0xFF, code >> 8, (int)res, have ? 1 : 0);
    }
    #endif
    return false;
  }

  uint32_t fsz = (uint32_t)f_size(&f);
  if (fsz < 64 || fsz > FL_MAX_BYTES) {
    f_close(&f);
    fl_seterr("size", (int)(fsz / 1024));
    return false;
  }

  /* 先看头，再决定要不要覆盖内置字体。512 字节够放 31 个块 ——
   * 文泉驿那份包有 17 块（按 Unicode 小区间切），256 字节只够 15 块，
   * 之前就是这里会把合法的包判成坏包。 */
  uint8_t head[512] __attribute__((aligned(4)));
  UINT br = 0;
  if (f_read(&f, head, sizeof(head), &br) != FR_OK || br != sizeof(head)) {
    f_close(&f);
    fl_seterr("hdr", -2);
    return false;
  }
  if (!fl_check_header(head, br, fsz)) {
    f_close(&f);
    fl_seterr("bad", (int)br);
    return false;
  }

  #ifdef SDTHEME_DIAG
  fl_tm_start();
  #endif
  if (f_lseek(&f, 0) != FR_OK) {
    f_close(&f);
    fl_seterr("seek", -3);
    return false;
  }

  uint32_t dst = (uint32_t)(uintptr_t)ROM_FONTBASE_U8;
  bool ok = fl_read_to_sdram(&f, dst, fsz);
  #ifdef SDTHEME_DIAG
  uint32_t ticks = fl_tm_ticks();
  fl_tm_stop();
  #endif
  f_close(&f);

  if (!ok) {
    fl_seterr("read", -4);
    return false;
  }

  /* 读完把窗口切回来，避免调用方拿到不一致的映射 */
  set_supercard_mode(MAPPED_SDRAM, true, true);

  #ifdef SDTHEME_DIAG
  /* 构建戳放行首：原来挂在行尾，30 列一裁就看不见，分不清是不是最新那份 */
  npf_snprintf(fl_diag, sizeof(fl_diag), "#%s %c%c %uKB %ums",
               FL_BUILD_TAG, code & 0xFF, code >> 8, (unsigned)(fsz / 1024),
               fl_ticks_to_ms(ticks));
  #endif
  return true;
}

bool fontload_load_lang(unsigned id) {
  /* 内置包（rom_boot 从 res/fonts-zh.pack 拷到 0x08E80000）已经就位，
   * 覆盖拉丁扩展 / 希腊 / 西里尔 / 假名 / 中日韩基本区 —— 除了韩文全都能画。
   * 命中内置的直接返回，**一次卡都不读**，开机零等待。
   *
   * 2026-10-23 实测出来的一条教训：把 690 KB 字体整体搬到 SD 卡上，
   * 每次开机都要读将近 1 秒，用户体感就是「卡」。所以改成内置打底、
   * SD 只做增量（换语言时才会用到）。 */
  if (fontload_builtin_covers(id)) {
    #ifdef SDTHEME_DIAG
    npf_snprintf(fl_diag, sizeof(fl_diag), "#%s builtin", FL_BUILD_TAG);
    #endif
    return true;
  }
  return fontload_load_code(lang_codes[id]);
}

bool fontload_lang_present(unsigned id) {
  if (fontload_builtin_covers(id))
    return true;
  return fontload_present(lang_codes[id]);
}

/* 内置包（rom_boot 从 res/fonts-ext.pack 拷到 0x08E80000，1,047,800 B）
 * 覆盖 en/es/fr/de/it/ca/pt/cs/ru/ja/zh/ko 全部 14 种语言 —— 也就是说
 * **开机永远不需要读卡**。
 *
 * 2026-10-23 的教训：把字体整体搬到 SD 卡听起来很美好（换语言不用刷固件），
 * 但 690 KB 走 SuperCard 的卡接口要将近 1 秒，每次开机都卡在那儿，实测下来
 * 体验不可接受。所以开机路径回到内置，SD 加载代码留着 —— 将来要放超大
 * 字体（比如 12 列高压缩包）或者用户自制字体，把这里改成按语言判断即可。 */
bool fontload_builtin_covers(unsigned id) {
  (void)id;
  return true;
}
