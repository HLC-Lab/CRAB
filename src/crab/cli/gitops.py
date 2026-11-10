"""Git helpers shared by `crab update` and the `crab wrappers` checkout commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(Exception):
    """A git command failed, or a step refused to change a repository."""


def git(repo: Path, *args: str, timeout: float = 120, env: dict[str, str] | None = None) -> str:
    """Run `git -C repo args` and return its stripped stdout.

    `env` is merged over the current environment for this one command.

    Raises:
        GitError: when git cannot run, times out, or exits non-zero.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)} failed in {repo}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return result.stdout.strip()


def is_repo_root(path: Path) -> bool:
    """Whether `path` holds a `.git` entry (a clone's root, not a subfolder)."""
    return (path / ".git").exists()


def superproject_root(path: Path) -> Path | None:
    """The working tree of the superproject when `path` is a submodule of one, else None.

    None also when `path` is not a repository or git cannot run: git prints nothing outside a
    submodule, so a plain repository nested in another one is not mistaken for a submodule.
    """
    try:
        found = git(path, "rev-parse", "--show-superproject-working-tree")
    except GitError:
        return None
    return Path(found) if found else None
