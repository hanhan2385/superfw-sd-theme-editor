"""无界面跑一遍「编辑器注入固件」的完整逻辑，再用 unicorn 验证落盘结果。

用法: python tools/test_inject.py <固件> <图片> [输出]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

from PIL import Image          # noqa: E402

import fwbake as FB            # noqa: E402
import fwbuild                 # noqa: E402
import main as M               # noqa: E402
import wallpaper as WP         # noqa: E402


def run(fw_path, img_path, out_path, slot=0, preset=0, dim=0.0):
    img = bytearray(open(fw_path, "rb").read())
    ok, why = M.fw_validate(img)
    assert ok, why
    lay = fwbuild.detect(img)

    # 和编辑器一样：先量化成 220 色（槽位 20..239 这一段）
    src = Image.open(img_path).convert("RGB")
    bgc = M.q5(M.rgb(M.PRESETS[preset][1][1]))
    fitted = WP.fit_image(src, "cover", bgc)
    idx0, pal, n = WP.quantize(fitted, dim, bgc, FB.PAL_N, True)
    idx = FB.map_wall_indices(idx0)
    pal = FB.slice_wall_palette(pal)

    info = FB.bake(img, None, indexed=idx, palette=pal)
    img = info["rom"]
    fwbuild.reseal(img)
    ok2, why2 = M.fw_validate(img)
    open(out_path, "wb").write(bytes(img))
    print("构建 %s  站点 0x%06X/%dB  钩子 0x%08X" % (
        lay.build, lay.patch_off, lay.patch_len, info["hook_addr"]))
    print("颜色 %d / %d   A 0x%06X  B 0x%06X  块 %d→%d" % (
        n, FB.PAL_N, info["a_rom"], info["b_rom"],
        info["old_blocks"], info["blocks"]))
    print("校验和 %s → 写出 %s" % ("通过" if ok2 else why2, out_path))
    return out_path


def verify(out_path):
    import verify_baked as V
    (layout, meta, site_rt, vram, pal, err, trace, state,
     stopped) = V.run(bytearray(open(out_path, "rb").read()))
    exp = bytes(open(out_path, "rb").read()[meta["a_rom"]:meta["a_rom"] + meta["a_len"]])
    exp += bytes(open(out_path, "rb").read()[meta["data_b_at"]:
                                          meta["data_b_at"] + meta["b_len"]])
    diff = sum(1 for i in range(FB.IMG_BYTES) if vram[i] != exp[i])
    exp_pal = bytes(open(out_path, "rb").read()[meta["pal_at"]:
                                                 meta["pal_at"] + meta["pal_n"] * 2])
    got = pal[FB.PAL_BASE * 2:(FB.PAL_BASE + meta["pal_n"]) * 2]
    pd = sum(1 for i in range(len(exp_pal)) if exp_pal[i] != got[i])
    ok = (diff == 0 and pd == 0 and err is None and stopped
          and state["dma"] == 0 and not state["bad"])
    print("验证 DMA %d 次（应为 0：CPU 拷贝）  位图差异 %d  调色板差异 %d  返回 %s → %s" % (
        state["dma"], diff, pd,
        "正确" if stopped else "错(%r)" % err, "通过" if ok else "失败"))
    return ok


if __name__ == "__main__":
    fw = sys.argv[1]
    im = sys.argv[2]
    args = [a for a in sys.argv[3:] if not a.startswith("--")]
    dim = 0.0
    if "--dim" in sys.argv:
        dim = float(sys.argv[sys.argv.index("--dim") + 1])
        args = [a for a in args if a != str(dim)]
    out = args[0] if args else "out/inject-test.bin"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    run(fw, im, out, dim=dim)
    sys.exit(0 if verify(out) else 1)
