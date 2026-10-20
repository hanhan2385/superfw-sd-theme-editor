# SuperFW SD 主题编辑器

给 GBA 烧录卡固件 **SuperFW** 做 SD 卡主题包的工具。

调好配色、图标、背景，存成 `theme.bin` 丢进 SD 卡，重开机就是新界面 —— **固件只刷一次，之后只换卡上的文件**。

![编辑器界面](docs/images/editor.png)

---

## 它能改什么

| 项目 | 内容 |
|---|---|
| 配色 | 6 套，每套 6 个角色：顶栏/底栏、内容区、文字、边框、选中行、游戏内菜单 |
| 图标 | 6 套，每套 17 个（文件、文件夹、卡带、工具…），可逐格改 |
| 壁纸 | 6 套，每套一张 240×144 |
| 页面背景 | 菜单顶部的 **7 个标签页可以各配一张**，不配就跟随当前套的主题图 |

取图规则是层层回落：**这一页有专属图 → 用它；没有 → 用当前套的主题图；再没有 → 纯色**。

预览直接用固件自己的点阵字库（unscii-16）和布局常量绘制，不是画个"示意图"，所以字号、字宽、哪里会被截断，都和真机一致。

---

## 为什么把主题放 SD 卡

刷固件有风险，改个配色不该冒这个险。所以这套工具的思路是：

**固件里只留读取逻辑，主题内容全部放在 `/.superfw/theme.bin`。**

- 改主题 = 换一个文件，不用刷机、不用重算校验
- 改坏了直接把文件删掉，固件回落成内置皮肤
- 主题文件和固件版本解耦，固件升级不用重新做主题

这个思路来自 [ZaindORp 的 EZ-FLASH OMEGA DE 内核](https://github.com/orzgithub/omega-de-kernel_ZaindORp) —— 它也是把主题文件放卡上、格式沿用官方资源，换上即生效。

---

## 快速开始

### 用现成的 exe

1. 从 [Releases](../../releases) 下载 `SuperFW-SD-Theme-Editor-*.exe`，双击就能跑，不用装 Python
2. 调好之后点「另存为…」，存成 `theme.bin`
3. 拷到 SD 卡的 `/.superfw/theme.bin`（固件也认 `/superfw/theme.bin` 和根目录 `/theme.bin`）
4. 插卡开机

> 卡上原来没有这个文件也没关系：固件第一次开机会自动生成一份默认的（内置皮肤、无壁纸），之后覆盖它就行。

### 从源码跑

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python app/sdedit.py
```

需要 Python 3.9 以上（开发和测试用的是 3.14），并带 tkinter —— 官方 Windows 安装包自带。

---

## 固件

`fw/` 是编译用的固件源码，基于 SuperFW，加了 SD 主题支持（`fw/src/sdtheme.c`、`fw/src/sdtheme.h`，以及 `fw/src/menu.c` 里的几处挂点）。

编译需要 ARM 交叉工具链：

```bash
bash tools/build_fw.sh chis       # 正式版
bash tools/build_fw.sh chis 1     # 诊断版：屏幕底部显示各阶段加载耗时
```

产物是 `fw/superfw.gba`，改扩展名为 `.fw` 才能走固件升级流程（`.gba` 会被当游戏跑）。

**主题文件版本要和固件匹配**：v3 的页面图必须配 v0.21 及以后的固件，更老的固件读到 v3 会静默跳过整份主题。

---

## theme.bin 格式

小端，所有字段 4 字节对齐。

```
0x0000  char[8]  "SFWTHM01"
0x0008  u16      版本 1 / 2 / 3
0x000A  u16      标志位
                   v1: bit0 = 带壁纸（6 套共用一张）
                   v2: bit(1+i) = 第 i 套有自己的壁纸
                   v3: bit(7+p) = 第 p 个页面有自己的壁纸
0x000C  u32      文件长度
0x0010  u16[36]  6 套 × 6 个 RGB555
0x0058  u8[6*4432] 6 套图标（4352 B 精灵 + 38 项调色板 + 对齐）
0x6838  壁纸段，每段 35000 B = 34560 索引 + 220 项调色板
```

自动挑选版本：全空 → v1（26,680 B）；6 套同图 → v1（61,680 B，老固件也能读）；每套不同 → v2（236,680 B）；有任何页面图 → v3（481,680 B）。

壁纸索引值是**调色板槽号 20..239**，不是从 0 开始。这一点很容易踩，写自己的工具时要留意。

---

## 自检

不需要图形界面，命令行就能跑：

```bash
python app/sdedit.py --selftest          # 编辑器自身：打包/读回/量化
python tools/test_e2e_theme.py           # 端到端：编辑器产物 → 固件怎么读
python tools/test_sdtheme_pack.py        # 格式打包与往返
python tools/test_sdtheme_lazy.py        # 按套/按页取段，确认不串位
python tools/test_robustness.py          # 坏文件、怪图片、异常路径
python tools/test_ui_sdtheme.py          # 界面状态机
python tools/check_firmware.py <fw 文件>  # 固件体检
```

`tools/` 下还有一批 `test_theme_*` / `test_ui_*` / `verify_*`，是早期"直接给官方固件打补丁"那条路线（路线 A）留下的回归测试，需要烘焙过的固件才能跑，没有可以忽略。

---

## 目录

```
app/      编辑器与格式实现
  sdedit.py   主程序（界面 + 预览渲染）
  sdtheme.py  theme.bin 读写
  gbafont.py  点阵字库渲染（预览用）
  wallpaper.py / fwbake.py / gbacvt.py  图像与固件相关工具
fw/       固件源码（基于 SuperFW，加 SD 主题支持）
tools/    构建、打包、自检脚本
docs/     设计文档与截图
```

---

## 许可

本项目按 **GPL-3.0-or-later** 发布，见 [LICENSE](LICENSE)。

`fw/` 目录是 [SuperFW](https://superfw.davidgf.net/) 的衍生代码，原作者 David Guillen Fandos (davidgf)，同样是 GPL-3.0。本项目的修改集中在 SD 主题相关部分。

点阵字库来自 [unscii](http://viznut.fi/unscii/)（Viznut，GPL）与 [GNU Unifont](https://unifoundry.com/unifont/)。

---

## 致谢

- **davidgf** —— [SuperFW](https://superfw.davidgf.net/) 固件作者，没有它就没有这套主题机制
- **ZaindORp** —— [EZ-FLASH OMEGA DE 内核](https://github.com/orzgithub/omega-de-kernel_ZaindORp)，SD 卡主题这套思路的来源
- **Viznut** —— [unscii](http://viznut.fi/unscii/) 点阵字库，预览和真机的字形都是它
- **GNU Unifont** —— 文字字形补全

---

制作：**小米粥不糊**
