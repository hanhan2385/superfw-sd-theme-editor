# 开发与构建

## 从源码运行

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python app/sdedit.py
```

需要 Python 3.9 以上（开发和测试用的是 3.14），并带 tkinter —— 官方 Windows 安装包自带。

## 编译固件

`fw/` 是主线固件源码（上游 SuperFW + SD 主题支持）；**发布用的分支固件源码在
`firmware-src/`**（原 `ref/superfw-fix` 的公开副本，也是 Release 的 GPL 对应源码）。

主线固件需要 ARM 交叉工具链：

```bash
bash tools/build_fw.sh chis       # 正式版
bash tools/build_fw.sh chis 1     # 诊断版：屏幕底部显示各阶段加载耗时
```

发布固件用分支源码编译（同样需要 `arm-none-eabi-gcc`，见 `firmware-src/README.md`）：

```bash
bash firmware-src/build_chis.sh
```

本地开发时 `ref/superfw-fix/build_chis.sh` 与它一致。

产物是 `fw/superfw.gba`。**改扩展名为 `.fw` 才能走固件升级流程** —— `.gba` 会被当游戏跑。

主题文件版本要和固件匹配：v3 的页面图必须配 v0.21 及以后的固件，更老的固件读到 v3 会静默跳过整份主题。

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

## 目录

```
app/      编辑器与格式实现
  sdedit.py     主程序（界面 + 预览渲染）
  sdtheme.py    theme.bin 读写
  gbafont.py    点阵字库渲染（预览用）
  wallpaper.py / fwbake.py / gbacvt.py   图像与固件相关工具
fw/       主线固件源码（基于 SuperFW，加 SD 主题支持）
firmware-src/  发布固件源码（分支，GPL 对应源码）
tools/    构建、打包、自检脚本
docs/     设计文档与截图
```

## 推送到 GitHub

某些网络环境下 `github.com` 主站不通（`git push` 报 CONNECT tunnel 502），但 `api.github.com` 可用。
这种情况下用 Git Data API 推送：

```bash
GITHUB_TOKEN=$(gh auth token) python tools/push_to_github.py <owner>/<repo> --message "提交说明"
```

脚本会对比远程 tree 的 sha，只上传变动的文件，几百个文件通常几秒完成。
