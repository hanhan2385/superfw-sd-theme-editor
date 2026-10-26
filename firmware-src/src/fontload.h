/*
 * SD 卡字体包加载接口。见 fontload.c 顶部说明。
 */
#ifndef FONTLOAD_H
#define FONTLOAD_H

#include <stdbool.h>
#include <stdint.h>

/* 按语言码（两个字符打包成 u16，如 'h'<<8|'z'）加载 /.superfw/font/xx.pack。
 * 成功 = 已经铺进 SDRAM 字体区；失败 = 保持原有内容（内置兜底包），可以继续用。 */
bool fontload_load_code(uint16_t code);

/* 按语言序号（0..LANG_COUNT-1）加载 / 查询。 */
bool fontload_load_lang(unsigned id);
bool fontload_lang_present(unsigned id);

/* 内置兜底包里有没有这个语言的界面字。
 * 现在只有中文（兜底包就是拿 zh.json 里那 330 个字造的），所以没卡的时候
 * 至少还能把界面切成中文来看；别的语言没包就老老实实拒绝切换。 */
bool fontload_builtin_covers(unsigned id);

/* 这个语言码的包在卡上存在吗（给菜单置灰用）。 */
bool fontload_present(uint16_t code);

/* 诊断构建下返回 "font zh 690KB 320ms"；正式版返回空串。 */
const char *fontload_diag(void);

#endif
