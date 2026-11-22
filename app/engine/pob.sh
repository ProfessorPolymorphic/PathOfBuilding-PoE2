#!/bin/sh
# Run a Lua script against the headless PoB engine, using the pinned LuaJIT
# and local rocks. Scripts run with cwd = src/ (PoB requires it).
#   app/engine/pob.sh <script.lua relative to app/engine> [args...]
#
# POB_LUAJIT / POB_ROCKS override the toolchain location (the Docker image
# installs them system-wide instead of under app/engine/).
set -e
ENGINE="$(cd "$(dirname "$0")" && pwd)"
LUAJIT="${POB_LUAJIT:-$ENGINE/.luajit/bin/luajit}"
ROCKS="${POB_ROCKS:-$ENGINE/.rocks}"
SCRIPT="$ENGINE/$1"; shift
# Make file arguments absolute, since we cd into src/ below.
for a in "$@"; do set -- "$@" "$(cd "$(dirname "$a")" && pwd)/$(basename "$a")"; shift; done
cd "$ENGINE/../../src"
LUA_PATH="../runtime/lua/?.lua;../runtime/lua/?/init.lua;$ROCKS/share/lua/5.1/?.lua;;" \
LUA_CPATH="$ROCKS/lib/lua/5.1/?.so;;" \
exec "$LUAJIT" "$SCRIPT" "$@"
