from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from dataclasses import dataclass
from threading import Event

import httpx

from . import control, db
from .config import Config


@dataclass
class Item:
    asset_id: str
    image: bytes
    mime: str


def _mime_of(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"RIFF"):
        return "image/webp"
    return "image/jpeg"


def _store_success(
    conn, cfg: Config, asset_id: str, description: str
) -> None:
    conn.execute(
        """
        UPDATE omni.assets SET
            description = %s,
            status = 'described',
            description_model = %s,
            description_at = now(),
            attempts = attempts + 1,
            last_error = NULL,
            updated_at = now()
        WHERE asset_id = %s AND status = 'pending'
        """,
        (description, cfg.describe_model, asset_id),
    )
    conn.commit()


def _store_failure(conn, asset_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE omni.assets SET
            attempts = attempts + 1,
            last_error = %s,
            status = CASE WHEN attempts + 1 >= 3 THEN 'failed' ELSE 'pending' END,
            updated_at = now()
        WHERE asset_id = %s AND status = 'pending'
        """,
        (error[:2000], asset_id),
    )
    conn.commit()


async def _fetch_thumbnail(
    client: httpx.AsyncClient, cfg: Config, asset_id: str, sem: asyncio.Semaphore
) -> Item:
    async with sem:
        response = await client.get(
            f"/api/assets/{asset_id}/thumbnail",
            params={"size": cfg.thumbnail_size},
        )
        response.raise_for_status()
        return Item(asset_id, response.content, _mime_of(response.content))


async def _analyze(
    client: httpx.AsyncClient, cfg: Config, item: Item, retries: int = 2
) -> str:
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
                            "url": f"data:{item.mime};base64,"
                            + base64.b64encode(item.image).decode()
                        },
                    },
                ],
            }
        ],
        "stream": False,
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.post(
                f"{cfg.describe_host}/v1/chat/completions", json=payload
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not content or not content.strip():
                raise ValueError("empty description")
            return content.strip()
        except Exception as error:  # noqa: BLE001 - retried below
            last_error = error
            if attempt < retries:
                await asyncio.sleep(3 * (attempt + 1))
    raise RuntimeError(f"model request failed: {last_error}")


async def run(
    cfg: Config,
    limit: int,
    prefetch: int = 32,
    stop_flag: Event | None = None,
) -> tuple[int, int]:
    """Describe up to ``limit`` pending assets with a prefetch queue.

    Thumbnails are fetched ahead of the model workers so the remote service
    always has a request ready. Only completed descriptions are committed;
    anything in flight or still queued stays ``pending``.
    """
    stop_flag = stop_flag or Event()
    with db.connect(cfg) as conn:
        rows = conn.execute(
            """
            SELECT asset_id::text AS asset_id
            FROM omni.assets
            WHERE status = 'pending' AND attempts < 3
            ORDER BY file_created_at DESC NULLS LAST, asset_id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    asset_ids = [row["asset_id"] for row in rows]
    if not asset_ids:
        return (0, 0)

    concurrency = max(1, cfg.max_concurrent)
    queue: asyncio.Queue[Item | None] = asyncio.Queue(maxsize=max(prefetch, concurrency * 2))
    write_conn = db.connect(cfg)
    counts = {"ok": 0, "failed": 0}
    started = time.time()

    def paused() -> bool:
        return stop_flag.is_set() or control.is_paused(write_conn)

    async def producer() -> None:
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(
            base_url=cfg.immich_url,
            headers={"x-api-key": cfg.immich_api_key},
            timeout=httpx.Timeout(120.0, connect=30.0),
        ) as client:
            for start in range(0, len(asset_ids), prefetch):
                if paused():
                    break
                batch = asset_ids[start : start + prefetch]
                items = await asyncio.gather(
                    *[_fetch_thumbnail(client, cfg, aid, sem) for aid in batch],
                    return_exceptions=True,
                )
                for item in items:
                    if isinstance(item, BaseException):
                        continue
                    await queue.put(item)
        for _ in range(concurrency):
            await queue.put(None)

    async def worker(index: int) -> None:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(1200.0, connect=30.0)
        ) as client:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if paused():
                    return
                try:
                    description = await _analyze(client, cfg, item)
                    _store_success(write_conn, cfg, item.asset_id, description)
                    counts["ok"] += 1
                except Exception as error:  # noqa: BLE001 - recorded per asset
                    _store_failure(write_conn, item.asset_id, str(error))
                    counts["failed"] += 1
                total = counts["ok"] + counts["failed"]
                if total % 8 == 0:
                    elapsed = max(time.time() - started, 1e-6)
                    print(
                        f"  described={counts['ok']} failed={counts['failed']} "
                        f"rate={counts['ok'] / elapsed * 60:.1f}/min",
                        flush=True,
                    )

    producer_task = asyncio.create_task(producer())
    workers = [asyncio.create_task(worker(i)) for i in range(concurrency)]
    try:
        await asyncio.gather(*workers)
    finally:
        producer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer_task
        write_conn.close()

    print(
        f"http describe chunk done: ok={counts['ok']} failed={counts['failed']} "
        f"requested={len(asset_ids)}",
        flush=True,
    )
    return (counts["ok"], counts["failed"])
