"""Required marks (`<YOUR_...>`) in presets: finding them, the override snippet, the file check."""

from __future__ import annotations

from pathlib import Path

import pytest

from crab.cli import presets
from crab.cli.preset_marks import check_file_marks, override_snippet, unset_marks
from crab.cli.presets import load_all_presets

PATH = Path("/shared/presets/lab.json")

MARKED = {
    "scheduler": "slurm",
    "env": {"UCX_DIR": "<YOUR_UCX_DIR>", "PLAIN": "1"},
    "sbatch": ["--account=<YOUR_ACCOUNT>", "--exclusive"],
    "launchers": {
        "srun": {"flags": ["--mpi=pmix"]},
        "mpirun": {
            "command": "<YOUR_OPENMPI_DIR>/bin/mpirun",
            "flags": ["--mca-x=<YOUR_X>", "--bind-to=core"],
        },
    },
}


def test_unset_marks_lists_env_then_sbatch_then_launchers() -> None:
    assert unset_marks(MARKED) == [
        "UCX_DIR",
        "--account",
        "launchers.mpirun.command",
        "--mca-x",
    ]


def test_unset_marks_is_empty_without_marks() -> None:
    assert unset_marks({"scheduler": "slurm", "env": {"A": "1"}, "sbatch": ["--exclusive"]}) == []


def test_each_label_is_listed_once() -> None:
    preset = {"sbatch": ["--account=<YOUR_A>", "--account=<YOUR_B>"]}
    assert unset_marks(preset) == ["--account"]


@pytest.mark.parametrize("text", ["<your_account>", "YOUR_ACCOUNT", "<YOUR_>", "<YOUR_a>"])
def test_text_that_only_looks_like_a_mark_is_not_one(text: str) -> None:
    assert unset_marks({"env": {"A": text}, "sbatch": [f"--account={text}"]}) == []


def test_a_mark_inside_a_longer_value_is_a_mark() -> None:
    assert unset_marks({"env": {"LD": "/opt/<YOUR_UCX>/lib"}}) == ["LD"]


def test_wrong_types_are_skipped_not_raised() -> None:
    preset = {"env": ["<YOUR_X>"], "sbatch": "<YOUR_X>", "launchers": {"mpirun": [1], "srun": 3}}
    assert unset_marks(preset) == []
    assert override_snippet("lab", preset) == {"lab": {}}


def test_override_snippet_holds_only_the_marked_items() -> None:
    assert override_snippet("lab", MARKED) == {
        "lab": {
            "env": {"UCX_DIR": "<YOUR_UCX_DIR>"},
            "sbatch": ["--account=<YOUR_ACCOUNT>"],
            "launchers": {
                "mpirun": {
                    "command": "<YOUR_OPENMPI_DIR>/bin/mpirun",
                    "flags": ["--mca-x=<YOUR_X>"],
                }
            },
        }
    }


def test_override_snippet_leaves_out_keys_without_marks() -> None:
    preset = {"sbatch": ["--account=<YOUR_ACCOUNT>"], "env": {"A": "1"}}
    assert override_snippet("lab", preset) == {"lab": {"sbatch": ["--account=<YOUR_ACCOUNT>"]}}


def test_check_file_marks_accepts_marks_a_patch_can_reach() -> None:
    check_file_marks(PATH, "lab", MARKED)


def test_a_mark_in_a_header_line_names_the_file_and_the_preset() -> None:
    body = {"header": ["#SBATCH --account=<YOUR_ACCOUNT>"]}
    with pytest.raises(ValueError) as exc:
        check_file_marks(PATH, "lab", body)
    message = str(exc.value)
    assert str(PATH) in message
    assert "'lab'" in message
    assert "header lines cannot be overridden" in message


@pytest.mark.parametrize(
    "items", [["-A <YOUR_ACCOUNT>"], ["--account", "<YOUR_ACCOUNT>"], ["--account <YOUR_ACCOUNT>"]]
)
def test_a_mark_in_a_flag_item_that_is_not_keyed_is_refused(items: list[str]) -> None:
    for body in (
        {"sbatch": items},
        {"launchers": {"mpirun": {"flags": items}}},
        {"launchers": {"srun": {"flags": items}}},
    ):
        with pytest.raises(ValueError) as exc:
            check_file_marks(PATH, "lab", body)
        message = str(exc.value)
        assert str(PATH) in message
        assert "--name=<YOUR_" in message


def test_the_loader_checks_each_shared_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "wrappers" / "presets"
    folder.mkdir(parents=True)
    bad = folder / "lab.json"
    bad.write_text('{"lab": {"scheduler": "slurm", "header": ["# <YOUR_ACCOUNT>"]}}')
    monkeypatch.setattr(presets, "shared_preset_folders", lambda root: [folder])
    with pytest.raises(ValueError) as exc:
        load_all_presets(tmp_path)
    assert str(bad) in str(exc.value)
    assert "header lines cannot be overridden" in str(exc.value)
