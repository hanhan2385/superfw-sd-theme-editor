固件内置字体的出处与许可
========================

固件里的字体在 res/fonts-ext.pack（983,948 B），内容是：

  主字形：文泉驿点阵宋体 10pt（WenQuanYi Bitmap Song）
          Copyright (C) 2004-2006 The WenQuanYi Project Board of Trustees, FangQ, Firefly
          许可：GNU GPL v2（附字体嵌入例外 · font embedding exception）
          http://wenq.org

  缺字补全：Fusion Pixel 缝合像素字体 12px（monospaced zh_hans）
          许可：SIL Open Font License 1.1
          https://github.com/TakWolf/fusion-pixel-font

  打包来源：github.com/Electr0Whale/superfw 的 wqy10pt-font 分支
          （res/fonts/build.py + bdf_to_pack.py + combine_fonts.py，
            由 wenquanyi_10pt.bdf 与 fusion-pixel-12px-monospaced-zh_hans.bdf 合成）

格式说明
--------
  FLAG_FW16 = 1   16 列定宽，每字 32 B
  FLAG_FW12 = 2   12 列定宽，每字 24 B（中文用这个）
  FLAG_COMP = 0x80000000   韩文合成（存 205 个字母，运行时组合 11,172 音节）
  变宽块：先一张 n 个 uint16 的索引（高 4 位宽-1，低 12 位列偏移），后面是列数据

关于页只显示最要紧的一行（"Font: WenQuanYi 10pt"），
编辑器的「关于」对话框里列出了完整的三条署名。
