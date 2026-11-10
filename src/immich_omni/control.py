from __future__ import annotations

from typing import Any

import psycopg

from .config import Config


def ensure_control(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO omni.control (id) VALUES (true) ON CONFLICT DO NOTHING")
    conn.commit()


def is_paused(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT paused FROM omni.control WHERE id").fetchone()
    return bool(row and row["paused"])


def set_paused(conn: psycopg.Connection, paused: bool) -> None:
    conn.execute(
        "UPDATE omni.control SET paused = %s, updated_at = now() WHERE id",
        (paused,),
    )
    conn.commit()


def touch_worker(conn: psycopg.Connection) -> None:
    conn.execute("UPDATE omni.control SET worker_seen_at = now() WHERE id")
    conn.commit()


def snapshot(conn: psycopg.Connection) -> dict[str, Any]:
    counts = conn.execute(
        """
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE status = 'pending')    AS pending,
            count(*) FILTER (WHERE status = 'described')  AS described,
            count(*) FILTER (WHERE status = 'embedded')   AS embedded,
            count(*) FILTER (WHERE status = 'failed')     AS failed,
            count(*) FILTER (
                WHERE description_at > now() - interval '30 minutes'
            ) AS described_window,
            extract(epoch FROM (
                now() - min(description_at) FILTER (
                    WHERE description_at > now() - interval '30 minutes'
                )
            )) AS window_span
        FROM omni.assets
        """
    ).fetchone()
    state = conn.execute(
        """
        SELECT paused,
               worker_seen_at,
               extract(epoch FROM (now() - worker_seen_at)) AS worker_age
        FROM omni.control WHERE id
        """
    ).fetchone()

    total = int(counts["total"])
    pending = int(counts["pending"])
    described = int(counts["described"])
    embedded = int(counts["embedded"])
    failed = int(counts["failed"])
    described_window = int(counts["described_window"])
    span = float(counts["window_span"] or 0)

    # Rolling 30 minute average: adapts to the shared remote model's speed
    # without the noise of a very short window.
    rate_per_sec = described_window / max(span, 60.0) if described_window else 0.0
    eta_seconds = pending / rate_per_sec if rate_per_sec > 0 and pending else None

    return {
        "total": total,
        "pending": pending,
        "described": described,
        "embedded": embedded,
        "failed": failed,
        "described_window": described_window,
        "rate_per_min": round(rate_per_sec * 60, 2),
        "eta_seconds": int(eta_seconds) if eta_seconds else None,
        "paused": bool(state["paused"]) if state else False,
        "worker_seen_at": state["worker_seen_at"].isoformat()
        if state and state["worker_seen_at"]
        else None,
        "worker_alive": bool(
            state
            and state["worker_age"] is not None
            and state["worker_age"] < 60
        ),
    }
