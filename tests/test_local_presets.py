"""Per-machine presets live in the untracked `local/presets.json`, merged over the shipped
`config/presets.json`, so accounts and cluster tweaks never conflict with `git pull`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crab.cli.contract import gather_info
from crab.cli.presets import load_all_presets

SHIPPED = {
    "_common": {"env": {"CRAB_ROOT": "__CWD__", "A": "shipped"}, "sbatch": ["--exclusive"]},
    "leonardo": {"description": "shipped", "env": {"X": "1"}, "sbatch": ["--account=PLACEHOLDER"]},
    "local": {"description": "no slurm", "env": {}},
}


def _root(tmp_path: Path, local: dict | str | None = None) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "presets.json").write_text(json.dumps(SHIPPED))
    if local is not None:
        (tmp_path / "local").mkdir()
        text = local if isinstance(local, str) else json.dumps(local)
        (tmp_path / "local" / "presets.json").write_text(text)
    return tmp_path


def test_without_a_local_file_the_shipped_presets_are_used(tmp_path: Path) -> None:
    assert load_all_presets(_root(tmp_path)) == SHIPPED


def test_a_local_preset_replaces_the_shipped_one_with_the_same_name(tmp_path: Path) -> None:
    mine = {"description": "mine", "sbatch": ["--account=REAL"]}
    presets = load_all_presets(_root(tmp_path, {"leonardo": mine}))
    assert presets["leonardo"] == mine
    assert presets["local"] == SHIPPED["local"]


def test_a_local_only_preset_is_added(tmp_path: Path) -> None:
    presets = load_all_presets(_root(tmp_path, {"mycluster": {"description": "new"}}))
    assert presets["mycluster"] == {"description": "new"}


def test_local_common_env_merges_key_by_key(tmp_path: Path) -> None:
    presets = load_all_presets(_root(tmp_path, {"_common": {"env": {"A": "mine", "B": "added"}}}))
    common = presets["_common"]
    assert common["env"] == {"CRAB_ROOT": "__CWD__", "A": "mine", "B": "added"}
    assert common["sbatch"] == ["--exclusive"]


def test_a_malformed_local_file_is_a_clear_error(tmp_path: Path) -> None:
    root = _root(tmp_path, "{not json")
    with pytest.raises(ValueError, match=r"local/presets\.json"):
        load_all_presets(root)


def test_crab_info_lists_local_presets_too(tmp_path: Path) -> None:
    info = gather_info(_root(tmp_path, {"mycluster": {"description": "new"}}))
    assert {"name": "mycluster", "description": "new"} in info["presets"]
