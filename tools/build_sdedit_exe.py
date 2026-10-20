# -*- coding: utf-8 -*-
"""单独给 SD 主题编辑器打 exe。

    python tools/build_sdedit_exe.py [输出目录]

默认输出到 dist/v0.30/。走 package_release.build_exe（含 PyInstaller 前置检查
和启动烟测，别绕过它）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from package_release import build_exe           # noqa: E402

NAME = "SuperFW-SD-Theme-Editor-v0.30"


def write_build_info():
    """把固件那次的构建信息写进 app/build_info.py，让预览显示和真机一模一样的版本行。"""
    meta = os.path.join(ROOT, "fw", "build_meta.txt")
    info = {}
    if os.path.exists(meta):
        for line in open(meta, encoding="utf-8"):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                info[k] = v
    src = ("# -*- coding: utf-8 -*-\n"
           "# 由 tools/build_sdedit_exe.py 生成，请勿手改。\n"
           "# 数据来自编译固件时写下的 fw/build_meta.txt。\n"
           "GIT_HASH = %r\n"
           "BUILD_DATE = %r\n" % (info.get("hash", ""), info.get("date", "")))
    out = os.path.join(ROOT, "app", "build_info.py")
    old = open(out, encoding="utf-8").read() if os.path.exists(out) else None
    if old != src:
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
    print("build_info:", info.get("hash"), info.get("date"))


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "v0.30")
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    write_build_info()
    build_exe(dest, NAME, script=os.path.join(ROOT, "app", "sdedit.py"))
    print("done:", os.path.join(dest, NAME + ".exe"))


if __name__ == "__main__":
    main()
