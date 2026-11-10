from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import Config

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


def connect(cfg: Config) -> psycopg.Connection:
    return psycopg.connect(cfg.app_dsn, row_factory=dict_row)


def admin_connect(cfg: Config) -> psycopg.Connection:
    return psycopg.connect(cfg.admin_dsn, autocommit=True)


def vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{value:.7f}" for value in values) + "]"


def apply_schema(conn: psycopg.Connection) -> None:
    statements = [part.strip() for part in SCHEMA_PATH.read_text().split(";") if part.strip()]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def chunks(items: list[dict], size: int) -> Iterator[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
