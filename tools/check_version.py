# -*- coding: utf-8 -*-
"""版本号一致性检查。

    python tools/check_version.py

版本号现在只有 `app/sdedit_ver.py` 一处定义，其它地方（窗口标题、exe 名、
输出目录、关于页）都从它取。这个脚本负责抓"有人又把版本写回去了"：

 1. `app/sdedit.py` / `tools/build_sdedit_exe.py` 里不许再出现 `v0.3x` 这类字面量；
 2. `app/build_info.py` 得存在（关于页要用）；
 3. `dist/<DIST_DIR>/` 的目录名要和当前版本一致（存在别的 v0.xx 只提示，不报错）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

import sdedit_ver as V                     # noqa: E402

# 三种历史上的写死形态：
#   1. 裸版本字符串        APP_VER = "0.30"
#   2. exe 名里的版本      NAME = "SuperFW-SD-Theme-Editor-v0.30"
#   3. 路径里的版本        os.path.join(ROOT, "dist", "v0.30")
# 故意不用 `v0\.\d\d` 通配：正文里"v0.18 起…""v0.30+ 能读"这类说明会被误报。
PATTERNS = [
    re.compile(r"""["']\s*v?0\.\d{1,2}\s*["']"""),
    re.compile(r"""-v0\.\d{1,2}\b"""),
    re.compile(r"""dist[/\\"]\s*,?\s*["']?v0\.\d{1,2}"""),
]

BAD, WARN = [], []


def scan(relpath, allow=()):
    p = os.path.join(ROOT, relpath)
    if not os.path.exists(p):
        BAD.append("%s 不存在" % relpath)
        return
    for i, line in enumerate(open(p, encoding="utf-8"), 1):
        if any(tok in line for tok in allow):
            continue
        if "sdedit_ver" in line or line.lstrip().startswith("#"):
            continue
        for pat in PATTERNS:
            m = pat.search(line)
            if m:
                BAD.append("%s:%d 又写死了版本号 %r" % (relpath, i, m.group(0).strip()))
                break


def main():
    print("当前版本 %s（%s）" % (V.APP_VER, "app/sdedit_ver.py"))
    print("  标题      %s" % V.APP_TITLE)
    print("  exe 名    %s.exe" % V.EXE_NAME)
    print("  输出目录  dist/%s/" % V.DIST_DIR)

    scan("app/sdedit.py")
    scan("tools/build_sdedit_exe.py")

    if not os.path.exists(os.path.join(ROOT, "app", "build_info.py")):
        BAD.append("app/build_info.py 不存在（关于页要读它）")

    dist = os.path.join(ROOT, "dist")
    if os.path.isdir(dist):
        vers = [n for n in os.listdir(dist)
                if re.fullmatch(r"v\d+\.\d+", n)]
        if V.DIST_DIR not in vers:
            WARN.append("dist/%s/ 还不存在（打完包才有）" % V.DIST_DIR)
        others = sorted(v for v in vers if v != V.DIST_DIR)
        if others:
            WARN.append("dist 里还有其它版本：%s" % ", ".join(others))

    if BAD:
        print("\n不一致：")
        for b in BAD:
            print("  x " + b)
        return 1
    for w in WARN:
        print("\n提示：" + w)
    print("\n版本号来源唯一，没有写死的副本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
