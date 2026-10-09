#!/bin/sh
# Builds sm64.dll (libsm64, for the "mario" console command) and runs the
# collision check, inside the kfx-mingw image (see mingw.Dockerfile).
# Given your own ROM, also generates the Mario imp mod from it.
# Run from anywhere after building keeperfx once (SDL3 headers come from out/docker-mingw/deps).
#   build/docker/sm64.sh [path/to/sm64.us.z64]
# Output: out/libsm64/dist/sm64.dll - copy it next to keeperfx.exe, together
# with the ROM; out/mods/mario_imp - copy into the game's mods/ folder.
set -e
ROM=""
if [ -n "$1" ]; then
    ROM="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"  # absolute, before leaving the caller's directory
fi
cd "$(dirname "$0")/../.."
if [ ! -d out/docker-mingw/deps ]; then
    echo "Build keeperfx first (see build/docker/mingw.Dockerfile)" >&2
    exit 1
fi
rm -f out/sm64.us.z64
if [ -n "$ROM" ]; then
    cp "$ROM" out/sm64.us.z64  # out/ is git-ignored; the container only sees the repo
fi
LIBSM64_COMMIT=fd11813208272b4271d92bd92feb8f3fdbe61be5

if [ ! -d out/libsm64 ]; then
    git clone https://github.com/libsm64/libsm64 out/libsm64
fi
git -C out/libsm64 checkout -q "$LIBSM64_COMMIT"

docker run --rm -v "$PWD":/src kfx-mingw sh -ec '
D=out/docker-mingw/deps
cd out/libsm64
# Windows DLL for the game; static libgcc so it only needs KERNEL32 and msvcrt.
# SSE2 math and -O2 (the libsm64 Makefile has neither): 32-bit x86 otherwise uses x87, which
# Rosetta emulates very slowly on Apple Silicon - Mario physics took ~30 ms a turn.
# Clean first so no x87 objects from an older build get linked in.
make clean >/dev/null
make lib OS=Windows_NT CC="i686-w64-mingw32-gcc -O2 -msse2 -mfpmath=sse" LDFLAGS="-shared -static-libgcc -lm" -j8
# Native copy for the check below, built separately so the two never mix
rm -rf /tmp/native && cp -r . /tmp/native && make -C /tmp/native clean >/dev/null && make -C /tmp/native lib -j8 >/dev/null
cd /src
gcc -std=gnu11 -w -Isrc -I/tmp/native/src \
    -I$D/sdl3/SDL3-3.4.12/i686-w64-mingw32/include \
    -I$D/sdl3_mixer/SDL3_mixer-3.2.4/i686-w64-mingw32/include \
    -I$D/sdl3_image/SDL3_image-3.4.4/i686-w64-mingw32/include \
    -I$D/openal/include -I$D/luajit/include -Ideps/centitoml \
    tests/sm64/collision_check.c -o /tmp/collision_check /tmp/native/dist/libsm64.so -lm \
    -Wl,--unresolved-symbols=ignore-all  # game functions the check never calls
LD_LIBRARY_PATH=/tmp/native/dist /tmp/collision_check
python3 -B tests/sm64/render_check.py
if [ -f out/sm64.us.z64 ]; then
    python3 -I tools/sm64/make_mario_mod.py --lib /tmp/native/dist/libsm64.so --rom out/sm64.us.z64 --out out/mods
fi
'
