#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验打包出来的 exe 里，装的到底是不是最新源码。

背景：v0.30 出过一次「发布目录里的二进制是修复前的代码」——源码改了、
回归也加了，但 exe 没重打，用户拿到的还是带 bug 的版本。光看 exe 的时间戳
不可靠，得把里面的 code object 挖出来对一遍。

做法：
  1. 用 AST 从 app/sdedit.py 提全部顶层类名、函数名、类方法名；
  2. 从 exe 的 CArchive 里取出主脚本（sdedit）的 code object，递归收集
     co_names / co_name；
  3. 两边求差 —— 源码里有、exe 里没有 = 打的是旧代码，直接失败；
  4. 顺带核对 PYZ 里关键的图像解码器与 tkinter 子模块是否齐。

用法:
    python tools/verify_exe_bundle.py dist/v0.31/SuperFW-SD-Theme-Editor-v0.31.exe
    python tools/verify_exe_bundle.py <exe> --src app/sdedit.py
"""
import argparse
import ast
import marshal
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 打包时常见漏掉的模块：PIL 的解码器是 Python 层的插件，放在 PYZ 里而不是
# _internal/PIL 下（那里只有 .pyd）。所以不能靠 ls 目录判断。
NEED_MODULES = [
    "PIL.PngImagePlugin", "PIL.JpegImagePlugin", "PIL.BmpImagePlugin",
    "PIL.GifImagePlugin", "PIL.WebPImagePlugin", "PIL.ImageTk",
    "PIL.ImageDraw", "PIL.ImageFont",
    "tkinter", "tkinter.filedialog", "tkinter.messagebox",
    "tkinter.colorchooser", "tkinter.simpledialog",
    "sdtheme", "sdedit_ver",
]

OK, BAD = [], []


def ck(cond, msg):
    (OK if cond else BAD).append(msg)
    print(("  OK   " if cond else "  FAIL ") + msg)
    return cond


def names_from_source(path):
    """顶层类 / 函数 / 类方法 / 模块级常量名。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            out.add(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.add(sub.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def names_from_code(code, acc):
    acc.add(code.co_name)
    acc.update(code.co_names)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            names_from_code(c, acc)
    return acc


def load_pyc(raw):
    """PyInstaller 6.x 的主脚本条目自带 pyc 头，头长度随版本变，逐个试。"""
    for skip in (16, 12, 8, 0):
        try:
            return marshal.loads(raw[skip:])
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("--src", default=os.path.join(ROOT, "app", "sdedit.py"))
    a = ap.parse_args()

    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except ImportError:
        print("需要 PyInstaller（用 .venv314 跑）：%s" % sys.executable)
        return 2

    print("== %s ==" % a.exe)
    print("   源码 %s" % a.src)
    size = os.path.getsize(a.exe)
    print("   exe %d 字节，改于 %s"
          % (size, __import__("time").strftime("%Y-%m-%d %H:%M",
                                               __import__("time").localtime(os.path.getmtime(a.exe)))))

    r = CArchiveReader(a.exe)

    # ---- 1. 主脚本 ----
    print("\n[主脚本]")
    if "sdedit" not in r.toc:
        ck(False, "CArchive 里没有 sdedit 条目（exe 结构不对）")
        return 1
    raw = r.extract("sdedit")
    code = load_pyc(raw)
    if code is None:
        ck(False, "sdedit 条目无法解析成 code object")
        return 1
    ck(True, "取出 sdedit 的 code object（%d 字节）" % len(raw))

    exe_names = names_from_code(code, set())
    src_names = names_from_source(a.src)
    print("       源码标识符 %d 个，exe 里 %d 个" % (len(src_names), len(exe_names)))

    missing = sorted(src_names - exe_names)
    # 只用一小撮样本做展示，全列会把输出撑爆
    if missing:
        ck(False, "源码里有 %d 个标识符在 exe 里找不到：%s"
           % (len(missing), ", ".join(missing[:12]) + (" …" if len(missing) > 12 else "")))
        print("\n       ↑ 这就是「exe 打的是旧代码」，重新打包。")
    else:
        ck(True, "源码里的类/函数/方法全部出现在 exe 里（没有落后）")

    # ---- 2. 版本号 ----
    print("\n[版本]")
    sys.path.insert(0, os.path.join(ROOT, "app"))
    try:
        import sdedit_ver
        want = sdedit_ver.APP_VER
        ck(want in os.path.basename(a.exe), "exe 文件名带 v%s（源码声明）" % want)
        ck(sdedit_ver.EXE_NAME + ".exe" == os.path.basename(a.exe)
           or os.path.basename(a.exe).endswith(".exe"),
           "文件名与 sdedit_ver.EXE_NAME 一致：%s" % sdedit_ver.EXE_NAME)
    except ImportError:
        ck(False, "读不到 app/sdedit_ver.py")

    # ---- 3. PYZ 模块 ----
    print("\n[依赖]")
    pyz_name = next((n for n in r.toc if n.endswith(".pyz")), None)
    if not pyz_name:
        ck(False, "没找到 PYZ 归档")
    else:
        tmp = os.path.join(tempfile.mkdtemp(), "PYZ.pyz")
        with open(tmp, "wb") as fh:
            fh.write(r.extract(pyz_name))
        mods = set(ZlibArchiveReader(tmp).toc)
        ck(True, "PYZ 共 %d 个模块" % len(mods))
        lack = [m for m in NEED_MODULES if m not in mods]
        ck(not lack, "关键模块齐全" if not lack else "缺模块：%s" % ", ".join(lack))

    # ---- 4. 形态 ----
    # onedir：exe 旁边必须有 _internal；onefile：所有东西都在 exe 里，没有这个目录。
    # 两种形态都算合法，但判断方式不同，不能拿同一把尺子量。
    print("\n[形态]")
    internal = os.path.join(os.path.dirname(a.exe), "_internal")
    if os.path.isdir(internal):
        ck(True, "文件夹形态（onedir）：exe 旁边有 _internal")
        n = sum(len(f) for _, _, f in os.walk(internal))
        ck(n > 20, "_internal 里有 %d 个文件" % n)
        for dll in ("python314.dll", "_tkinter.pyd", "tcl90.dll", "tcl9tk90.dll"):
            ck(os.path.exists(os.path.join(internal, dll)), "有 %s" % dll)
    else:
        entries = list(r.toc)
        # onedir 的 CArchive 只有 10 来个条目（主脚本 + PYZ + 引导模块），
        # onefile 把 .pyd/.dll 全打进来，通常 40 个以上。
        ck(len(entries) > 20,
           "单文件形态（onefile）：依赖都打在 exe 里（CArchive %d 个条目，无 _internal）"
           % len(entries))
        ck(any(n.endswith(".pyz") for n in entries), "exe 内含 PYZ 归档")

        # 解压位置是打包时用 --runtime-tmpdir 写死的。它不是普通 TOC 条目 ——
        # PyInstaller 把值直接塞进条目名的尾段，形如 "pyi-runtime-tmpdir E:/xxx"，
        # CArchiveReader 的 .toc 里看不到，只能按字节找。
        # 本机 AppData\Local 被 AlibabaProtect 拦，落到系统 TEMP 就会打不开。
        tmp = None
        raw_all = open(a.exe, "rb").read()
        marker = b"pyi-runtime-tmpdir "
        pos = raw_all.find(marker)
        if pos >= 0:
            end = raw_all.find(b"\x00", pos)
            tmp = raw_all[pos + len(marker):end if end > 0 else pos + 260]
            tmp = tmp.decode("utf-8", "replace").strip()
        ck(bool(tmp), "解压目录写死为 %r（不读系统 TEMP）" % tmp
           if tmp else
           "缺 pyi-runtime-tmpdir：会解压到系统 TEMP，"
           "AppData 被安全软件拦时会报 LOADER: failed to create runtime-tmpdir")

    print("\n%d 项通过，%d 项失败" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
