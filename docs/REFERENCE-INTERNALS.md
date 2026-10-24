# SuperFW 主题编辑器 —— 内部细节全量参考（MEMORY.md 的完整版）

> 这份是 `.workbuddy/memory/MEMORY.md` 拆出来的详细附录。MEMORY.md 只保留「忘了就会出事」的铁律和索引，
> 具体偏移、常量、格式分段、构建参数都在这里。要动固件/格式/打包/发布之前，先把对应小节读一遍。
> 日常日志仍在 `.workbuddy/memory/2026-*.md`。

## 0. 产品线
- **A 烘焙注入（兜底）**：官方固件二进制注入（`app/fwbake.py`/`wallpaper.py`/`fwbuild.py`），受空白区容量限制。
- **B SD 卡主题（主路线 v0.17+）**：源码编译固件 + `/.superfw/theme.bin`；刷一次固件后只换 SD 文件。
  两条源码线：`fw/`（主线，源 davidgfnet/superfw）与 `ref/superfw-fix/`（分支 r75）。
- https://github.com/hanhan2385/superfw-sd-theme-editor（public，Latest = v0.30）。

## 1. 固件运行时
- ROM 是 wrapper：`rom_boot.S` 把载荷拷到 **0x027C0000** 再 `swi 0x00`；菜单跑 EWRAM（251K）。
  运行地址 = `0x027C0000+(O-payload_start)`（chis 载荷起点 `04f01fe5`+0x0B48）。**跳 ROM 绝对地址 → 必崩**。
- 0x08 窗口：游戏 scratch(2MB) → 浏览器目录项(~13MB) → 0x08E80000 字体 → `0x09000000..0x09800000`
  hiscratch(8MB) → 0x09C00000 用户补丁库 → 0x09D00000 补丁库 → 0x09E00000 内置资源。**24MB 以上不解码**。
- `setup_video()` 先 `dma_memset16(VRAM,0xffff)` → 白屏 = 已进菜单、第一帧没画完就崩。
- **IWRAM 硬上限 `__IWRAM_END__ ≤ 0x03003F00`(16128)**，`gba_ewram.ld` 的 ASSERT 链接期拦
  （`Not enough free IWRAM for stack`）。32K 里 0x40 svc + 0xA0 irq + 0x4000 用户栈下限。
  查明细：`make -n` 抓链接命令 → 加 `-Wl,--noinhibit-exec -Wl,-Map=…` 重跑。
- **NOR 分区**（`src/common.h`）：固件区 `0x000000..0x200000`、元数据区 `0x200000..0x400000`（≥4 MiB 芯片）。
  外部烧录 `tools/make_flash_bin.py`（补 0xFF、不改 `fw_size`；`--base dump.bin --size 0x400000` 保住元数据区）。
  元数据区擦掉没关系（`flashmgr_load` 失败就清零重建）。见 `docs/FLASH-BIN.md`。

## 2. 路线 A 烘焙铁律
- 补丁点 = `menu_render` 内容区内联 DMA3（DAD=frame+0xF00、17280 半字），chis `0x6118` → 换 `BL`。
- **钩子只许 CPU（ldmia/stmia）盖图，绝不 DMA 从卡带 SDRAM 读位图**；**真机 VRAM 不吃 8 位写**，一律 32 位；
  `stmia` 16 B/次。**只写当前帧 `frame+0xF00` 一页**。
- 位图 Flash 直读（`fwbake.build_hook`）：`strh 0xA55A×2` + `strh 4×2` 到 `0x09FFFFFE`，结束写 7；204 B；
  首字 0/FF → 整片洋红 0xFA。已实机闭环。
- 顺序 **背景→主题→图标→reseal**，否则 `check_rom()` 失败、背景静默跳过。
- `bake()` 只跑原始固件；已带图用 `rebake()`。源必须 `in/cn.fw`/`in/cn.bin`；尾部 `.org` 段 0x190000/0x198000 不是空位。
- 校验要 `header->fw_size == 文件大小`：`.fw`/`.gba` = **1,671,680 B**；补到 2MB 升级器拒收。
  **`.gba` 只当游戏跑**，固件升级只认 `.fw`。

## 3. 数据与尺寸
- `themes[]` 6×6×uint16（fg,bg,ft,hi,hi_blend,sh），chis @0x020CF4。`reload_theme()` 只写 FG/BG/FT/HI +
  IGM_PAL_* + `MEM_PALETTE[256+SEL_COLOR]`，**不碰 20..239**；实机切完要「保存到 SD 卡」。
