# syntax=docker/dockerfile:1.7

# immich-analyze v0.6.1 with the small patch in docker/immich-analyze/patches:
#   * --jsonl        stream one JSON object per asset
#   * --assets-file  only process the given asset ids (resume support)
#   * --no-writeback never write descriptions back to Immich
FROM rust:1.91-alpine AS analyzer

ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY}

RUN apk add --no-cache \
        build-base \
        ca-certificates \
        curl \
        openssl-dev \
        patch \
        pkgconfig

WORKDIR /src
RUN curl -fsSL https://github.com/timasoft/immich-analyze/archive/refs/tags/v0.6.1.tar.gz \
        | tar -xz --strip-components=1

COPY docker/immich-analyze/patches/ /patches/
RUN patch -p1 < /patches/0001-jsonl-and-asset-filter.patch

RUN cargo build --release --locked

FROM python:3.12-slim

COPY --from=analyzer /src/target/release/immich-analyze /usr/local/bin/immich-analyze

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY sql/ ./sql/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "immich_omni"]
CMD ["status"]
