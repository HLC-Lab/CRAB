"""`crab update`: move a CRAB install and its wrappers checkout forward.

- By default it goes to the release: a checkout moves to the newest tag of its line (`vX.Y.Z` on
  the product line, `vX.Y.Z+sbatchman` on the sbatchman line, ADR-030), and the wrappers clone
  moves to the tag `vX.Y.Z` of the CRAB version it ends up on. Without such a wrappers tag the
  wrappers stay where they are. `--head` follows the branch for both instead.
- A checkout with no release tag, or in `--head` mode on a branch, fast-forwards that branch.
- A pip install is not touched (no git call on it): only the wrappers move, and the result
  points to `pipx upgrade crab-hpc`.
- Local edits to tracked files stop the update before anything changes.
- Dependencies are reinstalled only when pyproject.toml changed.
- The wrappers checkout is handled by `crab.cli.wrappers_clone`: moved when it is its own git
  clone, cloned when it is missing or holds only leftovers of the old tracked folder, and left
  alone when it holds anything else.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from crab import paths
from crab.cli.gitops import GitError, git, is_repo_root
from crab.cli.wrappers_clone import WRAPPERS_REPO_URL, modified_files, update_wrappers_clone

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(\+sbatchman)?$")
_VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE)

PIP_HINT = "pipx upgrade crab-hpc"

# The presets moved to crab-wrappers; edits to a tracked presets file this release no longer has
# need a manual migration.
_OLD_PRESET_FILES = ("config/presets.json", "src/crab/presets.json")
_PRESETS_MOVED = (
    "The presets moved to crab-wrappers in this version, and {file} is gone: put your changes as "
    "patches in local/presets/<name>.json, run `git checkout {file}`, then `crab update` again"
)

# One error type: a git failure and a refusal both stop the update, and `except UpdateError`
# must catch git failures.
UpdateError = GitError


def _version(tag: str) -> tuple[int, int, int] | None:
    match = _TAG.match(tag)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _newest_tag(repo: Path, sbatchman_line: bool) -> str | None:
    tags = [t for t in git(repo, "tag", "--list", "v*").splitlines() if _version(t)]
    line = [t for t in tags if t.endswith("+sbatchman") == sbatchman_line]
    return max(line, key=lambda t: _version(t) or (0, 0, 0)) if line else None


def _tag_version(tag: str) -> str | None:
    """`1.0.0` for `v1.0.0` and `v1.0.0+sbatchman`; None when `tag` is not a release tag."""
    match = _TAG.match(tag)
    return f"{match[1]}.{match[2]}.{match[3]}" if match else None


def _tree_version(root: Path) -> str:
    """CRAB's version as written in the checkout at `root`."""
    try:
        text = (root / "src" / "crab" / "__init__.py").read_text()
    except OSError as exc:
        raise UpdateError(f"cannot read CRAB's version in {root}: {exc}") from exc
    match = _VERSION_LINE.search(text)
    if not match:
        raise UpdateError(f"no __version__ in {root}/src/crab/__init__.py")
    return match[1]


def _crab_version_after(root: Path, crab: dict[str, Any]) -> str:
    """The CRAB version the update ended on: the release tag it moved to, else the tree's file."""
    return (_tag_version(crab["new"]) if crab["mode"] == "tag" else None) or _tree_version(root)


def _refuse_local_edits(root: Path) -> None:
    edited = git(root, "diff", "--name-only", "HEAD")
    if edited:
        for file in _OLD_PRESET_FILES:
            if file in edited.splitlines():
                raise UpdateError(_PRESETS_MOVED.format(file=file))
        files = ", ".join(edited.splitlines())
        raise UpdateError(
            f"Local edits to tracked files would be overwritten: {files}. Move per-machine "
            "changes to local/ (presets, receipts, wrappers), or commit or stash them, then retry."
        )