- 图标 4352 B = 17×256 B（4 个 8×8 tile，8bpp）+ 38 色。`_quantize()` 出 `nearest+1` →
  **调色板第 0 槽恒透明**，颜色写 1..37。0..7 文件类 / 8..14 顶栏（`i+ICON_RECENT`）/ 15,16 隐藏项。
  Logo 31×7 仅关于页 ×4 + 游戏内菜单 ×2。
- **索引偏移铁律**：`WP.quantize` 输出已 +PAL_FIRST(4)，过 `SLOTS[]` 前必须剥掉，
  统一走 `fwbake.map_wall_indices()`/`slice_wall_palette()`。
- **两个色数常量**：`fwbuild.PAL_N=236`、`fwbake.PAL_N=220`（SLOTS 20..239）；自写脚本必须 `colors=220`。

## 4. SD 卡主题格式
- 路径回退：`/.superfw/theme.bin` → `/superfw/theme.bin` → `/theme.bin`。
- **头偏移**：`magic 8B + ver(u16@8) + flags(u16@10) + size(u32@12)`。**flags 在 10，不是 9**
  （9 是 ver 高字节，误当 flags 会得出「六套都没壁纸」的假结论）。固件读 `b[10] | (b[11]<<8)`。
- **v1**：`"SFWTHM01"`+ver+flags+len；0x10 配色 72 B；0x58 图标 6×4432；0x6838 壁纸 34560；0xEF38 壁纸调色板 440。
  带壁纸 61,680 / 不带 26,680。
- **v2**：ver=2；flags `bit(1+i)`；0x6838 起 6 段各 35000，全满 `0x39CC8`。
- **v3**：+`0x39CC8` 起 7 段页面图，flags `bit(7+p)`，全满 `0x75990`。优先级 **页面图 > 当前套默认图 > BG 纯色**；
  页面序 = `MENUTAB_*`（最近/文件/烧录/设置/界面/工具/关于）。
- **v4**：0x75990 起 u32×2 = 42 位掩码（位序 `set*7+page`），0x75998 起 42×35000，共 1,951,688。
- **v5**：老布局 + 末尾 36 B（6 套×3 整屏色：载入底/进度条/游戏内菜单底）；固件用「fsz − 尾巴」反推基础版本，
  **绝不能用 `b[8] >= 4` 判布局**（一律 `_BASE_BY_SIZE[len(data) - tail]`）。
- **v6（v0.30）**：v5 + 223 B（31×7 logo 索引 217 + 3 个 RGB555 写调色板 1..3），尾巴 259。
  **只有 v0.30+ 能读，老固件按长度整份拒绝 → 退回内置皮肤**。
  `sdtheme.pack` 自动挑：全空→v1 26,680；6 套同图→v1 61,680；有页面图→v3；否则 v2。演示包 `tools/make_sdtheme_v6_demo.py`。
- 实现 `app/sdtheme.py` + `fw/src/sdtheme.c`，**常量两边必须一致**（`test_sdtheme_pack` 解析 #define 对照）。
- v2 壁纸**按需读**：开机只读 0x00..0x6838；`sdtheme_apply_theme(thnum)` 里 `thnum != sdt_cur` 就
  `sdt_wall_ready=false`；`sdt_load_wall()` `f_lseek(0x6838+thnum*35000)` 读 35 KB 进同一缓存（0xF0F8）。
- 挂点（主线 `fw/src/menu.c` 612/2399/2743）：`menu_init` 里 `sdtheme_load()` 在 `reload_theme()` **之前**；
  `reload_theme()` 末尾 `sdtheme_apply_theme(thnum)`；`menu_render` 内容区有壁纸走 `sdtheme_render_wall()`；
  补丁库写入后 `sdtheme_invalidate()`。
- `themes[]`/`icons_img`/`icons_pal` 虽 `const` 但链接在 EWRAM → 运行时可写（`menu_override_*`）。
- **缓存地址只许 2MB 升级 scratch**：`0x081E0000`（首选）/`0x08100000`，都在 `sdr_state->scratch`。
  **绝不碰 0x09800000 及更高**：chis 上那档别名到目录项缓存，而 `sdtheme_load()` 在 `browser_reload()`
  之后 → 砸掉文件索引 → 任何 `.fw` 报「无法读取文件!」（15:18 版翻车根因）。
- **读缓冲必须静态**：用户栈只 16KB，`tmp[1024]` 在 `menu_init → sdtheme_load` 链上会溢出踩坏 FatFs。
- 诊断版 `bash tools/build_fw.sh chis 1` → 底部 y=114..158 四行：`boot: d.. n.. r.. t.. ms` / `open=` /
  `rd= mg=` / `OK c= wall=`。比正式版多 ~300 B。
