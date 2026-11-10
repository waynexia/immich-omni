from __future__ import annotations

import argparse
import sys
from typing import Any

from psycopg import sql

from . import analyzer, db, llama
from .config import Config
from .immich import ImmichClient


def cmd_init_db(cfg: Config) -> int:
    with db.admin_connect(cfg) as admin:
        role = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (cfg.pg_user,)
        ).fetchone()
        if role:
            admin.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(cfg.pg_user), sql.Literal(cfg.pg_password)
                )
            )
        else:
            admin.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(cfg.pg_user), sql.Literal(cfg.pg_password)
                )
            )
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS omni AUTHORIZATION {}").format(
                sql.Identifier(cfg.pg_user)
            )
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(cfg.pg_user)
            )
        )

    with db.connect(cfg) as conn:
        db.apply_schema(conn)
    print(f"schema omni ready (owner {cfg.pg_user})")
    return 0


def cmd_sync(cfg: Config, limit: int | None = None) -> int:
    total = 0
    with ImmichClient(cfg) as client, db.connect(cfg) as conn:
        for asset in client.iter_image_assets(limit=limit):
            conn.execute(
                """
                INSERT INTO omni.assets (
                    asset_id, original_file_name, file_created_at,
                    local_date_time, width, height
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (asset_id) DO UPDATE SET
                    original_file_name = EXCLUDED.original_file_name,
                    file_created_at = EXCLUDED.file_created_at,
                    local_date_time = EXCLUDED.local_date_time,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    updated_at = now()
                """,
                (
                    asset["id"],
                    asset.get("originalFileName"),
                    asset.get("fileCreatedAt"),
                    asset.get("localDateTime"),
                    asset.get("width"),
                    asset.get("height"),
                ),
            )
            total += 1
            if total % 500 == 0:
                conn.commit()
                print(f"synced {total} assets")
        conn.commit()
    print(f"sync done: {total} image assets")
    return total


def cmd_describe(cfg: Config, limit: int) -> int:
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
        ids = [row["asset_id"] for row in rows]
        if not ids:
            print("no pending assets to describe")
            return 0

        print(f"describing {len(ids)} assets with {cfg.describe_model}")
        described = 0
        failed = 0
        for record in analyzer.run(cfg, ids):
            asset_id = record.get("asset_id")
            if not asset_id:
                continue
            status = record.get("status")
            description = (record.get("description") or "").strip()
            if status == "success" and description:
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
                    WHERE asset_id = %s
                    """,
                    (description, cfg.describe_model, asset_id),
                )
                conn.commit()
                described += 1
            else:
                error = str(record.get("error") or description or status)[:2000]
                conn.execute(
                    """
                    UPDATE omni.assets SET
                        attempts = attempts + 1,
                        last_error = %s,
                        status = CASE WHEN attempts + 1 >= 3 THEN 'failed' ELSE 'pending' END,
                        updated_at = now()
                    WHERE asset_id = %s
                    """,
                    (error, asset_id),
                )
                conn.commit()
                failed += 1
            if (described + failed) % 20 == 0:
                print(f"  described={described} failed={failed}")

    print(f"describe done: ok={described} failed={failed} requested={len(ids)}")
    return described


def cmd_embed(cfg: Config, limit: int, batch_size: int) -> int:
    with db.connect(cfg) as conn:
        rows = conn.execute(
            """
            SELECT asset_id::text AS asset_id, description
            FROM omni.assets
            WHERE status = 'described' AND embedding IS NULL AND attempts < 3
            ORDER BY description_at NULLS LAST, asset_id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        if not rows:
            print("no described assets waiting for embeddings")
            return 0

        llama.wait_ready(cfg, timeout=cfg.timeout)
        print(f"embedding {len(rows)} descriptions")
        embedded = 0
        for group in db.chunks(rows, batch_size):
            try:
                vectors = llama.embed(
                    cfg, [row["description"] or "" for row in group]
                )
            except Exception as error:  # noqa: BLE001 - persist and keep going
                message = f"embedding failed: {error}"[:2000]
                for row in group:
                    conn.execute(
                        """
                        UPDATE omni.assets SET
                            attempts = attempts + 1,
                            last_error = %s,
                            status = CASE WHEN attempts + 1 >= 3 THEN 'failed' ELSE status END,
                            updated_at = now()
                        WHERE asset_id = %s
                        """,
                        (message, row["asset_id"]),
                    )
                conn.commit()
                continue

            for row, vector in zip(group, vectors, strict=True):
                conn.execute(
                    """
                    UPDATE omni.assets SET
                        embedding = %s::public.vector,
                        status = 'embedded',
                        embedding_model = %s,
                        embedding_at = now(),
                        attempts = attempts + 1,
                        last_error = NULL,
                        updated_at = now()
                    WHERE asset_id = %s
                    """,
                    (db.vector_literal(vector), cfg.llama_model, row["asset_id"]),
                )
            conn.commit()
            embedded += len(group)
            print(f"  embedded={embedded}/{len(rows)}")

    print(f"embed done: {embedded} vectors ({cfg.embed_dim} dimensions)")
    return embedded


