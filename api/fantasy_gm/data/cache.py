"""Raw-response disk cache so backfill is resumable and idempotent.

Each response is stored under a deterministic key derived from endpoint + params. A
cached key is never re-fetched, so an interrupted backfill resumes without duplicating
work or hammering the source.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class RawCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(endpoint: str, params: dict[str, Any]) -> str:
        payload = endpoint + "|" + json.dumps(params, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _path(self, endpoint: str, params: dict[str, Any]) -> Path:
        return self.cache_dir / f"{self._key(endpoint, params)}.json"

    def has(self, endpoint: str, params: dict[str, Any]) -> bool:
        return self._path(endpoint, params).exists()

    def get(self, endpoint: str, params: dict[str, Any]) -> Any | None:
        p = self._path(endpoint, params)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def set(self, endpoint: str, params: dict[str, Any], value: Any) -> None:
        self._path(endpoint, params).write_text(json.dumps(value))
