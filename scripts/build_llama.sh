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

cmake -S "$SRC" -B "$SRC/build" \
    -DGGML_HIP=ON \
    -DAMDGPU_TARGETS="$TARGET" \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_CURL=OFF \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF

cmake --build "$SRC/build" -j "$(nproc)" --target llama-server

mkdir -p "$ROOT/docker/llama/bin"
cp "$SRC/build/bin/llama-server" "$ROOT/docker/llama/bin/llama-server"
echo "copied llama-server to docker/llama/bin/"
