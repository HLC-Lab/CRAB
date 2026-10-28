"""Where a config's wrapper `path` points on disk, and loading it."""

from __future__ import annotations

import glob
import importlib.util
import os
import pathlib
from types import ModuleType
from typing import Any

from crab import paths

_CRAB_ROOT = str(paths.crab_home())

# What an untracked `wrappers/` checkout leaves behind: git keeps ignored files and folders when a
# pull removes the tracked ones, so a folder holding only these is not a checkout.
_LEFTOVER_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache")
_LEFTOVER_SUFFIXES = (".pyc",)

_WRAPPERS_REPO = "https://github.com/HLC-Lab/crab-wrappers"


class WrapperPathError(ValueError):
    """A config names wrappers that are not on the search path: a user error, no traceback."""


def wrapper_search_path() -> list[str]:
    """Folders searched for a relative wrapper path, in order.

    The untracked `local/wrappers` comes first (private or in-progress wrappers), then each
    folder in CRAB_PATH_WRAPPERS (an os.pathsep-separated list), or the checkout's `wrappers/`
    when CRAB_PATH_WRAPPERS is unset.
    """
    shared = paths.env_path_list("CRAB_PATH_WRAPPERS")
    return [str(paths.local_path(pathlib.Path(_CRAB_ROOT), "wrappers"))] + (
        shared or [os.path.join(_CRAB_ROOT, "wrappers")]
    )


def _direct_hit(path: str) -> str | None:
    """The first search-path folder's `<folder>/<path>` that exists, else None."""
    for folder in wrapper_search_path():
        candidate = os.path.join(folder, path)
        if os.path.exists(candidate):
            return candidate
    return None


def _moved_match(path: str) -> str | None:
    """The one `<folder>/<suite>/*/<name>` a missed `<suite>/<name>` path moved to, else None.

    Only a two-part relative path qualifies (the Blink wrappers moved one level down, into one
    folder per upstream group). The first search-path folder with any match decides: when it
    holds two or more, the path is ambiguous and nothing is returned.
    """
    parts = pathlib.PurePath(path).parts
    if os.path.isabs(path) or len(parts) != 2:
        return None
    suite, name = parts
    for folder in wrapper_search_path():
        pattern = os.path.join(glob.escape(os.path.join(folder, suite)), "*", glob.escape(name))
        matches = sorted(m for m in glob.glob(pattern) if os.path.isfile(m))
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


def resolve_wrapper_path(path: str) -> str:
    """The file a config's wrapper `path` names: the first match along the search path.

    Absolute paths are returned unchanged. A relative `<suite>/<name>.py` found nowhere but
    present once in a subfolder (`<suite>/<group>/<name>.py`) resolves to that file: wrappers
    moved into groups and old configs keep working (`moved_wrapper_note` says so). Any other
    relative path found nowhere resolves the way it did before the search path existed (under
    the first CRAB_PATH_WRAPPERS folder, else relative to the working directory), so old
    configs and error messages stay the same.
    """
    if os.path.isabs(path):
        return path
    direct = _direct_hit(path)
    if direct is not None:
        return direct
    moved = _moved_match(path)
    if moved is not None:
        return moved
    configured = paths.env_path_list("CRAB_PATH_WRAPPERS")
    if configured:
        return os.path.join(configured[0], path)
    return path


def moved_wrapper_note(path: str) -> str | None:
    """A message when `path` only resolves through a moved wrapper's new location, else None.

    None for an absolute path, a direct hit, a miss, and an ambiguous move.
    """
    if os.path.isabs(path) or _direct_hit(path) is not None:
        return None
    moved = _moved_match(path)
    if moved is None:
        return None
    suite, name = pathlib.PurePath(path).parts
    group = pathlib.Path(moved).parent.name
    return f"wrapper {path} moved to {suite}/{group}/{name}: update the config"


# Blink wrappers before the move to CMake and group folders: old relpath -> new relpath, or None
# for a wrapper that was removed (`_REMOVED_NOTES` says what replaces it). Transcribed from the
# "Old name | New name" table and the "Bursty noise wrappers are gone" section of
# wrappers/blink/README.md (the crab-wrappers repository).
_RENAMED: dict[str, str | None] = {
    "blink/a2a_b.py": "blink/alltoall/alltoall_b.py",
    "blink/a2a_nb.py": "blink/alltoall/alltoall_nb.py",
    "blink/a2a_comm_only.py": "blink/alltoall/alltoall_comm_only.py",
    "blink/a2a_man.py": "blink/alltoall/alltoall_man.py",
    "blink/agtr_comm_only.py": "blink/allgather/allgather_comm_only.py",
    "blink/ardc_b.py": "blink/allreduce/allreduce_b.py",
    "blink/ardc_nb.py": "blink/allreduce/allreduce_nb.py",
    "blink/bdc_b.py": "blink/broadcast/broadcast_b.py",
    "blink/bdc_nb.py": "blink/broadcast/broadcast_nb.py",
    "blink/barrier.py": "blink/barrier/barrier_b.py",
    "blink/inc_b.py": "blink/incast/incast_b.py",
    "blink/inc_bsnbr.py": "blink/incast/incast_bsnbr.py",
    "blink/inc_get.py": "blink/incast/incast_get.py",
    "blink/inc_nb.py": "blink/incast/incast_nb.py",
    "blink/inc_put.py": "blink/incast/incast_put.py",
    "blink/o2o_bsnbr.py": "blink/pairwise/pairwise_bsnbr.py",
    "blink/o2o_nb.py": "blink/pairwise/pairwise_nb.py",
    "blink/ping-pong_b.py": "blink/pingpong/pingpong_b.py",
    "blink/pw-ping-pong_b.py": "blink/pingpong/pingpong_pairwise_b.py",
    "blink/bursty_noise_a2a.py": None,
    "blink/bursty_noise_incast.py": None,
}

