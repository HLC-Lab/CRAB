"""The shared crab-wrappers clone that `crab update` and `crab wrappers` manage.

The wrappers checkout is pulled when it is its own git clone, cloned when it is missing or
holds only leftovers of the old tracked folder, and left alone when it holds anything else.
`clone_status` reads its state (commit, edits, GitHub's head) without changing it.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from crab.cli.gitops import GitError, git, is_repo_root

# The shared wrappers repository. `wrappers/` is a separate git clone of it, ignored by CRAB's
# own git; the wrapper contract is ADR-031.
WRAPPERS_REPO_URL = "https://github.com/HLC-Lab/crab-wrappers.git"

# What a wrappers/ folder keeps after CRAB stopped tracking it: ignored caches only. Tool caches
# go wholesale; a `__pycache__` counts only when it holds nothing but `*.pyc` files.
_WRAPPERS_CACHE_DIRS = (".pytest_cache", ".ruff_cache")

# A cluster login node may be slow or offline: `status` must not hang on GitHub.
_LS_REMOTE_TIMEOUT = 20

# `ls-remote` runs unattended, often over SSH from the dashboard: it must fail, not prompt.
_NO_PROMPT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}

_URL_USERINFO = re.compile(r"(://)[^/@\s]*@")


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


def _tag_commit(repo: Path, tag: str) -> str | None:
    """The commit `tag` names in `repo`, or None when the tag does not exist."""
    try:
        return git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}")
    except GitError:
        return None


def _no_tag_line(tag: str, sha: str) -> str:
    return f"no wrappers tag {tag} yet, left at {sha[:12]}"


def _default_branch(repo: Path) -> str:
    """The branch `origin` calls its default (`origin/HEAD`), asking origin when it is unset."""
    try:
        return git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix(
            "origin/"
        )
    except GitError:
        git(repo, "remote", "set-head", "origin", "--auto")
        return git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix(
            "origin/"
        )


def _leave_detached_head(repo: Path) -> None:
    """Check out the default branch when `repo` is detached (release mode leaves it so)."""
    try:
        git(repo, "symbolic-ref", "-q", "HEAD")
    except GitError:
        git(repo, "checkout", "-q", _default_branch(repo))


def _pin_to_tag(repo: Path, entry: dict[str, Any], tag: str, old: str) -> dict[str, Any]:
    """Check out `tag` in the existing clone `repo`, or keep it in place when there is no such tag."""
    git(repo, "fetch", "-q", "--tags", "origin")
    target = _tag_commit(repo, tag)
    if target is None:
        kept = {"action": "kept", "old": old[:12], "new": old[:12]}
        return {**entry, **kept, "detail": _no_tag_line(tag, old)}
    if target != old:
        git(repo, "checkout", "-q", tag)
    action = "updated" if target != old else "up to date"
    return {**entry, "action": action, "old": old[:12], "new": target[:12]}


def update_wrappers_clone(
    root: Path,
    wrappers_dir: Path,
    url: str,
    tag: str | None = None,
    rejoin_branch: bool = False,
) -> dict[str, Any]:
    """Clone or move the wrappers checkout at `wrappers_dir`.

    Without `tag` it follows the branch: a fast-forward pull (a clone left detached by a release
    update goes back to the default branch first when `rejoin_branch` is set; `crab wrappers
    pull` leaves that to the owner and stops). With `tag` it moves to that tag, and keeps the
    checkout where it is when the tag does not exist yet (`action` "kept", the reason in `detail`).

    Raises:
        GitError: when a git command fails or the leftovers cannot be cleaned.
    """
    ref = "release" if tag else "head"
    entry: dict[str, Any] = {
        "dir": str(wrappers_dir),
        "action": "",
        "old": None,
        "new": None,
        "ref": ref,
    }
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
        cloned = {**entry, "action": "cloned"}
        if tag and _tag_commit(wrappers_dir, tag):
            git(wrappers_dir, "checkout", "-q", tag)
        elif tag:
            cloned["detail"] = _no_tag_line(tag, git(wrappers_dir, "rev-parse", "HEAD"))
        return {**cloned, "new": git(wrappers_dir, "rev-parse", "HEAD")[:12]}
    if not is_repo_root(wrappers_dir):
        inside = root.resolve() in wrappers_dir.resolve().parents
        why = "it is part of this CRAB checkout" if inside else "it is not a git clone"
        return {**entry, "action": "skipped", "detail": f"{wrappers_dir}: {why}"}
    old = git(wrappers_dir, "rev-parse", "HEAD")
    if tag:
        return _pin_to_tag(wrappers_dir, entry, tag, old)
    if rejoin_branch:
        _leave_detached_head(wrappers_dir)
    git(wrappers_dir, "pull", "--ff-only", "-q")
    new = git(wrappers_dir, "rev-parse", "HEAD")
    action = "updated" if new != old else "up to date"
    return {**entry, "action": action, "old": old[:12], "new": new[:12]}


def _without_credentials(text: str) -> str:
    """`text` with the user[:password]@ part of any scheme:// URL removed."""
    return _URL_USERINFO.sub(r"\1", text)


