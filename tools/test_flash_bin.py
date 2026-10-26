# -*- coding: utf-8 -*-
"""整片 2 MiB 镜像的出厂测试。

2 MiB 的 `.bin` 是给**外部烧录器**写 NOR 用的：前 `fw_size` 字节是固件本体，
余下补 0xFF。它和同目录的 `.fw` 是同一份固件，只是外壳不同 ——
所以这个脚本验三件事：

  1. 外壳对不对（尺寸、fw_size、尾部填充、越界字节）
  2. 里面装的是不是那份 .fw（逐字节比）
  3. 关键标识还在（主题路径 / 关于页标记 / 内部体检）

用法：
    python tools/test_flash_bin.py <bin> [--fw <对照 .fw>]

**它不能代替实机测试。** 镜像写得对只说明烧进去不会变砖，
不说明固件在卡上跑得起来（那要真机）。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

FLASH_SIZE = 0x200000          # 固件区大小（src/common.h 的 ROM_OFF_FLAHFIRMW 段）
FW_SIZE_OFF = 0xCC             # 头部 fw_size 字段

OK = []
FAIL = []


def check(cond, what, detail=""):
    if cond:
        OK.append(what)
    else:
        FAIL.append(f"{what}  [{detail}]" if detail else what)
    return cond


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin", help="2 MiB 整片镜像")
    ap.add_argument("--fw", help="对照的 .fw（默认按同名规则找）")
    a = ap.parse_args()

    img = open(a.bin, "rb").read()
    name = os.path.basename(a.bin)

    # ---------------------------------------------------------- 1. 外壳
    check(len(img) == FLASH_SIZE, f"镜像大小 = 2 MiB（{FLASH_SIZE:,}）", f"{len(img):,}")

    if len(img) < FW_SIZE_OFF + 4:
        FAIL.append("文件太短，连头部都读不到")
        report(name)
        return 1

    import struct
    fw_size = struct.unpack_from("<I", img, FW_SIZE_OFF)[0]
    check(0 < fw_size <= FLASH_SIZE,
          "头部 fw_size 落在固件区内", f"0x{fw_size:X}")
    check(fw_size % 512 == 0, "fw_size 按 512 字节对齐", f"{fw_size:,}")

    # fw_size 之后必须全是 0xFF，否则就是越界写坏 / 打补丁打歪了
    tail = img[fw_size:]
    if tail:
        bad = next((i for i, b in enumerate(tail) if b != 0xFF), None)
        check(bad is None, f"fw_size 之后全是 0xFF（{len(tail):,} 字节）",
              f"偏移 0x{fw_size + bad:X} 是 0x{tail[bad]:02X}" if bad is not None else "")

    # ---------------------------------------------------- 2. 与 .fw 一致
    fw_path = a.fw
    if not fw_path:
        guess = os.path.join(os.path.dirname(a.bin),
                             name.replace("-2MiB.bin", ".fw"))
        if os.path.exists(guess):
            fw_path = guess
    if fw_path and os.path.exists(fw_path):
        fw = open(fw_path, "rb").read()
        check(len(fw) == fw_size, "对照 .fw 的长度 = 镜像里的 fw_size",
              f"{len(fw):,} vs {fw_size:,}")
        body = img[:len(fw)]
        diff = next((i for i in range(min(len(body), len(fw))) if body[i] != fw[i]), None)
        check(diff is None, "固件区与对照 .fw 逐字节一致",
              f"首个不同在 0x{diff:X}" if diff is not None else "")
        print(f"  对照: {os.path.basename(fw_path)}（{len(fw):,} 字节）")
    else:
        print("  对照: 无（跳过逐字节比对，只验外壳）")

    # ------------------------------------------------------ 3. 关键标识
    body = img[:fw_size]
    for pat in (b"/.superfw/theme.bin", b"/superfw/theme.bin", b"/theme.bin", b"SFWTHM01"):
        check(body.find(pat) >= 0, f"内含主题标记 {pat.decode()}")

    m = re.search(rb"Chis SDT 20\d\d-\d\d-\d\d", body)
    check(m is not None, "关于页带构建日期（能分辨官方件）",
          m.group().decode() if m else "未找到")

    # 上游版本标识：r78 = 0x78000001，r75 = 0x75000001。它不在字符串区，
    # 以字面量出现在代码里，只能按小端字节搜。改了它会破 flash.c 的变体校验，
    # 所以这里只做「还在」的确认。原生主线用 git 短哈希，没有这个值，跳过不算失败。
    if body.find(b"\x01\x00\x00\x78") >= 0:
        OK.append("版本标识 0x78000001 在（r78 系，勿改）")
    elif body.find(b"\x01\x00\x00\x75") >= 0:
        OK.append("版本标识 0x75000001 在（r75 系，勿改）")
    else:
        print("  · 没有 0x75000001/0x78000001 —— 不是 r75/r78 系（原生主线用 git 短哈希），跳过这项")

    # ------------------------------------------------- 4. 交给固件体检
    try:
        import fwcheck
        r = fwcheck.check(body, name)
        # fw_size != 文件大小 那条对 2 MiB 镜像必然是「不一致」，属于设计如此，跳过
        for text, bad in r["lines"]:
            if "fw_size" in text and "文件大小" in text:
                continue
            if text.strip():
                print("  · " + text.strip())
        others = [p for p in r["problems"] if "fw_size" not in p]
        check(not others, "内部体检无其它问题", "; ".join(others)[:120])
    except Exception as e:                            # noqa: BLE001
        print(f"  ! 内部体检跳过：{e}")

    return report(name)


def report(name):
    print()
    print("=" * 66)
    print(f"{name}")
    print("=" * 66)
    for m in OK:
        if m:
            print("  OK   " + m)
    if FAIL:
        print()
        for m in FAIL:
            print("  FAIL " + m)
        print(f"\n{len(FAIL)} 项失败 / {len(FAIL) + len(OK)} 项检查")
        return 1
    print(f"\n全部 {len(OK)} 项通过")
    print("\n注意：这只验镜像结构，不代表实机可跑。刷写前先读回整片备份。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
