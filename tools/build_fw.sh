#!/usr/bin/env bash
# 编译「SD 卡主题」版 SuperFW 固件。
#
# 依赖（都在 .toolchain/ 下，不需要系统装 devkitPro）：
#   .toolchain/armgcc/xpack-arm-none-eabi-gcc-*/bin   arm-none-eabi-gcc 13.3.1
#   .toolchain/w64devkit/w64devkit/bin                make / cpp / g++（编 upkr 用）
#
# 用法：tools/build_fw.sh [chis|sd|lite]      默认 chis（= 国内 1.6MB 那个）
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BOARD=${1:-chis}
# 第二个参数：1 = 编译「诊断版」（屏幕上显示 SD 主题装载结果）
DIAG=${2:-0}

W64="$ROOT/.toolchain/w64devkit/w64devkit/bin"
ARM=$(ls -d "$ROOT"/.toolchain/armgcc/xpack-arm-none-eabi-gcc-*/bin 2>/dev/null | head -1)

if [ -z "$ARM" ] || [ ! -x "$ARM/arm-none-eabi-gcc.exe" ]; then
  echo "找不到 arm-none-eabi-gcc，先解压 .toolchain/armgcc.zip 到 .toolchain/armgcc/" >&2
  exit 1
fi

export PATH="$ARM:$W64:$PATH"
export PYTHON="${PYTHON:-$ROOT/.venv314/Scripts/python.exe}"

cd "$ROOT/fw"

# 内置模拟器要用 upkr 压缩过；缺了就现场编一个。
if [ ! -x ./upkr.elf ]; then
  echo "== 编译 upkr =="
  g++ -std=c++20 -O2 -o upkr.elf tools/upkr.cc
fi
for f in emu/*.gba.bin; do
  out="${f%.bin}.comp"
  [ -f "$out" ] || ./upkr.elf -l 4 "$f" "$out"
done

echo "== make BOARD=$BOARD SDTHEME_DIAG=$DIAG =="
# mingw 版 make 默认拿 cmd.exe 当 shell，命令里的续行和重定向会挂；显式换成
# Git Bash（路径必须是 Windows 形式，make 不认 /e/... ）
GITBASH=$(cygpath -w "$(command -v bash)" 2>/dev/null || echo "")
[ -z "$GITBASH" ] && GITBASH="C:/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.3.0/bin/bash.exe"

# 编译开关变了 make 察觉不到，碰一下源文件强制重编（不要用 rm：这里的 rm 被
# 安全删除拦截器接管，失败关闭会把脚本打断）
touch src/menu.c src/sdtheme.c

make BOARD="$BOARD" SDTHEME_DIAG="$DIAG" SHELL="$GITBASH" -j"$(nproc 2>/dev/null || echo 4)"

echo
echo "产物：$ROOT/fw/superfw.gba  ($(stat -c %s superfw.gba 2>/dev/null || wc -c < superfw.gba) 字节)"
