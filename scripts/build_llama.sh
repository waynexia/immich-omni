#!/usr/bin/env bash
# Build llama-server with HIP for the local GPU and place it in the Docker
# build context. Requires ROCm (hipcc) and cmake on the host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${LLAMA_SRC:-/tmp/llama.cpp}"
TARGET="${AMDGPU_TARGETS:-gfx1201}"

if [[ ! -d "$SRC" ]]; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$SRC"
fi

BUILD_DIR="${LLAMA_BUILD_DIR:-$SRC/build-hip}"

# The distro clang and the ROCm clang must not be mixed: HIP headers from
# /opt/rocm expect the libdevice declarations shipped with the ROCm LLVM.
HIP_PATH="${HIP_PATH:-/opt/rocm}"
PATH="$HIP_PATH/bin:$PATH" cmake -S "$SRC" -B "$BUILD_DIR" \
    -DGGML_HIP=ON \
    -DAMDGPU_TARGETS="$TARGET" \
    -DCMAKE_HIP_COMPILER="$HIP_PATH/lib/llvm/bin/clang++" \
    -DCMAKE_HIP_FLAGS="--hip-path=$HIP_PATH" \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_CURL=OFF \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF

PATH="$HIP_PATH/bin:$PATH" cmake --build "$BUILD_DIR" -j "$(nproc)" --target llama-server

rm -rf "$ROOT/docker/llama/bin"
mkdir -p "$ROOT/docker/llama/bin"
cp -a "$BUILD_DIR/bin/." "$ROOT/docker/llama/bin/"
echo "copied llama-server to docker/llama/bin/"
