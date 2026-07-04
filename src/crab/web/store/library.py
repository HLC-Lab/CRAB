"""Local library of authored experiment configs (Phase 3, decision I3).

Each saved config is one JSON file under ``<data_dir>/experiments/<id>.json``
holding ``{id, name, updated_at, config}``. ``config`` is the engine-shaped
``{global_options, experiments}`` document the dashboard authors and later
submits; the store persists it verbatim (shape validation lives at the author
layer, not here). The library is laptop-local and non-secret.
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
    return s or "config"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LibraryEntry(BaseModel):
    id: str
    name: str
    updated_at: str
    config: dict


class LibraryStore:
    """CRUD over the on-disk config library. Cheap to construct; reads on demand."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def _dir(self) -> Path:
        return self._settings.experiments_dir

    def _path(self, entry_id: str) -> Path:
        if not _VALID_ID.match(entry_id):
            raise InputError(f"Invalid config id {entry_id!r}.")
        return self._dir / f"{entry_id}.json"

    def _read(self, path: Path) -> LibraryEntry:
        try:
            return LibraryEntry.model_validate_json(path.read_text())
        except (OSError, ValueError) as exc:
            raise InputError(f"Could not read config file {path.name}.", detail=str(exc)) from exc

    def _write(self, entry: LibraryEntry) -> None:
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
    def list(self) -> list[LibraryEntry]:
        if not self._dir.is_dir():
            return []
        entries = [self._read(p) for p in self._dir.glob("*.json")]
        return sorted(entries, key=lambda e: e.updated_at, reverse=True)

    def get(self, entry_id: str) -> LibraryEntry:
        path = self._path(entry_id)
        if not path.is_file():
            raise NotFoundError(f"No saved config with id {entry_id!r}.")
        return self._read(path)

    def create(self, name: str, config: dict) -> LibraryEntry:
        entry = LibraryEntry(
            id=self._unique_id(_slug(name)),
            name=name.strip() or "Untitled",
            updated_at=_now(),
            config=config,
        )
        self._write(entry)
        return entry

    def update(self, entry_id: str, name: str, config: dict) -> LibraryEntry:
        if not self._path(entry_id).is_file():
            raise NotFoundError(f"No saved config with id {entry_id!r}.")
        entry = LibraryEntry(
            id=entry_id, name=name.strip() or "Untitled", updated_at=_now(), config=config
        )
        self._write(entry)
        return entry

    def duplicate(self, entry_id: str) -> LibraryEntry:
        src = self.get(entry_id)
        copied = deepcopy(src.config)
        # Keep the in-config use-case name in sync with the library copy.
        go = copied.get("global_options")
        if isinstance(go, dict) and isinstance(go.get("name"), str) and go["name"]:
            go["name"] = f"{go['name']} copy"
        return self.create(f"{src.name} copy", copied)

    def delete(self, entry_id: str) -> None:
        path = self._path(entry_id)
        if not path.is_file():
            raise NotFoundError(f"No saved config with id {entry_id!r}.")
        path.unlink()
