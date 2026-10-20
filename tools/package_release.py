# -*- coding: utf-8 -*-
"""打成独立发布目录：dist/v<版本>/ —— 已有的版本目录绝不覆盖，方便回溯。

用法:
  python tools/package_release.py [--fw 源固件...] [--wall 图片] [--dim 0.35]
                                  [--no-exe] [--preset 0]

做的事：
  1. 读 app/version.py 的 VERSION
  2. 建 dist/v<VERSION>/（已存在就报错退出）
  3. 用 PyInstaller 打 exe（可选）
  4. 把源固件烘焙背景图、写入 6 套主题，放进 固件/
  5. 生成预览图、README.md、SHA256SUMS.txt
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw         # noqa: E402

import fwbake as FB                      # noqa: E402
import fwbuild                           # noqa: E402
import main as M                         # noqa: E402
import version as VER                    # noqa: E402
import wallpaper as WP                   # noqa: E402

DEFAULT_FW = [
    ("in/cn.bin", "SuperFW-chis-r75-CN-{v}.gba", "卡带镜像 2 MB（外部烧录器写整片）"),
    ("in/cn.fw", "SuperFW-chis-CN-{v}.fw", "1.6 MB（SuperFW 内置升级器可接受）"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bake_one(src, out, wall_img, dim, preset):
    rom = bytearray(open(src, "rb").read())
    lay = fwbuild.detect(rom)
    bgc = M.q5(M.rgb(M.PRESETS[preset][1][1]))
    fitted = WP.fit_image(Image.open(wall_img).convert("RGB"), "cover", bgc)
    idx0, pal, n = WP.quantize(fitted, dim, bgc, FB.PAL_N, True)
    idx = FB.map_wall_indices(idx0)
    pal = FB.slice_wall_palette(pal)

    info = FB.bake(rom, None, indexed=idx, palette=pal)
    rom = info["rom"]
    for s in range(6):                       # 6 套主题全写，避免设备选的是别的槽
        WP.patch_theme(rom, M.PRESETS[preset][1], slot=s, layout=lay)
    fwbuild.reseal(rom)
    ok, why = M.fw_validate(rom)
    if not ok:
        raise SystemExit("校验失败: %s" % why)
    open(out, "wb").write(bytes(rom))
    return lay, info, n


def _check_pyi_python():
    """打 exe 的解释器必须带 tkinter —— 否则出来的 exe 双击就崩。"""
    r = subprocess.run([sys.executable, "-c", "import tkinter"],
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(
            "当前解释器没有 tkinter，打出来的 exe 会打不开。\n"
            "换一个带 tkinter 的 Python 来打包，例如：\n"
            "  .venv314\\Scripts\\python.exe tools\\package_release.py\n"
            "（%s）" % (r.stderr or b"").decode("utf-8", "replace").strip()[:200])


def _smoke_exe(exe):
    """冒烟测试：窗口程序如果一启动就崩，进程会立刻退出。

    刚搬过来的 exe 有时会被杀软/索引短暂占住，第一次启动会立刻退出，
    所以失败要重试几次再判定，别把好包误判成坏包。
    """
    import time
    last = ""
    for attempt in range(3):
        p = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = p.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            p.terminate()                       # 还在跑 = 正常，窗口开着
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            return
        last = "%s%s" % ((err or out or b"").decode("utf-8", "replace").strip(),
                         "" if p.returncode == 0 else "\n退出码 %s" % p.returncode)
        time.sleep(1.5)
    raise SystemExit("打出来的 exe 连续 3 次一启动就退出，说明有问题：%s" % (last or "无输出"))


def build_exe(dest_dir, name, script=None):
    """用 PyInstaller 打包 exe，先落到临时目录再搬过去。"""
    _check_pyi_python()
    script = script or os.path.join(ROOT, "app", "main.py")
    icon = os.path.join(ROOT, "app", "icon.ico")
    work = os.path.join(ROOT, "build", "_rel")
    outd = os.path.join(ROOT, "dist_new")
    os.makedirs(outd, exist_ok=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--windowed",
           "--name", name,
           "--distpath", outd, "--workpath", work,
           "--specpath", work,
           "--paths", os.path.join(ROOT, "app")]
    if os.path.exists(icon):
        cmd += ["--icon", os.path.abspath(icon),
                "--add-data", "%s%s." % (os.path.abspath(icon), os.pathsep)]
    cmd.append(script)
    subprocess.run(cmd, check=True)
    exe = os.path.join(outd, name + ".exe")
    if not os.path.exists(exe):
        raise SystemExit("没生成 %s" % exe)
    shutil.move(exe, os.path.join(dest_dir, name + ".exe"))
    _smoke_exe(os.path.join(dest_dir, name + ".exe"))
    print("%s 启动检查通过" % name)


README = """# SuperFW 主题编辑器  v{ver}

