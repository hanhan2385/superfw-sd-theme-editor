# -*- coding: utf-8 -*-
"""把本地文件上传到已有 GitHub Release 的资产区（不重建 Release、不动正文）。

为什么需要：某些网络环境下 uploads.githubusercontent.com 走 curl 是通的，
而 gh CLI 的 keyring token 可能失效；本脚本和 push_to_github.py 一样
优先 GITHUB_TOKEN / GH_TOKEN，其次读 gh 的 hosts.yml。

用法：
    python tools/upload_release_assets.py <owner>/<repo> <tag> 文件1 [文件2 ...]

同名资产已存在时会先删再传（GitHub 不支持覆盖上传）。
"""

import json
import os
import subprocess
import sys
import urllib.request

API = "https://api.github.com"
UA = "superfw-sd-theme-editor-publisher"


def token():
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
    raise SystemExit("没有 token：先 `gh auth login -h github.com`，或设置 GITHUB_TOKEN")


def api(method, path, tok, payload=None, ctype="application/json", data=None):
    url = path if path.startswith("http") else API + path
    cmd = ["curl", "-sS", "--fail-with-body", "--max-time", "180",
           "-X", method, url,
           "-H", "Accept: application/vnd.github+json",
           "-H", "User-Agent: " + UA,
           "-H", "Authorization: Bearer " + tok]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        data = json.dumps(payload).encode("utf-8")
    elif data is not None:
        cmd += ["-H", "Content-Type: " + ctype, "--data-binary", "@-"]
    elif method in ("POST", "PATCH", "DELETE"):
        cmd += ["-H", "Content-Length: 0"]
    p = subprocess.run(cmd, input=data, capture_output=True)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        raise SystemExit("API 调用失败：%s\n%s" % (method + " " + url, out or p.stderr.decode("utf-8", "replace")))
    return json.loads(out) if out.strip() else {}


def main(argv):
    if len(argv) < 4:
        raise SystemExit(__doc__)
    repo, tag = argv[1], argv[2]
    files = argv[3:]
    tk = token()

    rel = api("GET", "/repos/%s/releases/tags/%s" % (repo, tag), tk)
    if "id" not in rel:
        raise SystemExit("找不到 Release %s：%s" % (tag, rel.get("message")))
    rid = rel["id"]
    have = {a["name"]: a["id"] for a in rel.get("assets", [])}
    print("Release %s（id %s），正文 %d 字，现有资产 %d 个"
          % (tag, rid, len(rel.get("body") or ""), len(have)))

    for f in files:
        name = os.path.basename(f).replace(" ", "")
        if name != os.path.basename(f):
            raise SystemExit("资产名不能带空格：%s" % f)
        if name in have:
            print("  删旧资产", name)
            api("DELETE", "/repos/%s/releases/assets/%d" % (repo, have[name]), tk)
        st = api("POST", "https://uploads.github.com/repos/%s/releases/%d/assets?name=%s"
                 % (repo, rid, name),
                 tk, data=open(f, "rb").read(),
                 ctype="application/octet-stream")
        if "state" not in st:
            raise SystemExit("上传失败 %s：%s" % (name, st))
        print("  已上传", name, st.get("size"), "字节", st.get("state"))
    print("完成：https://github.com/%s/releases/tag/%s" % (repo, tag))


if __name__ == "__main__":
    main(sys.argv)
