"""Preset layers on disk: the shared folders' `presets/*.json`, the package file, then the user's
override files, resolved by `resolve_presets` (ADR-041)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crab import paths
from crab.cli.contract import gather_info
from crab.cli.orchestrator import load_environment_config
from crab.cli.presets import load_all_presets

pytestmark = pytest.mark.usefixtures("real_preset_dirs")

SHIPPED = {
    "leonardo": {
        "description": "shipped",
        "scheduler": "slurm",
        "env": {"X": "1"},
        "sbatch": ["--account=PLACEHOLDER", "--exclusive"],
    },
    "local": {"description": "no slurm", "scheduler": "local"},
}


@pytest.fixture(autouse=True)
def _shipped_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The package layer is a fixture file, not the package's real presets."""
    shipped = tmp_path / "shipped" / "presets.json"
    shipped.parent.mkdir()
    shipped.write_text(json.dumps(SHIPPED))
    monkeypatch.setattr(paths, "shipped_presets", lambda: shipped)


@pytest.fixture
def overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user's override dir (created), reached through CRAB_PATH_PRESETS."""
    directory = tmp_path / "overrides"
    directory.mkdir()
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(directory))
    return directory


@pytest.fixture
def wrappers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One shared wrapper folder, reached through CRAB_PATH_WRAPPERS."""
    folder = tmp_path / "wrappers-a"
    folder.mkdir()
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(folder))
    return folder


def _write(folder: Path, name: str, body: dict | str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


def test_without_shared_folders_or_overrides_the_package_presets_are_used(tmp_path: Path) -> None:
    assert load_all_presets(tmp_path) == SHIPPED


def test_a_shared_preset_wins_over_the_package_one_as_a_whole(
    tmp_path: Path, wrappers: Path
) -> None:
    shared = {"description": "shared", "scheduler": "slurm"}
    _write(wrappers / "presets", "x.json", {"leonardo": shared, "added": shared})
    presets = load_all_presets(tmp_path)
    assert presets["leonardo"] == shared
    assert presets["added"] == shared
    assert presets["local"] == SHIPPED["local"]


def test_the_first_shared_folder_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _write(first / "presets", "a.json", {"cl": {"description": "first", "scheduler": "slurm"}})
    _write(second / "presets", "a.json", {"cl": {"description": "second", "scheduler": "slurm"}})
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", f"{first}:{second}")
    assert load_all_presets(tmp_path)["cl"]["description"] == "first"
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", f"{second}:{first}")
    assert load_all_presets(tmp_path)["cl"]["description"] == "second"


def test_a_duplicate_name_in_one_shared_folder_names_both_files(
    tmp_path: Path, wrappers: Path
) -> None:
    one = _write(wrappers / "presets", "a.json", {"cl": {"scheduler": "slurm"}})
    two = _write(wrappers / "presets", "b.json", {"cl": {"scheduler": "slurm"}})
    with pytest.raises(ValueError) as exc:
        load_all_presets(tmp_path)
    assert str(one) in str(exc.value)
    assert str(two) in str(exc.value)


def test_without_the_env_var_the_checkout_wrappers_folder_is_read_not_local_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRAB_PATH_WRAPPERS", raising=False)
    _write(tmp_path / "wrappers" / "presets", "a.json", {"shared": {"scheduler": "slurm"}})
    _write(
        tmp_path / "local" / "wrappers" / "presets", "b.json", {"hidden": {"scheduler": "slurm"}}
    )
    presets = load_all_presets(tmp_path)
    assert "shared" in presets
    assert "hidden" not in presets


def test_an_override_file_patches_the_shared_preset(tmp_path: Path, overrides: Path) -> None:
    _write(overrides, "mine.json", {"leonardo": {"sbatch": ["--account=REAL"]}})
    leonardo = load_all_presets(tmp_path)["leonardo"]
    assert leonardo["sbatch"] == ["--account=REAL", "--exclusive"]
    assert leonardo["description"] == "shipped"
    assert leonardo["env"] == {"X": "1"}


def test_override_files_are_read_in_name_order(tmp_path: Path, overrides: Path) -> None:
    _write(overrides, "b.json", {"leonardo": {"description": "from b"}})
    _write(overrides, "a.json", {"mine": {"scheduler": "local", "description": "from a"}})
    presets = load_all_presets(tmp_path)
    assert presets["leonardo"]["description"] == "from b"
    assert presets["mine"]["description"] == "from a"
    assert list(presets)[-1] == "mine"


def test_the_same_name_in_two_override_files_names_both(tmp_path: Path, overrides: Path) -> None:
    one = _write(overrides, "a.json", {"leonardo": {"description": "a"}})
    two = _write(overrides, "b.json", {"leonardo": {"description": "b"}})
    with pytest.raises(ValueError) as exc:
        load_all_presets(tmp_path)
    assert str(one) in str(exc.value)
    assert str(two) in str(exc.value)


def test_a_shared_variant_picks_up_the_override_of_its_parent(
    tmp_path: Path, wrappers: Path, overrides: Path
) -> None:
    _write(
        wrappers / "presets",
        "leonardo.json",
        {
            "leonardo": {"scheduler": "slurm", "sbatch": ["--account=PLACEHOLDER"]},
            "leonardo_dcgp": {"extends": "leonardo", "sbatch": ["--partition=dcgp"]},
        },
    )
    _write(overrides, "mine.json", {"leonardo": {"sbatch": ["--account=REAL"]}})
    presets = load_all_presets(tmp_path)
    assert presets["leonardo_dcgp"]["sbatch"] == ["--account=REAL", "--partition=dcgp"]
    assert "extends" not in presets["leonardo_dcgp"]


def test_the_old_local_presets_file_is_refused_with_the_move_message(
    tmp_path: Path, overrides: Path
) -> None:
    old = _write(tmp_path / "local", "presets.json", {"leonardo": {"description": "old"}})
    with pytest.raises(ValueError) as exc:
        load_all_presets(tmp_path)
    assert str(old) in str(exc.value)
    assert str(overrides) in str(exc.value)
    assert "1.2" in str(exc.value)


def test_the_old_user_config_presets_file_is_refused_with_the_move_message(
    tmp_path: Path, overrides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setattr(paths, "config_dir", lambda: config)
    old = _write(config, "presets.json", {"leonardo": {"description": "old"}})
    with pytest.raises(ValueError) as exc:
        load_all_presets(tmp_path)
    assert str(old) in str(exc.value)
    assert str(overrides) in str(exc.value)


def test_a_file_that_is_not_json_names_that_file(tmp_path: Path, wrappers: Path) -> None:
    bad = _write(wrappers / "presets", "bad.json", "{not json")
    with pytest.raises(ValueError, match="not valid JSON") as exc:
        load_all_presets(tmp_path)
    assert str(bad) in str(exc.value)


def test_a_bad_override_file_names_that_file(tmp_path: Path, overrides: Path) -> None:
    bad = _write(overrides, "bad.json", "{not json")
    with pytest.raises(ValueError, match="not valid JSON") as exc:
        load_all_presets(tmp_path)
    assert str(bad) in str(exc.value)


def test_a_missing_preset_message_names_every_place_searched(
    tmp_path: Path, wrappers: Path, overrides: Path
) -> None:
    with pytest.raises(KeyError) as exc:
        load_environment_config("nope", tmp_path)
    message = str(exc.value)
    assert str(wrappers / "presets") in message
    assert str(tmp_path / "shipped" / "presets.json") in message
    assert str(overrides) in message


def test_a_missing_package_file_is_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "shipped_presets", lambda: tmp_path / "absent.json")
    with pytest.raises(FileNotFoundError, match="absent.json"):
        load_environment_config("leonardo", tmp_path)


def test_crab_info_lists_override_presets_too(tmp_path: Path, overrides: Path) -> None:
    _write(overrides, "mine.json", {"mycluster": {"description": "new", "scheduler": "slurm"}})
    info = gather_info(tmp_path)
    assert any(p["name"] == "mycluster" and p["description"] == "new" for p in info["presets"])


def test_crab_info_parses_the_presets_of_the_root_it_is_given(
    tmp_path: Path, overrides: Path
) -> None:
    mine = {"description": "lab", "scheduler": "local", "hosts": ["ws1:8"], "launcher": "mpirun"}
    _write(overrides, "lab.json", {"lab": mine})
    info = gather_info(tmp_path)
    lab = next(p for p in info["presets"] if p["name"] == "lab")
    assert lab == {
        "name": "lab",
        "description": "lab",
        "scheduler": "local",
        "launcher": "mpirun",
        "hosts": ["ws1"],
    }
