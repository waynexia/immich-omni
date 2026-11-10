# Runtime image for the host-built llama-server.
#
# The binary is compiled on the host (Arch, glibc 2.44) against /opt/rocm, so
# the image only needs a compatible glibc and the ROCm runtime mounted from the
# host. Build it with `make llama` (see scripts/build_llama.sh).
FROM archlinux:latest

RUN printf 'Server = https://mirrors.tuna.tsinghua.edu.cn/archlinux/$repo/os/$arch\n' > /etc/pacman.d/mirrorlist \
    && pacman -Sy --noconfirm --needed gcc-libs libgomp fmt google-glog libdrm numactl elfutils zlib \
    && pacman -Scc --noconfirm

COPY bin/ /opt/llama/bin/

ENV LD_LIBRARY_PATH=/opt/llama/bin:/opt/rocm/lib:/opt/rocm/lib/rocm_sysdeps/lib

ENTRYPOINT ["/opt/llama/bin/llama-server"]
