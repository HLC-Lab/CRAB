"""Local library of SbatchMan campaign drafts (plan 086).

Each saved campaign is one JSON file ``<dir>/<id>.json`` holding
``{id, name, updated_at, spec}``. ``spec`` is the frontend's own editable
campaign state (top-level settings + each group's raw ``Draft``, `{var}`
placeholders included) — held opaque here, same treatment as
``store/library.py``'s ``LibraryEntry.config``. Unlike the experiment library,
a campaign draft is never itself submitted to `crab run`, so there is no
config-shape validation pass over it (a group's `{var}`-templated numnodes
would fail one anyway).

Lives alongside the experiment library (``library_dir`` if set, else the data
dir — ADR-014) but in its own ``campaigns/`` subfolder, so entries never mix
with the experiment library's flat ``<id>.json`` files.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from crab.web.errors import InputError, NotFoundError
from crab.web.settings import Settings, get_settings

_NON_SLUG = re.compile(r"[^a-z0-9]+")
# Ids are our own slugs; this guards path-traversal when an id comes from a URL.
_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _slug(name: str) -> str:
    s = _NON_SLUG.sub("-", name.strip().lower()).strip("-")
    return s or "campaign"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CampaignEntry(BaseModel):
    id: str
    name: str
    updated_at: str
    spec: dict


class CampaignLibraryStore:
    """CRUD over the on-disk campaign-draft library. Cheap to construct; reads on demand."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def _dir(self) -> Path:
        return self._settings.campaign_library_dir

    def _path(self, entry_id: str) -> Path:
        if not _VALID_ID.match(entry_id):
            raise InputError(f"Invalid campaign id {entry_id!r}.")
        return self._dir / f"{entry_id}.json"

    def _read(self, path: Path) -> CampaignEntry:
        try:
            return CampaignEntry.model_validate_json(path.read_text())
        except (OSError, ValueError) as exc:
            raise InputError(f"Could not read campaign file {path.name}.", detail=str(exc)) from exc

    def _write(self, entry: CampaignEntry) -> None:
        self._settings.ensure_dirs()
        path = self._path(entry.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(entry.model_dump_json(indent=2))
        tmp.replace(path)  # atomic

    def _unique_id(self, base: str) -> str:
        existing = {p.stem for p in self._dir.glob("*.json")} if self._dir.is_dir() else set()
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    # -- public API ----------------------------------------------------------
    def list(self) -> list[CampaignEntry]:
        if not self._dir.is_dir():
            return []
        entries = [self._read(p) for p in self._dir.glob("*.json")]
        return sorted(entries, key=lambda e: e.updated_at, reverse=True)

    def get(self, entry_id: str) -> CampaignEntry:
        path = self._path(entry_id)
        if not path.is_file():
            raise NotFoundError(f"No saved campaign with id {entry_id!r}.")
        return self._read(path)

    def create(self, name: str, spec: dict) -> CampaignEntry:
        entry = CampaignEntry(
            id=self._unique_id(_slug(name)),
            name=name.strip() or "Untitled",
            updated_at=_now(),
            spec=spec,
        )
        self._write(entry)
        return entry

    def update(self, entry_id: str, name: str, spec: dict) -> CampaignEntry:
        if not self._path(entry_id).is_file():
            raise NotFoundError(f"No saved campaign with id {entry_id!r}.")
        entry = CampaignEntry(
            id=entry_id, name=name.strip() or "Untitled", updated_at=_now(), spec=spec
        )
        self._write(entry)
        return entry

    def duplicate(self, entry_id: str) -> CampaignEntry:
        src = self.get(entry_id)
        return self.create(f"{src.name} copy", deepcopy(src.spec))

    def delete(self, entry_id: str) -> None:
        path = self._path(entry_id)
        if not path.is_file():
            raise NotFoundError(f"No saved campaign with id {entry_id!r}.")
        path.unlink()
