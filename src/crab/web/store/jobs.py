"""Local registry of submitted jobs (Phase 4: submit & monitor).

Live status/logs always come from a fresh ``crab status``/``crab logs --json`` call
(the API layer's job); this store only persists what the dashboard itself recorded
at submit time, plus the last state a poll observed. Stored as one JSON array file
at ``settings.jobs_file`` (unlike the config library's one-file-per-entry — job
records are backend-managed only, never hand-edited, so a single array file is
simpler and sufficient), atomic write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from crab.web.errors import NotFoundError
from crab.web.settings import Settings, get_settings

JOBS_VERSION = 1


def _now() -> str:
    # Microsecond precision (unlike the config library's seconds): back-to-back
    # submissions must still sort newest-first in the jobs list.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class JobRecord(BaseModel):
    id: str
    cluster: str
    job_id: str
    data_dir: str
    system: str
    config_name: str
    config_snapshot: dict
    submitted_at: str
    last_known_state: str = "UNKNOWN"


class _Store(BaseModel):
    version: int = JOBS_VERSION
    jobs: list[JobRecord] = Field(default_factory=list)


class JobsStore:
    """CRUD over the local job registry. Cheap to construct; reads on demand."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def _path(self) -> Path:
        return self._settings.jobs_file

    def _read(self) -> _Store:
        if not self._path.is_file():
            return _Store()
        return _Store.model_validate_json(self._path.read_text())

    def _write(self, store: _Store) -> None:
        self._settings.ensure_dirs()
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(store.model_dump_json(indent=2))
        tmp.replace(self._path)  # atomic

    def list(self) -> list[JobRecord]:
        return sorted(self._read().jobs, key=lambda j: j.submitted_at, reverse=True)

    def get(self, record_id: str) -> JobRecord:
        for rec in self._read().jobs:
            if rec.id == record_id:
                return rec
        raise NotFoundError(f"No job record with id {record_id!r}.")

    def create(
        self,
        *,
        cluster: str,
        job_id: str,
        data_dir: str,
        system: str,
        config_name: str,
        config_snapshot: dict,
    ) -> JobRecord:
        rec = JobRecord(
            id=f"{cluster}:{job_id}",
            cluster=cluster,
            job_id=job_id,
            data_dir=data_dir,
            system=system,
            config_name=config_name,
            config_snapshot=config_snapshot,
            submitted_at=_now(),
        )
        store = self._read()
        store.jobs.append(rec)
        self._write(store)
        return rec

    def update(self, record_id: str, *, last_known_state: str) -> JobRecord:
        store = self._read()
        for i, rec in enumerate(store.jobs):
            if rec.id == record_id:
                updated = rec.model_copy(update={"last_known_state": last_known_state})
                store.jobs[i] = updated
                self._write(store)
                return updated
        raise NotFoundError(f"No job record with id {record_id!r}.")
