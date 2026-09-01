"""Settings and on-disk locations for the CRAB web dashboard (laptop side).

All persistent laptop state lives under platform-standard config/data dirs
(via ``platformdirs``, already a core dependency). Locations can be overridden
with environment variables — primarily for tests and power users:

* ``CRAB_WEB_CONFIG_DIR``  — overrides the config dir (holds ``clusters.json``).
* ``CRAB_WEB_DATA_DIR``    — overrides the data dir (library, jobs, cache, logs).
* ``CRAB_WEB_LIBRARY_DIR`` — puts the experiment-config library in a directory of
  the user's choosing (e.g. a git repo or synced folder) instead of the data dir
  (see docs/dev/dashboard/decisions/ ADR-014). Existing entries are copied over
  on first run.
* ``CRAB_WEB_SBATCHMAN`` — enable the SbatchMan integration mode (the campaign
  generator), equivalent to launching ``crab web --sbatchman`` (plan 084).

Nothing secret is stored here (see ``.crab-web-dev/05-instructions.md`` §7);
``clusters.json`` holds only non-secret connection profile fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import platformdirs

APP_NAME = "crab"
APP_AUTHOR = "crab"

# Bound to localhost only — the dashboard is a personal tool, never exposed.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class Settings:
    """Resolved, immutable paths and runtime defaults for one dashboard instance."""

    config_dir: Path
    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # Optional override for the built-frontend location (tests / custom builds).
    static_override: Path | None = None
    # Optional user-chosen home for the experiment library (ADR-014).
    library_dir: Path | None = None
    # SbatchMan integration mode: gates the campaign-generator UI + its API routes
    # (plan 084). Off by default; enabled per-launch via `crab web --sbatchman`.
    sbatchman: bool = False

    # ---- derived locations -------------------------------------------------
    @property
    def clusters_file(self) -> Path:
        """Non-secret cluster connection profiles."""
        return self.config_dir / "clusters.json"

    @property
    def experiments_dir(self) -> Path:
        """Library of authored experiment configs (user-chosen dir, else data dir)."""
        return self.library_dir if self.library_dir is not None else self.data_dir / "experiments"

    @property
    def jobs_file(self) -> Path:
        """Local registry of submitted jobs (for monitoring + history)."""
        return self.data_dir / "jobs.json"

    @property
    def cache_dir(self) -> Path:
        """Fallback cache of small JSON blobs (logs/history) for a disconnected cluster."""
        return self.data_dir / "cache"

    @property
    def results_cache_dir(self) -> Path:
        """Fetched result CSV trees, namespaced per cluster (plan 065)."""
        return self.data_dir / "results_cache"

    @property
    def sbatchman_dir(self) -> Path:
        """Local copies of composed SbatchMan campaign YAML files (plan 084)."""
        return self.data_dir / "sbatchman_campaigns"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "logs" / "web.log"

    @property
    def static_dir(self) -> Path:
        """Built Vue assets shipped with the package (may be absent in dev)."""
        if self.static_override is not None:
            return self.static_override
        return Path(__file__).resolve().parent / "static"

    def ensure_dirs(self) -> None:
        """Create every directory we own. Idempotent; safe to call on every boot."""
        for path in (
            self.config_dir,
            self.data_dir,
            self.experiments_dir,
            self.cache_dir,
            self.log_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _resolve_dir(env_var: str, fallback: str) -> Path:
    override = os.environ.get(env_var)
    return Path(override).expanduser() if override else Path(fallback)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, honouring env overrides. Cached."""
    config_dir = _resolve_dir(
        "CRAB_WEB_CONFIG_DIR", platformdirs.user_config_dir(APP_NAME, APP_AUTHOR)
    )
    data_dir = _resolve_dir("CRAB_WEB_DATA_DIR", platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
    library_raw = os.environ.get("CRAB_WEB_LIBRARY_DIR")
    library_dir = Path(library_raw).expanduser() if library_raw else None
    port_raw = os.environ.get("CRAB_WEB_PORT")
    try:
        port = int(port_raw) if port_raw else DEFAULT_PORT
    except ValueError:
        port = DEFAULT_PORT
    # Explicit truthiness (never rely on Python string-truthiness — see the audit's
    # config-coercion findings): only these exact tokens enable the mode.
    sbatchman = os.environ.get("CRAB_WEB_SBATCHMAN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return Settings(
        config_dir=config_dir,
        data_dir=data_dir,
        port=port,
        library_dir=library_dir,
        sbatchman=sbatchman,
    )
