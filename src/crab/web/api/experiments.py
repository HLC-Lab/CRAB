"""``/api/experiments`` — the laptop-local library of authored configs.

Pure local CRUD over :class:`LibraryStore`; no cluster involved. The config
body is stored verbatim, but saves also report shape WARNINGS (never
rejections — the engine is the final authority; see ADR-015) so the UI can
tell the user when a config looks engine-incompatible.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.web.models import validate_config
from crab.web.store.library import LibraryEntry, LibraryStore

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


class ConfigBody(BaseModel):
    name: str = ""
    config: dict = Field(default_factory=dict)


class SavedEntry(LibraryEntry):
    """A stored entry plus any shape warnings for the config just saved."""

    warnings: list[str] = []


def _store(request: Request) -> LibraryStore:
    return LibraryStore(request.app.state.settings)


def _saved(entry: LibraryEntry, config: dict) -> SavedEntry:
    return SavedEntry(**entry.model_dump(), warnings=validate_config(config))


@router.get("")
async def list_experiments(request: Request) -> list[LibraryEntry]:
    return _store(request).list()


@router.post("", status_code=201)
async def create_experiment(body: ConfigBody, request: Request) -> SavedEntry:
    return _saved(_store(request).create(body.name, body.config), body.config)


@router.get("/{entry_id}")
async def get_experiment(entry_id: str, request: Request) -> LibraryEntry:
    return _store(request).get(entry_id)


@router.put("/{entry_id}")
async def update_experiment(entry_id: str, body: ConfigBody, request: Request) -> SavedEntry:
    return _saved(_store(request).update(entry_id, body.name, body.config), body.config)


@router.post("/{entry_id}/duplicate", status_code=201)
async def duplicate_experiment(entry_id: str, request: Request) -> LibraryEntry:
    return _store(request).duplicate(entry_id)


@router.delete("/{entry_id}", status_code=204)
async def delete_experiment(entry_id: str, request: Request) -> None:
    _store(request).delete(entry_id)