def _upstream_head(wrappers_dir: Path, branch: str | None) -> str:
    """The sha `origin` has for the clone's branch (its HEAD when detached), without fetching.

    Raises:
        GitError: when origin is unreachable or does not have the branch.
    """
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    out = git(
        wrappers_dir, "ls-remote", "origin", ref, timeout=_LS_REMOTE_TIMEOUT, env=_NO_PROMPT_ENV
    )
    if not out:
        raise GitError(f"origin has no {ref}")
    return out.split()[0]


def _state(ahead: int, behind: int) -> str:
    if ahead and behind:
        return "diverged"
    if behind:
        return "behind"
    if ahead:
        return "ahead"
    return "up to date"


def modified_files(wrappers_dir: Path) -> list[str]:
    """Tracked files whose content differs from HEAD (staged or not); [] when not a clone.

    Untracked files are not listed: they do not block a fast-forward. `git diff` rewrites the
    index when it refreshes stale stat data (even with --no-optional-locks), so it runs on a
    throwaway copy of the index: the shared clone is only read.

    Raises:
        GitError: when git fails in a clone.
    """
    if not is_repo_root(wrappers_dir):
        return []
    index = wrappers_dir / git(wrappers_dir, "rev-parse", "--git-path", "index")
    with tempfile.TemporaryDirectory(prefix="crab-index-") as scratch:
        copy = Path(scratch) / "index"
        try:
            shutil.copyfile(index, copy)
        except OSError as exc:
            raise GitError(f"cannot read the index of {wrappers_dir}: {exc}") from exc
        out = git(
            wrappers_dir,
            "--no-optional-locks",
            "diff",
            "--name-only",
            "-z",
            "HEAD",
            env={"GIT_INDEX_FILE": str(copy)},
        )
    return [path for path in out.split("\0") if path]


def clone_status(wrappers_dir: Path) -> dict[str, Any]:
    """Describe the wrappers clone at `wrappers_dir` without changing anything.

    `subject` is the first line of the HEAD commit's message (None when there is no commit).

    Never fetches: when GitHub's head is a commit the clone has not fetched, the state is
    "behind" and the counts are None. Tracked edits are listed, untracked files are not. The
    origin URL is reported without any user:password part.
    """
    status: dict[str, Any] = {
        "dir": str(wrappers_dir),
        "clone": False,
        "detail": None,
        "commit": None,
        "subject": None,
        "branch": None,
        "modified": [],
        "upstream": {"url": None, "head": None, "error": None},
        "behind": None,
        "ahead": None,
        "state": "unknown",
    }
    if not wrappers_dir.exists():
        status["detail"] = "missing: `crab wrappers pull` clones it"
        return status
    if not is_repo_root(wrappers_dir):
        status["detail"] = "not a git clone"
        return status
    status["clone"] = True
    try:
        status["commit"] = git(wrappers_dir, "rev-parse", "--verify", "-q", "HEAD")
    except GitError:
        status["detail"] = "no commits yet"
        return status
    try:
        status["subject"] = git(wrappers_dir, "log", "-1", "--format=%s")
    except GitError:
        status["subject"] = None  # the commit is still reported
    try:
        try:
            status["branch"] = git(wrappers_dir, "symbolic-ref", "--short", "-q", "HEAD")
        except GitError:
            status["branch"] = None  # detached HEAD
        status["modified"] = modified_files(wrappers_dir)
    except GitError as exc:
        status["detail"] = _without_credentials(str(exc))
        return status

    upstream = status["upstream"]
    try:
        upstream["url"] = _without_credentials(git(wrappers_dir, "remote", "get-url", "origin"))
        upstream["head"] = _upstream_head(wrappers_dir, status["branch"])
    except GitError as exc:
        upstream["error"] = _without_credentials(str(exc))
        return status

    try:
        git(wrappers_dir, "cat-file", "-e", f"{upstream['head']}^{{commit}}")
    except GitError:
        status["state"] = "behind"
        return status
    try:
        counts = git(
            wrappers_dir, "rev-list", "--left-right", "--count", f"HEAD...{upstream['head']}"
        )
        ahead, behind = (int(n) for n in counts.split())
    except (GitError, ValueError) as exc:
        upstream["error"] = _without_credentials(str(exc))
        return status
    status["ahead"], status["behind"] = ahead, behind
    status["state"] = _state(ahead, behind)
    return status
