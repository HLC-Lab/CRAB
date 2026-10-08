"""Machine-readable ``--json`` seam — the contract the web backend speaks to.

The web dashboard (laptop) never screen-scrapes human output: it calls these
``crab ... --json`` commands over SSH and parses the result. Keeping the seam
here, in the ``crab`` package on the cluster, means the engine and its security
logic stay authoritative — the laptop only consumes structured data.

Every gatherer:
* returns a plain ``dict``/``list`` (JSON-serialisable),
* takes explicit paths / an injectable command runner so it is unit-testable
  without a real cluster,
* degrades gracefully (missing files, unloadable wrappers, absent ``sinfo``)
  rather than raising — partial data beats a crash for an introspection call.

"""

from __future__ import annotations

import csv
import glob
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from crab import __version__
from crab.cli.presets import load_all_presets
from crab.core.execution.scheduler.base import Scheduler
from crab.core.execution.scheduler.local import LocalScheduler
from crab.core.execution.scheduler.slurm import CommandRunner, SlurmScheduler
from crab.log import CrabLogger

# Bump on any breaking change to the shapes below. Reported by `crab info` so
# the backend can detect laptop<->cluster skew (ContractError).
CONTRACT_SCHEMA = 1

# Resolve the framework root the same way the rest of the package does.
# contract.py is at <root>/src/crab/cli/contract.py → parents[3] is <root>.
_CRAB_ROOT = Path(__file__).resolve().parents[3]


def _crab_version() -> str:
    return __version__


# --------------------------------------------------------------------------- #
# info
# --------------------------------------------------------------------------- #
def gather_info(crab_root: Path | None = None) -> dict[str, Any]:
    """Version handshake + available presets."""
    root = Path(crab_root) if crab_root else _CRAB_ROOT

    presets: list[dict[str, str]] = []
    try:
        raw = load_all_presets(root)
        for name, body in raw.items():
            if name in ("_common", "example_preset"):
                continue
            desc = body.get("description", "") if isinstance(body, dict) else ""
            presets.append({"name": name, "description": desc})
    except (OSError, ValueError):
        # No presets file / malformed → empty list, not a failure.
        presets = []

    return {
        "schema": CONTRACT_SCHEMA,
        "crab_version": _crab_version(),
        "crab_root": str(root),
        "presets": sorted(presets, key=lambda p: p["name"]),
    }


# --------------------------------------------------------------------------- #
# history (metadata.csv registries)
# --------------------------------------------------------------------------- #
# Column order written by ExperimentRunner._write_to_registry.
_HISTORY_COLUMNS = (
    "job_name",
    "experiment_name",
    "timestamp",
    "numnodes",
    "ppn",
    "apps_list",
    "status",
    "tags",
    "relative_path",
    "total_runs",
    "failed_runs",
)


