"""``/api/experiments`` — the laptop-local library of authored configs (I3).

Pure local CRUD over :class:`LibraryStore`; no cluster involved. The config
body is stored verbatim (engine-shaped ``{global_options, experiments}``);
shape validation is layered on later.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from crab.web.store.library import LibraryEntry, LibraryStore

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


class ConfigBody(BaseModel):
    name: str = ""
    config: dict = Field(default_factory=dict)


def _store(request: Request) -> LibraryStore:
    return LibraryStore(request.app.state.settings)


@router.get("")
async def list_experiments(request: Request) -> list[LibraryEntry]:
    return _store(request).list()


@router.post("", status_code=201)
async def create_experiment(body: ConfigBody, request: Request) -> LibraryEntry:
    return _store(request).create(body.name, body.config)


@router.get("/{entry_id}")
async def get_experiment(entry_id: str, request: Request) -> LibraryEntry:
    return _store(request).get(entry_id)


@router.put("/{entry_id}")
async def update_experiment(entry_id: str, body: ConfigBody, request: Request) -> LibraryEntry:
    return _store(request).update(entry_id, body.name, body.config)


@router.post("/{entry_id}/duplicate", status_code=201)
async def duplicate_experiment(entry_id: str, request: Request) -> LibraryEntry:
    return _store(request).duplicate(entry_id)


@router.delete("/{entry_id}", status_code=204)
async def delete_experiment(entry_id: str, request: Request) -> None:
    _store(request).delete(entry_id)