生成时间：{when}
构建代号：{codename}

## 这一版改了什么

{changes}

## 目录里都有什么

| 文件 | 说明 |
|---|---|
| `SuperFW-Theme-Editor-v{ver}.exe` | 修改器本体（配色 + 背景图 + 图标），双击就能跑，不需要装 Python |
{fw_row}| `预览/` | 修改器界面、图标位置示意、菜单合成预览、裁切示意、6 套主题对比、6 套图标对比 |
| `SHA256SUMS.txt` | 所有文件的校验值 |

从 v0.13 起只发这一个程序：以前那两个单独的「背景图写入」「图片转 GBA」已经
不需要了 —— 写背景图是修改器里的第 ② 步，其余设置不碰也不会被改。

## 只想换背景图

不用整套配色都改。打开修改器：

1. **② 背景图** 里选一张图（选完立刻显示「写进固件后的真实样子」：已按 240×144
   裁好、量化为 220 色、并按当前压暗滑块压过了）；
2. 点「选择固件…」选卡带自带的 `.fw` / `.gba`（第一次注入时程序会先算一遍空位，
   够不够放会直接告诉你）；
3. 直接点「注入到固件…」，其余步骤不动就行 —— 主题和图标保持固件里原来的样子。

固件里**已经有背景图**时会直接换成新的那张（不需要空位，钩子会按当前版本重写一遍）；
固件是全新的就在里面找两段空白塞位图，并把钩子装进 EWRAM 载荷。输出文件名会带上
`-6themes-wall` 或 `-themeN-wall`，不会覆盖你的原始固件。


## 6 套主题（v0.15 起）

固件本来就有 6 套配色（源码 `menu.c` 的 `themes[]`），实机在
**界面设置 → 主题颜色** 里用左右键切 `< 1 >` … `< 6 >`，选完按
**将设置保存到SD卡** 才会记住。

修改器里「① 配色 · 6 套主题」那 6 张卡片，就是这 6 套：

- **点卡片** = 开始编辑那一套；改颜色只记在当前这套上，切走再切回来颜色还在；
- 下面那行**「套预设」小色条** = 把现成配色填进当前这一套，另外 5 套不动；
- 想 6 套统一成一套配色，点「把当前这套配色铺到 6 套」；
- 注入时默认**6 套各写各自的配色 + 图标**（配色每槽 12 字节，共 72 字节）。取消勾选
  「6 套各写各自的配色 + 图标」就只写当前这一套的配色，其余 5 套配色保持固件原样；
  图标那部分不受这个开关影响 —— 只要 6 套图标不完全一样就会照写（见下一节）。

输出文件名：6 套一起写是 `原名-6themes-wall.ext`，只写一套是 `原名-themeN-wall.ext`。

切主题**不会盖掉背景图**：固件切主题只改调色板 0..15 和 256+SEL_COLOR，
壁纸用的 20..239 不动，所以 6 套主题可以共用同一张背景图。
`预览/6套主题.png` 是 6 套分别长什么样的对比图。

