"""`crab update`: move a CRAB install and its wrappers checkout forward.

- A checkout on a branch (developers, or any install made before the first release tag)
  fast-forwards that branch.
- A checkout sitting on a release tag moves to the newest tag of its line: `vX.Y.Z` on the
  product line, `vX.Y.Z+sbatchman` on the sbatchman line (ADR-030).
- Local edits to tracked files stop the update before anything changes.
- Dependencies are reinstalled only when pyproject.toml changed.
- The wrappers checkout is pulled when it is its own git clone, cloned when it is missing,
  and left alone when it is still part of the CRAB checkout.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(\+sbatchman)?$")
_GIT_TIMEOUT_S = 120


class UpdateError(Exception):
    """The update could not run, or stopped before changing anything it could not finish."""


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"git {' '.join(args)} failed in {repo}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise UpdateError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return result.stdout.strip()


def _is_repo_root(path: Path) -> bool:
    return (path / ".git").exists()


def _version(tag: str) -> tuple[int, int, int] | None:
    match = _TAG.match(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _newest_tag(repo: Path, sbatchman_line: bool) -> str | None:
    tags = [t for t in _git(repo, "tag", "--list", "v*").splitlines() if _version(t)]
    line = [t for t in tags if t.endswith("+sbatchman") == sbatchman_line]
    return max(line, key=lambda t: _version(t) or (0, 0, 0)) if line else None


def _update_crab(root: Path, pip: Callable[[Path], None], to_release: bool) -> dict[str, Any]:
    if not _is_repo_root(root):
        raise UpdateError(f"{root} is not a git checkout; reinstall CRAB with git to update it.")
    edited = _git(root, "diff", "--name-only", "HEAD")
    if edited:
        files = ", ".join(edited.splitlines())
        raise UpdateError(
            f"Local edits to tracked files would be overwritten: {files}. Move per-machine "
            "changes to local/ (presets, receipts, wrappers), or commit or stash them, then retry."
        )

    before = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    release = None
    if branch != "HEAD" and to_release:
        # A fresh install cloned from a branch: the branch name gives the release line.
        _git(root, "fetch", "-q", "--tags", "origin")
        release = _newest_tag(root, sbatchman_line=branch == "sbatchman")
    if release:
        _git(root, "checkout", "-q", release)
        mode, old, new = "tag", branch, release
    elif branch != "HEAD":
        _git(root, "pull", "--ff-only", "-q")
        mode, old, new = "branch", before[:12], _git(root, "rev-parse", "HEAD")[:12]
    else:
        try:
            current = _git(root, "describe", "--tags", "--exact-match")
        except UpdateError:
            current = _git(root, "describe", "--tags", "--abbrev=0")
        _git(root, "fetch", "-q", "--tags", "origin")
        target = _newest_tag(root, sbatchman_line=current.endswith("+sbatchman")) or current
        if target != current:
            _git(root, "checkout", "-q", target)
        mode, old, new = "tag", current, target

    after = _git(root, "rev-parse", "HEAD")
    changed = after != before
    reinstall = changed and bool(
        _git(root, "diff", "--name-only", before, after, "--", "pyproject.toml")
    )
    if reinstall:
        pip(root)
    return {"mode": mode, "old": old, "new": new, "changed": changed, "reinstalled": reinstall}


def _update_wrappers(root: Path, wrappers_dir: Path, url: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"dir": str(wrappers_dir), "action": "", "old": None, "new": None}
    if not wrappers_dir.exists():
        if not url:
            return {**entry, "action": "skipped", "detail": "no wrappers repository configured"}
        _git(wrappers_dir.parent, "clone", "-q", url, str(wrappers_dir))
        return {**entry, "action": "cloned", "new": _git(wrappers_dir, "rev-parse", "HEAD")[:12]}
    if not _is_repo_root(wrappers_dir):
        inside = root.resolve() in wrappers_dir.resolve().parents
        why = "it is part of this CRAB checkout" if inside else "it is not a git clone"
        return {**entry, "action": "skipped", "detail": f"{wrappers_dir}: {why}"}
    old = _git(wrappers_dir, "rev-parse", "HEAD")
    _git(wrappers_dir, "pull", "--ff-only", "-q")
    new = _git(wrappers_dir, "rev-parse", "HEAD")
    action = "updated" if new != old else "up to date"
    return {**entry, "action": action, "old": old[:12], "new": new[:12]}


def run_update(
    root: Path,
    wrappers_dir: Path,
    wrappers_url: str,
    pip: Callable[[Path], None],
    to_release: bool = False,
) -> dict[str, Any]:
    """Update the CRAB checkout at `root`, then the wrappers checkout.

    With `to_release`, a checkout on a branch moves to the newest release tag of that branch's
    line instead of fast-forwarding (the installer uses this right after cloning).

    Raises:
        UpdateError: when CRAB cannot be updated (nothing is changed) or the wrappers update fails.
    """
    from crab.cli.contract import CONTRACT_SCHEMA

    crab = _update_crab(root, pip, to_release)
    wrappers = _update_wrappers(root, wrappers_dir, wrappers_url)
    return {"schema": CONTRACT_SCHEMA, "crab": crab, "wrappers": wrappers}


# The shared wrappers repository (the separate crab-wrappers checkout, see ADR-031/ADR-032).
WRAPPERS_REPO_URL = "https://github.com/HLC-Lab/crab-wrappers.git"


def _pip_install(root: Path) -> None:
    """Reinstall CRAB into the running environment, keeping the web extra if it is installed."""
    import importlib.util
    import sys

    target = f"{root}[web]" if importlib.util.find_spec("fastapi") else str(root)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", target], check=True)


def handle_update(args: Any) -> None:
    import sys

    from crab.cli import contract
    from crab.core import wrapper_paths

    root = Path(wrapper_paths._CRAB_ROOT)
    wrappers_dir = Path(wrapper_paths.wrapper_search_path()[1])
    try:
        result = run_update(
            root, wrappers_dir, WRAPPERS_REPO_URL, _pip_install, to_release=args.to_release
        )
    except (UpdateError, subprocess.CalledProcessError) as exc:
        print(f"crab: update stopped: {exc}", file=sys.stderr)
        sys.exit(1)

    def human(data: dict[str, Any]) -> None:
        crab = data["crab"]
        moved = (
            f"{crab['old']} -> {crab['new']}" if crab["changed"] else f"{crab['new']} (up to date)"
        )
        print(f"CRAB ({crab['mode']}): {moved}")
        if crab["reinstalled"]:
            print("  dependencies reinstalled (pyproject.toml changed)")
        w = data["wrappers"]
        detail = w.get("detail") or (f"{w['old']} -> {w['new']}" if w["old"] else w["new"] or "")
        print(f"wrappers: {w['action']}" + (f" ({detail})" if detail else ""))

    contract.emit(result, args.json, human)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("update", help="Update this CRAB install and its wrappers")
    parser.add_argument(
        "--to-release",
        action="store_true",
        help="On a branch checkout, switch to the newest release tag of that branch's line.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=handle_update)
