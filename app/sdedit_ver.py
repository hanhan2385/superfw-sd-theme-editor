# -*- coding: utf-8 -*-
"""SD 主题编辑器的版本号 —— 唯一来源。

以前有三处各写一份（`app/sdedit.py` 的 APP_VER、`tools/build_sdedit_exe.py` 的
NAME、发布目录名），发版时漏改一处就会出现「标题写 0.31、exe 叫 v0.30、
装进 dist/v0.30」这种自相矛盾。现在统一从这里取：

    app/sdedit.py               -> APP_TITLE / APP_VER
    tools/build_sdedit_exe.py   -> EXE_NAME / DIST_DIR
    tools/check_version.py      -> 一致性校验

改版本只改 `APP_VER` 一行，其它全部跟着变。
（注意：`app/version.py` 是「烘焙注入」老编辑器那条线的版本号，和这里不是一回事。）
"""

APP_VER = "0.32"

APP_NAME = "SuperFW SD 主题编辑器"
APP_TITLE = "%s v%s" % (APP_NAME, APP_VER)

EXE_NAME = "SuperFW-SD-Theme-Editor-v%s" % APP_VER
DIST_DIR = "v%s" % APP_VER

# 关于对话框里那一行
RELEASE_TAG = "v%s" % APP_VER