## 6 套主题各带一套图标（v0.16 起）

上面那 6 套不止配色：**每套还能各带一套图标**。右上「③ 图标」面板跟着当前这套走，
改第 3 套的图标不会动到第 1 套；某个槽位的图标被改过，卡片上会多一个「· 图标」。

- 逐个换完，直接「注入到固件…」，6 套图标会一起写进去；
- 想 6 套统一成同一套图标，点「**把这套图标铺到 6 套**」；
- 6 套图标**完全一样**时只写 1 份（省 26 KB），行为跟以前一样。

写进去的位置和切换方式：每套 4,432 字节（4,352 精灵 + 38 色调色板）连续排在
固件空白区，旁边记一条 `SFWICON` 定位记录。钩子每帧读一次 `menu_theme`，
发现换了套才把对应那份拷进 VRAM 的 `0x06014000` / `0x05000200` —— 同套重跑不重复搬，
不额外占帧时间。所以实机在 **界面设置 → 主题颜色** 切 1..6 时，配色和图标是一起换的。

两条要知道的规则：

1. **图标挂在那条背景图钩子上**。固件里本来没有背景图、你也没选图时，注入会先垫一张
   和「内容区背景」同色的纯色底（画面看不出变化），只为了让钩子有落点；
2. **空间不够就单独降级**。比如 r75 的 2 MB 整片镜像本来就紧，放不下 26 KB 图标表时，
   配色和背景图照写，弹窗里会明确写「6 套图标 没写成：原因」，不会静默丢掉。
   `预览/6套图标.png` 是同一份固件里 6 套图标长什么样的对比。

## 更省事的一条路：SD 卡主题（v0.17 起）

不想每次换皮肤都重刷固件的话：刷一次「SD 主题版」固件，之后换皮肤只换 SD 卡上的一个文件。

1. 在修改器里把 6 套配色、6 套图标、背景图都调好；
2. 点「**导出 SD 主题…**」，存成 `theme.bin`；
3. 拷到 SD 卡的 `/.superfw/theme.bin`（跟 `settings.txt` 同一个目录，名字必须一样）；
4. 重启进菜单。实机在 **界面设置 → 主题颜色** 切 1..6，配色和图标整套跟着换。

这条路**不需要**找固件空白区、不写钩子、不重算校验和 —— 6 套就是文件里的数据：

| 文件 | 大小 | 内容 |
|---|---|---|
| 带背景图 | 61,680 B | 6 套配色 + 6 套图标 + 1 张 240×144 壁纸 |
| 不带背景图 | 26,680 B | 6 套配色 + 6 套图标（内容区保持主题底色） |

没放这个文件时，SD 主题版固件的行为跟原版完全一样。**官方固件不认这个文件**，
那种情况继续用「注入到固件…」。

## 修改器怎么用

界面从左到右四步：

1. **① 配色** —— 6 套主题各一张卡片（见上一节），点哪张就编辑哪套；
   也可以逐个改下面 6 个颜色。
   「文字看得清吗」会算正文色分别和背景、和选中条的对比度，低于 4.5:1 会标黄。
2. **② 背景图** —— 选一张图即可。成品**固定 240×144**：原图超出的部分自动裁掉，
   下面会直接写明裁掉多少像素、放大或缩小几倍；**点缩略图可以把想要的那块挪到保留区中间**。
3. **③ 图标** —— 17 个 16×16 图标 + 1 个品牌 Logo。点一个格子，左边预览会
   **自动切到它会出现的界面，并用黄框把位置圈出来**；鼠标停在格子上也会弹小提示，
   写着「06 · GBA 卡带 / 文件浏览里 .gba 游戏那一行」。看清了再点「换这个图标的图…」。
   这一套图标只属于**当前这套主题**，想 6 套统一就点「把这套图标铺到 6 套」。
