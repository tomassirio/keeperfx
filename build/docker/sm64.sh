#!/bin/sh
# Builds sm64.dll (libsm64, for the "mario" console command) and runs the
# collision check, inside the kfx-mingw image (see mingw.Dockerfile).
# Run from the repo root after building keeperfx once (SDL3 headers come from out/docker-mingw/deps).
#   build/docker/sm64.sh
# Output: out/libsm64/dist/sm64.dll - copy it next to keeperfx.exe, together
# with your own sm64.us.z64 ROM.
set -e
LIBSM64_COMMIT=fd11813208272b4271d92bd92feb8f3fdbe61be5

if [ ! -d out/libsm64 ]; then
    git clone https://github.com/libsm64/libsm64 out/libsm64
fi
git -C out/libsm64 checkout -q "$LIBSM64_COMMIT"

docker run --rm -v "$PWD":/src kfx-mingw sh -ec '
D=out/docker-mingw/deps
cd out/libsm64
# Windows DLL for the game; static libgcc so it only needs KERNEL32 and msvcrt
make lib OS=Windows_NT CC=i686-w64-mingw32-gcc LDFLAGS="-shared -static-libgcc -lm" -j8
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
'
