# Runtime image for the host-built llama-server.
#
# The binary is compiled on the host (Arch, glibc 2.44) against /opt/rocm, so
# the image only needs a compatible glibc and the ROCm runtime mounted from the
# host. Build it with `make llama` (see scripts/build_llama.sh).
FROM archlinux:latest

RUN pacman -Sy --noconfirm --needed gcc-libs libgomp \
    && pacman -Scc --noconfirm

COPY bin/llama-server /usr/local/bin/llama-server

ENV LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib/rocm_sysdeps/lib

ENTRYPOINT ["/usr/local/bin/llama-server"]
