#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""superfw-fix 适配自检 —— 校验 SD 卡主题移植的结构约束与构建产物。

跑法（仓库根目录）：
    .venv314/Scripts/python.exe tools/verify_sffix_port.py

做三件事：
 1. 源码约束：fork 的 sdtheme.c/h 与主线只允许在「缓存地址 + 读缓冲大小」上有差异，
    主题包格式常量必须逐字节一致（否则编出来的固件读不了编辑器出的 theme.bin）。
 2. 挂点约束：menu.c 里的七个钩子都在，且顺序正确（load 在 reload 之前等）。
 3. 产物约束：superfw.gba 大小 / 头部 fw_size / 资源符号尺寸 / 主题路径字符串。
"""
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORK = os.path.join(ROOT, "ref", "superfw-fix")
NM = os.path.join(ROOT, ".toolchain", "armgcc",
                  "xpack-arm-none-eabi-gcc-13.3.1-1.1", "bin", "arm-none-eabi-nm.exe")

FAIL = []
OK = []


def check(cond, msg, detail=""):
    if cond:
        OK.append(msg)
    else:
        FAIL.append(msg + (("  [" + detail + "]") if detail else ""))


def read(p):
    with open(p, "rb") as f:
        return f.read()


def text(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------- 1. 源码约束
main_c = text(os.path.join(ROOT, "fw", "src", "sdtheme.c"))
fork_c = text(os.path.join(FORK, "src", "sdtheme.c"))
main_h = text(os.path.join(ROOT, "fw", "src", "sdtheme.h"))
fork_h = text(os.path.join(FORK, "src", "sdtheme.h"))

# sdtheme.h：必须和主线逐字节一致 —— 接口一个字都不许动。
check(main_h == fork_h, "sdtheme.h 与主线逐字节一致（接口未改动）")

# 从两份 sdtheme.c 里抽所有 #define 的数值常量，必须一致。
# 唯一的例外是 SDT_BUFSZ（读缓冲，本分支的 .bss 在 IWRAM，压到 1KB）。
def defines(src):
    out = {}
    for m in re.finditer(r"^#define\s+(SDT_\w+)\s+(.+?)\s*(?:/\*.*)?$", src, re.M):
        name, val = m.group(1), m.group(2).strip()
        if name == "SDT_BUFSZ":
            continue
        out[name] = re.sub(r"\s+", "", val)
    return out


dm, df = defines(main_c), defines(fork_c)
diff = {k: (dm.get(k), df.get(k)) for k in set(dm) | set(df) if dm.get(k) != df.get(k)}
check(not diff, "sdtheme.c 的 SDT_* 常量与主线完全一致（theme.bin 格式兼容）", str(diff)[:200])
check(set(dm) <= set(df), "主线所有格式常量在 fork 里都还在")

# 允许的差异仅限：缓存候选地址 + 读缓冲大小
allowed_pat = re.compile(r"sdt_cand|SDT_BUFSZ|sdt_buf|0x08[0-9A-Fa-f]{6}|0x09[0-9A-Fa-f]{6}")
strip = lambda s: [l for l in s.splitlines()
                   if l.strip() and not l.lstrip().startswith(("*", "/*", "//"))]
m_lines, f_lines = strip(main_c), strip(fork_c)
check(allowed_pat.search(fork_c) is not None, "fork 的缓存候选地址已按新布局改写")

# 去掉注释和空行之后，fork 和主线只许在「缓存地址 / 读缓冲」这几行上不同。
# 这条是防漂移的总闸：真有人往 sdtheme.c 里塞了别的逻辑，这里会直接报出来。
bad_lines = [l for l in f_lines if l not in set(m_lines) and not allowed_pat.search(l)]
check(not bad_lines, "sdtheme.c 去掉注释后与主线只差缓存地址/读缓冲",
      str([l.strip()[:60] for l in bad_lines[:3]]))

cand = re.findall(r"0x([0-9A-Fa-f]{8}),\s*/\*", fork_c)
cand = [int(x, 16) for x in cand]
check(len(cand) == 3, "缓存候选地址有 3 个", str([hex(c) for c in cand]))
check(all(c != 0x081E0000 for c in cand),
      "缓存地址不含 0x081E0000（superfw-fix 的 MENU_OVERLAY_KERNEL_ROM）")
# chis 不切 bank 只解码 16MB：0x09000000 起的 hiscratch 直接读写探测失败
# （2026-10-20 主线实机诊断：0x09000000 / 0x09400000 全失败）。
# 缓存只能放 2MB scratch，并避开 overlay（0x081E0000 起，最大 64KB）。
check(all(0x08000000 <= c < 0x08200000 for c in cand),
      "缓存地址都落在 2MB scratch（0x08000000..0x08200000）",
      str([hex(c) for c in cand]))
check(all(c + 0xC000 <= 0x081E0000 or c >= 0x081F0000 for c in cand),
      "缓存段不覆盖菜单 overlay（0x081E0000..0x081F0000）",
      str([hex(c) for c in cand]))
check(all(c + 0xC000 <= 0x08200000 for c in cand),
      "缓存段不越过 scratch 末尾",
      str([hex(c) for c in cand]))
check(min(abs(a - b) for i, a in enumerate(cand) for b in cand[i + 1:]) >= 0xC000,
      "候选地址之间的间距 >= 缓存大小 0xC000")

size_m = re.search(r"#define\s+SDT_C_SIZE\s+(0x[0-9A-Fa-f]+)", fork_c)
check(size_m is not None, "SDT_C_SIZE 可解析")

# --------------------------------------------------------------- 2. 挂点约束
menu = text(os.path.join(FORK, "src", "menu.c"))


def pos(pat, src=menu):
    m = re.search(pat, src)
    return m.start() if m else -1


i_load = pos(r"sdtheme_load\(menu_theme\)")
i_reload = pos(r"reload_theme\(menu_theme\);")
check(i_load > 0 and i_reload > i_load,
      "menu_init：sdtheme_load() 在 reload_theme() 之前", f"{i_load} / {i_reload}")
check(pos(r"sdtheme_save_default\(menu_theme\)") > 0,
      "menu_init：卡上没有 theme.bin 时自动生成默认主题")

reload_body = menu[menu.index("void reload_theme(unsigned thnum)"):
                   menu.index("void reload_theme(unsigned thnum)") + 2600]
check("sdtheme_apply_theme(thnum);" in reload_body,
      "reload_theme()：末尾调用 sdtheme_apply_theme(thnum)")

render_body = menu[menu.index("void menu_render(unsigned fcnt)"):
                   menu.index("void menu_render(unsigned fcnt)") + 4000]
i_poll = render_body.find("sdtheme_poll();")
i_page = render_body.find("sdtheme_set_page(smenu.menu_tab);")
i_wall = render_body.find("sdtheme_render_wall(frame")
i_blank = render_body.find("dma_memset16(&frame[16*SCREEN_WIDTH]")
check(0 <= i_poll < i_page, "menu_render()：先 poll 再 set_page", f"{i_poll} / {i_page}")
check(("if (sdtheme_wall_enabled())" in render_body or
       "!sdtheme_wall_enabled() || !sdtheme_render_wall(frame)" in render_body) and
      0 < i_wall < i_blank,
      "menu_render()：有壁纸走 render_wall，否则回退纯色底")

gen = menu[menu.index("bool generate_patches_progress"):]
gen = gen[:gen.index("}") + 1]
check("sdtheme_invalidate_scan();" in gen,
      "generate_patches_progress()：扫描窗占用 hiscratch 后按需挂失效标记")

scan = menu[menu.index("static bool build_patches_progress"):]
scan = scan[:scan.index("patchengine_process_rom")] if "patchengine_process_rom" in scan else scan[:3000]
check("sdtheme_invalidate_scan();" in scan,
      "build_patches_progress()：进扫描循环前按需失效（缓存落在扫描窗才算脏）")

load = menu[menu.index("static void loadrom_progress(unsigned done"):]
load = load[:load.index("static bool loadrom_progress_abort")]
check("paint_loadbg_wall()" in load and "sdtheme_arm_loadbg(" in menu,
      "loadrom_progress()：载入画面铺缓存里的壁纸（第一帧只读一次，写 NOR 复用同一份）")
check("EX_PAL_LOADBG" in load and "EX_PAL_LOADFG" in load,
      "loadrom_progress()：底色/进度条走 v5 扩展色槽")
check(all(x in menu for x in ("#define EX_PAL_IGMBG", "#define EX_PAL_LOADBG",
                              "#define EX_PAL_LOADFG")),
      "EX_PAL_* 槽位已定义")
check("MEM_PALETTE[EX_PAL_LOADBG] = themes[thnum].bg_color;" in reload_body and
      "MEM_PALETTE[EX_PAL_LOADFG] = themes[thnum].fg_color;" in reload_body,
      "reload_theme()：扩展色槽有默认值（= BG/FG）")

check("sdtheme_diag_text()" in menu and "menu_boot_timing" in menu,
      "诊断构建的底部四行小字已接进 menu_render()")

# 游戏内菜单整屏底色：必须读 ingame_menu_palette[5]，而不是 fork 原来的 [1]。
# loader.c 把 MEM_PALETTE[240..247] 整段拷进这个数组，[5] 正好是 245 = v5 扩展色槽。
igm = text(os.path.join(FORK, "src", "ingame_menu.c"))
loader = text(os.path.join(FORK, "src", "loader.c"))
base_m = re.search(r"#define\s+ING_PALETTE_BASE\s+(\d+)", loader)
igmbase = int(base_m.group(1)) if base_m else -1
exp_m = re.search(r"#define\s+SDT_EXP_IGMBG\s+(\d+)", fork_h)
exp = int(exp_m.group(1)) if exp_m else -1
check(igmbase >= 0 and exp >= 0 and igmbase + 5 == exp,
      "loader 的 ING_PALETTE_BASE+5 == SDT_EXP_IGMBG（游戏内菜单底色通道对得上）",
      f"base={igmbase} exp={exp}")
check("MEM_PALETTE[BG_COLOR] = ingame_menu_palette[5];" in igm,
      "ingame_menu.c：整屏底色读 [5]（v5 扩展色），不再读 [1]")
check("ingame_menu_palette[1];" not in igm,
      "ingame_menu.c：没有残留读 [1] 的底色代码")
check("MEM_PALETTE[EX_PAL_IGMBG]  = themes[thnum].bg_color;" in reload_body,
      "reload_theme()：游戏内菜单底色有默认值（= BG，无主题时不改变原行为）")

i_cheat = menu.find('WRITE_LOG("Loaded cheats returned %d"')
check(i_cheat > 0 and "sdtheme_invalidate_scan();" in menu[i_cheat:i_cheat + 500],
      "作弊码导入后按需挂失效标记（CHTC 工具占用 0x09000000..0x09300000）")
check("#include \"sdtheme.h\"" in menu, "menu.c 已 include sdtheme.h")
for fn in ("menu_override_themes", "menu_override_icons", "menu_override_logo",
           "menu_refresh_icons", "menu_copy_themes", "menu_icons_img_ptr",
           "menu_icons_pal_ptr"):
    check(re.search(r"^\w[\w \*]*\b" + fn + r"\(.*\)\s*\{", menu, re.M) is not None,
          f"资源覆盖入口 {fn}() 已实现")

# ---------------------------------------------------------------- 3. 产物约束
IMG = os.path.join(FORK, "superfw.gba")
if not os.path.exists(IMG):
    FAIL.append("superfw.gba 不存在（先跑 bash ref/superfw-fix/build_chis.sh）")
else:
    d = read(IMG)
    check(len(d) <= 2 * 1024 * 1024, "固件 <= 2MB（升级器硬上限）", f"{len(d)} B")
    check(len(d) % 512 == 0, "固件按 512 字节对齐（fw-fixer 补过）", f"{len(d)} B")
    fw_size = struct.unpack_from("<I", d, 0xCC)[0]
    check(fw_size == len(d), "头部 0xCC 的 fw_size == 文件大小", f"{fw_size} vs {len(d)}")
    for pat in (b"/.superfw/theme.bin", b"/superfw/theme.bin", b"/theme.bin", b"SFWTHM01"):
        check(d.find(pat) >= 0, f"固件内含字符串 {pat.decode()}")

    # 关于页第 3 行：官方 r75 是 "Chis variant"，版本号又都是 75000001
    # （改不得，flash.c 拿 hw_variant 前 4 字节做变体校验），所以这一行
    # 是机器上唯一能分辨「官方件 / 这个主题改版」的地方，不能丢。
    m = re.search(rb"Chis SDT 20\d\d-\d\d-\d\d", d)
    check(m is not None, "关于页第 3 行含 SDT 标记 + 构建日期（可分辨官方件）",
          m.group().decode() if m else "未找到")

    ELF = os.path.join(FORK, "firmware.ewram.elf")
    if os.path.exists(ELF) and os.path.exists(NM):
        out = subprocess.run([NM, "-S", ELF], capture_output=True, text=True).stdout
        syms = {}
        for line in out.splitlines():
            p = line.split()
            if len(p) == 4:
                syms[p[3]] = int(p[1], 16)
        want = {"icons_img": 17 * 256, "icons_pal": 38 * 2,
                "logo_img": 31 * 7, "logo_pal": 3 * 2, "themes": 6 * 6 * 2}
        for k, v in want.items():
            check(syms.get(k) == v, f"资源符号 {k} 大小 = {v}", hex(syms.get(k, 0)))

# --------------------------------------------------------------------- 输出
print()
for m in OK:
    print("  OK   " + m)
if FAIL:
    print()
    for m in FAIL:
        print("  FAIL " + m)
    print(f"\n{len(FAIL)} 项失败 / {len(OK) + len(FAIL)} 项检查")
    sys.exit(1)
print(f"\n全部 {len(OK)} 项通过")
