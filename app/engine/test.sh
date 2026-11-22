#!/bin/sh
# Run PoB's busted test suite natively with the pinned LuaJIT.
set -e
ENGINE="$(cd "$(dirname "$0")" && pwd)"
cd "$ENGINE/../.."
export PATH="$ENGINE/.luajit/bin:$PATH"
eval "$(luarocks --lua-version=5.1 --tree="$ENGINE/.rocks" path)"
exec "$ENGINE/.rocks/bin/busted" --lua="$ENGINE/.luajit/bin/luajit" "$@" </dev/null
