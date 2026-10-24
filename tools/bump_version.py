# -*- coding: utf-8 -*-
"""把 VERSION 加 0.01，并可顺手追加一条改动说明。

用法:
  python tools/bump_version.py                          # 只加 0.01
  python tools/bump_version.py "改了什么"                # 加 0.01 + 记一条 CHANGES
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "app", "version.py")


def main():
    src = open(PATH, encoding="utf-8").read()
    m = re.search(r'^VERSION\s*=\s*"(\d+)\.(\d+)"', src, re.M)
    if not m:
        raise SystemExit("version.py 里没找到 VERSION")
    major, minor = int(m.group(1)), int(m.group(2))
    new = "%d.%02d" % (major, minor + 1)

    src = src.replace(m.group(0), 'VERSION = "%s"' % new, 1)

    if len(sys.argv) > 1:
        note = " ".join(sys.argv[1:]).replace('"', "'")
        src = src.replace("CHANGES = [", 'CHANGES = [\n    "%s",' % note, 1)

    open(PATH, "w", encoding="utf-8").write(src)
    print("版本 %s.%02d -> %s" % (major, minor, new))


if __name__ == "__main__":
    main()
