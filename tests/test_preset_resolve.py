"""Preset resolution (ADR-041): override files patch shared presets, `extends` makes variants.

`resolve_presets` is pure, so these tests pass fake paths and parsed JSON objects, with no disk.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from crab.cli.presets import resolve_presets

SHARED_FILE = Path("shared/leonardo.json")
OVERRIDE_FILE = Path("override/mine.json")

LEONARDO: dict[str, Any] = {
    "description": "Leonardo",
    "scheduler": "slurm",
    "launcher": "srun",
    "env": {"CRAB_ROOT": "__CWD__", "UCX_TLS": "rc", "KEEP": "1"},
    "sbatch": ["--account=<YOUR_ACCOUNT>", "--partition=boost_usr_prod", "--time=01:00:00"],
    "header": ["module load openmpi"],
    "notes": ["Use the boost partition."],
    "launchers": {
        "srun": {"flags": ["--mpi=pmix", "--cpu-bind=cores"]},
        "mpirun": {
            "command": "mpirun",
            "dialect": "openmpi4",
            "flags": ["--map-by", "node"],
            "export": ["PATH", "LD_LIBRARY_PATH"],
        },
    },
}


def shared(**presets: Any) -> list[tuple[Path, dict[str, Any]]]:
    return [(SHARED_FILE, dict(presets))]


def override(**presets: Any) -> list[tuple[Path, dict[str, Any]]]:
    return [(OVERRIDE_FILE, dict(presets))]


def one(shared_files: Any, override_files: Any, name: str) -> dict[str, Any]:
    return resolve_presets([shared_files], override_files)[name]


def test_shared_preset_without_override_is_unchanged() -> None:
    result = resolve_presets([shared(leonardo=LEONARDO)], [])
    assert result == {"leonardo": LEONARDO}
    assert "extends" not in result["leonardo"]


def test_same_name_override_merges_env_by_key_and_null_removes() -> None:
    result = one(
        shared(leonardo=LEONARDO),
        override(leonardo={"env": {"UCX_TLS": "ud", "NEW": "x", "KEEP": None}}),
        "leonardo",
    )
    assert result["env"] == {"CRAB_ROOT": "__CWD__", "UCX_TLS": "ud", "NEW": "x"}
    assert None not in result["env"].values()


def test_sbatch_named_option_replaces_in_place_and_others_append() -> None:
    result = one(
        shared(leonardo=LEONARDO),
        override(leonardo={"sbatch": ["--account=REAL", "--qos=x", "--exclusive"]}),
        "leonardo",
    )
    assert result["sbatch"] == [
        "--account=REAL",
        "--partition=boost_usr_prod",
        "--time=01:00:00",
        "--qos=x",
        "--exclusive",
    ]


def test_launcher_flags_follow_the_keyed_rule_and_two_item_flags_append() -> None:
    result = one(
        shared(leonardo=LEONARDO),
        override(
            leonardo={
                "launchers": {
                    "srun": {"flags": ["--mpi=pmi2", "--exact"]},
                    "mpirun": {"flags": ["--map-by", "core"]},
                }
            }
        ),
        "leonardo",
    )
    assert result["launchers"]["srun"]["flags"] == ["--mpi=pmi2", "--cpu-bind=cores", "--exact"]
    assert result["launchers"]["mpirun"]["flags"] == ["--map-by", "node", "--map-by", "core"]


def test_launchers_new_kind_added_command_dialect_replace_export_unions() -> None:
    result = one(
        shared(leonardo=LEONARDO),
        override(
            leonardo={
                "launchers": {
                    "direct": {"command": "run"},
                    "mpirun": {
                        "command": "/opt/mpirun",
                        "dialect": "hydra",
                        "export": ["PATH", "NEW", "NEW", "A"],
                    },
                }
            }
        ),
        "leonardo",
    )
    launchers = result["launchers"]
    assert launchers["direct"] == {"command": "run"}
    assert launchers["mpirun"]["command"] == "/opt/mpirun"
    assert launchers["mpirun"]["dialect"] == "hydra"
    assert launchers["mpirun"]["export"] == ["PATH", "LD_LIBRARY_PATH", "NEW", "A"]
    assert launchers["srun"] == LEONARDO["launchers"]["srun"]


def test_header_override_lines_come_after_shared_ones() -> None:
    result = one(
        shared(leonardo=LEONARDO), override(leonardo={"header": ["module load mine"]}), "leonardo"
    )
    assert result["header"] == ["module load openmpi", "module load mine"]


def test_notes_are_appended_without_exact_duplicates() -> None:
    result = one(
        shared(leonardo=LEONARDO),
        override(leonardo={"notes": ["Use the boost partition.", "My own note."]}),
        "leonardo",
    )
    assert result["notes"] == ["Use the boost partition.", "My own note."]


def test_scalar_fields_replace() -> None:
    base = {
        "description": "a",
        "scheduler": "local",
        "launcher": "direct",
        "exclusive": False,
        "hosts": ["h1"],
        "hostfile": "one.txt",
    }
    patch = {
        "description": "b",
        "scheduler": "slurm",
        "launcher": "mpirun",
        "exclusive": True,
        "hosts": ["h2", "h3"],
        "hostfile": "two.txt",
    }
    assert one(shared(x=base), override(x=patch), "x") == patch


def test_field_only_in_override_is_added_and_field_only_in_shared_stays() -> None:
    result = one(
        shared(x={"scheduler": "slurm", "description": "d"}),
        override(x={"sbatch": ["--qos=x"], "env": {"A": "1", "B": None}}),
        "x",
    )
    assert result == {
        "scheduler": "slurm",
        "description": "d",
        "sbatch": ["--qos=x"],
        "env": {"A": "1"},
    }


def test_shared_extends_inherits_parent_and_replaces_keyed_option() -> None:
    dcgp = {"extends": "leonardo", "sbatch": ["--partition=dcgp"]}
    result = resolve_presets([shared(leonardo=LEONARDO, leonardo_dcgp=dcgp)], [])
    variant = result["leonardo_dcgp"]
    assert "extends" not in variant
    assert variant["env"] == LEONARDO["env"]
    assert variant["header"] == LEONARDO["header"]
    assert variant["sbatch"] == [
        "--account=<YOUR_ACCOUNT>",
        "--partition=dcgp",
        "--time=01:00:00",
    ]


def test_variant_picks_up_the_users_patch_of_its_parent() -> None:
    dcgp = {"extends": "leonardo", "sbatch": ["--partition=dcgp"]}
    result = resolve_presets(
        [shared(leonardo=LEONARDO, leonardo_dcgp=dcgp)],
        override(leonardo={"sbatch": ["--account=REAL"]}),
    )
    assert "--account=REAL" in result["leonardo_dcgp"]["sbatch"]
    assert "--partition=dcgp" in result["leonardo_dcgp"]["sbatch"]
    assert "--account=<YOUR_ACCOUNT>" not in result["leonardo_dcgp"]["sbatch"]


def test_override_only_name_with_extends_is_a_variant_of_shared_or_override_presets() -> None:
    result = resolve_presets(
        [shared(leonardo=LEONARDO)],
        [
            (
                OVERRIDE_FILE,
                {
                    "mine": {"extends": "leonardo", "env": {"KEEP": None}},
                    "mine2": {"extends": "mine", "header": ["echo hi"]},
                },
            )
        ],
    )
    assert result["mine"]["env"] == {"CRAB_ROOT": "__CWD__", "UCX_TLS": "rc"}
    assert result["mine"]["sbatch"] == LEONARDO["sbatch"]
    assert result["mine2"]["env"] == result["mine"]["env"]
    assert result["mine2"]["header"] == ["module load openmpi", "echo hi"]
    assert "extends" not in result["mine"] and "extends" not in result["mine2"]


def test_override_only_complete_preset_is_taken_with_env_nulls_dropped() -> None:
    body = {"scheduler": "slurm", "env": {"A": "1", "B": None}, "sbatch": ["--qos=x"]}
    result = resolve_presets([], override(mine=body))
    assert result == {"mine": {"scheduler": "slurm", "env": {"A": "1"}, "sbatch": ["--qos=x"]}}


def test_override_only_without_extends_or_scheduler_says_run_crab_update() -> None:
    with pytest.raises(ValueError) as error:
        resolve_presets([], override(mine={"sbatch": ["--qos=x"]}))
    message = str(error.value)
    assert str(OVERRIDE_FILE) in message
    assert "'mine'" in message
    assert "crab update" in message


def test_extends_of_a_missing_name_names_file_preset_and_parent() -> None:
    with pytest.raises(ValueError) as error:
        resolve_presets([], override(mine={"extends": "nothere"}))
    message = str(error.value)
    assert str(OVERRIDE_FILE) in message
    assert "'mine'" in message
    assert "'nothere'" in message


def test_extends_cycle_names_file_and_cycle() -> None:
    with pytest.raises(ValueError) as error:
        resolve_presets([shared(a={"extends": "b"}, b={"extends": "a"})], [])
    message = str(error.value)
    assert str(SHARED_FILE) in message
    assert "a -> b -> a" in message


def test_self_extend_is_a_cycle() -> None:
    with pytest.raises(ValueError) as error:
        resolve_presets([], override(a={"extends": "a"}))
    message = str(error.value)
    assert str(OVERRIDE_FILE) in message
    assert "a -> a" in message


def test_same_name_in_two_files_of_one_shared_folder_names_both_files() -> None:
    folder = [
        (Path("shared/a.json"), {"x": {"scheduler": "slurm"}}),
        (Path("shared/b.json"), {"x": {"scheduler": "local"}}),
    ]
    with pytest.raises(ValueError) as error:
        resolve_presets([folder], [])
    message = str(error.value)
    assert "'x'" in message
    assert "shared/a.json" in message and "shared/b.json" in message


def test_same_name_in_two_override_files_names_both_files() -> None:
    files = [
        (Path("override/a.json"), {"x": {"scheduler": "slurm"}}),
        (Path("override/b.json"), {"x": {"scheduler": "local"}}),
    ]
    with pytest.raises(ValueError) as error:
        resolve_presets([], files)
    message = str(error.value)
    assert "override/a.json" in message and "override/b.json" in message


def test_first_shared_folder_wins_whole() -> None:
    first = [(Path("one/p.json"), {"x": {"scheduler": "slurm", "env": {"A": "1"}}})]
    second = [
        (Path("two/p.json"), {"x": {"scheduler": "local", "env": {"B": "2"}, "header": ["h"]}})
    ]
    result = resolve_presets([first, second], [])
    assert result == {"x": {"scheduler": "slurm", "env": {"A": "1"}}}


def test_patch_of_a_shared_preset_cannot_set_extends() -> None:
    with pytest.raises(ValueError) as error:
        resolve_presets(
            [shared(leonardo=LEONARDO, other=LEONARDO)],
            override(leonardo={"extends": "other"}),
        )
    message = str(error.value)
    assert str(OVERRIDE_FILE) in message
    assert "'leonardo'" in message
    assert "extends" in message


def test_inputs_are_not_mutated() -> None:
    dcgp = {"extends": "leonardo", "env": {"KEEP": None}, "sbatch": ["--partition=dcgp"]}
    shared_layers = [shared(leonardo=LEONARDO, leonardo_dcgp=dcgp)]
    override_layer = [
        (
            OVERRIDE_FILE,
            {
                "leonardo": {"env": {"KEEP": None}, "sbatch": ["--account=REAL"], "notes": ["n"]},
                "mine": {"scheduler": "slurm", "env": {"A": None}},
                "more": {"extends": "mine", "header": ["h"]},
            },
        )
    ]
    shared_before = copy.deepcopy(shared_layers)
    override_before = copy.deepcopy(override_layer)
    result = resolve_presets(shared_layers, override_layer)
    result["leonardo"]["env"]["INJECTED"] = "1"
    result["leonardo"]["sbatch"].append("--injected")
    result["leonardo"]["launchers"]["srun"]["flags"].append("--injected")
    assert shared_layers == shared_before
    assert override_layer == override_before


def test_wrong_container_type_is_replaced_not_crashed_on() -> None:
    result = one(
        shared(x={"scheduler": "slurm", "env": {"A": "1"}, "sbatch": ["--a=1"]}),
        override(x={"env": "oops", "sbatch": "--b=2", "launchers": ["bad"]}),
        "x",
    )
    assert result["env"] == "oops"
    assert result["sbatch"] == "--b=2"
    assert result["launchers"] == ["bad"]


def test_results_do_not_alias_override_only_values_or_each_other() -> None:
    override_layer = [
        (
            OVERRIDE_FILE,
            {
                "mine": {
                    "scheduler": "slurm",
                    "env": {"A": "1"},
                    "hosts": ["h1"],
                    "launchers": {"srun": {"flags": ["--mpi=pmix"]}},
                },
                "leonardo": {
                    "launchers": {"direct": {"command": "run", "flags": ["--x"], "export": ["E"]}}
                },
            },
        )
    ]
    before = copy.deepcopy(override_layer)
    dcgp = {"extends": "leonardo", "sbatch": ["--partition=dcgp"]}
    result = resolve_presets([shared(leonardo=LEONARDO, leonardo_dcgp=dcgp)], override_layer)
    variant_before = copy.deepcopy(result["leonardo_dcgp"])
    result["mine"]["env"]["INJECTED"] = "1"
    result["mine"]["hosts"].append("INJECTED")
    result["leonardo"]["launchers"]["direct"]["export"].append("INJECTED")
    result["mine"]["launchers"]["srun"]["flags"].append("--injected")
    result["leonardo"]["launchers"]["direct"]["flags"].append("--injected")
    result["leonardo"]["env"]["INJECTED"] = "1"
    result["leonardo"]["sbatch"].append("--injected")
    result["leonardo"]["launchers"]["mpirun"]["export"].append("INJECTED")
    assert override_layer == before
    assert result["leonardo_dcgp"] == variant_before


def test_repeated_keyed_option_in_a_patch_keeps_the_last_at_the_first_position() -> None:
    with_base = one(
        shared(
            x={
                "scheduler": "slurm",
                "sbatch": ["--a=0", "--b=0"],
                "launchers": {"srun": {"flags": ["--f=0"]}},
            }
        ),
        override(
            x={
                "sbatch": ["--q=1", "--a=1", "--q=2"],
                "launchers": {"srun": {"flags": ["--g=1", "--f=1", "--g=2"]}},
            }
        ),
        "x",
    )
    assert with_base["sbatch"] == ["--a=1", "--b=0", "--q=2"]
    assert with_base["launchers"]["srun"]["flags"] == ["--f=1", "--g=2"]


def test_repeated_keyed_option_in_a_patch_without_a_base_list_collapses_too() -> None:
    result = one(
        shared(x={"scheduler": "slurm"}),
        override(
            x={
                "sbatch": ["--q=1", "--q=2"],
                "launchers": {"mpirun": {"flags": ["--g=1", "--g=2", "--map-by", "core"]}},
            }
        ),
        "x",
    )
    assert result["sbatch"] == ["--q=2"]
    assert result["launchers"]["mpirun"]["flags"] == ["--g=2", "--map-by", "core"]
    new_preset = resolve_presets(
        [], override(y={"scheduler": "slurm", "sbatch": ["--q=1", "--q=2"]})
    )
    assert new_preset["y"]["sbatch"] == ["--q=2"]


def test_layer_file_that_is_not_an_object_names_the_file() -> None:
    with pytest.raises(ValueError) as error:
        resolve_presets([], [(OVERRIDE_FILE, ["not", "an", "object"])])  # type: ignore[list-item]
    assert str(OVERRIDE_FILE) in str(error.value)


@pytest.mark.parametrize("layer", ["shared", "override"])
def test_a_common_entry_in_any_file_is_refused_naming_the_file(layer: str) -> None:
    common = {"_common": {"env": {"CRAB_ROOT": "__CWD__"}}}
    if layer == "shared":
        files = ([[(SHARED_FILE, common)]], [])
        bad = SHARED_FILE
    else:
        files = ([shared(leonardo=LEONARDO)], [(OVERRIDE_FILE, common)])
        bad = OVERRIDE_FILE
    with pytest.raises(ValueError, match="_common.*removed in CRAB 1.2.*each preset") as error:
        resolve_presets(*files)
    assert str(bad) in str(error.value)