4. **④ 将要改动的地方** —— 写入前逐条列出会改哪些数据、多少字节；选好固件后
   还会显示具体偏移。确认无误再点「注入到固件…」（官方固件走这条），
   或者点「导出 SD 主题…」只产出一个 `theme.bin`（配合 SD 主题版固件）。

背景图的规则：不管原图多大，进固件的永远是一张 240×144、220 色的 8bpp 位图。
所以大图不会写不进去，只会被裁；小图会被放大，界面会提示"可能发糊"。

## 图标到底出现在哪

17 个图标 + 1 个 Logo 的位置，是照固件源码 `menu.c` 的真实调用点整理的，不是猜的：

| 编号 | 图标 | 出现在菜单的哪 |
|---|---|---|
| 01 | 文件夹 | 文件浏览里，所有文件夹那一行 |
| 02 | 二进制文件 | 文件浏览里，不是游戏/升级包的普通文件 |
| 03 | 升级文件 | 文件浏览里，`.fw` 固件升级包 |
| 04–08 | GB / GBC / GBA / SMS / NES 卡带 | 文件浏览里对应后缀那一行（`.gba` 在 NOR 闪存列表里也用） |
| 09 | 最近游玩 | 顶栏第 1 格 |
| 10 | 磁盘 / SD | 顶栏第 2 格；载入选项里「记住设置」右边 |
| 11 | NOR 闪存 | 顶栏第 3 格 |
| 12 | 设置 | 顶栏第 4 格 |
| 13 | 界面 / 语言 | 顶栏第 5 格 |
| 14 | 工具 | 顶栏第 6 格 |
| 15 | 关于 | 顶栏第 7 格 |
| 16–17 | 隐藏文件夹 / 隐藏文件 | 文件浏览里，被标成隐藏的项目（平时不显示） |
| ★ | 品牌 Logo（31×7） | 「关于」页放大 4 倍画一次；**游戏内菜单顶部**放大 2 倍 |

顶栏那 7 格就是菜单最上面一排按钮，顺序固定；点图标时预览会直接跳到那一屏并圈出来。

## 背景图怎么裁

不管原图什么比例，进固件的永远是一张 240×144、220 色的 8bpp 位图：

- 原图比例不是 5:3 时，超出的部分**自动裁掉**（居中）；界面会直接写出
  「上下共裁 660 px（55%）· 缩小 3.8×」这种精确数字；
- 想换保留哪一块，**点缩略图上那个位置**就行，那块会自动挪到绿色框里；
- 原图比 240×144 还小时会被放大，界面会提示"可能糊"；
- `预览/裁切示意.png` 就是一张 900×1200 竖图放进去后的实际样子。

### 到底需要什么样的图

**什么图都能用**，没有格式、尺寸、比例的限制（png/jpg/bmp/gif/webp 都行）。
进固件前一律处理成 240×144，所以大图不会写不进去，只会被裁。

只有三条建议：

| 项 | 建议 |
|---|---|
| 分辨率 | 原图 ≥ 480×288（2 倍以上），再小放大就发糊 |
| 比例 | 尽量接近 5:3，裁掉的部分最少 |
| 亮暗 | 别和文字色撞。菜单文字是**直接压在图上**的，不是压在主题底色上 |

第三条界面会替你判断：图和文字色对比不够时会直接写「图偏暗，深色字看不清 →
建议用 Dark 主题」。也可以拉「压暗」滑块把图压暗一点。

### 以前为什么写不进去

v0.07 及更早会报「可用的空闲区不足 2 段（找到 1）」，原因不在图，而是
**固件里那一大片空白没被算进来**：assets 资源块只用到前 153 KB，后面还有 89 KB
全是 0，但那一段紧挨着资源块尾部，旧代码「零区只要碰到有东西在用的地方就整段丢掉」，
于是把它一起扔了，只剩尾部 29 KB —— 而背景图要 35 KB。

v0.08 改成把「有东西在用」的区间从零区里减掉，剩下的照样能用：
chis 构建现在有 89,608 + 29,044 字节两段，正好放得下。

