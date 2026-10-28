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


def test_crab_home_is_read_at_call_time(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.reload(paths)
    monkeypatch.setattr(paths, "is_checkout", lambda: False)
    monkeypatch.setenv("CRAB_HOME", str(xdg / "late"))
    assert paths.crab_home() == xdg / "late"


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# env_path and validate_env_paths
# --------------------------------------------------------------------------- #
LIST_AND_SCALAR_NAMES = [
    "CRAB_HOME",
    "CRAB_PATH_PRESETS",
    "CRAB_WEB_CONFIG_DIR",
    "CRAB_WEB_DATA_DIR",
    "CRAB_WEB_LIBRARY_DIR",
]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """None of the folder variables set."""
    for name in [*LIST_AND_SCALAR_NAMES, "CRAB_PATH_WRAPPERS"]:
        monkeypatch.delenv(name, raising=False)


def test_env_path_unset_and_empty_are_none(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert paths.env_path("CRAB_TEST_X") is None
    monkeypatch.setenv("CRAB_TEST_X", "")
    assert paths.env_path("CRAB_TEST_X") is None


def test_env_path_absolute_is_returned(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_TEST_X", "/abs/dir")
    assert paths.env_path("CRAB_TEST_X") == Path("/abs/dir")


def test_env_path_expands_the_tilde(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CRAB_TEST_X", "~/x")
    assert paths.env_path("CRAB_TEST_X") == tmp_path / "home" / "x"


@pytest.mark.parametrize("raw", ["rel/home", "./x", "../x"])
def test_env_path_relative_is_refused(
    raw: str, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_TEST_X", raw)
    with pytest.raises(ValueError) as caught:
        paths.env_path("CRAB_TEST_X")
    message = str(caught.value)
    assert "CRAB_TEST_X" in message
    assert raw in message
    assert "absolute" in message
    assert "\n" not in message


def test_env_path_with_a_dollar_is_a_template_not_refused(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_TEST_X", "$SCRATCH/w")
    assert paths.env_path("CRAB_TEST_X") == Path("$SCRATCH/w")


def test_env_path_relative_with_a_deleted_cwd_still_raises_value_error(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    monkeypatch.setenv("CRAB_TEST_X", "rel/home")
    with pytest.raises(ValueError) as caught:
        paths.env_path("CRAB_TEST_X")
    assert "CRAB_TEST_X" in str(caught.value)


def test_validate_env_paths_passes_with_nothing_set(clean_env: None) -> None:
    paths.validate_env_paths()


@pytest.mark.parametrize("name", LIST_AND_SCALAR_NAMES)
def test_validate_env_paths_refuses_each_relative_name(
    name: str, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "rel/x")
    with pytest.raises(ValueError, match=name):
        paths.validate_env_paths()


def test_validate_env_paths_accepts_absolute_values(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in LIST_AND_SCALAR_NAMES:
        monkeypatch.setenv(name, "/abs/x")
    paths.validate_env_paths()


def test_validate_env_paths_checks_every_wrappers_entry(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "/abs/a:rel/b")
    with pytest.raises(ValueError, match="CRAB_PATH_WRAPPERS"):
        paths.validate_env_paths()
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", "rel/b:/abs/a")
    with pytest.raises(ValueError, match="CRAB_PATH_WRAPPERS"):
        paths.validate_env_paths()


@pytest.mark.parametrize("value", ["/abs/a::/abs/b", "/abs/a:$SCRATCH/w", ":/abs/a:", "~/w"])
def test_validate_env_paths_accepts_valid_wrappers_lists(
    value: str, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", value)
    paths.validate_env_paths()


@pytest.mark.parametrize("name", ["CRAB_PATH_BLINK", "CRAB_PATH_WRAPPERSX", "CRAB_HOMEX"])
def test_validate_env_paths_never_matches_a_prefix(
    name: str, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "bin/x")
    paths.validate_env_paths()


def test_installed_relative_crab_home_is_refused(
    installed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_HOME", "rel/home")
    with pytest.raises(ValueError, match="CRAB_HOME"):
        paths.crab_home()
    with pytest.raises(ValueError, match="CRAB_HOME"):
        paths.config_dir()


def test_installed_absolute_crab_home_keeps_working(
    installed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_HOME", "/abs/h")
    assert paths.crab_home() == Path("/abs/h")
    assert paths.config_dir() == Path("/abs/h/config")


def test_checkout_relative_crab_home_refuses_config_dir_only(
    xdg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_HOME", "rel/home")
    assert paths.is_checkout()
    with pytest.raises(ValueError, match="CRAB_HOME"):
        paths.config_dir()
    assert paths.crab_home() == OLD_WALK_UP
