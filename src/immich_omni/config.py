from __future__ import annotations

import os
import re
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo

DEFAULT_DESCRIBE_PROMPT = (
    "请为这张图片生成一段用于中文搜索的详细描述，只输出描述本身，不要任何开场白。"
    "用中文覆盖：主要对象、人物及其动作和关系、场景与地点、画面中可见的文字、"
    "颜色与材质、食物/物品/动物/建筑等具体类别，以及拍摄类型"
    "（自拍、截图、壁纸、城市、风景、美食、文档、表情包等）。"
    "尽量使用用户可能搜索的关键词，控制在 120 字以内。"
)

DEFAULT_QUERY_INSTRUCTION = "给定一句中文图片搜索词，找出与描述最匹配的图片"

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env(name: str, default: str = "", *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"environment variable {name} is required")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    immich_url: str
    immich_api_key: str
    pg_host: str
    pg_port: int
    pg_db: str
    pg_admin_user: str
    pg_admin_password: str
    pg_user: str
    pg_password: str
    llama_url: str
    llama_model: str
    embed_dim: int
    describe_host: str
    describe_model: str
    describe_prompt: str
    analyze_bin: str
    work_dir: str
    thumbnail_size: str
    max_concurrent: int
    timeout: int
    query_instruction: str

    @classmethod
    def from_env(cls) -> Config:
        pg_user = _env("OMNI_DB_USER", "omni")
        if not _IDENTIFIER.match(pg_user):
            raise SystemExit(f"invalid OMNI_DB_USER: {pg_user!r}")
        return cls(
            immich_url=_env("IMMICH_URL", "http://immich_server:2283").rstrip("/"),
            immich_api_key=_env("IMMICH_API_KEY", required=True),
            pg_host=_env("IMMICH_PG_HOST", "immich_postgres"),
            pg_port=_int_env("IMMICH_PG_PORT", 5432),
            pg_db=_env("IMMICH_PG_DB", "immich"),
            pg_admin_user=_env("IMMICH_PG_ADMIN_USER", "postgres"),
            pg_admin_password=_env("IMMICH_DB_PASSWORD", required=True),
            pg_user=pg_user,
            pg_password=_env("OMNI_DB_PASSWORD", required=True),
            llama_url=_env("LLAMA_URL", "http://llama-embed:8080").rstrip("/"),
            llama_model=_env("LLAMA_MODEL", "qwen3-embedding-8b"),
            embed_dim=_int_env("EMBED_DIM", 1024),
            describe_host=_env(
                "DESCRIBE_HOST", "https://mbp-dpsk.tailc4cf9.ts.net"
            ).rstrip("/"),
            describe_model=_env(
                "DESCRIBE_MODEL", "mlx-community/Qwen3-VL-32B-Instruct-4bit"
            ),
            describe_prompt=_env("DESCRIBE_PROMPT", DEFAULT_DESCRIBE_PROMPT),
            analyze_bin=_env("ANALYZE_BIN", "/usr/local/bin/immich-analyze"),
            work_dir=_env("WORK_DIR", "/work"),
            thumbnail_size=_env("THUMBNAIL_SIZE", "preview"),
            max_concurrent=_int_env("ANALYZE_CONCURRENCY", 2),
            timeout=_int_env("ANALYZE_TIMEOUT", 300),
            query_instruction=_env(
                "QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION
            ),
        )

    @property
    def admin_dsn(self) -> str:
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.pg_db,
            user=self.pg_admin_user,
            password=self.pg_admin_password,
        )

    @property
    def app_dsn(self) -> str:
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.pg_db,
            user=self.pg_user,
            password=self.pg_password,
        )
