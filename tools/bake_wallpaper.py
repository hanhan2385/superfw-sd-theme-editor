"""把一张图烘焙进 SuperFW 固件，并自检。

用法:
  python tools/bake_wallpaper.py <固件> <图片> [-o 输出] [--dim 0.15] [--quiet]

输出默认写在 out/<固件名>-baked.bin，并跑一遍 tools/verify_baked.py 的等价检查。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import fwbake as B        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("firmware")
    ap.add_argument("image")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--dim", type=float, default=0.0,
                    help="压暗比例 0..1，让主题文字更清楚")
    ap.add_argument("--verify", action="store_true", default=True)
    ap.add_argument("--no-verify", dest="verify", action="store_false")
    a = ap.parse_args()

    from PIL import Image
    rom = bytearray(open(a.firmware, "rb").read())
    img = Image.open(a.image)

    info = B.bake(rom, img, dim=a.dim)
    rom = info["rom"]
    L, site = info["layout"], info["site"]

    out = a.out
    if out is None:
        stem = os.path.splitext(os.path.basename(a.firmware))[0]
        out = os.path.join(HERE, "..", "out", stem + "-baked.bin")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    import fwbuild as F
    F.reseal(rom)
    open(out, "wb").write(bytes(rom))

    print("固件    %s  %d B  fw_size 0x%X" % (
        a.firmware, len(rom), F.fw_size_of(rom)))
    print("站点    0x%06X -> 运行址 0x%08X（%d B，frame 在 %s）" % (
        site.patch_off, info["site_runtime"], site.patch_len, site.frame_reg))
    print("钩子    EWRAM 0x%08X（ROM 0x%06X，%d B）" % (
        info["hook_addr"], info["hook_rom"], len(info["hook"])))
    print("位图    A ROM 0x%06X -> Flash 0x%08X（%d B）" % (
        info["a_rom"], info["a_flash"], info["a_len"]))
    print("        B ROM 0x%06X -> Flash 0x%08X（%d B）" % (
        info["b_rom"], info["b_flash"], info["b_len"]))
    print("调色板  Flash 0x%08X（%d 色 -> VRAM 槽 20..239）" % (
        info["pal_flash"], B.PAL_N))
    print("颜色    实际用到 %d 色" % info["colors"])
    print("输出    %s" % os.path.abspath(out))

    if a.verify:
        import verify_baked as V
        (layout, meta, site_rt, vram, page1, pal, err, trace, state,
         stopped) = V.run(bytearray(open(out, "rb").read()))
        exp = (bytes(rom[info["a_rom"]:info["a_rom"] + info["a_len"]])
               + bytes(rom[info["b_rom"] + B.HDR_SIZE + B.PAL_N * 2:
                            info["b_rom"] + B.HDR_SIZE + B.PAL_N * 2 + info["b_len"]]))
        diff = sum(1 for i in range(B.IMG_BYTES) if vram[i] != exp[i])
        exp_pal = bytes(rom[info["b_rom"] + B.HDR_SIZE:
                            info["b_rom"] + B.HDR_SIZE + B.PAL_N * 2])
        got_pal = pal[B.PAL_BASE * 2:(B.PAL_BASE + B.PAL_N) * 2]
        pd = sum(1 for i in range(len(exp_pal)) if exp_pal[i] != got_pal[i])
        guard = pal[0:B.PAL_BASE * 2] + pal[(B.PAL_BASE + B.PAL_N) * 2:0x400]
        ok_guard = all(x == 0xFF for x in guard)
        ok = (diff == 0 and pd == 0 and err is None and state["dma"] == 0
              and ok_guard and not state["bad"] and stopped)
        print("\n自检")
        print("  DMA 执行 %d 次（应为 0：位图由 CPU 搬）%s" % (
            state["dma"], "，失败 %s" % state["bad"] if state["bad"] else ""))
        print("  位图差异 %d / %d 字节" % (diff, B.IMG_BYTES))
        print("  调色板差异 %d 字节；保护槽 %s" % (
            pd, "没被写" if ok_guard else "被写了!"))
        print("  返回点 %s" % ("正确 0x%08X" % (site_rt + meta["patch_len"])
                              if stopped else "不对（err=%r）" % err))
        print("  结论 %s" % ("通过" if ok else "失败"))
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
