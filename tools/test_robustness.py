# -*- coding: utf-8 -*-
"""健壮性检查：拿坏文件去撞解析器，确认「报错清楚」而不是「莫名其妙崩」。

只测 sdedit.py + sdtheme.py 这一侧的边界；真机行为另说。

    python tools/test_robustness.py
"""
import io
import os
import shutil
import random
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import sdtheme as S                        # noqa: E402

FAIL = []


def ok(cond, msg):
    if cond:
        print("  OK   " + msg)
    else:
        print("  FAIL " + msg)
        FAIL.append(msg)


def expect_error(fn, desc, want=ValueError):
    """必须抛指定异常，而且带人话。不许抛 IndexError/KeyError/AttributeError。"""
    try:
        fn()
    except want as e:
        text = str(e)
        ok(len(text) > 3, "%s → %s(%s)" % (desc, want.__name__, text[:40]))
    except Exception as e:                 # 别的异常都算不合格
        ok(False, "%s → 抛了 %s（应该是 %s）：%s"
           % (desc, type(e).__name__, want.__name__, str(e)[:50]))
    else:
        ok(False, "%s → 居然没报错" % desc)


def base_file():
    """造一份最小的合法 v1（只有图标，没壁纸）。"""
    import fwassets
    icons = [(bytes(fwassets.ICONS_IMG), [0] * 38)]
    return S.pack([("#FF0000",) * 6], icons, None, None)