- 编辑器 `export_sdtheme()` **总是 6 套全写**；固件含 `/.superfw/theme.bin` →
  `Layout.build='sdtheme'`, `patch_off=-1`, `wallpaper.is_patched()` False。只做 chis。

## 5. SD 主题编辑器（`app/sdedit.py`）
- 只读写 theme.bin，不碰固件；独立 Tk，不 import `main.py`。打包 `tools/build_sdedit_exe.py`。
- 壁纸按套存：`Doc.wall_rgb/idx/pal/dirty` 都是 6 元素列表，「选图/裁剪/移除」只动当前套，
  `copy_wall()` 铺给其余 5 套。`packed_size()` 预测大小，状态栏「壁纸 n/6（v?）」。
- 壁纸解码走 P 模式：`frombytes("P") + putpalette + convert("RGB")`。
- **图标调色板铁律：像素值 v → `pal[v]`**（第 0 槽恒透明）。依据 `ref/superfw/res/iconcv.py`
  （`pal.index(col)+1` 配 `"0x0000," + colors`；固件把 `icons_pal` 整块拷到 `&MEM_PALETTE[256]`）。
  **别写 `pal[v-1]`** —— v0.18 整套错位一格。写回 `pal = [0] + [color555(c)…]` 补零到 38。
- **量化前不要 8 对齐**（`(r+4)//8*8`）：555 网格再对齐会整体偏一档。≤37 色原样，>37 才 median-cut。
- 图标/壁纸存原始字节 + dirty → 「打开再保存」逐字节一致（`--selftest --file`）。
- 未修问题清单见 `docs/editor-review-2026-10-22.md`（约 20 项）；`dist/v0.30` 的 exe 是修复前代码。

## 6. 环境 / 构建 / 打包 / 签名
- **Python 只用 `.venv314/Scripts/python.exe`**（3.14.7 + tkinter + capstone + unicorn + PIL + PyInstaller）。托管 3.13 无 tkinter。
- **onefile 铁律**：bootloader 读 **`TMPDIR` → `TMP` → `TEMP`**；`--runtime-tmpdir .` = **CWD** 不是 exe 目录。
  **PowerShell 工具环境稳定复现 `Could not create temporary directory!`，bash / Python subprocess 下正常**
  → 验证 onefile 只能用 bash 或 Python。
  **本机用 `--runtime-tmpdir "E:\SuperFW-Cache"`**：`AlibabaProtect` + `AliPaladin` 会拒未签名 exe 在 `AppData\Local`
  下 `CreateDirectory`（`LOADER: failed to create runtime-tmpdir path ... 拒绝访问`）。判断方法：bash 在同一路径
  `mkdir` 能成功 = 拦截按进程签名算，换路径只是绕。**`E:\SuperFW-Cache` 保留为空目录**。**别用 `.`（CWD）**。
  onedir 兜底仍在源码里（`build_exe(..., onefile=False)` / `--onedir`，零解压，**必须有 `_internal`**，缺了静默退出码 -1）。
  onefile 在 Windows 是**父子两进程**：Popen 的 pid 是父（23 模块、无 `python314.dll`）。
  `%` 在 argparse help 里要写 `%%`；bootloader 用 `ExpandEnvironmentStringsW` 展开 `%VAR%`。
- **签名**：`tools/install_codesign_cert.ps1` 建自签证书 `CN=SuperFW Theme Editor` 并导入
  `CurrentUser\Root` + `TrustedPublisher`；`tools/sign_exe.ps1` 调 `Set-AuthenticodeSignature -HashAlgorithm SHA256`。
  **签名必须在打包之后**（重建会让签名失效且哈希变），**签完必须重建 `SHA256SUMS.txt`**。
  **自签名只在本机受信任，换机器照样弹「无法验证发布者」**。
- **`.ps1` 必须纯 ASCII**（PowerShell 5.1 把无 BOM 的 UTF-8 当 ANSI 读）。
- 固件构建：`.toolchain/armgcc/…13.3.1/bin` + `.toolchain/w64devkit/w64devkit/bin`；
  `bash tools/build_fw.sh chis [0|1]`。**mingw make 必须 `SHELL=<Git Bash 的 Windows 路径>`**；下载走 `ghfast.top`。
