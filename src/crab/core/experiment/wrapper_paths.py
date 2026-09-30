"""Where a config's wrapper `path` points on disk, and loading it."""

from __future__ import annotations

import importlib.util
import os
import pathlib
from types import ModuleType

_CRAB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


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


def load_module(path: str) -> ModuleType:
    """Import a Python file (a wrapper or a workload manager) by its path."""
    name = pathlib.Path(path).stem
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
