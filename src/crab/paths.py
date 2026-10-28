"""Where CRAB keeps its state: in the repo for a git checkout, in user dirs for a pip install.

Every module that needs the crab home, the config dir or the preset files asks here, so the
checkout-versus-installed decision is made in one place. A checkout keeps the in-repo layout
(`data/`, `local/`, `benchmarks/`, `wrappers/`); an install uses platformdirs user dirs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import platformdirs

APP_NAME = "crab"
APP_AUTHOR = "crab"

# Variables that name one folder, and variables that name a list of folders (`os.pathsep`
# separated). Exact names only: a receipt exports `CRAB_PATH_<ID>`, which is not a folder setting.
ENV_PATH_NAMES = (
    "CRAB_HOME",
    "CRAB_PATH_PRESETS",
    "CRAB_WEB_CONFIG_DIR",
    "CRAB_WEB_DATA_DIR",
    "CRAB_WEB_LIBRARY_DIR",
)
ENV_PATH_LIST_NAMES = ("CRAB_PATH_WRAPPERS",)


def check_env_value(name: str, raw: str) -> str:
    """`raw` (a value of variable `name`) with `~` expanded, otherwise as typed; relative is refused.

    A value that still holds a `$` is a template that is expanded later (a job never aborts on
    it), so it is returned as it is.

    Raises:
        ValueError: the value is a relative path; the message names the variable.
    """
    value = os.path.expanduser(raw)
    if "$" in value or os.path.isabs(value):
        return value
    try:
        example = f" (for example {os.path.abspath(value)})"
    except OSError:  # the current directory was deleted
        example = ""
    raise ValueError(
        f"{name}={raw!r} is a relative path, which would mean a different folder depending on "
        f"where CRAB runs. Set {name} to an absolute path{example}."
    )


def env_path(name: str) -> Path | None:
    """The folder named by environment variable `name`: None when unset or empty.

    Raises:
        ValueError: the value is a relative path (see `check_env_value`).
    """
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(check_env_value(name, raw))


def env_path_list(name: str) -> list[str]:
    """The folders named by list variable `name` (`os.pathsep` separated), empty entries dropped.

    Entries are strings with `~` expanded and otherwise exactly as typed.

    Raises:
        ValueError: an entry is a relative path (see `check_env_value`).
    """
    raw = os.environ.get(name, "")
    return [check_env_value(name, entry) for entry in raw.split(os.pathsep) if entry]


def validate_env_paths() -> None:
    """Check every folder variable once, so a relative value stops a command at startup.

    Raises:
        ValueError: a variable (or an entry of a list variable) is a relative path.
    """
    for name in ENV_PATH_NAMES:
        env_path(name)
    for name in ENV_PATH_LIST_NAMES:
        env_path_list(name)


def _env_home() -> Path | None:
    """CRAB_HOME, read at call time: None when unset."""
    return env_path("CRAB_HOME")


def _checkout_root_of(file: Path) -> Path | None:
    """The repo root when `file` is `<root>/src/crab/paths.py` of a source tree, else None."""
    file = file.resolve()
    root = file.parents[2]
    if not (root / "pyproject.toml").is_file():
        return None
    if (root / "src" / "crab" / "paths.py").resolve() != file:
        return None
    return root


def checkout_root() -> Path | None:
    """The git checkout (or source tree) this package runs from, None when installed."""
    if not is_checkout():
        return None
    return _checkout_root_of(Path(__file__))


def is_checkout() -> bool:
    """True when running from a source tree."""
    return _checkout_root_of(Path(__file__)) is not None


def layout() -> Literal["checkout", "installed"]:
    """The layout name reported by `crab info`."""
    return "checkout" if is_checkout() else "installed"


def crab_home() -> Path:
    """The CRAB_ROOT/__CWD__ value: the repo root, else CRAB_HOME, else the user data dir."""
    root = checkout_root()
    if root is not None:
        return root
    home = _env_home()
    if home is not None:
        return home
    return Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))


def config_dir() -> Path:
    """The per-user config dir: `$CRAB_HOME/config` if CRAB_HOME is set, else the user config dir."""
    home = _env_home()
    if home is not None:
        return home / "config"
    return Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))


def local_path(root: Path, *parts: str) -> Path:
    """A per-machine file: `<root>/local/...` in a checkout, `config_dir()/...` when installed."""
    if is_checkout():
        return root.joinpath("local", *parts)
    return config_dir().joinpath(*parts)


def user_presets_dir(root: Path) -> Path:
    """The directory of the user's override preset files: CRAB_PATH_PRESETS, else `local_path(root, "presets")`."""
    override = env_path("CRAB_PATH_PRESETS")
    if override is not None:
        return override
    return local_path(root, "presets")


def old_preset_files(root: Path) -> tuple[Path, Path]:
    """The single-file overrides read before 1.2, now refused: `config_dir()/presets.json` and `<root>/local/presets.json`."""
    return config_dir() / "presets.json", root / "local" / "presets.json"
