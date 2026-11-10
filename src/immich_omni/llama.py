from __future__ import annotations

import math
import time

import httpx

from .config import Config


def normalize(values: list[float], dim: int) -> list[float]:
    """Matryoshka truncation to ``dim`` followed by L2 normalisation."""
    truncated = values[:dim]
    norm = math.sqrt(sum(value * value for value in truncated)) or 1.0
    return [value / norm for value in truncated]


def embed(cfg: Config, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    with httpx.Client(timeout=cfg.timeout) as client:
        response = client.post(
            f"{cfg.llama_url}/v1/embeddings",
            json={"model": cfg.llama_model, "input": texts},
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
    return [normalize(item["embedding"], cfg.embed_dim) for item in data]


def query_text(cfg: Config, query: str) -> str:
    if not cfg.query_instruction:
        return query
    return f"Instruct: {cfg.query_instruction}\nQuery: {query}"


def wait_ready(cfg: Config, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{cfg.llama_url}/health")
                if response.status_code == 200:
                    return
        except httpx.HTTPError as error:
            last_error = error
        time.sleep(3)
    raise SystemExit(f"llama.cpp server at {cfg.llama_url} not ready: {last_error}")