- **`rm` 被 safe-delete 接管**（带盘符前缀报 `SAFE_DELETE_INVALID_PATH`），失败会打断 `set -e` → 强制重编用 `touch`。
- `tools/emulate_ewram.py` 按真实布局仿真，验补丁必须用它。
- 自检脚本：`verify_baked`/`test_rebake_hook`/`build_diag_wall`/`mgba_dma_bits`/`test_theme_slots`/`test_ui_slots`/
  `test_theme_icons`/`test_ui_icons`/`test_auto_wall`/`test_crop`/`test_click`/`test_img2gba`/
  `test_sdtheme_pack`/`test_ui_sdtheme`/`test_flash_bin`/`verify_exe_bundle`/`check_theme_bin`。**只验结构，不代替实机**。
- **`dist/图标素材/`**：`内置原始/`（`tools/extract_icons.py`）+ 各新方案（`tools/make_icon_sets.py`）。
  每套 `16x16/` + `128x128/` + `原始数据/`（`icons.raw` 4352 / `.pal` 76 / `.json`）+ 精灵图。17 图共用调色板 ≤37 色。
  `snap()` 必须落在 `rgb555(color555(c))` 上（`(v<<3)|(v>>2)` 是位复制不是左移）。回灌 `sdedit.icons_to_raw` 逐字节相同 = 无损判据。
- `tools/make_style_presets.py`：出「6 套风格」提案包（`dist/主题风格提案/`），打成一个 v5 `theme.bin`；
  `--audit` 算四项 WCAG 对比度，`--compare` 出对照图，`--mono-icons 3` 压绿阶。
- **配色硬指标**：正文/内容底 ≥7、正文/选中条 ≥4.5、选中条/内容底 ≥1.6、顶栏暗字/顶栏 ≥3.5。
- **固件点阵字库只收常用字**：`gbafont.cell()` 缺字返回 adv=5 + 空 mask（静默留白）；桌面图文字用系统 TTF（msyh.ttc）。
- 发布 `tools/package_release.py`（`--with-firmware` 才带固件）。截图 `shot_sdedit_pw.py --theme <临时 theme.bin> --slot 3`。
  校验别用 `raw.githubusercontent.com`（超时），走 `gh api -H "Accept: ...raw"`。
- 体检 `app/fwcheck.check()`。`find_baked()['b_rom']` 不是位图 B 起点，真数据在 `data_b_at`。
- **目录**：最新 `dist/v0.31/`，**9 个文件 / 29 MB** —— 根上 4 项：`SuperFW-SD-Theme-Editor-v0.31.exe`
  （onefile 21.6 MB，**不依赖 `_internal`、不读系统 TEMP**，解压到 `E:\SuperFW-Cache`）、`theme.bin`、
  `快速上手.md`、`SHA256SUMS.txt`；加 `文档/`、`固件/`（4 个）。**「备用-免解压版」已取消**。
  `v0.19`..`v0.30` 在 `archive/2026-10-22/releases/`。
  版本号只改 `app/sdedit_ver.py` 的 `APP_VER`；`tools/check_version.py` 查漏。
  **`tools/cleanup_project.py` 是 v0.21 规则，早已过时**，别直接 `--apply`。`.toolchain/` 2.3G 不能删。
- **救命设施 `.workbuddy/file-history/<sessionId>/<hash>@vN`** 存 Edit/Write **之前**的快照；python 脚本直接改的不生成。
- **测试脚本铁律**：只许写 `tempfile.mkdtemp()`，绝不拿仓库真实文件当写靶子。
- 已 git 化（GPL-3.0，`fw/` 是 davidgf 的代码），`dist/` 不进 git。

### 发布通道（网络受限）
- **用户口径：先本地校对，别主动往 GitHub 发/建 Release**；要发布先问，或等他明确说「发」。
- `git ls-remote`/`push --dry-run` 能过，**真传 pack 就断**（`invalid index-pack output`）。
- **推代码只能用 `tools/push_to_github.py`**（blob→tree→commit→force ref）。`export GITHUB_TOKEN="$(gh auth token)"`；
  `gh` 在 `/c/Program Files/GitHub CLI`。见 `docs/PUBLISH.md`。`--root` 把远程历史压成一条，再 PATCH tag 挪过去。
- 发 Release：`--target` 填 commitsha；**资产名不能带中文**；改正文用 release `id`；
  `gh release create` 会自动设 Latest → 附加 Release 必须 `--latest=false`。现有 `v0.30`（Latest）、`sffix-v1`。
- Release 正文只写「怎么用 + 图片规格」：壁纸/页面图 240×144(5:3)；图标 17 个 16×16 透明 PNG ≤37 色；Logo 31×7 三色。

