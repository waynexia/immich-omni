from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict

import httpx
import psycopg
from psycopg.rows import dict_row

from . import db, llama
from .config import Config

_MODEL_CACHE: str | None = None
DEFAULT_CLIP_MODEL = "nllb-clip-large-siglip__mrl"


@dataclass
class Hit:
    asset_id: str
    file_name: str | None
    description: str | None
    score: float
    qwen_rank: int | None
    clip_rank: int | None
    qwen_similarity: float | None
    clip_similarity: float | None
    has_embedding: bool


def _clip_model_name(cfg: Config) -> str:
    """Read the configured CLIP model from Immich's system metadata."""
    global _MODEL_CACHE
    if _MODEL_CACHE:
        return _MODEL_CACHE
    try:
        with psycopg.connect(cfg.admin_dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT (value::jsonb -> 'machineLearning' -> 'clip' ->> 'modelName') AS name
                FROM system_metadata WHERE key = 'system-config'
                """
            ).fetchone()
        _MODEL_CACHE = (row or {}).get("name") or DEFAULT_CLIP_MODEL
    except Exception:  # noqa: BLE001 - fall back to the known model
        _MODEL_CACHE = DEFAULT_CLIP_MODEL
    return _MODEL_CACHE


def _clip_embedding(cfg: Config, query: str) -> list[float]:
    """Encode text with the same CLIP model Immich uses for smart search."""
    entries = {"clip": {"textual": {"modelName": _clip_model_name(cfg)}}}
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        response = client.post(
            f"{cfg.machine_learning_url}/predict",
            files={
                "entries": (None, json.dumps(entries)),
                "text": (None, query),
            },
        )
        response.raise_for_status()
    raw = response.json()["clip"]
    return json.loads(raw) if isinstance(raw, str) else raw


def _clip_from_db(cfg: Config, query: str, limit: int) -> list[dict]:
    """Query smart_search directly and keep the raw cosine similarity."""
    embedding = _clip_embedding(cfg, query)
    literal = "[" + ",".join(f"{value:.7f}" for value in embedding) + "]"
    with psycopg.connect(cfg.admin_dsn, row_factory=dict_row) as conn:
        # The clip_index is built with a single list, matching Immich's probes=1.
        conn.execute(f"SET LOCAL vchordrq.probes = {int(cfg.clip_probes)}")
        return conn.execute(
            """
            SELECT smart_search."assetId"::text AS asset_id,
                   1 - (smart_search.embedding <=> %s::vector) AS similarity
            FROM smart_search
            JOIN asset ON asset.id = smart_search."assetId"
            WHERE asset."deletedAt" IS NULL
              AND asset.visibility IN ('timeline', 'archive')
            ORDER BY smart_search.embedding <=> %s::vector
            LIMIT %s
            """,
            (literal, literal, limit),
        ).fetchall()


def _clip_from_api(cfg: Config, query: str, limit: int) -> list[dict]:
    """Fallback to the public smart-search endpoint (ordering only)."""
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
    items = assets if isinstance(assets, list) else assets.get("items", [])
    return [
        {"asset_id": item["id"], "similarity": None}
        for item in items
        if item.get("type") == "IMAGE"
    ]


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


def search(cfg: Config, query: str, limit: int = 50, rrf_k: int = 60) -> dict:
    """Fuse Qwen description vectors with Immich CLIP vectors via weighted RRF."""
    started = time.perf_counter()
    candidates = min(max(limit * 4, 200), 500)

    qwen_started = time.perf_counter()
    qwen_rows = _qwen_ranked(cfg, query, candidates)
    qwen_ms = (time.perf_counter() - qwen_started) * 1000

    clip_started = time.perf_counter()
    clip_error: str | None = None
    clip_source = "db"
    try:
        clip_rows = _clip_from_db(cfg, query, candidates)
    except Exception as db_error:  # noqa: BLE001 - fall back to the API
        try:
            clip_rows = _clip_from_api(cfg, query, candidates)
            clip_source = "api-fallback"
            clip_error = str(db_error)
        except Exception as api_error:  # noqa: BLE001
            clip_rows = []
            clip_source = "unavailable"
            clip_error = f"db: {db_error}; api: {api_error}"
    clip_ms = (time.perf_counter() - clip_started) * 1000

    qwen_rank = {
        row["asset_id"]: rank for rank, row in enumerate(qwen_rows, start=1)
    }
    qwen_similarity = {
        row["asset_id"]: row["similarity"] for row in qwen_rows
    }
    clip_rank = {
        row["asset_id"]: rank for rank, row in enumerate(clip_rows, start=1)
    }
    clip_similarity = {
        row["asset_id"]: row["similarity"] for row in clip_rows
    }
    union = list(qwen_rank) + [
        asset_id for asset_id in clip_rank if asset_id not in qwen_rank
    ]

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
                file_name=row.get("original_file_name"),
                description=row.get("description"),
                score=score,
                qwen_rank=qwen_rank.get(asset_id),
                clip_rank=clip_rank.get(asset_id),
                qwen_similarity=qwen_similarity.get(asset_id),
                clip_similarity=clip_similarity.get(asset_id),
                has_embedding=bool(row.get("has_embedding")),
            )
        )

    hits.sort(
        key=lambda hit: (
            -hit.score,
            -max(hit.qwen_similarity or 0.0, hit.clip_similarity or 0.0),
            hit.qwen_rank if hit.qwen_rank is not None else 10**9,
            hit.clip_rank if hit.clip_rank is not None else 10**9,
        )
    )
    results = [asdict(hit) for hit in hits[:limit]]
    return {
        "query": query,
        "results": results,
        "clip_source": clip_source,
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
