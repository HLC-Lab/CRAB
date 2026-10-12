"""Where a config's wrapper `path` points on disk, and loading it."""

from __future__ import annotations

import importlib.util
import os
import pathlib
from types import ModuleType

_CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# What an untracked `wrappers/` checkout leaves behind: git keeps ignored files and folders when a
# pull removes the tracked ones, so a folder holding only these is not a checkout.
_LEFTOVER_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache")
_LEFTOVER_SUFFIXES = (".pyc",)

_WRAPPERS_REPO = "https://github.com/HLC-Lab/crab-wrappers"


def wrapper_search_path() -> list[str]:
    """Folders searched for a relative wrapper path, in order.

    The untracked `local/wrappers` comes first (private or in-progress wrappers), then each
    folder in CRAB_PATH_WRAPPERS (an os.pathsep-separated list), or the checkout's `wrappers/`
    when CRAB_PATH_WRAPPERS is unset.
    """
    configured = os.environ.get("CRAB_PATH_WRAPPERS")
    shared = [d for d in configured.split(os.pathsep) if d] if configured else []
    return [os.path.join(_CRAB_ROOT, "local", "wrappers")] + (
        shared or [os.path.join(_CRAB_ROOT, "wrappers")]
    )


def resolve_wrapper_path(path: str) -> str:
    """The file a config's wrapper `path` names: the first match along the search path.

    Absolute paths are returned unchanged. A relative path found nowhere resolves the way it
    did before the search path existed (under the first CRAB_PATH_WRAPPERS folder, else
    relative to the working directory), so old configs and error messages stay the same.
    """
    if os.path.isabs(path):
        return path
    for folder in wrapper_search_path():
        candidate = os.path.join(folder, path)
        if os.path.exists(candidate):
            return candidate
    configured = os.environ.get("CRAB_PATH_WRAPPERS")
    if configured:
        return os.path.join(configured.split(os.pathsep)[0], path)
    return path


def _is_usable_checkout(folder: str) -> bool:
    """True when `folder` holds anything besides the leftovers of a removed checkout."""
    for _dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in _LEFTOVER_DIRS]
        if any(not name.endswith(_LEFTOVER_SUFFIXES) for name in filenames):
            return True
    return False


def missing_wrappers_hint() -> str:
    """How to get the wrappers when the default checkout has none, else "".

    Empty when CRAB_PATH_WRAPPERS is set: the user chose their own folders.
    """
    if os.environ.get("CRAB_PATH_WRAPPERS"):
        return ""
    if _is_usable_checkout(os.path.join(_CRAB_ROOT, "wrappers")):
        return ""
    return f"wrappers/ is missing: run `crab update` to clone the wrappers ({_WRAPPERS_REPO})"


def load_module(path: str) -> ModuleType:
    """Import a Python file (a wrapper or a workload manager) by its path.

    Raises:
        ImportError: if the file cannot be imported as Python code.
    """
    name = pathlib.Path(path).stem
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {path!r} as Python code (a wrapper must be a .py file).")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
