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

## 2. 推代码：走 API，不走 git push

仓库：<https://github.com/hanhan2385/superfw-sd-theme-editor>（默认分支 `main`）。

**这台机器 `git fetch` / `git push` 是坏的**：`git ls-remote` 能通（只发小包），
`git push --dry-run` 也能协商成功，但真正传 pack 时报
`fatal: fetch-pack: invalid index-pack output` —— 所以别指望普通 push。

推代码用 `tools/push_to_github.py`（Git Data API：blob → tree → commit → force 更新 ref）：

```bash
export PATH="/c/Program Files/GitHub CLI:$PATH"
export GITHUB_TOKEN="$(gh auth token)"
python tools/push_to_github.py hanhan2385/superfw-sd-theme-editor --branch main
```

- 只推 `git ls-files` 里的文件（`.gitignore` 已过滤 `dist/`、`.toolchain/`、`.venv314/`）
- **增量**：先拿远程 head 的 tree，内容没变的文件复用 sha，不再上传（第一次 337 个，
  之后一次通常只传十几二十个）
- 空仓库要先 `PUT README.md` 破 409，脚本已处理
- **它产生的是新提交**（`parent` = 远程 head），所以远程 sha 和本地 sha 永远不一样；
  内容一致就行，别拿 sha 对比

### 想让公开仓库只有一条提交（不暴露开发过程 / 版本号）

加 `--root`：生成没有父提交的根提交，旧提交从 ref 上摘掉后变成不可达，
仓库首页每个文件后面只显示这一条说明，`11 Commits` 会变回 `1 Commit`。

```bash
python tools/push_to_github.py hanhan2385/superfw-sd-theme-editor --branch main \
  --root --message "SuperFW SD Theme Editor"
```

**tag 也要一起挪**，否则旧提交顺着 tag 还摸得到：

```bash
gh api repos/hanhan2385/superfw-sd-theme-editor/git/refs/tags/v0.30 \
  -X PATCH -F sha=<新的 commitsha> -F force=true
```

挪 tag 不会动 Release（资产和说明都在），页面上 tag 指向新提交。
副作用：本地 git 历史照旧详细，但和远程从此两套 —— 以后推的是「远程 head 的子树 + 本地工作区内容」，
别再指望 `git log` 能和 GitHub 对上。

**本仓库的固定做法：每次都用 `--root` 推**，远程永远只有一条 `SuperFW SD Theme Editor`，
推完顺手把 tag 挪到新提交。

## 3. 发 Release

`dist/` 不进 git —— 21 MB 的 exe 每次打包都变，进 git history 会越来越臃肿。

用 `gh release create`（走 `api.github.com` + `uploads.github.com`，这两条路是通的）。
**`--target` 要填推送脚本打印的 commitsha，不是本地 sha**；资产只挂 exe + 正式固件：

```bash
gh release create v0.30 --repo hanhan2385/superfw-sd-theme-editor \
  --target <push 脚本输出的 commitsha> \
  --title "SuperFW SD Theme Editor v0.30（自定义 Logo / 主题包 v6）" \
  --notes-file /tmp/rel-v030.md \
  dist/v0.30/SuperFW-SD-Theme-Editor-v0.30.exe \
  dist/v0.30/SuperFW-Chis-SDTheme.fw
```

**Release 正文写给人看的用法，不写开发过程**：怎么用（5 步）+ 图片规格表就够了，
版本历史、内部修复细节留在 `dist/<ver>/说明.md`。

**坑：资产名别用中文。** `说明.md` 传上去会变成 `default.md`（GitHub 拒绝非 ASCII 名后
gh 的兜底名）。当初传错了就 `gh release delete-asset <tag> default.md --repo ... --yes`。

删资产：

```bash
gh release delete-asset v0.30 <文件名> --repo hanhan2385/superfw-sd-theme-editor --yes
```

**改 Release 正文**用 `gh api` PATCH，注意路径要用 `id` 不能用 `tags/<tag>`（用 tag 会 404）：

```bash
RID=$(gh api repos/<owner>/<repo>/releases/tags/v0.30 --jq .id)
gh api "repos/<owner>/<repo>/releases/$RID" -X PATCH -F body=@/tmp/newbody.md
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
