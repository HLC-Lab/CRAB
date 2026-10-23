"""`crab presets show`: a resolved preset, where each value came from, what is unset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from crab import paths
from crab.cli import contract, main, presets_show
from crab.cli.preset_marks import override_snippet
from crab.cli.presets import load_all_presets
from crab.cli.presets_show import handle_presets_show, show

pytestmark = pytest.mark.usefixtures("real_preset_dirs")

LEONARDO = {
    "description": "Leonardo",
    "scheduler": "slurm",
    "env": {"UCX_IB_SL": "1"},
    "sbatch": ["--account=<YOUR_ACCOUNT>", "--partition=p"],
    "header": ["module purge"],
    "notes": ["Fill in the account."],
}
DCGP = {"extends": "leonardo", "sbatch": ["--partition=dcgp"]}


@pytest.fixture
def overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "overrides"
    directory.mkdir()
    monkeypatch.setenv("CRAB_PATH_PRESETS", str(directory))
    return directory


@pytest.fixture
def shared_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`a/presets/leonardo.json` holding `leonardo` and `leonardo_dcgp`, as the shared folder."""
    folder = tmp_path / "a"
    monkeypatch.setenv("CRAB_PATH_WRAPPERS", str(folder))
    file = folder / "presets" / "leonardo.json"
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"leonardo": LEONARDO, "leonardo_dcgp": DCGP}))
    return file


@pytest.fixture
def crab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The root the handler reads presets from."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(paths, "crab_home", lambda: root)
    return root


def _write(folder: Path, name: str, body: dict) -> Path:
    path = folder / name
    path.write_text(json.dumps(body))
    return path