## 7. superfw-fix 分支（`ref/superfw-fix/`，产物 `dist/superfw-fix/`）
- **发布基线 r75**：固件头 `0x0C8 = 0x75000001`（小端 u32，字符串搜不到，字节 `01 00 00 75`），
  关于页 `Chis SDT 2026-10-21`，1,969,664 B。
- **r78 已适配未发布**：`archive/2026-10-22/out/r78-theme-2026-10-22/`（`0x78000001`、1,970,176 B、
  恢复内置 Hack 1428 条、修 v6 Logo 尾段 259 B 按 4 字节 DMA 丢 3 字节色、缓存 `0x081F0000`）。**完整实机验收没做**。
- **校验 `SHA256SUMS.txt` 要用 `encoding="utf-8-sig"`** —— r78 那份首行有 BOM，按 utf-8 读必然误报"不符"。
- 上游 moribaka/superfw-fix r75（master `8dd68502`）chis 定制：菜单压缩重写 / SNES / 软件 RTC / 中文 hack /
  放宽 ROM 头 / ROM 库 1742 条。**源码编译**，非烘焙。构建 `bash ref/superfw-fix/build_chis.sh [SDTHEME_DIAG=1]`；
  自检 `tools/verify_sffix_port.py`（49 项）。**字节比对不能当「是否原生」判据**（官方 vs 无补丁重建也差 75.2%）。
- **hiscratch 只用前 1MB 扫描窗 + ~20KB 尾表**；写 ROM 走 2MB `sdr_state->scratch`。
  `0x081E0000` 是 `MENU_OVERLAY_KERNEL_ROM` → 缓存改 `0x09400000 → 0x09700000 → 0x09000000`。
  `SDT_BUFSZ` 1024（.bss 在 IWRAM，4KB 顶穿栈断言）。
- **游戏内菜单（L+R）只接整屏底色**：读 `ingame_menu_palette[5]`（=`MEM_PALETTE[245]`，`ING_PALETTE_BASE=240`）。
- **那 5 个 IGM 图标不换，两个固件都不换**（用户 2026-10-21 明确「都不要」）。**别再自作主张接这条**。
- **关于页第 3 行必须带标记**：上游 `FW_FLAVOUR " variant"`，版本号两边都是 `75000001`（`flash.c:457`）→
  分支改成 `FW_FLAVOUR " SDT " SDT_BUILD_DATE`。**副作用：构建不可复现**。判分支件看第二行括号：`75000001`=分支，git 短哈希=原生。
- 预算：正式 IWRAM 14,968 / EWRAM 241,512；诊断 15,128 / 242,476。主镜像 `-Os`（EWRAM 99.78% 时必须）；
  `emu/sfc-patches.bin` 从官方 r75 `0x1860a8` 提取（3244 B）。
- 补丁产出 5 个文件（`Makefile`、`src/sdtheme.c/h`、`src/menu.c`、`src/ingame_menu.c`）：
  拷进 `out/pk/{a,b}` 同构目录 `diff -ruN --strip-trailing-cr` 再 `sed` 去时间戳。

## 8. 排障判据
- 烘焙实机：内容区**纯黑** → 钩子跑了但读到全 0；**主题底色** → 钩子没被调用；**花屏** → 地址算错。
- SD 主题"一点没变" → 跑诊断版看四行字，别靠猜。
- mGBA 跑不了 SuperFW 本体（要 CPLD 握手）；Thumb `b .` 必须 0xE7FE；GDB 单包 256 B。
- GBA DMA3：`CNT_H` bit15 使能（**不是 bit14**），bit8-9 宽度（01=32 位），bit12-13 启动时机；
  `CNT_L` 搬完读回 0，要轮询并设上限。
- unicorn：`UC_HOOK_INTR` 里 intno==2 才是 SWI，hook 内 PC 已指下一条，ARM SWI 号 = `(insn>>16)&0xFF`。
- `sdtheme.parse()` 的 `wall_pal` 是 **RGB555 整数**，画预览必须过 `sdtheme.rgb555()`。
- **`os.startfile(exe)` / `cmd /c bat` 在工具环境会挂起**（GUI 子进程继承管道句柄）→ 用
  `subprocess.Popen(stdout=DEVNULL, stderr=DEVNULL, stdin=DEVNULL)` + 枚举窗口标题。
  **后果**：exe 若以 `dist/` 为 CWD 启动，搜狗输入法会在那儿落一个 `debug.log`，发布前要删。
- 「改名→测试→改回」这类验证，还原那步用 `mv`（exe 刚被 kill 句柄未释放，`os.rename` 会失败）。
