# -*- coding: utf-8 -*-
"""独立 exe 的入口。

实现全在 `app/gbacvt.py` —— 主题编辑器里的「图片 → GBA」按钮打开的是同一份代码，
所以独立跑和从修改器里跑出来的字节完全一致。这里只负责把 app/ 放进 sys.path。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(os.path.dirname(_HERE), "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)
if _HERE in sys.path:                 # 别 import 到自己（同名文件）
    sys.path.remove(_HERE)

import gbacvt                                                    # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(gbacvt.main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:                                            # noqa: BLE001
        import traceback
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror("出错了", traceback.format_exc()[-1500:])
        except Exception:                                        # noqa: BLE001
            pass
        sys.exit(1)