def cmd_search(cfg: Config, query: str, limit: int, as_json: bool) -> int:
    vector = llama.embed(cfg, [llama.query_text(cfg, query)])[0]
    literal = db.vector_literal(vector)
    with db.connect(cfg) as conn:
        rows = conn.execute(
            """
            SELECT asset_id::text AS asset_id,
                   original_file_name,
                   description,
                   1 - (embedding <=> %s::public.vector) AS score
            FROM omni.assets
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::public.vector
            LIMIT %s
            """,
            (literal, literal, limit),
        ).fetchall()

    if as_json:
        import json

        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 0

    for row in rows:
        description = (row["description"] or "").replace("\n", " ")
        print(
            f"{row['score']:.4f}  {row['asset_id']}  "
            f"{row['original_file_name'] or '-'}  {description[:100]}"
        )
    print(f"{len(rows)} results")
    return 0


def cmd_status(cfg: Config) -> int:
    with db.connect(cfg) as conn:
        rows = conn.execute(
            "SELECT status, count(*) AS n FROM omni.assets GROUP BY status ORDER BY status"
        ).fetchall()
        total = conn.execute("SELECT count(*) AS n FROM omni.assets").fetchone()["n"]
        vectors = conn.execute(
            "SELECT count(*) AS n FROM omni.assets WHERE embedding IS NOT NULL"
        ).fetchone()["n"]
    for row in rows:
        print(f"{row['status']:>10}  {row['n']}")
    print(f"{'total':>10}  {total}")
    print(f"{'vectors':>10}  {vectors}")
    return 0


def cmd_run(cfg: Config, chunk: int, sync_limit: int | None, max_rounds: int) -> int:
    cmd_sync(cfg, limit=sync_limit)
    for round_index in range(1, max_rounds + 1):
        described = cmd_describe(cfg, chunk)
        cmd_embed(cfg, chunk * 4, batch_size=8)
        if described == 0:
            break
        print(f"round {round_index} done")
    cmd_status(cfg)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="immich-omni",
        description="Chinese descriptions + Qwen3 embeddings for Immich, in a separate pg schema.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the omni role, schema and tables")

    sync = sub.add_parser("sync", help="mirror Immich image asset metadata")
    sync.add_argument("--limit", type=int, default=None)

    describe = sub.add_parser("describe", help="generate descriptions for pending assets")
    describe.add_argument("--limit", type=int, default=128)

    embed = sub.add_parser("embed", help="embed described assets")
    embed.add_argument("--limit", type=int, default=2048)
    embed.add_argument("--batch", type=int, default=8)

    search = sub.add_parser("search", help="search descriptions in Chinese")
    search.add_argument("query")
    search.add_argument("-k", "--limit", type=int, default=10)
    search.add_argument("--json", action="store_true")

    sub.add_parser("status", help="show pipeline progress")

    run = sub.add_parser("run", help="sync + describe + embed until no pending assets")
    run.add_argument("--chunk", type=int, default=64)
    run.add_argument("--sync-limit", type=int, default=None)
    run.add_argument("--max-rounds", type=int, default=10)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.from_env()
    try:
        if args.command == "init-db":
            return cmd_init_db(cfg)
        if args.command == "sync":
            cmd_sync(cfg, args.limit)
            return 0
        if args.command == "describe":
            cmd_describe(cfg, args.limit)
            return 0
        if args.command == "embed":
            cmd_embed(cfg, args.limit, args.batch)
            return 0
        if args.command == "search":
            return cmd_search(cfg, args.query, args.limit, args.json)
        if args.command == "status":
            return cmd_status(cfg)
        if args.command == "run":
            return cmd_run(cfg, args.chunk, args.sync_limit, args.max_rounds)
    except KeyboardInterrupt:
        print("\ninterrupted; progress is committed, rerun the same command to continue")
        return 130
    raise SystemExit(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
