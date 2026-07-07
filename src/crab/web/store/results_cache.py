"""On-disk cache of CSV result trees fetched from a cluster over SFTP (plan 065).

Populated on demand when a job's Results tab requests a fetch (``api/results.py``);
never fetched automatically. One directory per (cluster, data_dir basename) under
``Settings.results_cache_dir``, mirroring the fetched tree unmodified so
``collect_result_data`` can walk it exactly as it would the live cluster directory.
No automatic eviction (owner decision, plan 065's Context) -- ``total_size()`` and
``clear()`` back the cache-management UI instead.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from crab.web.settings import Settings, get_settings


class ResultsCache:
    """Cheap to construct; paths are computed on demand, nothing cached in memory."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def path_for(self, cluster: str, data_dir_basename: str) -> Path:
        return self._settings.results_cache_dir / cluster / data_dir_basename

    def total_size(self) -> int:
        root = self._settings.results_cache_dir
        if not root.is_dir():
            return 0
        return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())

    def clear(self) -> None:
        root = self._settings.results_cache_dir
        if root.is_dir():
            shutil.rmtree(root)