def _run(
    capsys: pytest.CaptureFixture[str], name: str, as_json: bool = False
) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of the handler."""
    code = 0
    try:
        handle_presets_show(argparse.Namespace(name=name, json=as_json))
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_show_attributes_each_value_to_its_file(
    tmp_path: Path, shared_file: Path, overrides: Path
) -> None:
    mine = _write(overrides, "mine.json", {"leonardo": {"env": {"MY": "1"}}})
    data = show(tmp_path, "leonardo_dcgp")

    assert data["preset"] == load_all_presets(tmp_path)["leonardo_dcgp"]
    assert data["sources"]["description"] == str(shared_file)
    assert data["sources"]["env.UCX_IB_SL"] == str(shared_file)
    assert data["sources"]["env.MY"] == str(mine)
    assert data["sources"]["sbatch --account"] == str(shared_file)
    assert data["sources"]["sbatch --partition"] == str(shared_file)
    assert data["sources"]["header"] == str(shared_file)
    assert data["extends"] == ["leonardo"]
    assert data["needs"] == ["--account"]
    assert data["notes"] == ["Fill in the account."]
    assert data["override"]["path"] == str(overrides / "leonardo_dcgp.json")
    assert data["override"]["snippet"] == override_snippet("leonardo_dcgp", data["preset"])


def test_an_override_value_becomes_the_source_and_clears_the_need(
    tmp_path: Path, shared_file: Path, overrides: Path
) -> None:
    mine = _write(overrides, "mine.json", {"leonardo": {"sbatch": ["--account=REAL"]}})
    data = show(tmp_path, "leonardo_dcgp")

    assert data["sources"]["sbatch --account"] == str(mine)
    assert data["sources"]["sbatch --partition"] == str(shared_file)
    assert data["needs"] == []
    assert data["override"]["snippet"] == {"leonardo_dcgp": {}}


def test_a_later_file_wins_the_source_of_a_replaced_field(
    tmp_path: Path, shared_file: Path, overrides: Path
) -> None:
    mine = _write(overrides, "mine.json", {"leonardo_dcgp": {"description": "mine"}})
    data = show(tmp_path, "leonardo_dcgp")

    assert data["sources"]["description"] == str(mine)
    assert data["sources"]["scheduler"] == str(shared_file)


def test_a_list_built_by_several_files_lists_them_all(
    tmp_path: Path, shared_file: Path, overrides: Path
) -> None:
    mine = _write(overrides, "mine.json", {"leonardo": {"header": ["module load x"]}})
    data = show(tmp_path, "leonardo")

    assert data["preset"]["header"] == ["module purge", "module load x"]
    assert data["sources"]["header"] == [str(shared_file), str(mine)]


def test_a_removed_env_key_has_no_source(
    tmp_path: Path, shared_file: Path, overrides: Path
) -> None:
    _write(overrides, "mine.json", {"leonardo": {"env": {"UCX_IB_SL": None}}})
    data = show(tmp_path, "leonardo")

    assert "env.UCX_IB_SL" not in data["sources"]


def test_launcher_fields_and_flags_are_attributed(
    tmp_path: Path, shared_file: Path, overrides: Path
) -> None:
    launchers = {"launchers": {"srun": {"command": "srun", "flags": ["--mpi=pmix", "--exact"]}}}
    base = _write(shared_file.parent, "mine.json", {"mine": {"scheduler": "slurm", **launchers}})
    patch = _write(
        overrides, "b.json", {"mine": {"launchers": {"srun": {"flags": ["--mpi=pmi2"]}}}}
    )
    data = show(tmp_path, "mine")

    assert data["sources"]["launchers.srun.command"] == str(base)
    assert data["sources"]["launchers.srun.flags --mpi"] == str(patch)
    assert data["sources"]["launchers.srun.flags"] == str(base)


def test_an_unknown_name_exits_1_naming_the_places(
    capsys: pytest.CaptureFixture[str], crab_root: Path, shared_file: Path, overrides: Path
) -> None:
    code, out, err = _run(capsys, "nope")

    assert code == 1
    assert out == ""
    assert "nope" in err
    assert str(shared_file.parent) in err
    assert str(overrides) in err


def test_a_loader_error_exits_1_with_its_message(
    capsys: pytest.CaptureFixture[str], crab_root: Path, overrides: Path
) -> None:
    old = crab_root / "local" / "presets.json"
    old.parent.mkdir()
    old.write_text("{}")
    code, out, err = _run(capsys, "leonardo")

    assert code == 1
    assert out == ""
    assert "single-file preset overrides were removed" in err


def test_json_errors_are_an_object_on_stdout(
    capsys: pytest.CaptureFixture[str], crab_root: Path, shared_file: Path, overrides: Path
) -> None:
    code, out, _ = _run(capsys, "nope", as_json=True)

    assert code == 1
    data = json.loads(out)
    assert set(data) == {"schema", "error"}
    assert data["schema"] == contract.CONTRACT_SCHEMA
    assert "nope" in data["error"]


def test_json_output_is_one_clean_document(
    capsys: pytest.CaptureFixture[str], crab_root: Path, shared_file: Path, overrides: Path
) -> None:
    code, out, err = _run(capsys, "leonardo_dcgp", as_json=True)

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert list(data) == [
        "schema",
        "name",
        "preset",
        "sources",
        "extends",
        "needs",
        "notes",
        "override",
    ]
    assert data["schema"] == contract.CONTRACT_SCHEMA
    assert data["name"] == "leonardo_dcgp"
    assert set(data["override"]) == {"path", "snippet"}
    assert data["override"]["path"] == str(overrides / "leonardo_dcgp.json")


def test_human_output_shows_fields_sources_needs_notes_and_the_skeleton(
    capsys: pytest.CaptureFixture[str], crab_root: Path, shared_file: Path, overrides: Path
) -> None:
    code, out, _ = _run(capsys, "leonardo_dcgp")

    assert code == 0
    assert "description: Leonardo" in out
    assert f"(from {shared_file})" in out
    assert "--partition=dcgp" in out
    assert "needs setup: --account" in out
    assert "Fill in the account." in out
    path = overrides / "leonardo_dcgp.json"
    assert f"To set your values, write {path}:" in out
    snippet = json.dumps({"leonardo_dcgp": {"sbatch": ["--account=<YOUR_ACCOUNT>"]}}, indent=2)
    assert snippet in out


def test_human_output_without_needs_still_names_the_override_path(
    capsys: pytest.CaptureFixture[str], crab_root: Path, shared_file: Path, overrides: Path
) -> None:
    _write(overrides, "mine.json", {"leonardo": {"sbatch": ["--account=REAL"]}})
    code, out, _ = _run(capsys, "leonardo")

    assert code == 0
    assert "needs setup: nothing" in out
    assert "To set your values" not in out
    assert str(overrides / "leonardo.json") in out


def test_the_real_parser_reaches_the_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(presets_show, "handle_presets_show", lambda args: captured.update(a=args))
    monkeypatch.setattr(sys, "argv", ["crab", "presets", "show", "x", "--json"])
    main.cli_router()

    args = captured["a"]
    assert isinstance(args, argparse.Namespace)
    assert args.name == "x"
    assert args.json is True
