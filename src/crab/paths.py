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

# Read once at import: it is a setting for a whole process (and its subprocesses).
_ENV_HOME = os.environ.get("CRAB_HOME") or None


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
    if _ENV_HOME:
        return Path(_ENV_HOME)
    return Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))


def config_dir() -> Path:
    """The per-user config dir: `$CRAB_HOME/config` if CRAB_HOME is set, else the user config dir."""
    if _ENV_HOME:
        return Path(_ENV_HOME) / "config"
    return Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))


def local_path(root: Path, *parts: str) -> Path:
    """A per-machine file: `<root>/local/...` in a checkout, `config_dir()/...` when installed."""
    if is_checkout():
        return root.joinpath("local", *parts)
    return config_dir().joinpath(*parts)


def shipped_presets() -> Path:
    """The presets file that ships inside the package."""
    return Path(__file__).resolve().parent / "presets.json"


def user_presets() -> Path:
    """The user's presets file: CRAB_PATH_PRESETS, else `config_dir()/presets.json`."""
    override = os.environ.get("CRAB_PATH_PRESETS")
    if override:
        return Path(override)
    return config_dir() / "presets.json"


def user_presets_dir(root: Path) -> Path:
    """The directory of the user's override preset files: CRAB_PATH_PRESETS, else `local_path(root, "presets")`."""
    override = os.environ.get("CRAB_PATH_PRESETS")
    if override:
        return Path(override)
    return local_path(root, "presets")


def old_preset_files(root: Path) -> tuple[Path, Path]:
    """The single-file overrides read before 1.2, now refused: `config_dir()/presets.json` and `<root>/local/presets.json`."""
    return config_dir() / "presets.json", root / "local" / "presets.json"
