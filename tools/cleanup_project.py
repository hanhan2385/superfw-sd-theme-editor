# -*- coding: utf-8 -*-
"""项目目录清理与归档（v0.21 实机验证通过后）。

原则：
  1. 历史发布包、路线 A 产物、调试截图 —— 移动归档，不删。
  2. 可重建的构建/仿真中间产物 —— 直接删除（PyInstaller 缓存、mGBA 帧输出）。
  3. 只动本脚本明确列出的路径，其余一律不碰。

用法：
  python tools/cleanup_project.py            # 只打印计划（dry-run）
  python tools/cleanup_project.py --apply    # 真正执行
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(ROOT, "archive", "2026-10-20")

# 直接删除：可重建的中间产物
DELETE = [
    "build/_rel",                    # package_release.py 的 PyInstaller workpath（~722M）
    "build/pyi",
    "build/SuperFW-Theme-Editor",
    "build/_mgba",
    "build/_inj",
    "out/mgba",                      # mGBA 仿真帧
    "out/regress",
    "out/diag",
    "out/img2gba-shell-test",
]

# 移动归档：旧发布包
RELEASES = ["dist/v0.%02d" % i for i in range(1, 19)]     # v0.01 .. v0.18

# 移动归档：路线 A（烘焙注入）遗留
ROUTE_A = [
    "dist/adapted-BROKEN",
    "dist/safe",
    "dist/safe-cn",
    "dist/app-injected.bin",
    "dist/superfw-chis-r75-胆大不怕死的来6-CN.bin",
    "dist/superfw-chis-r75-胆大不怕死的来6-CN-Wall.bin",
    "dist/superfw-chis-r75-胆大不怕死的来6-CN-Aurora.bin",
    "dist/SuperFW-SDTheme-test1.zip",
    "dist/SuperFW-Theme-Editor.exe",     # v0.13 时代的单 exe
    "dist/wallpaper",
    "dist/verify",
    "dist/icons",
    "dist/bocchi-1536x921.png",
    "dist/bocchi-wallpaper-240x144.png",
    "dist/sample-wallpaper.png",
    "dist/app-preview-about.png",
    "dist/app-preview-browser.png",
    "dist/app-preview-dim.png",
    "dist/ui-shot.png",
    "dist/ui-shot-left.png",
    "dist/wallpaper-emulated.png",
    "dist/walltest-preview.png",
    "dist_new",                          # package_release.py 的输出目录（空）
]

# v0.21 的关键截图：留在 dist/v0.21/screenshots/
KEEP_SHOTS = [
    "final_ui.png", "final_indep.png", "final_fallback.png",
    "real_all2.png", "real_35.png", "p3456.png",
    "theme_test_sets.png", "theme_test_sets2.png",
    "indep_proof.png", "fallback_ui.png",
]


def rel(p):
    return os.path.relpath(p, ROOT)


def plan():
    print("=== 会删除（可重建的中间产物）===")
    total = 0
    for p in DELETE:
        full = os.path.join(ROOT, p)
        if os.path.exists(full):
            sz = _size(full)
            total += sz
            print("  - %-34s %8s" % (p, _human(sz)))
        else:
            print("  ? %-34s 不存在，跳过" % p)
    print("  合计释放约 %s" % _human(total))

    print("\n=== 会归档到 archive/2026-10-20/ ===")
    for group, items in (("releases", RELEASES), ("route-a", ROUTE_A)):
        for p in items:
            full = os.path.join(ROOT, p)
            if os.path.exists(full):
                print("  %-12s %-50s %8s" % (group, p, _human(_size(full))))
            else:
                print("  %-12s %-50s 不存在，跳过" % (group, p))

    print("\n=== 保留 ===")
    print("  dist/v0.19 v0.20 v0.21（最近三代发布）")
    print("  dist/v0.21/screenshots/（v0.21 关键截图 %d 张）" % len(KEEP_SHOTS))
    print("  其余 out/*.png 移到 archive/2026-10-20/debug/screenshots/")


def _size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    n = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n


def _human(n):
    for u in ("B", "K", "M", "G"):
        if n < 1024:
            return "%.1f%s" % (n, u) if u != "B" else "%d%s" % (n, u)
        n /= 1024.0
    return "%.1fT" % n


def move(src, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        print("  ! 目标已存在，跳过：%s" % rel(dst))
        return
    shutil.move(src, dst)
    print("  -> %s" % rel(dst))


def apply():
    print("=== 删除中间产物 ===")
    for p in DELETE:
        full = os.path.join(ROOT, p)
        if not os.path.exists(full):
            print("  ? %s 不存在" % p)
            continue
        shutil.rmtree(full, ignore_errors=False) if os.path.isdir(full) else os.remove(full)
        print("  x %s" % p)

    print("\n=== 归档旧发布包 ===")
    for p in RELEASES:
        full = os.path.join(ROOT, p)
        if os.path.exists(full):
            move(full, os.path.join(ARCH, "releases"))

    print("\n=== 归档路线 A 产物 ===")
    for p in ROUTE_A:
        full = os.path.join(ROOT, p)
        if os.path.exists(full):
            move(full, os.path.join(ARCH, "route-a"))

    print("\n=== 收拢截图 ===")
    keep_dir = os.path.join(ROOT, "dist", "v0.21", "screenshots")
    os.makedirs(keep_dir, exist_ok=True)
    for name in KEEP_SHOTS:
        src = os.path.join(ROOT, "out", name)
        if os.path.exists(src):
            dst = os.path.join(keep_dir, name)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                print("  keep %s" % name)

    shots = os.path.join(ARCH, "debug", "screenshots")
    n = 0
    for name in sorted(os.listdir(os.path.join(ROOT, "out"))):
        if name.lower().endswith(".png"):
            move(os.path.join(ROOT, "out", name), shots)
            n += 1
    print("  归档旧截图 %d 张" % n)

    misc = os.path.join(ARCH, "debug", "misc")
    for name in ("demo", "sdtheme", "photo", "img2gba-demo", "probe3.py"):
        src = os.path.join(ROOT, "out", name)
        if os.path.exists(src):
            move(src, misc)

    print("\n=== 剩余 out/ ===")
    for name in sorted(os.listdir(os.path.join(ROOT, "out"))):
        print("  ", name)


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply()
    else:
        plan()