_BURST_FLAGS = "-endl -blength <length> -bpause <pause>"

_REMOVED_NOTES: dict[str, str] = {
    "blink/bursty_noise_a2a.py": (
        "use blink/alltoall/alltoall_nb.py with the burst flags "
        f"({_BURST_FLAGS}, and -msgsize 2097152 for the old 2 MiB per peer); "
        'set "collect": false, "end": "f" for the app'
    ),
    "blink/bursty_noise_incast.py": (
        "use blink/incast/incast_nb.py with the burst flags "
        f"({_BURST_FLAGS}, and -msgsize 2097152 for the old 2 MiB per peer); "
        'set "collect": false, "end": "f" for the app'
    ),
}


def renamed_wrapper_note(path: str) -> str | None:
    """A message when `path` is a pre-rename Blink wrapper name, else None.

    Says the new path for a renamed wrapper, or what replaces a removed one. Looks only at the
    name: whether a file exists is `wrapper_path_problem`'s question.
    """
    key = pathlib.PurePath(path).as_posix()
    if key not in _RENAMED:
        return None
    new = _RENAMED[key]
    if new is not None:
        return f"wrapper {path} was renamed to {new}: update the config"
    return f"wrapper {path} was removed: {_REMOVED_NOTES[key]}"


def wrapper_path_problem(path: str) -> str | None:
    """Why a config's wrapper `path` names no file on the search path, or None when it does.

    A direct hit, a moved match and an existing absolute path are all fine. A known old name gets
    its rename note; any other miss gets `missing_wrappers_hint()` when there is one.
    """
    if os.path.isfile(resolve_wrapper_path(path)):
        return None
    message = (
        f"wrapper {path} not found on the wrapper search path ({', '.join(wrapper_search_path())})"
    )
    detail = renamed_wrapper_note(path) or missing_wrappers_hint()
    return f"{message}. {detail}" if detail else message


def config_wrapper_path_problems(
    config: dict[str, Any],
    only: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """`wrapper_path_problem` for every app of every experiment of a parsed config.

    `only` limits the check to those experiment keys (a `crab run --only` rerun). `env` is the
    job's execution environment (a preset's `env`): a CRAB_PATH_WRAPPERS set there wins over this
    process's, as it does in the job. Its bash variables are expanded here; a value that still
    holds a `$` after that is not guessed at, and the check is skipped. Placeholder paths
    (`{var}`) are skipped: they are only known once a sweep expands them. A config shape that
    holds no experiments or apps yields nothing (the schema checks own that). The legacy
    `applications` form counts as one experiment, as the engine reads it.
    """
    preset_value = (env or {}).get("CRAB_PATH_WRAPPERS")
    if preset_value is None:
        return _config_problems(config, only)
    expanded = os.path.expandvars(preset_value)
    if "$" in expanded:
        return []
    saved = os.environ.get("CRAB_PATH_WRAPPERS")
    os.environ["CRAB_PATH_WRAPPERS"] = expanded
    try:
        return _config_problems(config, only)
    finally:
        if saved is None:
            del os.environ["CRAB_PATH_WRAPPERS"]
        else:
            os.environ["CRAB_PATH_WRAPPERS"] = saved


def _config_problems(config: dict[str, Any], only: list[str] | None) -> list[str]:
    experiments = config.get("experiments")
    if experiments is None and "applications" in config:
        legacy = config["applications"]
        if isinstance(legacy, dict) and "apps" in legacy:
            experiments = {"default_ex": legacy}
        else:
            experiments = {"default_ex": {"apps": legacy}}
    if not isinstance(experiments, dict):
        return []
    problems: list[str] = []
    for key, experiment in experiments.items():
        if only is not None and key not in only:
            continue
        apps = experiment.get("apps") if isinstance(experiment, dict) else None
        if not isinstance(apps, dict):
            continue
        for app in apps.values():
            path = app.get("path") if isinstance(app, dict) else None
            if not isinstance(path, str) or not path.strip() or "{" in path:
                continue
            problem = wrapper_path_problem(path)
            if problem is not None and problem not in problems:
                problems.append(problem)
    return problems


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
    command = "crab update" if paths.is_checkout() else "crab wrappers pull"
    return f"wrappers/ is missing: run `{command}` to clone the wrappers ({_WRAPPERS_REPO})"


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
