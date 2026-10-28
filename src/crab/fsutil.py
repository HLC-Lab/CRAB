"""Small filesystem helpers: leaving a folder before removing it, and absolute binary paths."""

from __future__ import annotations

import os
import shlex


def _is_inside(path: str, folder: str) -> bool:
    """True when `path` is `folder` or lies below it (both already real paths)."""
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives, or a mix of absolute and relative paths
        return False


def leave_dir_before_remove(target: str | os.PathLike[str]) -> None:
    """Move the process out of `target` when it is, or lies in, the current folder.

    A removed current folder breaks `os.getcwd()`, `os.path.abspath` and git run from it, and
    so every later relative path in the same run. The process goes to the real parent of
    `target` (the folder that survives), else to the user's home folder, else to `/`. A
    current folder that is already gone is left the same way, whatever `target` is. Anything
    else is untouched.
    """
    real_target = os.path.realpath(target)
    try:
        cwd = os.path.realpath(os.getcwd())
    except OSError:  # FileNotFoundError: the current folder was deleted
        pass
    else:
        if not _is_inside(cwd, real_target):
            return
    for destination in (os.path.dirname(real_target), os.path.expanduser("~"), "/"):
        try:
            os.chdir(destination)
        except OSError:
            continue
        os.environ["PWD"] = os.getcwd()
        return


def _is_path_like(token: str) -> bool:
    """A word that names a path: not a flag, and it has a separator, a leading `~`, or is `.`/`..`."""
    if token.startswith("-"):
        return False
    return os.sep in token or token.startswith("~") or token in (".", "..")


def _absolute_token(token: str) -> str:
    """One path word made absolute and normalised (see `absolute_binary` for the rules)."""
    if "$" in token:
        raise ValueError(
            f"{token!r}: environment variables are not expanded here, type the full path."
        )
    expanded = os.path.expanduser(token)
    if expanded.startswith("~"):
        raise ValueError(f"{token!r}: the leading ~ could not be expanded, type the full path.")
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    try:
        return os.path.abspath(expanded)
    except OSError as error:  # FileNotFoundError: the current folder was deleted
        raise ValueError(
            f"{token!r} is relative and the current folder no longer exists, give the full path."
        ) from error


def absolute_binary(raw: str, *, bare_is_path: bool = False) -> str:
    """A binary path as typed by a person, made absolute so a receipt can store it.

    A path-like word (no leading `-`, and it has a `/`, starts with `~`, or is `.` or `..`) has
    `~` expanded and is made absolute from the current folder (`abspath`, symlinks are kept; an
    absolute result goes through `normpath`). A bare command name (`xhpl`, `pw.x`) is left alone,
    unless `bare_is_path` says the person was asked for a path: then a single word is always one.
    A launcher line of several words has each path-like word made absolute and the other words
    kept; when no word changed the raw string is returned as given, as are empty values.

    Raises:
        ValueError: a path-like word names an environment variable or an unknown user, or is
            relative while the current folder no longer exists. The message says what to type.
    """
    try:
        tokens = shlex.split(raw)
    except ValueError:  # an unbalanced quote: split on whitespace instead
        tokens = raw.split()
    if not tokens:
        return raw
    if len(tokens) == 1:
        token = tokens[0]
        if token.startswith("-") or not (bare_is_path or _is_path_like(token)):
            return raw
        return _absolute_token(token)
    converted = [_absolute_token(t) if _is_path_like(t) else t for t in tokens]
    if converted == tokens:
        return raw
    return shlex.join(converted)
