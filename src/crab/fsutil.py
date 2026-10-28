"""Small filesystem helpers: leaving a folder before removing it, and binary paths."""

from __future__ import annotations

import os
import re
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


_PLACEHOLDER = re.compile(r"(?<!\$)\{")


def _has_placeholder(word: str) -> bool:
    """True for a sweep placeholder such as `{dir}`: a `{` not preceded by `$` (`${VAR}` is not one)."""
    return _PLACEHOLDER.search(word) is not None


def _split_words(value: str) -> list[str]:
    """`shlex.split`, or a whitespace split when a quote is unbalanced."""
    try:
        return shlex.split(value)
    except ValueError:
        return value.split()


def is_path_like(token: str) -> bool:
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
    tokens = _split_words(raw)
    if not tokens:
        return raw
    if len(tokens) == 1:
        token = tokens[0]
        if token.startswith("-") or not (bare_is_path or is_path_like(token)):
            return raw
        return _absolute_token(token)
    converted = [_absolute_token(t) if is_path_like(t) else t for t in tokens]
    if converted == tokens:
        return raw
    return shlex.join(converted)


def expand_binary(value: str) -> str:
    """A configured binary with `~` and `$VAR` expanded, the way the job's shell would see it.

    Each word that holds no sweep placeholder (`{dir}`) goes through `os.path.expandvars` and
    `os.path.expanduser`; a variable that is not set stays as typed. The words are rejoined with
    `shlex.join` only when one changed, otherwise `value` is returned exactly as given.
    """
    words = _split_words(value)
    expanded = [
        w if _has_placeholder(w) else os.path.expanduser(os.path.expandvars(w)) for w in words
    ]
    if expanded == words:
        return value
    return shlex.join(expanded)


def relative_path_word(value: str) -> str | None:
    """The first path-like word of a binary that depends on the current folder, else None.

    A word counts when `is_path_like` says so and it is not absolute, does not start with `~`,
    holds no `$` (expanded later, where the job runs) and no sweep placeholder (`{dir}`).
    """
    for word in _split_words(value):
        if not is_path_like(word) or _has_placeholder(word):
            continue
        if os.path.isabs(word) or word.startswith("~") or "$" in word:
            continue
        return word
    return None
