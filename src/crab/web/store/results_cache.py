"""On-disk cache of CSV result trees fetched from a cluster over SFTP (plan 065).

Populated on demand when a job's Results tab requests a fetch (``api/results.py``);
never fetched automatically. One directory per (cluster, system, data_dir basename)
under ``Settings.results_cache_dir``, mirroring the fetched tree unmodified so
``collect_result_data`` can walk it exactly as it would the live cluster directory.
System-scoped (plan 077 S4) so a CLI-only job -- which has no local registry record,
only a (cluster, system, job_basename) identity -- can be cached and found again.
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

    def path_for(self, cluster: str, system: str, data_dir_basename: str) -> Path:
        return self._settings.results_cache_dir / cluster / system / data_dir_basename

    def list_cached(self) -> list[tuple[str, str, str]]:
        """Every cached (cluster, system, job_basename) triple, ignoring stray
        entries and leftover directories from plan 065's 2-level
        (``<cluster>/<job_basename>``) cache layout, superseded by plan 077 S4's
        3-level one. Walking a leftover 065 tree with the 3-level assumption
        misreads its job_basename as a "system" and one of its experiment
        subfolders as the "job_basename". A genuine job_basename leaf always
        contains experiment SUBFOLDERS (mirroring the fetched tree, which is
        never CSVs directly at the job level); a misread experiment leaf
        contains CSV files directly, with no subdirectories -- that's the
        signal used to tell them apart, rather than deleting/migrating old
        cache state.
        """
        root = self._settings.results_cache_dir
        if not root.is_dir():
            return []
        triples = []
        for cluster_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for system_dir in sorted(p for p in cluster_dir.iterdir() if p.is_dir()):
                for job_dir in sorted(p for p in system_dir.iterdir() if p.is_dir()):
                    if any(child.is_dir() for child in job_dir.iterdir()):
                        triples.append((cluster_dir.name, system_dir.name, job_dir.name))
        return triples

    def total_size(self) -> int:
        root = self._settings.results_cache_dir
        if not root.is_dir():
            return 0
        return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())

    def clear(self) -> None:
        root = self._settings.results_cache_dir
        if root.is_dir():
            shutil.rmtree(root)
