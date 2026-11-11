#!/usr/bin/env python3
"""Prefetched concurrency benchmark for the remote vision model.

All thumbnails are downloaded before timing starts, so the measured wall time
reflects model capacity only (no Immich round-trips in the hot path). Results
are printed only; nothing is written to Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import httpx
from pathlib import Path

sys.path.insert(0, "/app/src")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from immich_omni import db  # noqa: E402
from immich_omni.config import Config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--configs", default="4,8,12")
    parser.add_argument("--timeout", type=int, default=1200)
    return parser.parse_args()


def mime_of(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"RIFF"):
        return "image/webp"
    return "image/jpeg"


async def fetch_images(
    cfg: Config, asset_ids: list[str]
) -> list[tuple[str, bytes, str]]:
    headers = {"x-api-key": cfg.immich_api_key}
    async with httpx.AsyncClient(
        base_url=cfg.immich_url, headers=headers, timeout=120
    ) as client:
        async def one(asset_id: str) -> tuple[str, bytes, str]:
            response = await client.get(
                f"/api/assets/{asset_id}/thumbnail", params={"size": "preview"}
            )
            response.raise_for_status()
            return asset_id, response.content, mime_of(response.content)

        return list(await asyncio.gather(*[one(asset_id) for asset_id in asset_ids]))


async def run_config(
    cfg: Config, images: list[tuple[str, bytes, str]], concurrency: int
) -> None:
    import base64

    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    lengths: list[int] = []
    errors: list[str] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(1200.0, connect=30.0)
    ) as client:
        async def one(asset_id: str, raw: bytes, mime: str) -> None:
            async with semaphore:
                payload = {
                    "model": cfg.describe_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": cfg.describe_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime};base64,"
                                        + base64.b64encode(raw).decode()
                                    },
                                },
                            ],
                        }
                    ],
                    "stream": False,
                }
                started = time.perf_counter()
                try:
                    response = await client.post(
                        f"{cfg.describe_host}/v1/chat/completions", json=payload
                    )
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"]
                    latencies.append(time.perf_counter() - started)
                    lengths.append(len(content))
                except Exception as error:  # noqa: BLE001 - benchmark
                    errors.append(f"{asset_id[:8]}: {error}")

        started = time.perf_counter()
        await asyncio.gather(
            *[one(asset_id, raw, mime) for asset_id, raw, mime in images]
        )
        elapsed = time.perf_counter() - started

    ok = len(latencies)
    avg = statistics.mean(latencies) if latencies else 0
    p50 = statistics.median(latencies) if latencies else 0
    print(
        f"N={concurrency:2d}  elapsed={elapsed:6.1f}s  ok={ok:2d}/{len(images)}  "
        f"err={len(errors):2d}  avg={avg:5.1f}s  p50={p50:5.1f}s  "
        f"chars={int(statistics.mean(lengths)) if lengths else 0}  "
        f"throughput={ok / elapsed * 60 if elapsed else 0:4.1f}/min",
        flush=True,
    )
    for error in errors[:3]:
        print(f"   error: {error}", flush=True)


async def main() -> None:
    args = parse_args()
    cfg = Config.from_env()
    with db.connect(cfg) as conn:
        rows = conn.execute(
            """
            SELECT asset_id::text AS asset_id FROM omni.assets
            WHERE status = 'pending' ORDER BY file_created_at DESC NULLS LAST
            LIMIT %s
            """,
            (args.count,),
        ).fetchall()
    asset_ids = [row["asset_id"] for row in rows]
    print(f"prefetching {len(asset_ids)} thumbnails ...", flush=True)
    prefetch_started = time.perf_counter()
    images = await fetch_images(cfg, asset_ids)
    print(
        f"prefetched in {time.perf_counter() - prefetch_started:.1f}s "
        f"({sum(len(raw) for _, raw, _ in images) / 1e6:.1f} MB)",
        flush=True,
    )
    for concurrency in [int(value) for value in args.configs.split(",")]:
        await run_config(cfg, images, concurrency)


if __name__ == "__main__":
    asyncio.run(main())
