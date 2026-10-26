#!/usr/bin/env bash
# superfw-fix (chis) 构建包装：把工具链 PATH + mingw make 的 SHELL 都设好，直接跑。
# 用法：bash ref/superfw-fix/build_chis.sh [额外的 make 目标/变量...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORK="$ROOT/ref/superfw-fix"

export PATH="$ROOT/.toolchain/armgcc/xpack-arm-none-eabi-gcc-13.3.1-1.1/bin:$ROOT/.toolchain/w64devkit/w64devkit/bin:$PATH"

# mingw 的 make 必须拿到 Git Bash 的 Windows 路径当 SHELL，否则 /bin/sh 解不出来。
GITBASH="$(cygpath -w "$(command -v bash 2>/dev/null || echo /usr/bin/bash)")"
[ -x "$(command -v bash)" ] || GITBASH='C:\Program Files\Git\bin\bash.exe'

cd "$FORK"
exec make BOARD=chis SHELL="$GITBASH" "$@"
