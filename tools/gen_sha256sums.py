# -*- coding: utf-8 -*-
"""生成 / 校验发布目录的 SHA256SUMS.txt。

    python tools/gen_sha256sums.py dist/v0.31            # 生成
    python tools/gen_sha256sums.py dist/v0.31 --check     # 校验

为什么单独写一个：以前是临时脚本，出过两次事故 ——
  1. 目录名被截断（`分支固件-` vs `分支固件-superfw-fix`），`os.path.exists` 返回
     False 后被静默 skip，清单少了 5 条也没人发现。现在**任何一个文件不可读就整体
     拒绝写入**，绝不"少写一条继续"。
  2. 读 r78 那份清单时按 `utf-8` 读，首行 BOM 变成 `\\ufeff462deceb` 必然误报不符。
     所以写不带 BOM，读一律 `utf-8-sig`。

跳过的：清单自己、`*.log`（搜狗输入法之类的外部日志会混进工作目录）、
`__pycache__/`、`.DS_Store`、`Thumbs.db`。
"""
import argparse
import hashlib
import os
import sys

SKIP_NAMES = {"SHA256SUMS.txt", ".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", ".git"}


def _sha(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def walk(root):
    """返回 [(相对路径, 绝对路径)]，路径统一用 / 分隔，按名字排序。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for n in sorted(filenames):
            if n in SKIP_NAMES or n.lower().endswith(".log"):
                continue
            ap = os.path.join(dirpath, n)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            out.append((rel, ap))
    out.sort(key=lambda x: x[0])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 / 校验 SHA256SUMS.txt")
    ap.add_argument("root", help="发布目录")
    ap.add_argument("--check", action="store_true", help="只校验，不写")
    a = ap.parse_args(argv)

    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        raise SystemExit("不是目录：%s" % root)
    files = walk(root)
    if not files:
        raise SystemExit("%s 里没有文件" % root)

    # 先全量算出哈希：任何一个读不了就直接失败，不写半份清单
    rows, bad = [], []
    for rel, path in files:
        try:
            rows.append((_sha(path), rel))
        except OSError as e:
            bad.append("%s: %s" % (rel, e))
    if bad:
        for b in bad:
            print("读不了 " + b, file=sys.stderr)
        raise SystemExit("有 %d 个文件读不了，拒绝写清单" % len(bad))

    sums_path = os.path.join(root, "SHA256SUMS.txt")

    if a.check:
        if not os.path.exists(sums_path):
            raise SystemExit("没有 %s" % sums_path)
        want = {}
        for line in open(sums_path, encoding="utf-8-sig"):     # BOM 兼容
            line = line.strip()
            if not line:
                continue
            h, _, n = line.partition("  ")
            want[n.strip()] = h.strip()
        missing = sorted(set(want) - {r for _h, r in rows})
        extra = sorted({r for _h, r in rows} - set(want))
        diff = [(h, r) for h, r in rows if r in want and want[r] != h]
        for m in missing:
            print("清单里有、磁盘上没有：%s" % m)
        for e in extra:
            print("磁盘上有、清单里没有：%s" % e)
        for h, r in diff:
            print("哈希不符：%s（磁盘 %s… / 清单 %s…）" % (r, h[:12], want[r][:12]))
        if missing or extra or diff:
            raise SystemExit("校验失败：缺 %d / 多 %d / 不符 %d"
                             % (len(missing), len(extra), len(diff)))
        print("%s 全部 %d 项校验通过" % (root, len(rows)))
        return 0

    body = "".join("%s  %s\n" % (h, r) for h, r in rows)
    # newline="" 防止 Windows 把 \n 变 \r\n；encoding 不带 BOM
    with open(sums_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    print("写入 %s：%d 条" % (sums_path, len(rows)))
    for h, r in rows:
        print("  %s  %s" % (h[:16], r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
