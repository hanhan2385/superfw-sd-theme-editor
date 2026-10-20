# -*- coding: utf-8 -*-
"""单独给 SD 主题编辑器打 exe。

    python tools/build_sdedit_exe.py [输出目录]

默认输出到 dist/v0.21/。走 package_release.build_exe（含 PyInstaller 前置检查
和启动烟测，别绕过它）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from package_release import build_exe           # noqa: E402

NAME = "SuperFW-SD-Theme-Editor-v0.21"


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "v0.21")
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    build_exe(dest, NAME, script=os.path.join(ROOT, "app", "sdedit.py"))
    print("done:", os.path.join(dest, NAME + ".exe"))


if __name__ == "__main__":
    main()
