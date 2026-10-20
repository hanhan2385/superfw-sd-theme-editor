# 发布到 GitHub 的步骤

仓库已经 `git init` 并做好了第一个提交（335 个文件，约 15 MB），`.gitignore` 已排除工具链、虚拟环境、原始固件、归档和 `dist/`。

## 1. 先把提交身份改成自己的

当前仓库本地配置是：

```bash
git config user.name "小米粥不糊"
git config user.email "xiaomizhoubuhu@users.noreply.github.com"
```

邮箱要是你 GitHub 账号绑定的那个（或 GitHub 的 noreply 地址），否则提交不会关联到你的账号。改完重写这次提交：

```bash
git config user.email "你的邮箱"
git commit --amend --reset-author --no-edit
```

## 2. 在 GitHub 建仓库，然后推

新建一个空仓库（**不要**勾选初始化 README / .gitignore / LICENSE，避免冲突），然后：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

## 3. 把 exe 和固件传到 Release

`dist/` 不进 git —— 21 MB 的 exe 每次打包都变，进 git history 会越来越臃肿。

在仓库页面 `Releases → Draft a new release`，tag 用 `v0.21`，把这几样拖进去：

```
dist/v0.21/SuperFW-SD-Theme-Editor-v0.21.exe
dist/v0.21/SuperFW-Chis-SDTheme.fw
dist/v0.21/SuperFW-Chis-SDTheme-Diag.fw
dist/v0.21/theme-test.bin          （可选：测试用主题包）
dist/v0.21/SHA256SUMS.txt
```

## 4. 许可提醒

项目按 **GPL-3.0** 发布（`LICENSE` 已放在根目录）。

原因不只是"选了 GPL"：`fw/` 是 [SuperFW](https://superfw.davidgf.net/) 的衍生代码，原作者 David Guillen Fandos 的授权就是 GPL-3.0-or-later。**分发 exe 或固件二进制时，必须同时提供对应源码** —— 现在源码就在同一个仓库里，满足这条。

如果以后想换许可证，`fw/` 部分换不掉，只能把固件拆成独立仓库。

## 5. 提交前自检

```bash
python app/sdedit.py --selftest
python tools/test_robustness.py
python tools/test_e2e_theme.py
```

`tools/test_robustness.py` 里有一条硬性约束写在注释里了：**测试脚本只许往系统临时目录写文件**。曾经因为拿仓库里的 `app/sdedit.py` 当保存目标，把源码覆盖成了 theme.bin。
