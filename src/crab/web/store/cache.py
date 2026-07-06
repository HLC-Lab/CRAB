"""Local fallback cache for data fetched from a cluster over SSH (plan 075).

Read-only cluster data (job logs, per-app error logs, `crab history` rows) is
never authoritative on the laptop (ADR-002: the engine on the cluster is
authoritative) — this cache exists only so a disconnected cluster doesn't
blank out a view that was already fetched once. Callers always try the live
call first and fall back to this only on failure (see `web/api/jobs.py`).

One JSON file per `(scope, key)` under `Settings.cache_dir`, degrading
gracefully on any read problem (missing/corrupt file) rather than raising —
same convention as `cli/contract.py`'s gatherers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crab.web.settings import Settings, get_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_key(key: str) -> str:
    return key.replace("/", "_")


class LocalCache:
    """Cheap to construct; reads/writes on demand, one file per entry."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _path(self, scope: str, key: str) -> Path:
        return self._settings.cache_dir / scope / f"{_safe_key(key)}.json"

    def write(self, scope: str, key: str, payload: dict[str, Any]) -> None:
        path = self._path(scope, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"fetched_at": _now(), "data": payload}, indent=2))
        tmp.replace(path)  # atomic

    def read(self, scope: str, key: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(scope, key).read_text())
        except (OSError, json.JSONDecodeError):
            return None