def check_broken_files():
    print("== 坏文件：必须 ValueError，且不能崩在别的地方 ==")
    good = base_file()

    expect_error(lambda: S.parse(b""), "空字节")
    expect_error(lambda: S.parse(b"X"), "1 字节")
    expect_error(lambda: S.parse(good[:10]), "只剩 10 字节")
    expect_error(lambda: S.parse(good[:len(good) // 2]), "截掉一半")
    expect_error(lambda: S.parse(b"XXXXXXXX" + good[8:]), "魔数错")
    expect_error(lambda: S.parse(good[:0x0C] + b"\xff\xff\xff\xff" + good[0x10:]),
                 "长度字段写成 0xffffffff")

    # 版本号不认识
    bad = bytearray(good)
    struct.pack_into("<H", bad, 8, 99)
    expect_error(lambda: S.parse(bytes(bad)), "版本号 99")


def check_flag_mismatch():
    print("== 标志位与内容不符 ==")
    good = bytearray(base_file())
    # v1 声称有壁纸，但文件长度不够
    struct.pack_into("<H", good, 10, 1)
    expect_error(lambda: S.parse(bytes(good)), "v1 标志说有壁纸但没数据")


def check_truncated_wall():
    print("== 壁纸段被截断 ==")
    good = bytearray(base_file())
    struct.pack_into("<H", good, 10, 1)
    struct.pack_into("<I", good, 12, 0)          # 长度字段也撒谎
    data = bytes(good) + b"\x00" * 100           # 只补 100 字节
    expect_error(lambda: S.parse(data), "补了 100 字节但不够 35000")


def check_bad_palette_indices():
    print("== 索引超出调色板（画出来会花屏的那些） ==")
    import sdedit
    pal = [0x7C00] * S.WALLPAL_N                 # 220 项
    idx = bytes([250] * S.WALL_BYTES)            # 250 > 219，越界
    im = sdedit._wall_to_rgb(idx, pal)
    ok(im.size == (S.WALL_W, S.WALL_H), "越界索引不崩，仍出 %s 的图" % (im.size,))
    ok(im.getpixel((0, 0)) is not None, "越界索引取到兜底色")


def check_short_palette():
    print("== 调色板项数不对 ==")
    import sdedit
    for n in (0, 1, 100, 219):
        pal = [0x7C00] * n
        idx = bytes([n - 1 if n else 0] * S.WALL_BYTES)
        try:
            im = sdedit._wall_to_rgb(idx, pal)
            ok(im.size == (S.WALL_W, S.WALL_H), "%d 项调色板不崩" % n)
        except Exception as e:
            ok(False, "%d 项调色板抛了 %s：%s" % (n, type(e).__name__, e))


def check_fuzz():
    print("== 随机破坏 200 次，只许 ValueError ==")
    random.seed(20261020)
    good = base_file()
    bad_kinds = {}
    for _ in range(200):
        buf = bytearray(good)
        for _ in range(random.randint(1, 8)):
            buf[random.randrange(len(buf))] = random.randrange(256)
        try:
            S.parse(bytes(buf))
        except ValueError:
            pass
        except Exception as e:
            bad_kinds.setdefault(type(e).__name__, str(e)[:40])
    ok(not bad_kinds, "没有抛非 ValueError 的：%s" % (bad_kinds or "无"))


def check_roundtrip_all_versions():
    print("== 三种版本都能读回 ==")
    import fwassets
    icons = [(bytes(fwassets.ICONS_IMG), [0] * 38)]
    pal = [("#123456",) * 6]
    walls = [bytes([20 + i]) * S.WALL_BYTES for i in range(6)]
    wpals = [[0x7C00] * S.WALLPAL_N for _ in range(6)]

    v1 = S.pack(pal, icons, None, None)
    ok(S.parse(v1)["version"] == 1, "无壁纸 → v1")

    v1b = S.pack(pal, icons, walls=walls[:1] * 6, wall_pals=wpals[:1] * 6)
    ok(S.parse(v1b)["version"] == 1 and len(v1b) == S.FILE_SIZE, "6 套同图 → v1 61,680")

    v2 = S.pack(pal, icons, walls=walls, wall_pals=wpals)
    ok(S.parse(v2)["version"] == 2, "6 套不同 → v2")

    pw = [None] * S.PAGE_COUNT
    pp = [None] * S.PAGE_COUNT
    pw[2] = walls[0]
    pp[2] = wpals[0]
    v3 = S.pack(pal, icons, walls=walls, wall_pals=wpals, page_walls=pw, page_pals=pp)
    ok(S.parse(v3)["version"] == 3, "带页面图 → v3")


def check_image_inputs():
    print("== 奇怪格式的图片也要能裁 ==")
    import sdedit
    import wallpaper as WP
    from PIL import Image

    cases = {
        "1x1": Image.new("RGB", (1, 1), (10, 20, 30)),
        "超宽 2000x10": Image.new("RGB", (2000, 10), (200, 100, 50)),
        "超高 10x2000": Image.new("RGB", (10, 2000), (50, 100, 200)),
        "灰度 L": Image.new("L", (400, 300), 128),
        "带透明 P": Image.new("P", (400, 300)),
        "RGBA": Image.new("RGBA", (400, 300), (255, 0, 0, 128)),
        "CMYK": Image.new("CMYK", (400, 300), (0, 255, 0, 0)),
    }
    for name, im in cases.items():
        try:
            out = WP.fit_image(im.convert("RGB"), mode="cover")
            didx, dpal, _ = WP.quantize(out, colors=220)
            ok(out.size == (S.WALL_W, S.WALL_H),
               "%s → %dx%d，量化出 %d 色" % (name, out.size[0], out.size[1], len(set(didx))))
        except Exception as e:
            ok(False, "%s 抛了 %s：%s" % (name, type(e).__name__, e))


def check_save_badpath():
    """存到不该存的地方必须报错。

    **绝不能**拿仓库里的真实文件当靶子 —— 曾经把 app/sdedit.py 当目标，
    d.save() 直接把它覆盖成了 theme.bin，源码当场没了。
    所有写操作只许落在系统临时目录里。
    """
    print("== 存到不存在的目录 / 把文件当目录 ==")
    import sdedit
    d = sdedit.Doc()
    tmp = tempfile.mkdtemp(prefix="sfw_rob_")

    blocker = os.path.join(tmp, "iam_a_file.txt")
    with open(blocker, "w", encoding="utf-8") as fh:
        fh.write("x")

    cases = [
        ("不存在的目录", os.path.join(tmp, "no_such_dir", "theme.bin")),
        ("把文件当目录", os.path.join(blocker, "theme.bin")),
    ]
    for desc, path in cases:
        try:
            d.save(path)
            ok(False, "%s 居然存成功了：%s" % (desc, path))
        except Exception as e:
            ok(isinstance(e, (OSError, ValueError)), "%s → %s" % (desc, type(e).__name__))
            ok(os.path.exists(blocker), "%s 时没损伤已存在的文件" % desc)

    # 正常路径仍要能存
    good = os.path.join(tmp, "ok.bin")
    d.save(good)
    ok(os.path.getsize(good) == S.ICONS_ONLY_SIZE, "正常路径能存，%d 字节" % os.path.getsize(good))
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    check_broken_files()
    check_flag_mismatch()
    check_truncated_wall()
    check_bad_palette_indices()
    check_short_palette()
    check_fuzz()
    check_roundtrip_all_versions()
    check_image_inputs()
    check_save_badpath()

    print()
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
