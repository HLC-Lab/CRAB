"""Cluster connection profiles — the only persistent laptop state for Phase 2.

Profiles are **non-secret** (see ``05-instructions.md`` §7): host, user, paths,
preset, auth *method*. No passwords or keys are stored — agent auth (the
Leonardo path) needs no secret, and a password (if ever used) is supplied
transiently at connect time, never written here.

Stored as ``{"version": 1, "clusters": [ <Profile>, ... ]}`` in
``<config_dir>/clusters.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from crab.web.errors import ConflictError, InputError, NotFoundError
from crab.web.settings import Settings, get_settings

PROFILES_VERSION = 1


class Profile(BaseModel):
    """A cluster the dashboard can connect to."""

    name: str = Field(min_length=1, description="Unique local alias for this cluster.")
    transport: Literal["ssh", "local"] = "ssh"

    # SSH connection (ignored for transport == 'local')
    host: str | None = None
    port: int = 22
    user: str | None = None

    # How to authenticate. 'agent' uses the inherited SSH_AUTH_SOCK (covers
    # Leonardo's step-cli cert and ordinary keys-in-agent); 'key' uses an
    # explicit private key file; 'password' is prompted at connect time.
    auth: Literal["agent", "key", "password"] = "agent"
    key_path: str | None = None

    # Host-key verification. 'strict' uses the system known_hosts; 'insecure'
    # disables verification — required for round-robin login nodes that present
    # different host keys per connection (common on large HPC front ends).
    hostkey_policy: Literal["strict", "insecure"] = "strict"

    # Base directory on the remote where CRAB lives. CRAB is installed at and
    # run from a `CRAB` subfolder of this dir (i.e. <remote_crab>/CRAB), so the
    # default `~` puts it at ~/CRAB. (Non-interactive shells don't activate
    # venvs or source rc files, so the run path does it explicitly.)
    remote_crab: str = "~"
    venv_activate: str | None = Field(
        default=None,
        description="Path to the venv activate script; defaults to <remote_crab>/CRAB/.venv/bin/activate.",
    )
    remote_setup: list[str] = Field(
        default_factory=list,
        description="Commands run before activation (e.g. 'module load python').",
    )

    preset: str | None = None

    def is_local(self) -> bool:
        return self.transport == "local"


class _Store(BaseModel):
    version: int = PROFILES_VERSION
    clusters: list[Profile] = Field(default_factory=list)


class ProfileStore:
    """CRUD over the on-disk profiles file. Cheap to construct; reads on demand."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def path(self) -> Path:
        return self._settings.clusters_file

    def _load(self) -> _Store:
        if not self.path.is_file():
            return _Store()
        try:
            return _Store.model_validate_json(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise InputError(
                f"Could not read profiles file at {self.path}.",
                detail=str(exc),
            ) from exc

    def _save(self, store: _Store) -> None:
        self._settings.ensure_dirs()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(store.model_dump_json(indent=2))
        tmp.replace(self.path)  # atomic

    # -- public API ----------------------------------------------------------
    def list(self) -> list[Profile]:
        return self._load().clusters

    def get(self, name: str) -> Profile:
        for p in self._load().clusters:
            if p.name == name:
                return p
        raise NotFoundError(f"No cluster profile named {name!r}.")

    def add(self, profile: Profile) -> Profile:
        store = self._load()
        if any(p.name == profile.name for p in store.clusters):
            raise ConflictError(f"A profile named {profile.name!r} already exists.")
        store.clusters.append(profile)
        self._save(store)
        return profile

    def update(self, name: str, profile: Profile) -> Profile:
        store = self._load()
        for i, p in enumerate(store.clusters):
            if p.name == name:
                store.clusters[i] = profile
                self._save(store)
                return profile
        raise NotFoundError(f"No cluster profile named {name!r}.")

    def remove(self, name: str) -> None:
        store = self._load()
        kept = [p for p in store.clusters if p.name != name]
        if len(kept) == len(store.clusters):
            raise NotFoundError(f"No cluster profile named {name!r}.")
        store.clusters = kept
        self._save(store)
