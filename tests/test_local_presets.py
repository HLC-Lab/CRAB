"""Preset layers on disk: the shared folders' `presets/*.json`, then the user's override files,
resolved by `resolve_presets` (ADR-041)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from crab import paths
from crab.cli import contract, main
from crab.cli.contract import gather_info, gather_nodes
from crab.cli.orchestrator import load_environment_config
from crab.cli.presets import load_all_presets

pytestmark = pytest.mark.usefixtures("real_preset_dirs")

BASE = {
    "leonardo": {
        "description": "base",
        "scheduler": "slurm",
        "env": {"X": "1"},
        "sbatch": ["--account=PLACEHOLDER", "--exclusive"],
    },
    "local": {"description": "no slurm", "scheduler": "local"},
}


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


@pytest.fixture
def base_in_wrappers(wrappers: Path) -> Path:
    """BASE as a file of the shared wrapper folder."""
    return _write(wrappers / "presets", "base.json", BASE)


def _write(folder: Path, name: str, body: dict | str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


def test_without_shared_folders_or_overrides_there_are_no_presets(tmp_path: Path) -> None:
    assert load_all_presets(tmp_path) == {}


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


@pytest.mark.usefixtures("base_in_wrappers")
def test_an_override_file_patches_the_shared_preset(tmp_path: Path, overrides: Path) -> None:
    _write(overrides, "mine.json", {"leonardo": {"sbatch": ["--account=REAL"]}})
    leonardo = load_all_presets(tmp_path)["leonardo"]
    assert leonardo["sbatch"] == ["--account=REAL", "--exclusive"]
    assert leonardo["description"] == "base"
    assert leonardo["env"] == {"X": "1"}


@pytest.mark.usefixtures("base_in_wrappers")
def test_override_files_are_read_in_name_order(tmp_path: Path, overrides: Path) -> None:
    _write(overrides, "b.json", {"leonardo": {"description": "from b"}})
    _write(overrides, "a.json", {"mine": {"scheduler": "local", "description": "from a"}})
    presets = load_all_presets(tmp_path)
    assert presets["leonardo"]["description"] == "from b"
    assert presets["mine"]["description"] == "from a"
    assert list(presets)[-1] == "mine"


@pytest.mark.usefixtures("base_in_wrappers")
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
    assert str(overrides) in message
    assert "package" not in message


def test_crab_info_without_any_preset_source_lists_none_and_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(tmp_path / "no-clone"))
    info = gather_info(tmp_path)
    assert info["presets"] == []
    assert "presets_error" not in info
    assert not any(source["exists"] for source in info["presets_sources"])


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
        "needs": [],
        "notes": [],
    }


# --- crab info: needs, notes, sources, loader errors -------------------------------------------

MARKED_LAB = {
    "lab": {
        "description": "lab cluster",
        "scheduler": "slurm",
        "sbatch": ["--account=<YOUR_ACCOUNT>"],
        "notes": ["one"],
    }
}


def test_crab_info_lists_a_preset_with_unset_marks_and_what_it_needs(
    tmp_path: Path, wrappers: Path, overrides: Path
) -> None:
    _write(wrappers / "presets", "lab.json", MARKED_LAB)
    lab = next(p for p in gather_info(tmp_path)["presets"] if p["name"] == "lab")
    assert lab["needs"] == ["--account"]
    assert lab["notes"] == ["one"]
    assert lab["scheduler"] == "slurm"
    assert "launcher" in lab
    assert lab["hosts"] == []
    assert "error" not in lab


def test_crab_info_needs_is_empty_once_an_override_fills_the_mark(
    tmp_path: Path, wrappers: Path, overrides: Path
) -> None:
    _write(wrappers / "presets", "lab.json", MARKED_LAB)
    _write(overrides, "mine.json", {"lab": {"sbatch": ["--account=REAL"]}})
    lab = next(p for p in gather_info(tmp_path)["presets"] if p["name"] == "lab")
    assert lab["needs"] == []
    assert lab["notes"] == ["one"]


def test_every_crab_info_preset_has_needs_and_notes(
    tmp_path: Path, wrappers: Path, overrides: Path, base_in_wrappers: Path
) -> None:
    _write(wrappers / "presets", "odd.json", {"odd": {"scheduler": "local", "notes": ["a", 3]}})
    _write(wrappers / "presets", "bad.json", {"bad": {"scheduler": "nope", "notes": ["why"]}})
    presets = {p["name"]: p for p in gather_info(tmp_path)["presets"]}
    assert presets["odd"]["notes"] == ["a"]
    assert presets["odd"]["needs"] == []
    assert "error" in presets["bad"]
    assert presets["bad"]["notes"] == ["why"]
    assert presets["bad"]["needs"] == []
    assert all("needs" in p and "notes" in p for p in presets.values())
    assert presets["leonardo"]["notes"] == []


def test_crab_info_reports_the_shared_folders_searched_and_the_overrides_dir(
    tmp_path: Path, overrides: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = tmp_path / "found"
    (found / "presets").mkdir(parents=True)
    missing = tmp_path / "missing"
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", f"{found}:{missing}")
    info = gather_info(tmp_path)
    assert info["presets_sources"] == [
        {"path": str(found / "presets"), "exists": True},
        {"path": str(missing / "presets"), "exists": False},
    ]
    assert info["presets_overrides"] == str(overrides)
    assert "presets_error" not in info


def test_crab_info_reports_why_the_presets_cannot_load(tmp_path: Path, overrides: Path) -> None:
    old = _write(tmp_path / "local", "presets.json", {"leonardo": {"description": "old"}})
    info = gather_info(tmp_path)
    assert info["presets"] == []
    assert str(old) in info["presets_error"]
    assert info["presets_overrides"] == str(overrides)


def test_crab_info_reports_a_duplicate_preset_name(tmp_path: Path, wrappers: Path) -> None:
    _write(wrappers / "presets", "a.json", {"dup": {"scheduler": "local"}})
    _write(wrappers / "presets", "b.json", {"dup": {"scheduler": "local"}})
    info = gather_info(tmp_path)
    assert info["presets"] == []
    assert "defined twice" in info["presets_error"]


def test_nodes_for_a_preset_with_unset_marks_still_refuses(tmp_path: Path, wrappers: Path) -> None:
    _write(wrappers / "presets", "lab.json", MARKED_LAB)
    data = gather_nodes(preset="lab", crab_root=tmp_path)
    assert set(data) == {"schema", "error"}
    assert "--account" in data["error"]


def test_a_missing_preset_says_to_run_crab_update_when_no_shared_folder_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(tmp_path / "no-clone"))
    with pytest.raises(KeyError, match="crab update"):
        load_environment_config("nope", tmp_path)


def test_a_missing_preset_does_not_say_crab_update_when_a_shared_folder_exists(
    tmp_path: Path, wrappers: Path
) -> None:
    (wrappers / "presets").mkdir()
    with pytest.raises(KeyError) as exc:
        load_environment_config("nope", tmp_path)
    assert "crab update" not in str(exc.value)


@pytest.fixture
def info_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`crab info` reads the root of the module; point it at an empty temp root."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(contract, "_CRAB_ROOT", root)
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "config")
    return root


def _info_output(capsys: pytest.CaptureFixture[str]) -> str:
    main.handle_info(argparse.Namespace(json=False))
    return capsys.readouterr().out


def test_crab_info_prints_the_overrides_dir_and_marks_a_preset_that_needs_setup(
    tmp_path: Path,
    wrappers: Path,
    overrides: Path,
    info_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(wrappers / "presets", "lab.json", MARKED_LAB)
    out = _info_output(capsys)
    assert f"overrides: {overrides}" in out
    assert "(needs setup: --account)" in out
    lab_line = next(line for line in out.splitlines() if line.strip().startswith("lab "))
    assert "lab cluster" in lab_line
    assert "crab update" not in out


def test_crab_info_prints_the_loader_error(
    tmp_path: Path, overrides: Path, info_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _write(info_root / "local", "presets.json", {"leonardo": {"description": "old"}})
    out = _info_output(capsys)
    assert str(old) in out
    assert "1.2" in out


def test_crab_info_tells_to_run_crab_update_when_no_shared_folder_exists(
    tmp_path: Path,
    overrides: Path,
    info_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(tmp_path / "no-clone"))
    assert "crab update" in _info_output(capsys)
