"""The shared crab-wrappers clone that `crab update` and `crab wrappers` manage.

The wrappers checkout is pulled when it is its own git clone, cloned when it is missing or
holds only leftovers of the old tracked folder, and left alone when it holds anything else.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from crab.cli.gitops import GitError, git, is_repo_root

# The shared wrappers repository. `wrappers/` is a separate git clone of it, ignored by CRAB's
# own git; the wrapper contract is ADR-031.
WRAPPERS_REPO_URL = "https://github.com/HLC-Lab/crab-wrappers.git"

# What a wrappers/ folder keeps after CRAB stopped tracking it: ignored caches only. Tool caches
# go wholesale; a `__pycache__` counts only when it holds nothing but `*.pyc` files.
_WRAPPERS_CACHE_DIRS = (".pytest_cache", ".ruff_cache")


def _all_pyc(path: Path) -> bool:
    """True for a `*.pyc` file, or a folder holding nothing else (recursively, no symlinks)."""
    if path.is_symlink():
        return False
    if path.is_dir():
        return all(_all_pyc(child) for child in path.iterdir())
    return path.suffix == ".pyc"


def _is_leftover(path: Path) -> bool:
    if path.is_symlink():
        return False
    if path.is_dir():
        if path.name in _WRAPPERS_CACHE_DIRS:
            return True
        if path.name == "__pycache__":
            return _all_pyc(path)
        return all(_is_leftover(child) for child in path.iterdir())
    return path.suffix == ".pyc"


def _clear_leftovers(directory: Path) -> bool:
    """Empty `directory` when it holds only leftovers of the old tracked wrappers folder.

    Removes the children and keeps the folder itself. Returns whether the folder is now empty.
    Nothing is removed unless every entry is a leftover.
    """
    children = list(directory.iterdir())
    if not all(_is_leftover(child) for child in children):
        return False
    for child in children:
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return True


def update_wrappers_clone(root: Path, wrappers_dir: Path, url: str) -> dict[str, Any]:
    """Clone or fast-forward the wrappers checkout at `wrappers_dir`.

    Raises:
        GitError: when a git command fails or the leftovers cannot be cleaned.
    """
    entry: dict[str, Any] = {"dir": str(wrappers_dir), "action": "", "old": None, "new": None}
    empty = False
    if url and wrappers_dir.is_dir() and not wrappers_dir.is_symlink():
        if not is_repo_root(wrappers_dir):
            try:
                empty = _clear_leftovers(wrappers_dir)
            except OSError as exc:
                raise GitError(
                    f"cannot clean the leftovers in {wrappers_dir}: {exc}; "
                    "remove it by hand and run crab update again"
                ) from exc
    if empty or not wrappers_dir.exists():
        if not url:
            return {**entry, "action": "skipped", "detail": "no wrappers repository configured"}
        git(wrappers_dir.parent, "clone", "-q", url, str(wrappers_dir))
        return {**entry, "action": "cloned", "new": git(wrappers_dir, "rev-parse", "HEAD")[:12]}
    if not is_repo_root(wrappers_dir):
        inside = root.resolve() in wrappers_dir.resolve().parents
        why = "it is part of this CRAB checkout" if inside else "it is not a git clone"
        return {**entry, "action": "skipped", "detail": f"{wrappers_dir}: {why}"}
    old = git(wrappers_dir, "rev-parse", "HEAD")
    git(wrappers_dir, "pull", "--ff-only", "-q")
    new = git(wrappers_dir, "rev-parse", "HEAD")
    action = "updated" if new != old else "up to date"
    return {**entry, "action": action, "old": old[:12], "new": new[:12]}
