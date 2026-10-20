# -*- coding: utf-8 -*-
"""Build a self-contained SuperFW theme editor using the firmware's own
bitmap font, icons and logo.

Inputs (relative to repo root):
  ref/superfw/res/fonts/unscii-16.hex        8x16 ASCII glyphs
  ref/superfw/res/fonts/unscii-16-full.hex   16x16 CJK glyphs
  ref/superfw/res/icons.png                  17 tiles of 16x16
  ref/superfw/res/logo.png                   31x7 logo
  ref/superfw/res/messages.py                English strings
  ref/superfw/res/lang/zh.json               Chinese strings

Output: theme-editor.html
"""
import base64, json, re, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "ref", "superfw", "res")

# ---------------------------------------------------------------- strings
src = open(os.path.join(RES, "messages.py"), encoding="utf-8").read()
EN = dict(re.findall(r'"(MSG_[A-Z0-9_]+)"\s*:\s*"([^"]*)"', src))
ZH = json.load(open(os.path.join(RES, "lang", "zh.json"), encoding="utf-8"))

KEYS = [
    "MSG_UIS_THEME", "MSG_UIS_LANG", "MSG_UIS_RECNT", "MSG_UIS_ANSPD",
    "MSG_UIS_BHID", "MSG_UIS_SAVE", "MSG_KNOB_ENABLED", "MSG_KNOB_DISABLED",
    "MSG_UIS_SPD2", "MSG_GBALOAD_MINFO", "MSG_LOADINFO_GAME",
    "MSG_LOADINFO_SAVE", "MSG_LOAD_GBA", "MSG_GBALOAD_MPATCH",
    "MSG_DEFS_PATCH", "MSG_LOADER_SAVET", "MSG_LOADER_MENU",
    "MSG_LOADER_RTCE", "MSG_PATCH_TYPE3", "MSG_TOOLS0_SDRAM",
    "MSG_TOOLS1_SRAM", "MSG_TOOLS2_BAT", "MSG_TOOLS3_BENCH",
    "MSG_TOOLS4_FBAK", "MSG_SAVETYPE_EEPROM", "MSG_LANG_NAME",
    "MSG_LOADER_ST0", "MSG_PATCH_TYPE_I3",
]
STRINGS = {}
for k in KEYS:
    STRINGS[k] = {"en": EN.get(k, ""), "zh": ZH.get(k) or EN.get(k, "")}

# ---------------------------------------------------------------- glyphs
def load_hex(path):
    out = {}
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if ":" not in line:
            continue
        cp_s, data = line.split(":", 1)
        try:
            cp = int(cp_s, 16)
        except ValueError:
            continue
        out[cp] = data.lower()
    return out

ascii_g = load_hex(os.path.join(RES, "fonts", "unscii-16.hex"))
full_g = load_hex(os.path.join(RES, "fonts", "unscii-16-full.hex"))

need = set(range(32, 127))
for pair in STRINGS.values():
    for lang in ("en", "zh"):
        for ch in pair[lang]:
            if ord(ch) > 127:
                need.add(ord(ch))
# a few symbols used by the mock data
for ch in "▸◂⯇⯈·…":
    need.add(ord(ch))
# literals that live in the HTML template (not in the string table)
tpl = open(os.path.join(ROOT, "tools", "editor_template.html"), encoding="utf-8").read()
for ch in tpl:
    if ord(ch) > 127:
        need.add(ord(ch))

glyphs = {}
missing = []
for cp in sorted(need):
    if cp in ascii_g:
        glyphs[str(cp)] = ascii_g[cp]
    elif cp in full_g:
        glyphs[str(cp)] = full_g[cp]
    elif cp > 127:
        missing.append(hex(cp))

print("glyphs:", len(glyphs), "missing:", missing)

# ---------------------------------------------------------------- images
def b64(path):
    return base64.b64encode(open(path, "rb").read()).decode()

ICONS = b64(os.path.join(RES, "icons.png"))
LOGO = b64(os.path.join(RES, "logo.png"))

# ---------------------------------------------------------------- presets
# Exactly menu.c:221-226
PRESETS = [
    {"id": 1, "name": {"en": "White", "zh": "白"}, "c": ["AAAAAA", "FFFFFF", "000000", "CCCCCC", "9999BB", "C08888"]},
    {"id": 2, "name": {"en": "Orange", "zh": "橙"}, "c": ["ECA551", "E7C092", "000000", "BDA27B", "90816E", "615D58"]},
    {"id": 3, "name": {"en": "Blue", "zh": "蓝"}, "c": ["26879C", "8FB1B8", "000000", "5296A5", "1D7F95", "6F8185"]},
    {"id": 4, "name": {"en": "Green", "zh": "绿"}, "c": ["308855", "88AA99", "000000", "778888", "777777", "606060"]},
    {"id": 5, "name": {"en": "Purple", "zh": "紫"}, "c": ["AD11C8", "E47AF6", "000000", "AD5DC6", "724095", "72667A"]},
    {"id": 6, "name": {"en": "Dark", "zh": "暗"}, "c": ["222222", "444444", "EEEEEE", "737573", "AAAAAA", "606060"]},
]

html = open(os.path.join(ROOT, "tools", "editor_template.html"), encoding="utf-8").read()
html = (html
        .replace("__GLYPHS__", json.dumps(glyphs, separators=(",", ":")))
        .replace("__STR__", json.dumps(STRINGS, ensure_ascii=False, separators=(",", ":")))
        .replace("__ICONS__", ICONS)
        .replace("__LOGO__", LOGO)
        .replace("__PRESETS__", json.dumps(PRESETS, ensure_ascii=False, separators=(",", ":"))))

out = os.path.join(ROOT, "theme-editor.html")
open(out, "w", encoding="utf-8").write(html)
print("wrote", out, len(html), "bytes")
