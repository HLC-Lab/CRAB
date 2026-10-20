"""`crab wrappers show`: one wrapper described for the dashboard.

What it is (module docstring, else the nearest README's first paragraph), its declared metrics,
its binary, and the GitHub URL of the file at the commit of the clone that holds it.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from crab.cli.gitops import GitError, git
from crab.cli.wrappers_catalog import _crab_root_env, _describe_app, is_wrapper_relpath
from crab.core.wrapper_paths import (
    missing_wrappers_hint,
    moved_wrapper_note,
    resolve_wrapper_path,
    wrapper_search_path,
)

_GITHUB_ORIGIN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
# A README line that is not prose: a heading, an HTML tag or a badge.
_NOT_PROSE = ("#", "<", "[![")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_GIT_TIMEOUT = 10


class BadWrapperPath(ValueError):
    """The path is absolute or climbs out of the wrappers folders."""


def _docstring(path: Path) -> str | None:
    """The module docstring, read without importing the file."""
    try:
        doc = ast.get_docstring(ast.parse(path.read_bytes()))
    except (SyntaxError, ValueError, OSError):
        return None
    return (doc or "").strip() or None


def _first_paragraph(text: str) -> str | None:
    """The first prose paragraph of a README, its lines joined with single spaces.

    Markdown links read as their text; everything else stays as written.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if lines:
                break
            continue
        if not lines and line.startswith(_NOT_PROSE):
            continue
        lines.append(line)
    return _MARKDOWN_LINK.sub(r"\1", " ".join(lines)) or None


def _describe_text(path: Path, folder: Path) -> dict[str, Any]:
    """What the wrapper is: its docstring, else the nearest README below the search folder."""
    doc = _docstring(path)
    if doc:
        return {"text": doc, "source": "docstring", "path": str(path.relative_to(folder))}
    current = path.parent
    while current != folder and folder in current.parents:
        readme = current / "README.md"
        if readme.is_file():
            try:
                text = _first_paragraph(readme.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                text = None
            if text:
                return {"text": text, "source": "readme", "path": str(readme.relative_to(folder))}
        current = current.parent
    return {"text": None, "source": None, "path": None}


def _git_out(folder: Path, *args: str) -> str | None:
    """Stdout of `git -C folder ...`, or None when git fails, times out or is missing."""
    try:
        return git(folder, *args, timeout=_GIT_TIMEOUT)
    except GitError:
        return None


def _source_of(path: Path) -> dict[str, str | None]:
    """The commit of the git clone holding `path` and the GitHub URL of the file at it.

    Both are None unless the file is in that commit: an untracked, staged-only or git-ignored
    file (such as one in `local/wrappers`) has no commit of its own.
    """
    source: dict[str, str | None] = {"commit": None, "url": None}
    toplevel = _git_out(path.parent, "rev-parse", "--show-toplevel")
    commit = _git_out(path.parent, "rev-parse", "HEAD")
    if not toplevel or not commit:
        return source
    real = Path(os.path.realpath(path.parent)) / path.name
    try:
        rel = real.relative_to(os.path.realpath(toplevel)).as_posix()
    except ValueError:
        return source
    # `HEAD:<path>` names the path literally (no glob) and reads the commit, not the index.
    if _git_out(path.parent, "cat-file", "-e", f"HEAD:{rel}") is None:
        return source
    source["commit"] = commit
    origin = _git_out(path.parent, "remote", "get-url", "origin")
    match = _GITHUB_ORIGIN.match(origin) if origin else None
    if match is None:
        return source
    base = f"https://github.com/{match['owner']}/{match['repo']}"
    source["url"] = f"{base}/blob/{commit}/{quote(rel, safe='/')}"
    return source


def _locate(relpath: str) -> tuple[Path, Path]:
    """The wrapper file `relpath` names and the search folder that holds it.

    The first search folder with the file wins; failing that, a moved file (an old
    `<suite>/<name>.py` path) is found where `resolve_wrapper_path` puts it.

    Raises:
        LookupError: if nothing along the search path is a wrapper with that path.
    """
    folders = [Path(f) for f in wrapper_search_path()]
    for folder in folders:
        if (folder / relpath).is_file():
            found, owner = folder / relpath, folder
            break
    else:
        moved = Path(resolve_wrapper_path(relpath)) if moved_wrapper_note(relpath) else None
        # A moved file sits at <folder>/<suite>/<group>/<name>.
        if moved is None or not moved.is_file() or moved.parents[2] not in folders:
            found = owner = None
        else:
            found, owner = moved, moved.parents[2]
    if found is None or owner is None or not is_wrapper_relpath(found.relative_to(owner)):
        hint = missing_wrappers_hint()
        raise LookupError(f"wrapper not found: {relpath}" + (f". {hint}" if hint else ""))
    return found, owner


def show_wrapper(relpath: str) -> dict[str, Any]:
    """Describe the wrapper `relpath` names: what it is, its metrics, binary and source.

    A metric's `conv` is True when the wrapper declares it a convergence target.

    Raises:
        BadWrapperPath: if `relpath` is absolute or has a `..` component.
        LookupError: if no wrapper along the search path has that path.
    """
    from crab.cli.contract import CONTRACT_SCHEMA

    if os.path.isabs(relpath) or ".." in Path(relpath).parts:
        raise BadWrapperPath(relpath)
    resolved, folder = _locate(relpath)

    with _crab_root_env():
        entry, app = _describe_app(resolved, folder)
    entry.pop("metrics")
    metrics = [
        {
            "name": m.get("name"),
            "unit": m.get("unit", ""),
            "role": m.get("role"),
            "conv": bool(m.get("conv")),
        }
        for m in (app.metadata if app is not None else [])
    ]
    return {
        "schema": CONTRACT_SCHEMA,
        **entry,
        "moved_from": relpath if moved_wrapper_note(relpath) else None,
        "description": _describe_text(resolved, folder),
        "metrics": metrics,
        "source": _source_of(resolved),
    }
