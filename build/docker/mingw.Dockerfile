# ---------------------------------------------------------------------------
# Windows (i686 MinGW) build image, same toolchain as CI (ubuntu-24.04).
# ---------------------------------------------------------------------------
# Lets non-Linux hosts (e.g. macOS) build keeperfx.exe. Homebrew's mingw-w64
# uses SJLJ exceptions + posix threads, which can't link the prebuilt
# deps/openal (DWARF + win32 threads); Ubuntu's toolchain matches it.
#
#   docker build -t kfx-mingw -f build/docker/mingw.Dockerfile build/docker
#   docker run --rm -v "$PWD":/src kfx-mingw sh -c \
#     'cmake -S . -B out/docker-mingw -G Ninja -DCMAKE_TOOLCHAIN_FILE=build/cmake/toolchains/mingw32.cmake -DCMAKE_BUILD_TYPE=Release && cmake --build out/docker-mingw'
#
# On macOS, tools/CMakeLists.txt has no png2ico for the host; generate
# res/keeperfx_icon.ico once beforehand (any .ico works, e.g. via Pillow).
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    g++-mingw-w64-i686 cmake ninja-build make ca-certificates git python3 python3-pil gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