## 刷完之后

| 现象 | 原因 / 处理 |
|---|---|
| 背景没出现 | v0.13 之前的原因是钩子用 DMA3 从卡带 SDRAM 读位图，真机读不到（调色板正常、位图全黑）。**用 v0.13 重新注入一次**即可；如果固件里原来那张图是旧版本写的，程序会顺手把钩子换掉 |
| 背景没出现（v0.13 之后还有） | 先确认刷的是注入出来的 `…-wall` / `…-带背景` 那个文件；再看「④ 将要改动的地方」里背景那几行是否完整列出，而不是写着「背景图 没写进去」 |
| 图标颜色发红 / 错位 | v0.06 之前的老版本有这个 bug（图标调色板整体错位一格），重新注入一次即可 |
| 切主题配色变了、图标没变 | 6 套图标没写进去。注入完成弹窗里那行「6 套图标 …」会写原因；最常见是固件空白区不够（r75 的 2 MB 整片镜像最容易碰上），这时配色和背景图照写，只是图标仍是同一套 |
| 图标换回原来那套了 | 刷的文件不是 6 套图标版，或者注入时勾了「只写当前这套」又只改了配色 —— 6 套图标只要不完全一样就会写，跟配色开关无关 |
| 主题颜色没变 | 6 套主题已经写成同一个配色；如果还是老样子，说明刷进设备的不是这份文件 |
| 提示「空闲区不足 2 段」 | v0.08 已修；若仍出现，说明这个固件本身能用的空间真的不够，换卡带原厂固件 |

## 固件

{fw_section}

## 要改东西怎么办

1. 打开修改器，左边挑主题色、右边换图标、中间放背景图；
2. 点「选择固件…」，选你的原始固件（`.bin` / `.fw` / `.gba` 都行）；
3. 「④ 将要改动的地方」会列出这次会改到的每一处偏移和字节数，确认无误再点「注入到固件…」。

如果选的固件里已经有背景图，换图就是**直接替换那一张**，不会再提示"不能重复烘焙"。

注入后文件名会变成 `原名-themeN-wall.ext`，不会覆盖你的原始固件。

## 背景图是怎么进固件的

原来的做法是在固件尾部塞一段跳板，让菜单渲染时跳过去执行 —— 但 SuperFW 的菜单
代码是 bootloader 拷到 EWRAM（0x027C0000）之后从那里取指的，跳板的 BL 偏移按
ROM 地址算会落到 EWRAM 之外，取指异常，而且 `setup_video()` 已经把 VRAM 刷成
0xFFFF —— 这就是白屏的原因。

现在的做法是把背景图**真正烘焙进去**：

1. 240×144 的 8bpp 位图（34,560 字节）分两段写进固件内部不被任何拷贝覆盖的全 0 区；
2. 把 bootloader 里 assets 的 `sdram_copy` 块数改大，让两段一起被搬进 SDRAM；
3. 钩子代码（{hooklen} 字节）写进 EWRAM 载荷的空位，随载荷一起进 EWRAM；
4. 把菜单「画内容区背景色」的那几条指令改成一条 `BL`，钩子里用 **CPU 的
   `ldmia`/`stmia`** 把 8bpp 位图逐块搬到 `frame+0xF00`，再写 220 个调色板槽（20..239）；
5. 钩子结束前把 r0-r7 全部恢复，校验和重算。

### v0.13 修的是什么（背景图一直不显示的真因）

v0.12 及更早的钩子是**用 DMA3 从卡带 SDRAM 读位图**的。可是卡带 SDRAM 挂在 CPLD
后面，固件自己从来没用 DMA 从这块区域**读**过数据 —— 渲染字体、读全局变量全是 CPU，
DMA 只用来**写** SDRAM。这条读路径在真机上拿不到数据，于是：

- 调色板（钩子里本来就是 CPU 搬的）→ 正常，所以菜单配色是对的；
- 位图（DMA 搬的）→ 一个字节都没进去，内容区还是原来的背景色。

