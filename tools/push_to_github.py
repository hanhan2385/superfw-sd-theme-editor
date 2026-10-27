# -*- coding: utf-8 -*-
"""把本地仓库通过 GitHub API 推进远程（不依赖 git push）。

为什么需要这个：某些网络环境下 `github.com` 主站和 git 协议被墙
（`git push` 报 CONNECT tunnel 502），但 `api.github.com` 是通的。
于是走 Git Data API：blob → tree → commit → 更新 ref，效果和 push 一样，
产生的是一次真实提交。

用法（需要 gh 已登录，或设置 GITHUB_TOKEN）：
    python tools/push_to_github.py <owner>/<repo> [--branch main] [--dry-run]
                                   [--message "提交说明"] [--root]

`--root` 生成没有父提交的根提交，等于把远程历史压成一条（旧提交变成不可达）。
适合「不想让公开仓库的提交列表暴露版本号 / 开发过程」的场合 —— 记得顺手把 tag
也 PATCH 到新提交，否则旧提交还能从 tag 链过去。

只推 `git ls-files` 里列出的文件（即 .gitignore 已过滤），不会把 2.3G 工具链送上去。
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"
UA = "superfw-sd-theme-editor-pusher"


def token():
    """优先 GITHUB_TOKEN，其次从 gh 的 hosts.yml 取。"""
    tk = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tk:
        return tk.strip()
    cfg = os.path.join(os.environ.get("APPDATA", ""), "GitHub CLI", "hosts.yml")
    if not os.path.exists(cfg):
        cfg = os.path.expanduser("~/.config/gh/hosts.yml")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s.startswith("oauth_token:"):
                    return s.split(":", 1)[1].strip().strip("'\"")
    raise SystemExit("没有 token：先 `gh auth login --with-token`，或设置 GITHUB_TOKEN")


def call(method, path, payload=None, tok=None, raw=False):
    url = path if path.startswith("http") else API + path
    cmd = ["curl", "-sS", "--fail-with-body", "--max-time", "120",
           "-X", method, url,
           "-H", "Accept: application/vnd.github+json",
           "-H", "User-Agent: " + UA,
           "-H", "Authorization: Bearer " + (tok or token())]
    data = None
    if payload is not None:
        # 走 stdin 传 JSON：仓库 tree 的 JSON 很大，Windows 命令行会超长。
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        data = json.dumps(payload).encode("utf-8")
    # Python 自带 urllib 在本机到 api.github.com 会 SSL EOF；curl 走系统 TLS 正常。
    r = subprocess.run(cmd, input=data, capture_output=True)
    if r.returncode != 0:
        detail = (r.stdout or r.stderr or b"").decode("utf-8", "replace")[:400]
        raise SystemExit("%s %s → curl %s\n%s" % (method, url, r.returncode, detail))
    body = r.stdout
    return body if raw else (json.loads(body) if body else None)


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    names = [n for n in out.split(b"\x00") if n]
    return [n.decode("utf-8") for n in names]


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    repo = argv[0]
    branch = "main"
    dry = "--dry-run" in argv
    # --root：生成一个没有父提交的根提交（等于把远程历史压成一次提交，
    # 旧提交从 ref 上摘掉后就变成不可达，仓库页只显示这一条）。
    root = "--root" in argv
    if "--branch" in argv:
        branch = argv[argv.index("--branch") + 1]

    files = tracked_files()
    print("文件 %d 个，目标 %s（分支 %s）" % (len(files), repo, branch))
    if dry:
        total = sum(os.path.getsize(os.path.join(ROOT, f)) for f in files
                    if os.path.isfile(os.path.join(ROOT, f)))
        print("总大小 %.1f MB，dry-run 到此为止" % (total / 1048576.0))
        return 0

    tok = token()

    # 1) 查仓库是否存在，不存在就建（公开仓库）
    owner, name = repo.split("/")
    try:
        call("GET", "/repos/%s" % repo, tok=tok)
        print("仓库已存在")
    except SystemExit:
        print("创建仓库 %s ..." % repo)
        call("POST", "/user/repos",
             {"name": name, "description":
              "给 GBA 烧录卡固件 SuperFW 做 SD 卡主题包的工具",
              "private": False, "has_issues": True, "has_wiki": False,
              "auto_init": False}, tok=tok)

    # 1.5) 空仓库不能建 blob（409 Git Repository is empty），先用 Contents API
    #      落一个文件把 git 对象库建起来；后面 force 更新 ref 覆盖掉，历史里不留痕迹。
    try:
        call("GET", "/repos/%s/git/ref/heads/%s" % (repo, branch), tok=tok)
    except SystemExit:
        print("空仓库，先落一份 README 建立初始提交 ...")
        with open(os.path.join(ROOT, "README.md"), "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        call("PUT", "/repos/%s/contents/README.md" % repo,
             {"message": "init", "content": content}, tok=tok)

    # 2) 增量：先拿远程 head 的 tree，内容没变的文件直接复用它的 sha，不再上传
    head_sha, remote_map = None, {}
    try:
        head_sha = call("GET", "/repos/%s/git/ref/heads/%s" % (repo, branch),
                        tok=tok)["object"]["sha"]
        tree = call("GET", "/repos/%s/git/trees/%s?recursive=1" % (repo, head_sha), tok=tok)
        remote_map = {e["path"]: e["sha"] for e in tree.get("tree", [])
                      if e["type"] == "blob"}
        print("远程已有 %d 个文件，做增量比对" % len(remote_map))
    except SystemExit:
        print("远程为空，走全量")

    print("上传 blob ...")
    entries = []
    fresh = reused = 0
    for i, rel in enumerate(files, 1):
        full = os.path.join(ROOT, rel)
        local_sha = subprocess.run(
            ["git", "hash-object", "--no-filters", "--", rel], cwd=ROOT,
            capture_output=True, text=True).stdout.strip()
        mode = "100755" if os.access(full, os.X_OK) else "100644"
        if remote_map.get(rel) == local_sha:
            entries.append({"path": rel, "mode": mode, "type": "blob",
                            "sha": local_sha})
            reused += 1
            continue
        with open(full, "rb") as fh:
            data = fh.read()
        try:
            blob = call("POST", "/repos/%s/git/blobs" % repo,
                        {"content": base64.b64encode(data).decode("ascii"),
                         "encoding": "base64"}, tok=tok)
        except SystemExit as e:
            raise SystemExit("文件 %s 上传失败：%s" % (rel, e))
        entries.append({"path": rel, "mode": mode, "type": "blob", "sha": blob["sha"]})
        fresh += 1
        if fresh % 40 == 0:
            print("  已传 %d 个" % fresh)
    print("  新上传 %d 个，复用 %d 个" % (fresh, reused))

    # 3) tree
    print("创建 tree ...")
    tree = call("POST", "/repos/%s/git/trees" % repo,
                {"tree": entries}, tok=tok)

    # 4) commit（不带 parent，作为首个提交）
    if "--message" in argv:
        msg = argv[argv.index("--message") + 1]
    else:
        msg = subprocess.run(["git", "log", "-1", "--pretty=%B"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip() or "初始提交"
    print("创建 commit ...")
    payload = {"message": msg, "tree": tree["sha"]}
    if head_sha and not root:
        payload["parents"] = [head_sha]
    elif root:
        print("  --root：不带 parent，远程历史压成单条提交")
    commit = call("POST", "/repos/%s/git/commits" % repo, payload, tok=tok)

    # 5) 建/更新分支引用；仓库刚建是空的，ref 不存在要先 create
    ref_path = "/repos/%s/git/refs" % repo
    try:
        call("PATCH", ref_path + "/heads/" + branch,
             {"sha": commit["sha"], "force": True}, tok=tok)
        print("更新分支 %s" % branch)
    except SystemExit:
        call("POST", ref_path, {"ref": "refs/heads/" + branch,
                                "sha": commit["sha"]}, tok=tok)
        print("创建分支 %s" % branch)

    print("\n完成：https://github.com/%s/tree/%s" % (repo, branch))
    print("commitsha=%s" % commit["sha"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
