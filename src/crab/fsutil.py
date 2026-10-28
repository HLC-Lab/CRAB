"""Small filesystem helpers shared by the commands that remove folders."""

from __future__ import annotations

import os


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