这正好对上「图标能显示、背景图永远不显示」的现象。现在整段搬运都改成 CPU 拷贝，
只依赖「CPU 能读 SDRAM」这一点，而它已经被字体渲染在真机上证明过了。

另外，以前在「已经带背景图的固件」上换图（rebake）只覆盖位图和调色板、**完全不重写
钩子**，所以旧固件怎么换图都还是那个错的钩子。现在换图时钩子会一起重写，等于顺手
把旧固件升级掉。


调色板只用 20..239，避开 0..3（背景/logo）、16..19（FG/BG/FT/HI）、
240..244（游戏内菜单）和图标 bank，所以顶栏、底栏、图标、游戏内菜单颜色都不受影响。

## 自检

每次烘焙都会跑一遍指令级仿真（unicorn）：按真实拷贝表把 SDRAM 铺好、把载荷放进
EWRAM、从渲染点开始执行，然后比对

- VRAM `frame+0xF00` 起 34,560 字节是否和固件里的位图完全一致
- 调色板槽 20..239 是否完全一致
- 保护槽有没有被写
- 钩子是否干净返回到 `站点+补丁长度`
- 钩子全程没有碰 DMA3（DMA 计数必须是 0）

本次结果见下面「烘焙记录」。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wall", default="dist/bocchi-1536x921.png")
    ap.add_argument("--dim", type=float, default=0.35)
    ap.add_argument("--preset", type=int, default=0)
    ap.add_argument("--no-exe", action="store_true")
    ap.add_argument("--with-firmware", action="store_true",
                    help="同时烘焙并附带固件（默认不发固件，只发修改器）")
    ap.add_argument("--force", action="store_true", help="版本目录已存在时也继续（会覆盖）")
    a = ap.parse_args()
    app_only = not a.with_firmware

    ver = VER.VERSION
    out_dir = os.path.join(ROOT, "dist", "v" + ver)
    if os.path.exists(out_dir) and not a.force:
        raise SystemExit(
            "dist/v%s 已经存在——版本目录不覆盖，回溯靠它。\n"
            "要重打就先把 app/version.py 的 VERSION 加 0.01（或用 --force）。" % ver)
    os.makedirs(out_dir, exist_ok=True)
    fw_dir = os.path.join(out_dir, "固件")
    pv_dir = os.path.join(out_dir, "预览")
    as_dir = os.path.join(out_dir, "素材")
    for d in (pv_dir,):
        os.makedirs(d, exist_ok=True)
    if not app_only:
        for d in (fw_dir, as_dir):
            os.makedirs(d, exist_ok=True)

    rec = []
    ftable = []
    preview_src = None
    tmp_dir = os.path.join(ROOT, "build", "_rel")
    os.makedirs(tmp_dir, exist_ok=True)
    for i, (src, pat, note) in enumerate(DEFAULT_FW):
        src_p = os.path.join(ROOT, src)
        if not os.path.exists(src_p):
            print("跳过（没有源文件）:", src)
            continue
        name = pat.format(v="v" + ver)
        # 只发程序时烘到临时目录，只用来出预览，不进发布目录
        dst = os.path.join(fw_dir if not app_only else tmp_dir, name)
        lay, info, n = bake_one(src_p, dst, os.path.join(ROOT, a.wall), a.dim, a.preset)
        import verify_baked as V
        (layout, meta, site_rt, vram, page1, pal, err, trace, state,
         stopped) = V.run(bytearray(open(dst, "rb").read()))
        rom = open(dst, "rb").read()
        exp = rom[meta["a_rom"]:meta["a_rom"] + meta["a_len"]]
        exp += rom[meta["data_b_at"]:meta["data_b_at"] + meta["b_len"]]
        diff = sum(1 for k in range(FB.IMG_BYTES) if vram[k] != exp[k])
        exp_pal = rom[meta["pal_at"]:meta["pal_at"] + meta["pal_n"] * 2]
        got = pal[FB.PAL_BASE * 2:(FB.PAL_BASE + meta["pal_n"]) * 2]
        pd = sum(1 for k in range(len(exp_pal)) if exp_pal[k] != got[k])
        ok = (diff == 0 and pd == 0 and err is None and stopped
              and state["dma"] == 0 and not state["bad"])
        rec.append("%-34s %-12s 站点 0x%06X/%2dB 钩子 0x%08X（%dB）"
                   " 位图差异 %d 调色板差异 %d 返回 %s → %s"
                   % (name, lay.build, lay.patch_off, lay.patch_len,
                      info["hook_addr"], len(info["hook"]), diff, pd,
                      "OK" if stopped else repr(err), "通过" if ok else "**失败**"))
        ftable.append("| `固件/%s` | %s，%d 色，已烘焙背景图 |" % (name, note, n))
        print(rec[-1])
        if not ok:
            raise SystemExit("烘焙自检没通过，停止打包")
        if preview_src is None and name.endswith(".gba"):
            preview_src = dst

    # 预览
    import preview_baked as PB
    if preview_src:
        wall, meta = PB.baked_wall(bytearray(open(preview_src, "rb").read()))
        r = M.Renderer()
        r.wall = wall
        pal = M.PRESETS[a.preset][1]
        r.pal_ref = pal
        names = ["browser", "uisettings", "loadinfo", "loadpatch", "tools", "info"]
        sheet = Image.new("RGB", (M.W * 2, M.H * 6), "#000000")
        for i, nm in enumerate(names):
            shot = r.screen(nm, pal, "zh", 2).convert("RGB")
            sheet.paste(shot.resize((M.W * 2, M.H * 2), Image.NEAREST), (0, i * M.H * 2))
        sheet.save(os.path.join(pv_dir, "菜单预览.png"))
        wall.resize((FB.IMG_W * 2, FB.IMG_H * 2), Image.NEAREST).save(
            os.path.join(pv_dir, "固件里的背景图.png"))
        # 6 套主题各自长什么样（实机「界面设置 → 主题颜色」里切的 1..6）
        tw, th = M.W * 2, M.H * 2
        gap, lab = 10, 18
        sheet6 = Image.new("RGB", (3 * tw + 4 * gap, 2 * (th + lab) + 3 * gap),
                           (24, 24, 22))
        d6 = ImageDraw.Draw(sheet6)
        r6 = M.Renderer()
        r6.wall = None
        for i, (name, cols) in enumerate(M.PRESETS):
            shot = r6.screen("browser", cols, "zh", 2).convert("RGB")
            cx = gap + (i % 3) * (tw + gap)
            cy = gap + (i // 3) * (th + lab + gap)
            sheet6.paste(shot.resize((tw, th), Image.NEAREST), (cx, cy + lab))
            d6.rectangle([cx - 1, cy + lab - 1, cx + tw, cy + lab + th],
                         outline=(70, 70, 64))
            d6.text((cx + 2, cy + 3), "%d  %s" % (i + 1, name), fill=(228, 226, 214))
        sheet6.save(os.path.join(pv_dir, "6套主题.png"))

        # 6 套各带一套图标：同一屏、每套配色 + 那套图标（跟示例固件同一套色相旋转）
        from make_demo_6themes import hue_shift
        sheet6i = Image.new("RGB", (3 * tw + 4 * gap, 2 * (th + lab) + 3 * gap),
                            (24, 24, 22))
        d6i = ImageDraw.Draw(sheet6i)
        r7 = M.Renderer()
        icon_base = [ic.copy() for ic in r7.icons]
        try:
            for i, (name, cols) in enumerate(M.PRESETS):
                r7.icons = [hue_shift(ic, (256 // 6) * i) for ic in icon_base]
                shot = r7.screen("browser", cols, "zh", 2).convert("RGB")
                cx = gap + (i % 3) * (tw + gap)
                cy = gap + (i // 3) * (th + lab + gap)
                sheet6i.paste(shot.resize((tw, th), Image.NEAREST), (cx, cy + lab))
                d6i.rectangle([cx - 1, cy + lab - 1, cx + tw, cy + lab + th],
                              outline=(70, 70, 64))
                d6i.text((cx + 2, cy + 3), "%d  图标 %s" % (i + 1, name),
                         fill=(228, 226, 214))
        finally:
            r7.icons = icon_base
        sheet6i.save(os.path.join(pv_dir, "6套图标.png"))
    for src, dst in (("app-ui.png", "修改器界面.png"),
                     ("app-portrait.png", "裁切示意.png"),
                     ("app-icon-where.png", "图标位置.png"),
                     ("app-icon-tip.png", "图标悬停提示.png")):
        shot = os.path.join(ROOT, "out", "ui", src)
        if os.path.exists(shot):
            shutil.copy(shot, os.path.join(pv_dir, dst))

    if not app_only:
        shutil.copy(os.path.join(ROOT, a.wall), as_dir)
        extra = os.path.join(ROOT, "dist", "bocchi-1536x921.png")
        if os.path.exists(extra) and os.path.abspath(extra) != os.path.abspath(
                os.path.join(ROOT, a.wall)):
            shutil.copy(extra, as_dir)

    hooklen = len(info["hook"]) if rec else 100
    if app_only:
        fw_row = ""
        fw_section = """## 固件

这一版**只发修改器**，不附带 `.gba` / `.fw` / `.bin`。

用你自己的固件在修改器里注入：`选择固件…` → 调色/放背景图/换图标 →
`注入到固件…`，输出文件名会变成 `原名-themeN-wall.ext`，不会覆盖原文件。

- 第一次注入（原始固件）：会写入位图、调色板，并把钩子装进 EWRAM 载荷。
- 已经带背景图的固件：直接**替换**那张图，钩子同时按本版重写一遍（旧版本写进去的
  DMA 钩子会在这里被换掉）。
"""
    else:
        fw_row = "| `固件/` | 已经烘焙好背景图 + 配色的成品固件 |\n"
        fw_section = """## 固件怎么选

{fortable}

- `.gba`：2 MB 整片镜像，校验头里的 fw_size 是 0x1E0E00（≠ 文件大小），
  **只能用外部烧录器整片写入**，SuperFW 自带升级器会拒收。
- `.fw`：1.6 MB，fw_size 正好等于文件大小，**SuperFW 内置升级器能接受**。
""".format(fortable="\n".join(ftable))

    readme = README.format(
        ver=ver, when=__import__("time").strftime("%Y-%m-%d %H:%M"),
        codename=VER.CODENAME,
        changes="\n".join("- " + c for c in VER.CHANGES),
        fw_row=fw_row,
        fw_section=fw_section,
        hooklen=hooklen)
    readme += "\n## 烘焙记录\n\n```\n" + "\n".join(rec) + "\n```\n"
    open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8").write(readme)

    if not a.no_exe:
        print("打包 exe …")
        # 从 v0.13 起只发修改器本体：
        # 背景图写入 = 修改器的第 ② 步，单独再发一个 exe 只会多一条维护路径。
        build_exe(out_dir, "SuperFW-Theme-Editor-v" + ver)

    lines = []
    for base, _dirs, files in os.walk(out_dir):
        for f in sorted(files):
            if f == "SHA256SUMS.txt":
                continue
            p = os.path.join(base, f)
            rel = os.path.relpath(p, out_dir).replace("\\", "/")
            lines.append("%s  %s" % (sha256(p), rel))
    open(os.path.join(out_dir, "SHA256SUMS.txt"), "w", encoding="utf-8").write(
        "\n".join(lines) + "\n")
    print("\n发布目录 %s" % out_dir)
    for l in lines:
        print("  " + l)


if __name__ == "__main__":
    main()
