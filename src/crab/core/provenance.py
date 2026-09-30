"""`crab_run.json`: which CRAB and which wrapper files produced a job's results.

Written by the worker into the job directory before any experiment runs. Unknown values (no
git, a wrapper file that cannot be read) are recorded as null rather than failing the job.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
from typing import Any

import crab
from crab.core.wrapper_paths import resolve_wrapper_path
from crab.wrappers.base import base

# Version of the results layout (data_app_<id>.csv, metadata.csv, this file).
RESULTS_SCHEMA = 1

_CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _git(directory: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _wrappers_dir() -> str:
    return os.environ.get("CRAB_PATH_WRAPPERS") or os.path.join(_CRAB_ROOT, "wrappers")


def _sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def build_provenance(config: dict[str, Any], nodes: list[str]) -> dict[str, Any]:
    wrappers_dir = _wrappers_dir()
    commit = _git(wrappers_dir, "rev-parse", "HEAD")
    status = _git(wrappers_dir, "status", "--porcelain", "--", ".") if commit else None

    apps: dict[str, dict[str, Any]] = {}
    for exp_name, exp in (config.get("experiments") or {}).items():
        for app_key, app in (exp.get("apps") or {}).items():
            path = os.path.abspath(resolve_wrapper_path(str(app.get("path", ""))))
            apps.setdefault(exp_name, {})[str(app_key)] = {"path": path, "sha256": _sha256(path)}

    return {
        "results_schema": RESULTS_SCHEMA,
        "crab_version": crab.__version__,
        "crab_commit": _git(_CRAB_ROOT, "rev-parse", "HEAD"),
        "wrapper_api": base.wrapper_api,
        "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "nodes": list(nodes),
        "wrappers": {
            "dir": wrappers_dir,
            "commit": commit,
            "dirty": (status != "") if status is not None else None,
        },
        "apps": apps,
    }


def write_provenance(job_dir: str, config: dict[str, Any], nodes: list[str]) -> None:
    """Write `<job_dir>/crab_run.json` for this job."""
    with open(os.path.join(job_dir, "crab_run.json"), "w") as f:
        json.dump(build_provenance(config, nodes), f, indent=2)
