#!/usr/bin/env python3
"""Resumable parallel HTTP range downloader for large GGUF model files.

Standard library only, so it can run inside a bare ``python:3.12-slim`` image.
Progress state is kept next to the output file as ``<output>.state``; deleting
it forces a fresh download. Every completed chunk is fsynced before it is
recorded, so an abrupt kill only loses the chunks that were in flight.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_URL = (
    "https://modelscope.cn/models/Qwen/Qwen3-Embedding-8B-GGUF/resolve/master/"
    "Qwen3-Embedding-8B-Q8_0.gguf"
)
DEFAULT_CHUNK = 16 * 1024 * 1024
USER_AGENT = "immich-omni-downloader/1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="source URL")
    parser.add_argument("--output", required=True, help="destination file")
    parser.add_argument("--connections", type=int, default=8, help="parallel ranges")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def remote_size(url: str, timeout: int) -> int:
    """Return the object size, tolerating servers that omit HEAD metadata."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length:
                return int(length)
    except urllib.error.URLError:
        pass

    request = urllib.request.Request(
        url, headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            return int(content_range.rsplit("/", 1)[1])
        length = response.headers.get("Content-Length")
        if length:
            return int(length)
    raise SystemExit(f"could not determine size of {url}")


def load_state(path: str, total: int, chunk_size: int) -> set[int]:
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return set()
    if state.get("total") != total or state.get("chunk_size") != chunk_size:
        print("state does not match remote file, starting over", file=sys.stderr)
        return set()
    return {int(index) for index in state.get("done", [])}


def save_state(path: str, total: int, chunk_size: int, done: set[int]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(
            {"total": total, "chunk_size": chunk_size, "done": sorted(done)},
            handle,
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def fetch_range(url: str, start: int, end: int, fd: int, retries: int, timeout: int) -> None:
    """Fetch ``[start, end]`` and write it at the matching file offset."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                expected = end - start + 1
                payload = response.read()
                if len(payload) != expected:
                    raise IOError(f"short range {start}-{end}: {len(payload)}/{expected}")
                os.pwrite(fd, payload, start)
                return
        except (urllib.error.URLError, OSError, IOError) as error:  # noqa: UP024
            last_error = error
            if attempt < retries:
                time.sleep(min(2**attempt, 30))
    raise SystemExit(f"range {start}-{end} failed after {retries} attempts: {last_error}")


def main() -> int:
    args = parse_args()
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    state_path = f"{output}.state"

    total = remote_size(args.url, args.timeout)
    chunk_size = args.chunk_size
    chunk_count = (total + chunk_size - 1) // chunk_size
    done = load_state(state_path, total, chunk_size)

    print(
        f"{output}: {total / 1e9:.2f} GB, {chunk_count} chunks, "
        f"{len(done)} already done, {args.connections} connections",
        flush=True,
    )

    fd = os.open(output, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.fstat(fd).st_size != total:
            os.ftruncate(fd, total)
        pending = [index for index in range(chunk_count) if index not in done]
        if not pending:
            print("already complete", flush=True)
            return 0

        started = time.time()
        initial_bytes = sum(
            min(chunk_size, total - index * chunk_size) for index in done
        )
        completed_bytes = initial_bytes
        lock = threading.Lock()

        def work(index: int) -> int:
            start = index * chunk_size
            end = min(start + chunk_size, total) - 1
            fetch_range(args.url, start, end, fd, args.retries, args.timeout)
            return index

        with ThreadPoolExecutor(max_workers=args.connections) as pool:
            futures = [pool.submit(work, index) for index in pending]
            for future in as_completed(futures):
                index = future.result()
                with lock:
                    done.add(index)
                    completed_bytes += min(
                        chunk_size, total - index * chunk_size
                    )
                    save_state(state_path, total, chunk_size, done)
                elapsed = max(time.time() - started, 1e-6)
                speed = (completed_bytes - initial_bytes) / elapsed
                print(
                    f"\r{completed_bytes / total * 100:5.1f}%  "
                    f"{completed_bytes / 1e9:6.2f}/{total / 1e9:.2f} GB  "
                    f"{speed / 1e6:5.1f} MB/s",
                    end="",
                    flush=True,
                )
        os.fsync(fd)
    finally:
        os.close(fd)

    if os.path.getsize(output) != total:
        raise SystemExit(f"size mismatch: {os.path.getsize(output)} != {total}")
    os.remove(state_path)
    print(f"\ndone: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
