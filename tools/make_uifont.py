# -*- coding: utf-8 -*-
"""从 unscii 全字库和 logo.png 里抽出编辑器用到的字符，生成 app/uifont_data.py。

为什么要这一步：unscii-16-full.hex 有 4 MB（两万多个汉字），而编辑器 UI 里
只用到百来个字。把 4 MB 打进 exe 不值当，所以只把用到的字形内联成一个
几 KB 的 Python 模块。开发时若没有这个模块，gbafont 会回落到直接读 .hex。

用法：python tools/make_uifont.py
"""

import base64
import io
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEX = os.path.join(ROOT, "fw", "res", "fonts", "unscii-16-full.hex")
LOGO = os.path.join(ROOT, "fw", "res", "logo.png")
OUT = os.path.join(ROOT, "app", "uifont_data.py")

SCAN = [os.path.join(ROOT, "app", "sdedit.py"),
        os.path.join(ROOT, "app", "gbafont.py"),
        os.path.join(ROOT, "app", "sdtheme.py")]

FONT_H = 16


def wanted_chars():
    chars = set(chr(c) for c in range(0x20, 0x7F))     # 可见 ASCII 全带上
    chars.update("，。、：；！？（）《》【】—…·")
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


def parse_hex(need):
    out = {}
    need_cp = set(ord(c) for c in need)
    with open(HEX, "r", encoding="ascii", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or ":" not in line:
                continue
            cp_s, bits = line.split(":", 1)
            try:
                cp = int(cp_s, 16)
            except ValueError:
                continue
            if cp not in need_cp:
                continue
            try:
                raw = bytes.fromhex(bits)
            except ValueError:
                continue
            if len(raw) == FONT_H:
                out[cp] = (8, tuple(raw))
            elif len(raw) == FONT_H * 2:
                out[cp] = (16, tuple((raw[y * 2] << 8) | raw[y * 2 + 1] for y in range(FONT_H)))
    return out


def main():
    chars = wanted_chars()
    glyphs = parse_hex(chars)
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
        "# 字形来源：unscii-16 (http://viznut.fi/unscii/, GPL)，与固件同一份。",
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
