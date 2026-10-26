# -*- coding: utf-8 -*-
"""单独给 SD 主题编辑器打 exe。

    python tools/build_sdedit_exe.py [输出目录] [--onedir]
    python tools/build_sdedit_exe.py --onedir --fw-hash 78000001 --fw-date 2026-10-22

默认输出到 `dist/<版本>/`，版本号取自 `app/sdedit_ver.py`（唯一来源）。
走 package_release.build_exe（含 PyInstaller 前置检查和启动烟测，别绕过它）。

`--fw-hash/--fw-date` 覆盖「关于页」里那行固件标识：
默认读 `fw/build_meta.txt`（主线固件那次构建），发布分支固件时手动指定，
让编辑器预览和真机看到的一致（分支件括号里是 `78000001` 这种版本标识，
不是 git 短哈希）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "app"))

from package_release import build_exe           # noqa: E402
import sdedit_ver                              # noqa: E402

NAME = sdedit_ver.EXE_NAME


def write_build_info(hash_=None, date=None):
    """把固件那次的构建信息写进 app/build_info.py，让预览显示和真机一模一样的版本行。"""
    if hash_ is None or date is None:
        meta = os.path.join(ROOT, "fw", "build_meta.txt")
        info = {}
        if os.path.exists(meta):
            for line in open(meta, encoding="utf-8"):
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k] = v
        hash_ = hash_ if hash_ is not None else info.get("hash", "")
        date = date if date is not None else info.get("date", "")
    src = ("# -*- coding: utf-8 -*-\n"
           "# 由 tools/build_sdedit_exe.py 生成，请勿手改。\n"
           "# 数据来自编译固件时写下的 fw/build_meta.txt，或命令行 --fw-hash/--fw-date。\n"
           "GIT_HASH = %r\n"
           "BUILD_DATE = %r\n" % (hash_, date))
    out = os.path.join(ROOT, "app", "build_info.py")
    old = open(out, encoding="utf-8").read() if os.path.exists(out) else None
    if old != src:
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
    print("build_info:", hash_, date)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="给 SD 主题编辑器打 exe")
    ap.add_argument("dest", nargs="?", default=None,
                    help="输出目录（单文件版是放 exe 的目录，文件夹版就是这个文件夹本身）")
    ap.add_argument("--onedir", action="store_true",
                    help="打成文件夹形态：免解压，双击即开，不碰系统临时目录")
    ap.add_argument("--fw-hash", default=None,
                    help="关于页括号里的版本标识，如 78000001（默认取 fw/build_meta.txt）")
    ap.add_argument("--fw-date", default=None,
                    help="关于页第三行的构建日期，如 2026-10-22（默认取 fw/build_meta.txt）")
    ap.add_argument("--tmpdir", default=None,
                    help="单文件版的解压目录。给了就不再用系统 TEMP，"
                         "例如 %%LOCALAPPDATA%%\\SuperFW-Theme-Editor")
    a = ap.parse_args()

    if a.dest:
        dest = os.path.abspath(a.dest)
    elif a.onedir:
        dest = os.path.join(ROOT, "dist", sdedit_ver.DIST_DIR, "免解压版")
    else:
        dest = os.path.join(ROOT, "dist", sdedit_ver.DIST_DIR)
    if not a.onedir:
        os.makedirs(dest, exist_ok=True)

    extra = ["--runtime-tmpdir", a.tmpdir] if a.tmpdir else []
    write_build_info(a.fw_hash, a.fw_date)
    build_exe(dest, NAME, script=os.path.join(ROOT, "app", "sdedit.py"),
              onefile=not a.onedir, extra_args=extra)
    print("done:", os.path.join(dest, NAME + ".exe"))


if __name__ == "__main__":
    main()
