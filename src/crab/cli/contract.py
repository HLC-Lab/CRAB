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

See ``.crab-web-dev/01-architecture.md`` for the documented shapes and
``.crab-web-dev/05-instructions.md`` for the standards.
"""

from __future__ import annotations

import csv
import glob
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Iterable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

# Bump on any breaking change to the shapes below. Reported by `crab info` so
# the backend can detect laptop<->cluster skew (ContractError).
CONTRACT_SCHEMA = 1

# Resolve the framework root the same way the rest of the package does.
# contract.py is at <root>/src/crab/cli/contract.py → parents[3] is <root>.
_CRAB_ROOT = Path(__file__).resolve().parents[3]


def _crab_version() -> str:
    try:
        return _pkg_version("crab")
    except PackageNotFoundError:
        return "unknown"


# --------------------------------------------------------------------------- #
# info
# --------------------------------------------------------------------------- #
def gather_info(crab_root: Path | None = None) -> dict[str, Any]:
    """Version handshake + available presets."""
    root = Path(crab_root) if crab_root else _CRAB_ROOT
    presets_file = root / "config" / "presets.json"

    presets: list[dict[str, str]] = []
    try:
        raw = json.loads(presets_file.read_text())
        for name, body in raw.items():
            if name in ("_common", "example_preset"):
                continue
            desc = body.get("description", "") if isinstance(body, dict) else ""
            presets.append({"name": name, "description": desc})
    except (OSError, json.JSONDecodeError):
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
    env = Path(env_dir) if env_dir else _CRAB_ROOT / "config" / "environments"
    wdir = (
        Path(wrappers_dir)
        if wrappers_dir
        else Path(os.environ.get("CRAB_PATH_WRAPPERS", _CRAB_ROOT / "wrappers"))
    )

    benchmarks = _gather_receipts(env) if env.is_dir() else []

    wrappers: list[dict[str, Any]] = []
    if wdir.is_dir():
        for py in sorted(wdir.rglob("*.py")):
            if py.name.startswith("_"):  # __init__.py and dunder helpers
                continue
            wrappers.append(_introspect_wrapper(py, wdir))

    return {"schema": CONTRACT_SCHEMA, "benchmarks": benchmarks, "wrappers": wrappers}


# --------------------------------------------------------------------------- #
# nodes (sinfo)
# --------------------------------------------------------------------------- #
# A command runner returns stdout as text. It raises FileNotFoundError when the
# binary is absent and subprocess.CalledProcessError on a non-zero exit — both
# are caught by callers for graceful degradation. Injectable for tests.
CommandRunner = Callable[[list[str]], str]


def _default_runner(cmd: list[str]) -> str:
    import subprocess

    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)


def _split_nodelist(s: str) -> list[str]:
    """Split a sinfo nodelist on top-level commas only (not inside brackets)."""
    result, depth, current = [], 0, []
    for ch in s:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            if current:
                result.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        result.append("".join(current))
    return result


def _expand_nodelist_token(token: str) -> list[str]:
    """Expand 'prefix[r1,r2]' into ['prefix[r1]', 'prefix[r2]']; pass plain tokens through."""
    start, end = token.find("["), token.rfind("]")
    if start == -1 or end == -1 or end < start:
        return [token]
    prefix, inner = token[:start], token[start + 1 : end]
    return [f"{prefix}[{r}]" for r in inner.split(",") if r]


def gather_nodes(runner: CommandRunner | None = None) -> dict[str, Any]:
    """Partitions and node tokens from ``sinfo``.

    Degrades to ``available: false`` (with a note) when ``sinfo`` is missing or
    fails — e.g. on the ``local`` preset or a non-Slurm host.
    """
    import subprocess

    run = runner or _default_runner
    result: dict[str, Any] = {
        "schema": CONTRACT_SCHEMA,
        "available": False,
        "partitions": [],
        "nodes": [],
    }

    try:
        part_out = run(["sinfo", "-h", "-o", "%R|%a|%D"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        result["note"] = f"sinfo unavailable: {type(exc).__name__}"
        return result

    result["available"] = True
    seen: set[str] = set()
    for line in part_out.splitlines():
        parts = line.split("|")
        if not parts or not parts[0].strip():
            continue
        name = parts[0].strip()
        if name in seen:
            continue
        seen.add(name)
        entry: dict[str, str | int] = {"name": name}
        if len(parts) > 1:
            entry["avail"] = parts[1].strip()
        if len(parts) > 2:
            try:
                entry["nodes"] = int(parts[2].strip())
            except ValueError:
                pass
        result["partitions"].append(entry)

    try:
        node_out = run(["sinfo", "-h", "-o", "%N"])
        tokens: list[str] = []
        for line in node_out.splitlines():
            for top in _split_nodelist(line.strip()):
                tokens.extend(_expand_nodelist_token(top))
        result["nodes"] = tokens
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass  # partitions still useful without the node breakdown

    return result


# --------------------------------------------------------------------------- #
# status (squeue → sacct fallback)
# --------------------------------------------------------------------------- #
def gather_status(job_ids: list[str], runner: CommandRunner | None = None) -> dict[str, Any]:
    """Current state of the given Slurm job ids.

    Tries ``squeue`` first (active/pending jobs); for ids not in the queue,
    falls back to ``sacct`` (completed/purged). Unknown ids report
    ``state: "UNKNOWN"`` rather than failing the whole call.
    """
    import subprocess

    run = runner or _default_runner
    states: dict[str, dict[str, Any]] = {}

    if job_ids:
        try:
            out = run(["squeue", "-h", "-o", "%i|%T", "-j", ",".join(job_ids)])
            for line in out.splitlines():
                jid, _, state = line.strip().partition("|")
                if jid:
                    states[jid] = {"job_id": jid, "state": state or "UNKNOWN", "source": "squeue"}
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    for jid in job_ids:
        if jid in states:
            continue
        try:
            out = run(["sacct", "-j", jid, "-n", "-P", "-o", "JobID,State,ExitCode"])
        except (FileNotFoundError, subprocess.CalledProcessError):
            states[jid] = {"job_id": jid, "state": "UNKNOWN", "source": "none"}
            continue
        found = None
        for line in out.splitlines():
            cols = line.split("|")
            # The primary job row has JobID exactly == jid (not jid.batch/.extern).
            if cols and cols[0].strip() == jid:
                found = {
                    "job_id": jid,
                    "state": cols[1].strip() if len(cols) > 1 else "UNKNOWN",
                    "exit_code": cols[2].strip() if len(cols) > 2 else None,
                    "source": "sacct",
                }
                break
        states[jid] = found or {"job_id": jid, "state": "UNKNOWN", "source": "none"}

    return {"schema": CONTRACT_SCHEMA, "jobs": [states[j] for j in job_ids]}


def gather_cancel(job_id: str, runner: CommandRunner | None = None) -> dict[str, Any]:
    """Cancel a Slurm job by id (``scancel``).

    A missing/already-terminal job reports ``cancelled: false`` with a
    ``detail`` hint rather than raising, so the web backend can show it
    without treating "nothing to cancel" as a request failure.
    """
    import subprocess

    run = runner or _default_runner
    try:
        run(["scancel", job_id])
    except FileNotFoundError:
        return {
            "schema": CONTRACT_SCHEMA,
            "job_id": job_id,
            "cancelled": False,
            "detail": "scancel is not available on this host.",
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema": CONTRACT_SCHEMA,
            "job_id": job_id,
            "cancelled": False,
            "detail": f"scancel exited {exc.returncode}; the job may already be gone.",
        }
    return {"schema": CONTRACT_SCHEMA, "job_id": job_id, "cancelled": True, "detail": None}


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