def _refuse_detached_head(root: Path) -> None:
    """`--head` needs a branch to follow: a checkout left on a release tag must pick one first."""
    if git(root, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        return
    try:
        current = git(root, "describe", "--tags", "--exact-match")
    except UpdateError:
        current = git(root, "rev-parse", "--short", "HEAD")
    raise UpdateError(
        f"CRAB is on release {current}, not a branch: run `git checkout <branch>` in {root}, "
        "then `crab update --head`"
    )


def _check_crab(root: Path, head: bool) -> None:
    """Refuse, before anything changes, a checkout `crab update` cannot move."""
    if not is_repo_root(root):
        raise UpdateError(f"{root} is not a git checkout; reinstall CRAB with git to update it.")
    _refuse_local_edits(root)
    if head:
        _refuse_detached_head(root)


def _check_wrappers(wrappers_dir: Path) -> None:
    """Refuse, before CRAB moves, a wrappers clone whose tracked files have local edits."""
    edited = modified_files(wrappers_dir)
    if edited:
        raise UpdateError(
            f"The wrappers clone {wrappers_dir} has local edits ({', '.join(edited)}): commit or "
            "stash them (or keep private wrappers in local/wrappers), then run crab update again."
        )


def _update_crab(root: Path, pip: Callable[[Path], None], to_release: bool) -> dict[str, Any]:
    before = git(root, "rev-parse", "HEAD")
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    release = None
    if branch != "HEAD" and to_release:
        # A fresh install cloned from a branch: the branch name gives the release line.
        git(root, "fetch", "-q", "--tags", "origin")
        release = _newest_tag(root, sbatchman_line=branch == "sbatchman")
    if release:
        git(root, "checkout", "-q", release)
        mode, old, new = "tag", branch, release
    elif branch != "HEAD":
        git(root, "pull", "--ff-only", "-q")
        mode, old, new = "branch", before[:12], git(root, "rev-parse", "HEAD")[:12]
    else:
        try:
            current = git(root, "describe", "--tags", "--exact-match")
        except UpdateError:
            current = git(root, "describe", "--tags", "--abbrev=0")
        git(root, "fetch", "-q", "--tags", "origin")
        target = _newest_tag(root, sbatchman_line=current.endswith("+sbatchman")) or current
        if target != current:
            git(root, "checkout", "-q", target)
        mode, old, new = "tag", current, target

    after = git(root, "rev-parse", "HEAD")
    changed = after != before
    reinstall = changed and bool(
        git(root, "diff", "--name-only", before, after, "--", "pyproject.toml")
    )
    if reinstall:
        pip(root)
    ref = "release" if to_release else "head"
    return {
        "mode": mode,
        "old": old,
        "new": new,
        "changed": changed,
        "reinstalled": reinstall,
        "ref": ref,
    }


def _pip_block(ref: str) -> dict[str, Any]:
    """The `crab` block of a pip install: nothing to move, a pointer to the right tool."""
    import crab

    return {
        "mode": "pip",
        "old": crab.__version__,
        "new": crab.__version__,
        "changed": False,
        "reinstalled": False,
        "hint": PIP_HINT,
        "ref": ref,
    }


def run_update(
    root: Path,
    wrappers_dir: Path,
    wrappers_url: str,
    pip: Callable[[Path], None],
    head: bool = False,
) -> dict[str, Any]:
    """Update the CRAB checkout at `root`, then the wrappers checkout.

    By default CRAB goes to its newest release tag and the wrappers to the same version's tag;
    with `head` both follow their branch. On a pip install CRAB itself is left alone.

    Raises:
        UpdateError: when CRAB cannot be updated (nothing is changed) or the wrappers update fails.
    """
    from crab.cli.contract import CONTRACT_SCHEMA

    ref = "head" if head else "release"
    checkout = paths.is_checkout()
    if checkout:
        _check_crab(root, head)
    _check_wrappers(wrappers_dir)
    if checkout:
        crab = _update_crab(root, pip, to_release=not head)
        version = None if head else _crab_version_after(root, crab)
    else:
        crab = _pip_block(ref)
        version = None if head else crab["new"]
    tag = f"v{version}" if version else None
    try:
        wrappers = update_wrappers_clone(root, wrappers_dir, wrappers_url, tag, rejoin_branch=True)
    except UpdateError as exc:
        if not crab["changed"]:
            raise
        raise UpdateError(f"CRAB is now at {crab['new']}; the wrappers step failed: {exc}") from exc
    return {"schema": CONTRACT_SCHEMA, "crab": crab, "wrappers": wrappers}


def _pip_install(root: Path) -> None:
    """Reinstall CRAB into the running environment, keeping the web extra if it is installed."""
    import importlib.metadata
    import importlib.util
    import sys

    # The dist was renamed to crab-hpc: the old one owns the `crab` script the new one writes.
    try:
        importlib.metadata.distribution("crab")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-q", "-y", "crab"],
            cwd=paths.neutral_dir(),
            check=True,
        )
    target = f"{root}[web]" if importlib.util.find_spec("fastapi") else str(root)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-e", target],
        cwd=paths.neutral_dir(),
        check=True,
    )


def handle_update(args: Any) -> None:
    import sys

    from crab.cli import contract
    from crab.core import wrapper_paths

    root = Path(wrapper_paths._CRAB_ROOT)
    wrappers_dir = Path(wrapper_paths.wrapper_search_path()[1])
    try:
        result = run_update(root, wrappers_dir, WRAPPERS_REPO_URL, _pip_install, head=args.head)
    except (UpdateError, subprocess.CalledProcessError) as exc:
        print(f"crab: update stopped: {exc}", file=sys.stderr)
        sys.exit(1)

    def human(data: dict[str, Any]) -> None:
        crab = data["crab"]
        moved = (
            f"{crab['old']} -> {crab['new']}" if crab["changed"] else f"{crab['new']} (up to date)"
        )
        if crab["mode"] == "pip":
            print(f"CRAB (pip install): {crab['new']}; to update CRAB run `{crab['hint']}`")
        else:
            print(f"CRAB ({crab['mode']}): {moved}")
        if crab["reinstalled"]:
            print("  dependencies reinstalled (pyproject.toml changed)")
        w = data["wrappers"]
        detail = w.get("detail") or (f"{w['old']} -> {w['new']}" if w["old"] else w["new"] or "")
        print(f"wrappers: {w['action']}" + (f" ({detail})" if detail else ""))

    contract.emit(result, args.json, human)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "update", help="Update this CRAB install and its wrappers (to the release, or --head)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--head",
        action="store_true",
        help="Follow the branch HEAD (CRAB and the wrappers) instead of the release.",
    )
    mode.add_argument(
        "--to-release",
        action="store_true",
        help="Go to the release: CRAB's newest tag and the matching wrappers tag. "
        "This is the default.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=handle_update)
