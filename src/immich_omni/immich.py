from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from .config import Config


class ImmichClient:
    """Thin wrapper over the Immich REST API.

    Only the endpoints needed by the pipeline are implemented. Images are always
    fetched through ``/api/assets/{id}/thumbnail``; the mounted library is never
    touched.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.immich_url,
            headers={"x-api-key": cfg.immich_api_key},
            timeout=httpx.Timeout(120.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ImmichClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def iter_image_assets(
        self, page_size: int = 1000, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        page = 1
        emitted = 0
        while True:
            response = self._client.post(
                "/api/search/metadata",
                json={
                    "page": page,
                    "size": page_size,
                    "type": "IMAGE",
                    "withExif": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            assets = payload.get("assets", [])
            items = assets if isinstance(assets, list) else assets.get("items", [])
            for asset in items:
                yield asset
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
            if len(items) < page_size:
                return
            page += 1

    def thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        response = self._client.get(
            f"/api/assets/{asset_id}/thumbnail", params={"size": size}
        )
        response.raise_for_status()
        return response.content

    def asset(self, asset_id: str) -> dict[str, Any]:
        response = self._client.get(f"/api/assets/{asset_id}")
        response.raise_for_status()
        return response.json()
