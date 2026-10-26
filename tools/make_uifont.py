# -*- coding: utf-8 -*-
"""从固件的字体包和 logo.png 里抽出编辑器用到的字符，生成 app/uifont_data.py。

为什么要这一步：`fonts-ext.pack` 有 983 KB（四万多个字形），而编辑器 UI 里
只用到百来个字。把整份打进 exe 不值当，所以只把用到的字形内联成一个
几十 KB 的 Python 模块。开发时若没有这个模块，gbafont 会回落去读 .pack。

用法：python tools/make_uifont.py
"""

import base64
import io
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "ref", "superfw-fix", "res", "fonts-ext.pack")
LOGO = os.path.join(ROOT, "fw", "res", "logo.png")
OUT = os.path.join(ROOT, "app", "uifont_data.py")

sys.path.insert(0, os.path.join(ROOT, "app"))

SCAN = [os.path.join(ROOT, "app", "sdedit.py"),
        os.path.join(ROOT, "app", "gbafont.py"),
        os.path.join(ROOT, "app", "sdtheme.py")]

FONT_H = 16


def wanted_chars():
    chars = set(chr(c) for c in range(0x20, 0x7F))     # 可见 ASCII 全带上
    chars.update("，。、：；！？（）《》【】—…·")
    # 界面代码里直接写的几何符号（本来在字符串里能扫到，点名是为了保险）
    # 2026-10-26：从 ◂▸▴▾（5 列小三角）换成 ◀▶▲▼（9~11 列）。子集不跟着换的话，
    # 预览里这两个字符查不到字形会静默画成空白 —— 看着像「代码没生效」。
    chars.update("◀▶▲▼")
    chars.add("?")            # 缺字回落用，必须留着，否则缺字在预览里是空白不是问号
    for p in SCAN:
        if not os.path.exists(p):
            continue
        with open(p, "rb") as fh:
            try:
                for tok in tokenize.tokenize(fh.readline):
                    if tok.type == tokenize.STRING:
                        s = tok.string
                        # 去掉引号/前缀，粗略取字面量；转义字符不影响字符集
                        body = re.sub(r"^[rbuRBUfF]{0,2}('''|\"\"\"|'|\")", "", s)
                        body = re.sub(r"('''|\"\"\"|'|\")$", "", body)
                        chars.update(body)
            except (tokenize.TokenError, IndentationError, SyntaxError):
                pass
    chars.discard("\n")
    chars.discard("\r")
    chars.discard("\t")
    return sorted(chars)


def parse_pack(need):
    """从固件的字体包里只挑用得上的字形（解析逻辑复用 app/gbafont.py，
    免得两边各写一份、越走越远）。"""
    import gbafont
    font = gbafont._parse_pack()
    need_cp = set(ord(c) for c in need)
    return {cp: (w, tuple(rows)) for cp, (w, rows) in font.items() if cp in need_cp}


def main():
    chars = wanted_chars()
    glyphs = parse_pack(chars)
    missing = sorted(set(ord(c) for c in chars) - set(glyphs))

    # logo 内联成 base64 PNG（31x7，只有几百字节）
    from PIL import Image
    im = Image.open(LOGO).convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    logo_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    lines = [
        "# -*- coding: utf-8 -*-",
        "# 由 tools/make_uifont.py 生成，请勿手改。",
        "# 字形来源：文泉驿 10pt 点阵（12×12），与固件 fonts-ext.pack 同一份。",
        "",
        "GLYPHS = {",
    ]
    for cp in sorted(glyphs):
        w, rows = glyphs[cp]
        lines.append("0x%04X: (%d, (%s))," % (cp, w, ",".join("0x%02X" % r for r in rows)))
    lines.append("}")
    lines.append("")
    lines.append("LOGO_PNG = (")
    for i in range(0, len(logo_b64), 76):
        lines.append('    "%s"' % logo_b64[i:i + 76])
    lines.append(")")
    lines.append("")

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))

    print("字符 %d 个，字形 %d 个，缺 %d 个" % (len(chars), len(glyphs), len(missing)))
    if missing:
        print("缺字（会回落成 '?'）：", "".join(chr(c) for c in missing))
    print("输出", OUT, os.path.getsize(OUT), "字节")


if __name__ == "__main__":
    main()
