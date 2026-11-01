"""The shipped placement example runs unchanged on every layout that has the hardware, and says why on the rest.

`examples/placement/pinned_gpu.json` names no device, core, partition or GPU request. Here it goes through
the real pre-submit check and the placement against each topology shape: the layouts with four GPUs and a
way to select a NIC accept it, the others refuse it with the numbers. The control twin differs in one word.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from crab.core.config_checks import check_config
from crab.core.execution.placement import place_experiment
from crab.core.execution.settings import ExecutionSettings
from settings_fixtures import slurm_settings
from topology_fixtures import SHAPES

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "placement"
ACCEPTING = [
    "four-gpus-four-nics",
    "eight-gpus-two-nics",
    "smt-two-sockets",
    "captured-four-gpu-four-nic-infiniband",
]
TOO_SMALL = ["two-gpus-two-port-cards", "amd-single-socket", "cpu-only"]


def load(name: str) -> dict[str, Any]:
    config: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return config


def settings_for(shape: str) -> ExecutionSettings:
    return dataclasses.replace(slurm_settings(), topology=SHAPES[shape]())


def place(config: dict[str, Any], settings: ExecutionSettings):
    app = config["experiments"]["alltoall_gpu"]["apps"]["0"]
    options = config["global_options"]
    return place_experiment(
        "alltoall_gpu",
        ["0"],
        [app],
        [["n0", "n1"]],
        {},
        {"ppn": int(options["ppn"])},
        settings,
        ["n0", "n1"],
        options,
    )


def test_the_example_holds_no_cluster_value() -> None:
    for name in ("pinned_gpu.json", "pinned_gpu_control.json"):
        text = (EXAMPLES / name).read_text()
        for forbidden in ("mlx5", "cpus", '"nics"', "partition", "gres", "CUDA_VISIBLE", "UCX_NET"):
            assert forbidden not in text, (name, forbidden)


def test_the_control_twin_differs_only_in_the_nic_choice_and_the_names() -> None:
    pinned, control = load("pinned_gpu.json"), load("pinned_gpu_control.json")
    assert pinned["experiments"]["alltoall_gpu"]["apps"]["0"]["bind"]["nic"] == "nearest"
    assert control["experiments"]["alltoall_gpu"]["apps"]["0"]["bind"]["nic"] == "default"
    for config in (pinned, control):
        config["global_options"]["name"] = "x"
        config["experiments"]["alltoall_gpu"]["description"] = "x"
        config["experiments"]["alltoall_gpu"]["apps"]["0"]["bind"]["nic"] = "x"
    assert pinned == control


@pytest.mark.parametrize("shape", ACCEPTING)
def test_the_pre_submit_check_accepts_the_example_on_a_layout_with_the_hardware(shape: str) -> None:
    for name in ("pinned_gpu.json", "pinned_gpu_control.json"):
        assert isinstance(check_config(load(name), settings_for(shape)), list)


@pytest.mark.parametrize("shape", ACCEPTING)
def test_the_pinned_example_gives_four_ranks_four_gpus_and_a_nic_each_the_control_gives_no_nic(
    shape: str,
) -> None:
    settings = settings_for(shape)
    pinned, control = (
        place(load("pinned_gpu.json"), settings),
        place(load("pinned_gpu_control.json"), settings),
    )
    assert pinned.gpus[0] == (0, 1, 2, 3) == control.gpus[0]
    assert pinned.nics[0] is not None and len(pinned.nics[0]) == 4
    assert control.nics[0] is None
    assert pinned.cpu_lists[0] == control.cpu_lists[0] and len(pinned.cpu_lists[0] or ()) == 4


def test_where_nics_are_shared_the_placement_says_so() -> None:
    pinned = place(load("pinned_gpu.json"), settings_for("eight-gpus-two-nics"))
    assert any("share NIC" in warning for warning in pinned.warnings)


@pytest.mark.parametrize("shape", TOO_SMALL)
def test_a_layout_with_too_few_gpus_refuses_the_example_with_both_numbers(shape: str) -> None:
    settings = settings_for(shape)
    count = len(settings.topology.gpus) if settings.topology else 0
    with pytest.raises(ValueError) as error:
        place(load("pinned_gpu.json"), settings)
    message = str(error.value)
    assert "bind.gpu" in message
    assert (f"the topology has {count}" in message) or (
        "needs GPUs, but the topology lists none" in message
    )


def test_a_stack_that_cannot_select_a_nic_refuses_the_pinned_example_but_runs_the_control() -> None:
    settings = settings_for("no-nic-selection")
    with pytest.raises(ValueError, match="this topology cannot select a NIC per rank"):
        place(load("pinned_gpu.json"), settings)
    control = place(load("pinned_gpu_control.json"), settings)
    assert control.gpus[0] == (0, 1, 2, 3) and control.nics[0] is None


def test_a_preset_without_a_topology_refuses_the_example_and_says_what_to_do() -> None:
    config = copy.deepcopy(load("pinned_gpu.json"))
    with pytest.raises(ValueError, match="bind needs a topology in the preset"):
        place(config, slurm_settings())
