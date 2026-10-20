"""Git helpers shared by `crab update` and the `crab wrappers` checkout commands."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """A git command failed, or a step refused to change a repository."""


def git(repo: Path, *args: str, timeout: float = 120) -> str:
    """Run `git -C repo args` and return its stripped stdout.

    Raises:
        GitError: when git cannot run, times out, or exits non-zero.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)} failed in {repo}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return result.stdout.strip()


def is_repo_root(path: Path) -> bool:
    return (path / ".git").exists()
