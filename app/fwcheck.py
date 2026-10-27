# -*- coding: utf-8 -*-
"""固件体检（核心）—— 确认一个固件文件里背景图到底是什么状态。

CLI 版在 tools/check_firmware.py；这里提供 GUI 可直接调用的 check()。
"""


def check(rom, fname="?"):
    """体检一份固件（bytes/bytearray）。

    返回 dict：
      lines   : 逐行体检结论 [(文本, 是否有问题)，可直接展示]
      problems: 结构性问题列表
      wall    : PIL.Image（240×144）—— 里面烘焙的背景图；没有则 None
      ok      : bool，problems 为空
    """
    import struct

    import fwbake as FB
    import fwbuild as F

    rom = bytes(rom)
    lines = []
    problems = []

    def L(s, bad=False):
        lines.append((s, bad))

    fw_size = F.fw_size_of(rom)
    if fw_size == len(rom):
        L("文件大小 %s 字节，fw_size 一致 ✓" % format(len(rom), ","))
    else:
        L("fw_size 0x%X ≠ 文件大小 %s —— 内置升级器会拒绝这个文件"
          % (fw_size, format(len(rom), ",")), True)
        problems.append("fw_size 与文件大小不一致（2MB 的 .bin 要用外部烧录器写整片，"
                        "走内置升级器必须用 .fw）")

    build = "?"
    layout = None
    try:
        layout = F.detect(rom)
        build = layout.build
        L("构建 %s，头部 sha256 校验通过 ✓" % layout.build)
    except Exception as e:                                     # noqa: BLE001
        L("构建探测失败（%s）—— 头部校验没过，不是编辑器最新产出" % e, True)
        problems.append("头部 sha256 校验失败：这个文件不是编辑器/写入工具"
                        "最新产出的那份")

    is_sdtheme = bool(layout is not None and getattr(layout, "sdtheme", False))
    baked = None if is_sdtheme else FB.find_baked(rom)
    if is_sdtheme:
        L("SD 卡主题固件 ✓：皮肤来自 SD 卡上的 /.superfw/theme.bin，固件本身不用改")
        L("    · 卡上没这个文件 → 第一次开机会自动生成一份（内置皮肤，26,680 字节）")
        L("    · 有 → 6 套配色 + 6 套图标 + 壁纸全从它读（实机切主题自动换）")
        L("    ·     · v1（26,680 / 61,680）= 一张壁纸 6 套共用；"
          "v2（236,680）= 每套各一张")
    elif not baked:
        L("没有背景图烘焙记录（SFWBAKE 头不存在）——刷上去内容区就是纯色", True)
        problems.append("固件里没有烘焙背景图：你拿去刷的不是带背景的输出文件")
    else:
        L("背景图已烘焙：位图 A 0x%06X + 位图 B 0x%06X，调色板 %d 色，"
                     "钩子 EWRAM 0x%08X"
                     % (baked["a_rom"], baked["data_b_at"], baked["pal_n"],
                        0x027C0000 + baked["hook_off"]))
        if baked["hook_off"] + 0x100 > baked["payload_len"]:
            problems.append("钩子落在载荷拷贝范围之外，实机必然取不到")
        first = struct.unpack_from("<H", rom, baked["patch_off"])[0]
        if (first & 0xF800) != 0xF000:
            problems.append("补丁点不是 BL（0x%04X）—— 菜单不会跳进钩子" % first)
        else:
            L("补丁点 0x%06X 是 BL ✓（菜单每帧都会跳进钩子画背景）"
                         % baked["patch_off"])

        # 钩子是哪一代：v0.14 起从 Flash 直读（先写 CPLD 模式寄存器），
        # 更早的读卡带 SDRAM —— 30MB 那块在真机上读不到，刷上去不出图。
        try:
            psrc = FB.find_layout(rom).payload_src
        except Exception:                                      # noqa: BLE001
            psrc = None
        hook = rom[psrc + baked["hook_off"]:psrc + baked["hook_off"] + 0x100] \
            if psrc is not None else b""
        if struct.pack("<I", FB.CPLD_REG) in hook:
            L("钩子是 Flash 直读版 ✓（v0.14 起，真机可出图）")
            ico = FB.find_icons(rom)
            if not ico and baked.get("icon_flash"):
                # 早期 v5 试作件只把图标表位置写在头上，没有尾部记录
                ico = {"table_rom": baked["icon_flash"] - FB.ROM_BASE,
                       "stride": baked.get("icon_stride") or FB.ICON_STRIDE,
                       "theme_addr": baked.get("theme_addr") or 0}
            if ico:
                L("6 套主题各带一套图标 ✓（表 0x%06X，每套 %d 字节；"
                  "实机「界面设置 → 主题颜色」切 1..6 会自动换）"
                  % (ico["table_rom"], ico["stride"]))
                if struct.pack("<I", ico["theme_addr"]) not in hook:
                    problems.append("图标表有了，但钩子里没读 menu_theme —— "
                                    "切主题不会换图标")
            else:
                L("6 套主题共用一套图标（没做独立图标时是正常的）")
        else:
            L("钩子是旧版（从卡带 SDRAM 读位图）—— 实机上背景图不会显示", True)
            problems.append("钩子是旧版：重新用 v0.14 及以后的编辑器注入一次，"
                            "或在编辑器里换成同一张图（会重写钩子）")

        # 解出图
        idx = bytes(rom[baked["a_rom"]:baked["a_rom"] + baked["a_len"]]) + \
              bytes(rom[baked["data_b_at"]:baked["data_b_at"] + baked["b_len"]])
        pal = struct.unpack_from("<%dH" % baked["pal_n"], rom, baked["pal_at"])
        from PIL import Image
        img = Image.new("RGB", (FB.IMG_W, FB.IMG_H))
        px = img.load()
        for i, v in enumerate(idx):
            x, y = i % FB.IMG_W, i // FB.IMG_W
            c = pal[v - FB.PAL_BASE] if FB.PAL_BASE <= v < FB.PAL_BASE + baked["pal_n"] else 0
            px[x, y] = FB.bgr555_to_rgb(c)
        colors = len(set(idx))
        L("里面这张背景图用到 %d 个色号%s"
                     % (colors,
                        "" if colors > 2 else "（太少，八成是空的/纯色）"))
        if colors <= 2:
            problems.append("背景图几乎是纯色：注入时选图可能失败了")
        return {"lines": lines, "problems": problems, "wall": img,
                "ok": not problems, "build": build}

    return {"lines": lines, "problems": problems, "wall": None,
            "ok": not problems, "build": build}
