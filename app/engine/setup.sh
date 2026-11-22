#!/bin/sh
# One-time setup of the headless engine toolchain on macOS (Homebrew required).
# Builds LuaJIT at the same commit PoB's CI uses (see repo Dockerfile) — newer
# LuaJIT releases reject the "#@" marker line PoB's entry files start with —
# and installs native Lua modules into app/engine/.rocks.
set -e
ENGINE="$(cd "$(dirname "$0")" && pwd)"
LUAJIT_COMMIT=2460b3ff93a1c955de3d62cfc825de7d68dc272e
brew list luarocks >/dev/null 2>&1 || brew install luarocks

if [ ! -x "$ENGINE/.luajit/bin/luajit" ]; then
	rm -rf "$ENGINE/.luajit-src"
	git clone -q https://github.com/LuaJIT/LuaJIT "$ENGINE/.luajit-src"
	git -C "$ENGINE/.luajit-src" checkout -q "$LUAJIT_COMMIT"
	export MACOSX_DEPLOYMENT_TARGET=14.0
	make -C "$ENGINE/.luajit-src" -j8 PREFIX="$ENGINE/.luajit"
	make -C "$ENGINE/.luajit-src" install PREFIX="$ENGINE/.luajit"
	ln -sf "$(ls "$ENGINE"/.luajit/bin/luajit-2.1.* | head -1 | xargs basename)" "$ENGINE/.luajit/bin/luajit"
	rm -rf "$ENGINE/.luajit-src"
fi

ROCKS="luarocks --lua-version=5.1 --tree=$ENGINE/.rocks --lua-dir=$ENGINE/.luajit LUA_INCDIR=$ENGINE/.luajit/include/luajit-2.1"
$ROCKS install luautf8
$ROCKS install luafilesystem
$ROCKS install busted 2.2.0-1
echo "Done. Try: app/engine/pob.sh smoke.lua app/api/tests/fixtures/shaman.xml"
