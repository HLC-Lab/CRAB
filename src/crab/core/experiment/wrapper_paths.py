"""Where a config's wrapper `path` points on disk."""

from __future__ import annotations

import os


def resolve_wrapper_path(path: str) -> str:
    """A relative wrapper path is looked up under CRAB_PATH_WRAPPERS when that is set.

    Absolute paths, and relative ones when CRAB_PATH_WRAPPERS is unset, are returned unchanged.
    """
    if not os.path.isabs(path) and "CRAB_PATH_WRAPPERS" in os.environ:
        return os.path.join(os.environ["CRAB_PATH_WRAPPERS"], path)
    return path
