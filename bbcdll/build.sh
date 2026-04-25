#!/bin/bash
# 构建 _ctypes.pyd 代理 DLL
#
# 前置条件:
#   1. 安装 MinGW-w64 (x86_64): https://www.mingw-w64.org/
#      或通过 MSYS2: pacman -S mingw-w64-x86_64-gcc
#   2. 确保 x86_64-w64-mingw32-gcc 在 PATH 中
#
# 用法:
#   bash build.sh
#
# 输出:
#   _ctypes.pyd  (放到 BBchannel/dist/BBchannel64/ 目录)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 优先用 MSYS2 MinGW64 的 gcc，否则用系统 gcc
if command -v x86_64-w64-mingw32-gcc &>/dev/null; then
    CC=x86_64-w64-mingw32-gcc
elif command -v gcc &>/dev/null; then
    CC=gcc
else
    echo "ERROR: 找不到 MinGW-w64 GCC"
    echo "请安装 MSYS2 后运行: pacman -S mingw-w64-x86_64-gcc"
    exit 1
fi

echo "Using compiler: $CC"
echo "Building _ctypes.pyd proxy DLL..."

$CC -shared \
    -o _ctypes.pyd \
    proxy_ctypes.c \
    -O2 \
    -Wall \
    -Wno-unused-parameter \
    -lkernel32

echo "Done! Output: _ctypes.pyd ($(stat -c%s _ctypes.pyd 2>/dev/null || wc -c < _ctypes.pyd) bytes)"
echo ""
echo "部署步骤:"
echo "  1. 将 BBchannel/dist/BBchannel64/_ctypes.pyd 重命名为 _ctypes_orig.pyd"
echo "  2. 将新编译的 _ctypes.pyd 复制到 BBchannel/dist/BBchannel64/"
echo "  3. 将 bbc_tcp_server.py 复制到 BBchannel/dist/BBchannel64/"
