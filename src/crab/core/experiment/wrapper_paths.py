"""Where a config's wrapper `path` points on disk, and loading it."""

from __future__ import annotations

import importlib.util
import os
import pathlib
from types import ModuleType


def resolve_wrapper_path(path: str) -> str:
    """A relative wrapper path is looked up under CRAB_PATH_WRAPPERS when that is set.

    Absolute paths, and relative ones when CRAB_PATH_WRAPPERS is unset, are returned unchanged.
    """
    if not os.path.isabs(path) and "CRAB_PATH_WRAPPERS" in os.environ:
        return os.path.join(os.environ["CRAB_PATH_WRAPPERS"], path)
    return path


def load_module(path: str) -> ModuleType:
    """Import a Python file (a wrapper or a workload manager) by its path."""
    name = pathlib.Path(path).stem
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
