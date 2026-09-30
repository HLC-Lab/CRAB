"""The wrappers on this machine: listing them, testing their sample cases, scaffolding new ones.

A sample case is a folder `<app>/samples/<case>/` holding `case.json` (`{"wrapper": "<file>.py",
"args": "...", "set": {...}}`), the application's `stdout.txt` (and optionally `stderr.txt` and
any files it wrote), and `expected.json`, the rows the wrapper must produce. Cases run through
the same parser a real run uses.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crab.core.data.parse import Parsed, ParseError, parse_output
from crab.core.experiment.wrapper_paths import load_module, wrapper_search_path

_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
UNVERIFIED_LIST = "unverified.txt"


def parse_saved_output(
    wrapper: str,
    stdout: bytes,
    stderr: bytes,
    run_dir: str,
    args: str = "",
    settings: dict[str, Any] | None = None,
) -> tuple[Any, Parsed]:
    """Load `wrapper`, feed it saved output, and parse it like a run would.

    Raises:
        ParseError: from parse_output.
        Exception: whatever loading or instantiating the wrapper raises.
    """
    app = load_module(wrapper).app(0, True, args)
    for key, value in (settings or {}).items():
        setattr(app, key, value)
    app.set_output(stdout, stderr)
    app.run_dir = os.path.abspath(run_dir)
    return app, parse_output(app)


def row_differences(got: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[str]:
    lines = []
    if len(got) != len(expected):
        lines.append(f"expected {len(expected)} rows, got {len(got)}")
    for i, (g, e) in enumerate(zip(got, expected, strict=False)):
        if g != e:
            lines.append(f"row {i}: expected {e}, got {g}")
    return lines


def _wrapper_files(folder: Path) -> list[Path]:
    """Wrapper files under `folder`: not `_`-prefixed helpers, not inside samples/."""
    return [
        p
        for p in sorted(folder.rglob("*.py"))
        if not p.name.startswith("_")
        and "samples" not in p.relative_to(folder).parts
        and "__pycache__" not in p.parts
    ]


# ---- list -------------------------------------------------------------------------------------


def _describe(path: Path, folder: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "relpath": str(path.relative_to(folder)),
        "path": str(path),
        "folder": str(folder),
        "loadable": False,
        "error": None,
        "benchmark_id": None,
        "executable": None,
        "keys": [],
        "metrics": [],
        "binary": {"status": "unknown", "path": None},
    }
    try:
        app = load_module(str(path)).app(0, False, "")
    except Exception as exc:  # arbitrary user code: report it, never crash the listing
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry
    entry.update(
        loadable=True,
        benchmark_id=app.benchmark_id or None,
        executable=app.executable or None,
        keys=list(app.keys),
        metrics=[m.get("name") for m in app.metadata],
    )
    try:
        binary, source = app.find_binary()
        entry["binary"] = {"status": source, "path": binary}
    except Exception as exc:
        entry["binary"] = {
            "status": "error",
            "path": None,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    return entry


def gather_wrappers() -> dict[str, Any]:
    """Every wrapper along the search path (the first file with a given relpath wins)."""
    from crab.cli.contract import CONTRACT_SCHEMA

    search = wrapper_search_path()
    seen: set[str] = set()
    wrappers = []
    for folder_str in search:
        folder = Path(folder_str)
        if not folder.is_dir():
            continue
        for path in _wrapper_files(folder):
            rel = str(path.relative_to(folder))
            if rel not in seen:
                seen.add(rel)
                wrappers.append(_describe(path, folder))
    return {"schema": CONTRACT_SCHEMA, "search_path": search, "wrappers": wrappers}


# ---- test -------------------------------------------------------------------------------------


@dataclass
class CaseResult:
    status: str  # PASS, FAIL, ERROR, NO SAMPLE, UNVERIFIED
    label: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "UNVERIFIED")


def _run_case(app_dir: Path, case_dir: Path) -> tuple[CaseResult, str | None]:
    label = f"{app_dir.name}/{case_dir.name}"
    try:
        case = json.loads((case_dir / "case.json").read_text())
        wrapper = str(app_dir / case["wrapper"])
        stderr_file = case_dir / "stderr.txt"
        _app, parsed = parse_saved_output(
            wrapper,
            (case_dir / "stdout.txt").read_bytes(),
            stderr_file.read_bytes() if stderr_file.exists() else b"",
            str(case_dir),
            case.get("args", ""),
            case.get("set"),
        )
        expected = json.loads((case_dir / "expected.json").read_text())
    except ParseError as exc:
        return CaseResult("ERROR", label, str(exc)), None
    except Exception as exc:  # a broken case or wrapper is a test failure, not a crash
        return CaseResult("ERROR", label, f"{type(exc).__name__}: {exc}"), None
    diffs = row_differences(parsed.rows, expected)
    status = CaseResult("FAIL", label, "; ".join(diffs)) if diffs else CaseResult("PASS", label)
    return status, os.path.normpath(wrapper)


def _unverified(folder: Path) -> set[str]:
    listing = folder / UNVERIFIED_LIST
    if not listing.exists():
        return set()
    lines = (line.split("#", 1)[0].strip() for line in listing.read_text().splitlines())
    return {line for line in lines if line}


def run_samples(apps: list[str] | None, strict: bool) -> list[CaseResult]:
    """Run every sample case (of `apps`, or all) along the search path.

    With `strict`, a wrapper that no case covers is a failure unless the folder's
    `unverified.txt` lists it.

    Raises:
        LookupError: if a named app exists in no folder.
    """
    results: list[CaseResult] = []
    found: set[str] = set()
    for folder_str in wrapper_search_path():
        folder = Path(folder_str)
        if not folder.is_dir():
            continue
        unverified = _unverified(folder)
        for app_dir in sorted(p for p in folder.iterdir() if p.is_dir()):
            if app_dir.name.startswith((".", "_")) or (apps and app_dir.name not in apps):
                continue
            found.add(app_dir.name)
            covered: set[str] = set()
            for case_json in sorted(app_dir.glob("samples/*/case.json")):
                result, wrapper = _run_case(app_dir, case_json.parent)
                results.append(result)
                if wrapper:
                    covered.add(wrapper)
            if strict:
                for path in _wrapper_files(app_dir):
                    if os.path.normpath(str(path)) in covered:
                        continue
                    rel = str(path.relative_to(folder))
                    status = "UNVERIFIED" if rel in unverified else "NO SAMPLE"
                    results.append(CaseResult(status, rel))
    missing = set(apps or []) - found
    if missing:
        raise LookupError(f"no app folder named {', '.join(sorted(missing))} on the search path")
    return results


# ---- new --------------------------------------------------------------------------------------

_TEMPLATE = '''"""{app} wrapper: say what the application measures and which versions this was checked with."""

from crab.wrappers.base import base


class app(base):
    # Program looked up on PATH when neither the config's "binary" nor a receipt names one.
    executable = "{app}"
    # Receipt id, for `crab receipts set {app} --binary <path>`.
    benchmark_id = "{app}"
    # Sweep dimensions every row carries, e.g. ["size"]. Empty when a run gives single values.
    keys = []
    metadata = [
        {{"name": "time", "unit": "s", "conv": True}},
    ]

    def read_data(self):
        # One dict per sample, holding every key and metric declared above. If the output is not
        # what you expect, raise: CRAB then fails the run instead of recording wrong numbers.
        rows = []
        for line in self.stdout.splitlines():
            if line.startswith("time:"):
                rows.append({{"time": float(line.split(":", 1)[1])}})
        return rows
'''

_README = """# {app}

