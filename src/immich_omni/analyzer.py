from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .config import Config


def _command(cfg: Config, ids_path: Path) -> list[str]:
    return [
        cfg.analyze_bin,
        "--jsonl",
        "--assets-file",
        str(ids_path),
        "--no-writeback",
        "--overwrite-policy",
        "all",
        "--disable-ai-wrapper",
        "--no-final-output",
        "--no-preflight-model-check",
        "--no-wait-for-immich",
        "--health-port",
        "0",
        "--interface",
        "openrouter",
        "--hosts",
        cfg.describe_host,
        "--model-name",
        cfg.describe_model,
        "--prompt",
        cfg.describe_prompt,
        "--thumbnail-size",
        cfg.thumbnail_size,
        "--max-concurrent",
        str(cfg.max_concurrent),
        "--timeout",
        str(cfg.timeout),
        "--max-retries",
        "2",
    ]


def run(cfg: Config, asset_ids: list[str]) -> Iterator[dict[str, Any]]:
    """Run the patched immich-analyze binary and yield one record per asset.

    Output is line-delimited JSON. The child process never writes descriptions
    back to Immich (``--no-writeback``); the caller persists them into the
    ``omni`` schema instead.
    """
    work_dir = Path(cfg.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{os.getpid()}-{int(time.time())}"
    ids_path = work_dir / f"assets-{stamp}.txt"
    log_path = work_dir / "analyzer.log"
    ids_path.write_text("\n".join(asset_ids) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["IMMICH_API_URL"] = cfg.immich_url
    env["IMMICH_API_KEY"] = cfg.immich_api_key
    env["IMMICH_ANALYZE_LANG"] = "en"

    process: subprocess.Popen[str] | None = None
    try:
        with log_path.open("ab", buffering=0) as log:
            process = subprocess.Popen(
                _command(cfg, ids_path),
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                bufsize=1,
                env=env,
            )
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
            process.wait()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        ids_path.unlink(missing_ok=True)
