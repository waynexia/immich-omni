from __future__ import annotations

import time
from dataclasses import dataclass, asdict

import httpx

from . import db, llama
from .config import Config


@dataclass
class Hit:
    asset_id: str
    file_name: str | None
    description: str | None
    score: float
    qwen_rank: int | None
    clip_rank: int | None
    has_embedding: bool


def _qwen_ranked(cfg: Config, query: str, limit: int) -> list[dict]:
    vector = llama.embed(cfg, [llama.query_text(cfg, query)], timeout=30.0)[0]
    literal = db.vector_literal(vector)
    with db.connect(cfg) as conn:
        return conn.execute(
            """
            SELECT asset_id::text AS asset_id,
                   description,
                   1 - (embedding <=> %s::public.vector) AS similarity
            FROM omni.assets
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::public.vector
            LIMIT %s
            """,
            (literal, literal, limit),
        ).fetchall()


def _clip_ranked(cfg: Config, query: str, limit: int) -> list[dict]:
    with httpx.Client(
        base_url=cfg.immich_url,
        headers={"x-api-key": cfg.immich_api_key},
        timeout=httpx.Timeout(120.0, connect=30.0),
    ) as client:
        response = client.post(
            "/api/search/smart", json={"query": query, "size": limit}
        )
        response.raise_for_status()
        payload = response.json()
    assets = payload.get("assets", {})
    return assets if isinstance(assets, list) else assets.get("items", [])


def search(cfg: Config, query: str, limit: int = 50, rrf_k: int = 60) -> dict:
    """Fuse Qwen description-vector search with Immich CLIP search via RRF."""
    started = time.perf_counter()
    candidates = max(limit, 50)

    qwen_started = time.perf_counter()
    qwen_rows = _qwen_ranked(cfg, query, candidates)
    qwen_ms = (time.perf_counter() - qwen_started) * 1000

    clip_started = time.perf_counter()
    clip_error: str | None = None
    try:
        clip_items = _clip_ranked(cfg, query, candidates)
    except Exception as error:  # noqa: BLE001 - the Qwen arm still works
        clip_items = []
        clip_error = str(error)
    clip_ms = (time.perf_counter() - clip_started) * 1000

    qwen_rank = {
        row["asset_id"]: rank for rank, row in enumerate(qwen_rows, start=1)
    }
    clip_rank = {
        item["id"]: rank for rank, item in enumerate(clip_items, start=1)
    }
    clip_names = {
        item["id"]: item.get("originalFileName") for item in clip_items
    }
    union = list(qwen_rank) + [item_id for item_id in clip_rank if item_id not in qwen_rank]

    meta: dict[str, dict] = {}
    if union:
        with db.connect(cfg) as conn:
            rows = conn.execute(
                """
                SELECT asset_id::text AS asset_id,
                       original_file_name,
                       description,
                       (embedding IS NOT NULL) AS has_embedding
                FROM omni.assets
                WHERE asset_id = ANY(%s::uuid[])
                """,
                (union,),
            ).fetchall()
        meta = {row["asset_id"]: row for row in rows}

    hits: list[Hit] = []
    for asset_id in union:
        score = 0.0
        if asset_id in qwen_rank:
            score += cfg.rrf_qwen_weight / (rrf_k + qwen_rank[asset_id])
        if asset_id in clip_rank:
            score += cfg.rrf_clip_weight / (rrf_k + clip_rank[asset_id])
        row = meta.get(asset_id, {})
        hits.append(
            Hit(
                asset_id=asset_id,
                file_name=row.get("original_file_name") or clip_names.get(asset_id),
                description=row.get("description"),
                score=score,
                qwen_rank=qwen_rank.get(asset_id),
                clip_rank=clip_rank.get(asset_id),
                has_embedding=bool(row.get("has_embedding")),
            )
        )

    hits.sort(
        key=lambda hit: (
            -hit.score,
            hit.qwen_rank if hit.qwen_rank is not None else 10**9,
            hit.clip_rank if hit.clip_rank is not None else 10**9,
        )
    )
    results = [asdict(hit) for hit in hits[:limit]]
    return {
        "query": query,
        "results": results,
        "clip_error": clip_error,
        "weights": {
            "qwen": cfg.rrf_qwen_weight,
            "clip": cfg.rrf_clip_weight,
        },
        "timings": {
            "qwen_ms": qwen_ms,
            "clip_ms": clip_ms,
            "total_ms": (time.perf_counter() - started) * 1000,
        },
    }
