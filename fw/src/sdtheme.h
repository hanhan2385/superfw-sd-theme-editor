/*
 * SD 卡主题（SuperFW 版「EZ-Omega 换肤思路」）
 *
 * 思路移植自 EZ-Flash Omega DE 内核的自定义主题：皮肤不烧进固件，而是
 * 放在 SD 卡上，内核启动时读进来覆盖界面资源。区别在于 SuperFW 的
 * 主题/图标资源是编译期常量（在 EWRAM 里），所以这里的做法是：
 *
 *   /.superfw/theme.bin  ──f_read──>  SDRAM 缓存（只放当前这一套）
 *                                    ├─ 72 B   配色   → memcpy 覆盖 themes[]
 *                                    ├─ 4432 B 当前套图标 → memcpy 进 icons_img/pal
 *                                    └─ 35000 B 当前套壁纸 → 每帧 CPU 拷进 VRAM
 *
 * v1 文件全篇只有一张壁纸（6 套共用），v2 是每套一张；两种都认。
 * 没有文件时一切照旧（编译进去的默认皮肤）。
 *
 * 装载策略：开机只读「当前这一套」，切到别的套时才读那一套，其余套一个
 * 字节都不碰。所以内存里永远只有一套（约 40 KB），开机读取量和 v0.19 相当
 * 甚至更少，代价是切主题时要现场读一次卡。
 */

#ifndef _SDTHEME_H_
#define _SDTHEME_H_

#include <stdbool.h>
#include <stdint.h>

/* 整屏界面的独立调色板槽（v5）。240..244 是原来那份 IGM 五色，
 * 245..247 是 v5 新增的三格 —— loader 会把 240..247 一起拷给游戏内菜单的
 * payload，所以 payload 里 245 就是 ingame_menu_palette[5]。 */
#define SDT_EXP_IGMBG   245   /* 游戏内菜单整屏底色 */
#define SDT_EXP_LOADBG  246   /* 载入画面底色 / 进度条轨道 */
#define SDT_EXP_LOADFG  247   /* 载入进度条本身 */

// 从 SD 卡装载主题并直接切到第 thnum 套，成功返回 true。失败时固件保持默认皮肤。
bool sdtheme_load(unsigned thnum);

// 上一次 sdtheme_load() 是不是因为「卡上根本没有这个文件」才失败
bool sdtheme_missing(void);

// 在 SD 卡上生成一份默认 theme.bin（内置皮肤，26,680 字节，不带壁纸），
// 然后按 thnum 装载一遍。只在 sdtheme_missing() 为真时调用。
bool sdtheme_save_default(unsigned thnum);

// SD 主题是否已经生效
bool sdtheme_active(void);

// 切换到第 thnum 套：图标换成那套的，壁纸也换成那套的（v2）。
// 没变化（或 thnum 越界）返回 false。需要读卡时会现场读一次。
bool sdtheme_apply_theme(unsigned thnum);

// 切到第 page 个标签页（0 = 最近，1 = 文件，…，6 = 关于）。
// 取图规则：这一页有自己的图 → 用它；否则 → 用当前套的默认图；都没有 → BG 纯色。
// 只有「要显示的段变了」才读卡，所以同一页内操作不产生读取。
void sdtheme_set_page(unsigned page);

/* v7 的界面透明度（0..16，0 = 实心）。which 取 SDT_ALPHA_BAR / SDT_ALPHA_SEL。
 * 没装主题、或者文件是 v7 之前的版本，一律返回 0 = 老行为。 */
#define SDT_ALPHA_BAR   0     /* 底栏 */
#define SDT_ALPHA_SEL   1     /* 列表选中条 */
#define SDT_ALPHA_MAX   16
uint8_t sdtheme_alpha(unsigned thnum, unsigned which);

// 当前这一套是否有可用壁纸
bool sdtheme_wall_enabled(void);

/* v8：把当前套的选中条纹理（16×16）铺进 63 号精灵的显存，调色板写 240..255。
 * auto_color 传本套的 hi_blend —— tile 里的 0 号像素全用它，所以换套会自动变色。
 * 返回 false = 这套没有纹理 / 文件不是 v8，调用方按老办法填一块实色。 */
bool sdtheme_hib_apply(uint16_t auto_color);

// 把壁纸铺到帧缓冲的内容区（第 16 行起，240x144）
void sdtheme_render_wall(volatile uint8_t *frame);

// 把壁纸铺到任意目标的前 144 行（240x144），不读卡。
// 载入进度画面用它 —— 那时 SD 卡正在读游戏 ROM，不能再去读主题文件。
// 缓存不可用（没壁纸 / 被生成补丁踩过 / 落在 ROM 载入区）时返回 false。
bool sdtheme_blit_wall_early(volatile uint8_t *dst);

// 把壁纸铺进**两个**帧缓冲（载入画面 / 写 NOR 画面共用那一屏）。
// 必须在缓存被覆盖之前调 —— 写 NOR 会把整块 2MB scratch 当 ROM 中转区。
bool sdtheme_arm_loadbg(void);
bool sdtheme_loadbg_armed(void);

// 缓存所在的 SDRAM 被别的功能踩了（写 NOR / 写固件会占满那两块 scratch），
// 把缓存标记成失效；下一次 sdtheme_poll() 会重读当前套。
void sdtheme_invalidate(void);

// 只是「扫描窗 / 工具代码占了 hiscratch」时的失效判断：
// 缓存落在 0x081F0000 时根本碰不到，不该白丢一次壁纸。
void sdtheme_invalidate_scan(void);

// 每帧调用：缓存失效且当前套确实需要壁纸时，重读一次。
// 只在 invalidate() 之后真正干活，平时就是一个 if。
void sdtheme_poll(void);

// 诊断文本（SDTHEME_DIAG 构建用来在屏幕上显示装载结果，Release 构建返回空串）
const char *sdtheme_diag_text(void);

#endif
