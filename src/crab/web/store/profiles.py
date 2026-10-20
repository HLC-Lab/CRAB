"""Cluster connection profiles — the only persistent laptop state for connections.

Profiles are **non-secret**: host, user, paths,
preset, auth *method*. No passwords or keys are stored — agent auth (the
Leonardo path) needs no secret, and a password (if ever used) is supplied
transiently at connect time, never written here.

Stored as ``{"version": 1, "clusters": [ <Profile>, ... ]}`` in
``<config_dir>/clusters.json``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from crab.web.errors import ConflictError, InputError, NotFoundError
from crab.web.settings import Settings, get_settings

PROFILES_VERSION = 1

# A profile name becomes a local folder name (results cache) and part of cache
# keys, so it must stay a single plain path segment.
_VALID_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
# `/api/wrappers/local/...` is the laptop, so no cluster profile may take the name.
_LAPTOP_NAME = "local"


def check_profile_name(name: str) -> None:
    """Raise InputError unless ``name`` is safe to use as a folder name.

    Only enforced when a name is chosen (add or rename): profiles saved before
    this rule existed keep loading and working under their old name.
    """
    if name == _LAPTOP_NAME:
        raise InputError(
            f'"{_LAPTOP_NAME}" is reserved for this laptop in the dashboard; choose another name.'
        )
    if not _VALID_NAME.match(name) or name in (".", ".."):
        raise InputError(
            f"Invalid profile name {name!r}. Use only letters, digits, '.', '_' and '-'."
        )


def check_crab_command(crab_command: str | None) -> None:
    """Raise InputError unless ``crab_command`` is unset or one executable.

    The command is placed in front of the ``crab`` arguments verbatim (only quoted),
    so whitespace would turn it into a command line; an empty value means "unset"
    and must be sent as null instead.
    """
    if crab_command is None:
        return
    if not crab_command or re.search(r"\s", crab_command):
        raise InputError(
            "Crab command must be one executable path or name with no spaces "
            "(for example ~/.local/bin/crab); leave it unset to use the CRAB checkout."
        )


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

    crab_command: str | None = Field(
        default=None,
        description=(
            "One executable that runs crab on the remote (e.g. ~/.local/bin/crab for a pipx "
            "install). Set: no cd into <remote_crab>/CRAB and no venv activation; "
            "remote_setup still runs first. Unset: the CRAB checkout in <remote_crab>/CRAB."
        ),
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
        check_profile_name(profile.name)
        check_crab_command(profile.crab_command)
        store = self._load()
        if any(p.name == profile.name for p in store.clusters):
            raise ConflictError(f"A profile named {profile.name!r} already exists.")
        store.clusters.append(profile)
        self._save(store)
        return profile

    def update(self, name: str, profile: Profile) -> Profile:
        if profile.name != name:
            check_profile_name(profile.name)
        check_crab_command(profile.crab_command)
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