def gather_history(data_root: Path | None = None, system: str | None = None) -> dict[str, Any]:
    """Past experiments parsed from per-system ``metadata.csv`` files.

    Args:
        data_root: the ``data/`` directory (default: ``<crab_root>/data``).
        system: limit to one system (subdir); otherwise scan all.
    """
    root = Path(data_root) if data_root else _CRAB_ROOT / "data"
    systems: Iterable[Path]
    if system:
        systems = [root / system]
    elif root.is_dir():
        systems = sorted(p for p in root.iterdir() if p.is_dir())
    else:
        systems = []

    rows: list[dict[str, Any]] = []
    for sys_dir in systems:
        registry = sys_dir / "metadata.csv"
        if not registry.is_file():
            continue
        try:
            with open(registry, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    entry = {k: (row.get(k) or "").strip() for k in _HISTORY_COLUMNS}
                    entry["system"] = sys_dir.name
                    if entry["relative_path"]:
                        entry["absolute_path"] = str((sys_dir / entry["relative_path"]).resolve())
                    else:
                        entry["absolute_path"] = str(sys_dir.resolve())
                    rows.append(entry)
        except OSError:
            continue

    return {"schema": CONTRACT_SCHEMA, "experiments": rows}


# --------------------------------------------------------------------------- #
# benchmarks (receipts) + wrappers (discovered .py files)
# --------------------------------------------------------------------------- #
def _gather_receipts(env_dir: Path) -> list[dict[str, Any]]:
    benchmarks: list[dict[str, Any]] = []
    for file in sorted(glob.glob(str(env_dir / "*.json"))):
        try:
            r = json.loads(Path(file).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        benchmarks.append(
            {
                "id": r.get("id", Path(file).stem),
                "type": r.get("type"),
                "target_arch": r.get("target_arch"),
                "binary_path": r.get("binary_path", ""),
                "launcher_override": r.get("launcher_override", ""),
                "hooks": r.get("hooks", {}),
            }
        )
    return benchmarks


def _wrappers_pkg_dir() -> Path | None:
    """Directory of the shared ``base`` module wrappers import (``from base import base``).

    Resolved from the installed ``crab.wrappers`` package so it works regardless
    of source/installed layout. Adding it to ``sys.path`` lets us introspect
    wrappers that subclass ``base``, mirroring how the engine loads them.
    """
    try:
        import crab.wrappers as _w

        return Path(_w.__file__).resolve().parent
    except Exception:
        return None


def _introspect_wrapper(path: Path, wrappers_root: Path) -> dict[str, Any]:
    """Best-effort metadata for one wrapper file.

    Wrappers are arbitrary user Python that may import siblings or the shared
    ``base`` module, so loading can fail. On failure we still return the file
    with ``loadable: false`` and the error, so the Author UI can list it.
    Class-level ``metadata`` is read without instantiating; ``benchmark_id``/
    ``bench_name`` are attempted in a guarded instantiation.
    """
    rel = str(path.relative_to(wrappers_root))
    entry: dict[str, Any] = {
        "file": path.name,
        "relpath": rel,
        "path": str(path),
        "group": rel.split(os.sep)[0] if os.sep in rel else "",
        "loadable": False,
        "benchmark_id": None,
        "bench_name": None,
        "metadata": [],
    }

    # Make the file's own dir (sibling imports) and the shared base package
    # importable, then clean up whatever we added.
    inject = [str(path.parent)]
    pkg = _wrappers_pkg_dir()
    if pkg is not None:
        inject.append(str(pkg))
    added = [d for d in inject if d not in sys.path]
    for d in added:
        sys.path.insert(0, d)
    mod_name = f"_crab_introspect_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            entry["error"] = "could not create import spec"
            return entry
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        app_cls = getattr(module, "app", None)
        if app_cls is None:
            entry["error"] = "no 'app' class"
            return entry

        entry["loadable"] = True
        meta = getattr(app_cls, "metadata", None)
        if isinstance(meta, list):
            entry["metadata"] = [
                {
                    "name": m.get("name"),
                    "unit": m.get("unit"),
                    "conv": bool(m.get("conv", False)),
                }
                for m in meta
                if isinstance(m, dict)
            ]
        # benchmark_id / bench_name often need an instance — guard it.
        try:
            inst = app_cls(0, False, "")
            bid = getattr(inst, "benchmark_id", None)
            entry["benchmark_id"] = bid if isinstance(bid, str) else None
            if hasattr(inst, "get_bench_name"):
                name = inst.get_bench_name()
                entry["bench_name"] = name if isinstance(name, str) else None
        except Exception:
            pass  # introspection-only; metadata already captured above
    except Exception as exc:  # noqa: BLE001 — any wrapper import error is non-fatal
        entry["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sys.modules.pop(mod_name, None)
        for d in added:
            try:
                sys.path.remove(d)
            except ValueError:
                pass
    return entry


def gather_benchmarks(
    env_dir: Path | None = None, wrappers_dir: Path | None = None
) -> dict[str, Any]:
    """Installed benchmarks (receipts) + discovered wrapper files."""
    # Legacy receipts first, then local/receipts, so a local receipt wins (see setup/memory.py).
    env_dirs = (
        [Path(env_dir)]
        if env_dir
        else [_CRAB_ROOT / "config" / "environments", _CRAB_ROOT / "local" / "receipts"]
    )
    if wrappers_dir:
        wdirs = [Path(wrappers_dir)]
    else:
        from crab.core import wrapper_paths

        # Same folders, same order as the engine's lookup; the first file with a relpath wins.
        wdirs = [Path(d) for d in wrapper_paths.wrapper_search_path()]

    by_id = {b["id"]: b for d in env_dirs if d.is_dir() for b in _gather_receipts(d)}
    benchmarks = [by_id[k] for k in sorted(by_id)]

    wrappers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for wdir in wdirs:
        if not wdir.is_dir():
            continue
        for py in sorted(wdir.rglob("*.py")):
            if py.name.startswith("_"):  # __init__.py and dunder helpers
                continue
            rel = str(py.relative_to(wdir))
            if rel in seen:
                continue
            seen.add(rel)
            wrappers.append(_introspect_wrapper(py, wdir))

    return {"schema": CONTRACT_SCHEMA, "benchmarks": benchmarks, "wrappers": wrappers}


# --------------------------------------------------------------------------- #
# scheduler (Slurm commands run behind the Scheduler interface)
# --------------------------------------------------------------------------- #
def _slurm_scheduler(runner: CommandRunner | None) -> Scheduler:
    # A logger with no handlers writes nothing: stdout stays clean JSON.
    return SlurmScheduler(CrabLogger(), str(_CRAB_ROOT), runner=runner)


# --------------------------------------------------------------------------- #
# nodes (sinfo)
# --------------------------------------------------------------------------- #
def gather_nodes(runner: CommandRunner | None = None) -> dict[str, Any]:
    """Partitions and node tokens from ``sinfo``.

    Degrades to ``available: false`` (with a note) when ``sinfo`` is missing or
    fails — e.g. on the ``local`` preset or a non-Slurm host.
    """
    return {"schema": CONTRACT_SCHEMA, **_slurm_scheduler(runner).describe_nodes()}


# --------------------------------------------------------------------------- #
# status and cancel (local jobs by state file, everything else Slurm)
# --------------------------------------------------------------------------- #
def _job_dicts(scheduler: Scheduler, ids: list[str]) -> list[dict[str, Any]]:
    """The scheduler's job states as contract dicts, in input order."""
    jobs: list[dict[str, Any]] = []
    for status in scheduler.status(ids):
        job: dict[str, Any] = {"job_id": status.job_id, "state": status.state}
        if status.exit_code is not None:
            job["exit_code"] = status.exit_code
        job["source"] = status.source
        jobs.append(job)
    return jobs


def gather_status(
    job_ids: list[str], runner: CommandRunner | None = None, crab_root: Path | None = None
) -> dict[str, Any]:
    """Current state of the given job ids.

    A job id with local state (written by a preset with `scheduler: "local"`) is
    resolved by the local scheduler. Everything else is asked of the Slurm scheduler
    (``squeue`` first, then ``sacct`` for ids not in the queue). Unknown ids report
    ``state: "UNKNOWN"`` rather than failing the whole call.
    """
    root = Path(crab_root) if crab_root else _CRAB_ROOT
    local = LocalScheduler(CrabLogger(), str(root))
    local_ids = [jid for jid in job_ids if local.knows(jid)]
    remaining_ids = [jid for jid in job_ids if jid not in local_ids]

    states: dict[str, dict[str, Any]] = {}
    for job in _job_dicts(local, local_ids):
        states[job["job_id"]] = job
    if remaining_ids:
        for job in _job_dicts(_slurm_scheduler(runner), remaining_ids):
            states[job["job_id"]] = job

    return {"schema": CONTRACT_SCHEMA, "jobs": [states[j] for j in job_ids]}


def gather_cancel(
    job_id: str, runner: CommandRunner | None = None, crab_root: Path | None = None
) -> dict[str, Any]:
    """Cancel a job by id.

    A job id with local state (a preset with `scheduler: "local"`) is cancelled locally.
    Everything else goes through the real ``scancel`` path.

    A missing/already-terminal job reports ``cancelled: false`` with a
    ``detail`` hint rather than raising, so the web backend can show it
    without treating "nothing to cancel" as a request failure.
    """
    root = Path(crab_root) if crab_root else _CRAB_ROOT
    local = LocalScheduler(CrabLogger(), str(root))
    scheduler: Scheduler = local if local.knows(job_id) else _slurm_scheduler(runner)
    result = scheduler.cancel(job_id)
    return {
        "schema": CONTRACT_SCHEMA,
        "job_id": job_id,
        "cancelled": result.cancelled,
        "detail": result.detail,
    }


# --------------------------------------------------------------------------- #
# logs (slurm_output.log / slurm_error.log in a job's data_dir)
# --------------------------------------------------------------------------- #
_LOG_FILENAMES = {"stdout": "slurm_output.log", "stderr": "slurm_error.log"}
_DEFAULT_LOG_MAX_BYTES = 200_000


def _read_log_tail(path: Path, max_bytes: int) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "content": "", "truncated": False}
    size = path.stat().st_size
    truncated = size > max_bytes
    with open(path, "rb") as fh:
        if truncated:
            fh.seek(size - max_bytes)
        raw = fh.read()
    return {
        "path": str(path),
        "exists": True,
        "content": raw.decode("utf-8", "replace"),
        "truncated": truncated,
    }


def gather_logs(data_dir: str | Path, max_bytes: int = _DEFAULT_LOG_MAX_BYTES) -> dict[str, Any]:
    """Read a job's captured stdout/stderr from its data directory.

    The engine writes ``slurm_output.log``/``slurm_error.log`` directly into a
    job's data_dir (``core/engine.py``'s sbatch header sets ``--output``/
    ``--error`` to those exact paths), so this reads them by fixed name rather
    than deriving a naming convention. Each side is capped to its last
    ``max_bytes`` so a runaway log can't hang the request. A file that hasn't
    been written yet (job not started, or no stderr produced) reports
    ``exists: false`` rather than raising.
    """
    base = Path(data_dir)
    return {
        "schema": CONTRACT_SCHEMA,
        "data_dir": str(base),
        "stdout": _read_log_tail(base / _LOG_FILENAMES["stdout"], max_bytes),
        "stderr": _read_log_tail(base / _LOG_FILENAMES["stderr"], max_bytes),
    }


def gather_experiment_logs(
    data_dir: str | Path, experiment_name: str, max_bytes: int = _DEFAULT_LOG_MAX_BYTES
) -> dict[str, Any]:
    """Read one experiment's per-app error logs from its directory.

    ``ExperimentRunner.execute`` writes ``error_app_<id>.log`` directly into the
    experiment's own directory (``<data_dir>/<experiment_name>``) whenever an
    app exits non-zero (``runner.py:344-350``) — never on success. Unlike
    ``gather_logs``, a missing experiment directory raises rather than
    degrading gracefully: the caller always derives ``experiment_name`` from a
    real ``crab history`` row, so a missing directory means the wrong
    data_dir/name was passed, not "no errors yet" — collapsing that into an
    empty list would silently hide the mistake.
    """
    exp_dir = Path(data_dir) / experiment_name
    if not exp_dir.is_dir():
        raise FileNotFoundError(
            f"No experiment directory named {experiment_name!r} under {data_dir}."
        )
    files = [
        {"app_id": p.stem.removeprefix("error_app_"), **_read_log_tail(p, max_bytes)}
        for p in sorted(exp_dir.glob("error_app_*.log"))
    ]
    return {"schema": CONTRACT_SCHEMA, "data_dir": str(exp_dir), "files": files}


# --------------------------------------------------------------------------- #
# output helper
# --------------------------------------------------------------------------- #
def emit(data: Any, as_json: bool, human: Callable[[Any], None]) -> None:
    """Print ``data`` as JSON (machine) or via ``human`` (terminal)."""
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        human(data)