What this wrapper runs and measures, and which application versions it was checked with.

## Install

How to get the application (module name, build steps, or a recipe), and the receipt command if
the binary is not on PATH: `crab receipts set {app} --binary /path/to/{app}`.

## Samples

`samples/<case>/` holds real output from a run (`stdout.txt`, plus any files it wrote) and the
rows this wrapper must produce (`expected.json`). Say where each sample comes from (system, date,
application version). Check them with `crab wrappers test {app}`.
"""


def scaffold(app: str, name: str | None, target: Path) -> Path:
    """Create `<target>/<app>/<name>.py` with a README and a passing example sample.

    Raises:
        ValueError: for an invalid name, or if the wrapper or its example case already exists.
    """
    name = name or app
    for value in (app, name):
        if not _NAME.match(value):
            raise ValueError(f"{value!r} may only use letters, digits, '_' and '-'")
    app_dir = target / app
    wrapper = app_dir / f"{name}.py"
    case_dir = app_dir / "samples" / ("example" if name == app else f"{name}-example")
    for path in (wrapper, case_dir):
        if path.exists():
            raise ValueError(f"{path} already exists")

    case_dir.mkdir(parents=True)
    wrapper.write_text(_TEMPLATE.format(app=app))
    readme = app_dir / "README.md"
    if not readme.exists():
        readme.write_text(_README.format(app=app))
    (case_dir / "case.json").write_text(json.dumps({"wrapper": wrapper.name, "args": ""}) + "\n")
    (case_dir / "stdout.txt").write_text("time: 1.25\ntime: 1.5\n")
    (case_dir / "expected.json").write_text(json.dumps([{"time": 1.25}, {"time": 1.5}]) + "\n")
    return wrapper
