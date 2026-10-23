"""paths.py: checkout vs installed layout, home and config dirs, preset files."""

from __future__ import annotations

import importlib
import shutil
from collections.abc import Iterator
from pathlib import Path

import platformdirs
import pytest

import crab.cli.contract
from crab import paths

pytestmark = pytest.mark.usefixtures("real_preset_dirs")

OLD_WALK_UP = Path(crab.cli.contract.__file__).resolve().parents[3]


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the platformdirs user dirs at tmp so nothing reads the real home."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.delenv("CRAB_HOME", raising=False)
    monkeypatch.delenv("CRAB_PATH_PRESETS", raising=False)
    return tmp_path


@pytest.fixture
def installed(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The installed layout with CRAB_HOME unset (module reloaded, restored after)."""
    importlib.reload(paths)
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    yield xdg
    monkeypatch.undo()
    importlib.reload(paths)


def _installed_with_home(
    xdg: Path, monkeypatch: pytest.MonkeyPatch, home: Path, *, checkout: bool
) -> None:
    """Set CRAB_HOME, reload (it is read at import) and force the layout."""
    monkeypatch.setenv("CRAB_HOME", str(home))
    importlib.reload(paths)
    monkeypatch.setattr(paths, "is_checkout", lambda: checkout)


@pytest.fixture(autouse=True)
def _restore_paths() -> Iterator[None]:
    yield
    importlib.reload(paths)


# --------------------------------------------------------------------------- #
# checkout (this test run is from a git checkout)
# --------------------------------------------------------------------------- #
def test_checkout_layout_and_roots_equal_the_old_walk_up(xdg: Path) -> None:
    importlib.reload(paths)
    assert paths.layout() == "checkout"
    assert paths.is_checkout()
    assert paths.checkout_root() == OLD_WALK_UP
    assert paths.crab_home() == OLD_WALK_UP


def test_crab_home_env_does_not_move_a_checkout(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_HOME", str(xdg / "elsewhere"))
    importlib.reload(paths)
    assert paths.crab_home() == OLD_WALK_UP
    assert paths.checkout_root() == OLD_WALK_UP


# --------------------------------------------------------------------------- #
# installed
# --------------------------------------------------------------------------- #
def test_installed_dirs_come_from_platformdirs(installed: Path) -> None:
    assert paths.layout() == "installed"
    assert paths.checkout_root() is None
    assert paths.crab_home() == installed / "xdg-data" / "crab"
    assert paths.config_dir() == installed / "xdg-config" / "crab"
    assert paths.crab_home() == Path(platformdirs.user_data_dir("crab", "crab"))
    assert paths.config_dir() == Path(platformdirs.user_config_dir("crab", "crab"))


def test_crab_home_env_sets_home_and_config_when_installed(
    xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = xdg / "my-home"
    _installed_with_home(xdg, monkeypatch, home, checkout=False)
    assert paths.layout() == "installed"
    assert paths.crab_home() == home
    assert paths.config_dir() == home / "config"


def test_crab_home_is_read_at_import(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.reload(paths)
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    monkeypatch.setenv("CRAB_HOME", str(xdg / "late"))
    assert paths.crab_home() == xdg / "xdg-data" / "crab"


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
def test_user_presets_default_is_under_config_dir(installed: Path) -> None:
    assert paths.user_presets() == paths.config_dir() / "presets.json"


def test_user_presets_env_wins(installed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = installed / "mine.json"
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(custom))
    assert paths.user_presets() == custom


def test_user_presets_env_wins_with_crab_home(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed_with_home(xdg, monkeypatch, xdg / "h", checkout=False)
    custom = xdg / "mine.json"
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(custom))
    assert paths.user_presets() == custom
    monkeypatch.delenv("CRAB_PATH_PRESETS")
    assert paths.user_presets() == xdg / "h" / "config" / "presets.json"


def test_user_presets_dir_checkout_is_root_local_presets(xdg: Path) -> None:
    importlib.reload(paths)
    root = xdg / "somewhere"
    assert paths.user_presets_dir(root) == root / "local" / "presets"


def test_user_presets_dir_installed_is_under_config_dir(installed: Path) -> None:
    assert paths.user_presets_dir(installed / "ignored") == paths.config_dir() / "presets"


def test_user_presets_dir_installed_with_crab_home(
    xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = xdg / "h"
    _installed_with_home(xdg, monkeypatch, home, checkout=False)
    assert paths.user_presets_dir(xdg / "ignored") == home / "config" / "presets"


def test_user_presets_dir_env_wins_in_both_layouts(
    xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = xdg / "mine"
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(custom))
    importlib.reload(paths)
    assert paths.user_presets_dir(xdg / "root") == custom
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    assert paths.user_presets_dir(xdg / "root") == custom


def test_old_preset_files_checkout(xdg: Path) -> None:
    importlib.reload(paths)
    root = xdg / "somewhere"
    assert paths.old_preset_files(root) == (
        paths.config_dir() / "presets.json",
        root / "local" / "presets.json",
    )


def test_old_preset_files_installed(installed: Path) -> None:
    root = installed / "ignored"
    assert paths.old_preset_files(root) == (
        paths.config_dir() / "presets.json",
        root / "local" / "presets.json",
    )


@pytest.mark.usefixtures("real_shipped_presets")
def test_shipped_presets_sits_in_the_package_dir() -> None:
    assert paths.shipped_presets() == Path(paths.__file__).resolve().parent / "presets.json"


# --------------------------------------------------------------------------- #
# local_path
# --------------------------------------------------------------------------- #
def test_local_path_checkout_uses_root_local(xdg: Path) -> None:
    importlib.reload(paths)
    root = xdg / "somewhere"
    assert paths.local_path(root, "receipts") == root / "local" / "receipts"
    assert paths.local_path(root, "wrappers", "x") == root / "local" / "wrappers" / "x"


def test_local_path_installed_uses_config_dir(installed: Path) -> None:
    assert paths.local_path(installed / "ignored", "receipts") == (
        installed / "xdg-config" / "crab" / "receipts"
    )


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def test_detection_false_for_a_site_packages_tree(tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "crab"
    pkg.mkdir(parents=True)
    copy = pkg / "paths.py"
    shutil.copy(paths.__file__, copy)
    assert paths._checkout_root_of(copy) is None


def test_detection_true_for_a_repo_tree(tmp_path: Path) -> None:
    pkg = tmp_path / "repo" / "src" / "crab"
    pkg.mkdir(parents=True)
    (tmp_path / "repo" / "pyproject.toml").write_text("[project]\n")
    copy = pkg / "paths.py"
    shutil.copy(paths.__file__, copy)
    assert paths._checkout_root_of(copy) == (tmp_path / "repo").resolve()


def test_detection_ignores_a_parent_with_pyproject_but_no_src(tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "crab"
    pkg.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    copy = pkg / "paths.py"
    shutil.copy(paths.__file__, copy)
    assert paths._checkout_root_of(copy) is None


def test_detection_requires_the_src_file_to_be_this_file(tmp_path: Path) -> None:
    top = tmp_path / "top"
    (top / "src" / "crab").mkdir(parents=True)
    (top / "pyproject.toml").write_text("[project]\n")
    (top / "src" / "crab" / "paths.py").write_text("# the repo's own\n")
    other = top / "build" / "crab" / "paths.py"
    other.parent.mkdir(parents=True)
    other.write_text("# another copy\n")
    assert paths._checkout_root_of(other) is None
