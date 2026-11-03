"""`bind.nic exclusive` across apps (plan 122): an exclusive NIC is kept to one app of a node.

Apps of the same chain never conflict; apps on different nodes never conflict; apps that name no NIC
are not checked. Shape `four-gpus-four-nics`: devices `nic0:1`..`nic3:1`, `nic{k}:1` near GPU k.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest

from crab.core.execution.placement import place_experiment
from crab.core.execution.settings import ExecutionSettings
from settings_fixtures import slurm_settings
from topology_fixtures import SHAPES

EXCLUSIVE = {"sbatch_directives": ["--exclusive"]}
EXCL = {"nic": {"pick": "spread", "exclusive": True}}
SPREAD = {"nic": "spread"}


def settings_for(shape: str = "four-gpus-four-nics") -> ExecutionSettings:
    return dataclasses.replace(slurm_settings(), topology=SHAPES[shape]())


def place(
    configs: list[dict[str, Any]],
    nodes: list[list[str]] | None = None,
    settings: ExecutionSettings | None = None,
    ppn: int = 2,
    allocation: dict[str, Any] | None = None,
):
    keys = [str(i) for i in range(len(configs))]
    return place_experiment(
        "e1",
        keys,
        configs,
        nodes or [["h0"]] * len(configs),
        allocation if allocation is not None else {"mode": "shared"},
        {"ppn": ppn},
        settings or settings_for(),
        [],
        EXCLUSIVE,
    )


def held_by_app_0(device: str) -> str:
    return re.escape(
        f"app 1: NIC {device} is held exclusively by app 0 (bind.nic exclusive) on the same node"
    )


def test_an_app_cannot_use_a_nic_an_earlier_exclusive_app_holds() -> None:
    with pytest.raises(ValueError, match=held_by_app_0("nic0:1")):
        place([{"bind": EXCL}, {"bind": SPREAD}])


def test_an_exclusive_app_cannot_take_a_nic_an_earlier_app_uses() -> None:
    message = "app 1: bind.nic exclusive: NIC nic0:1 is also used by app 0 on the same node"
    with pytest.raises(ValueError, match=re.escape(message)):
        place([{"bind": SPREAD}, {"bind": EXCL}])


def test_apps_on_different_nodes_do_not_conflict() -> None:
    placed = place([{"bind": EXCL}, {"bind": SPREAD}], nodes=[["h0"], ["h1"]])
    assert placed.nics == [("nic0:1", "nic1:1"), ("nic0:1", "nic1:1")]


def test_exclusive_apps_with_different_nics_coexist() -> None:
    bind = {"gpu": "per-rank", "nic": {"pick": "nearest", "exclusive": True}}
    placed = place([{"bind": bind}, {"bind": bind}])
    assert placed.nics == [("nic0:1", "nic1:1"), ("nic2:1", "nic3:1")]


def test_a_chained_app_may_use_its_heads_exclusive_nics() -> None:
    placed = place([{"bind": EXCL}, {"start": "s0", "bind": SPREAD}])
    assert placed.nics == [("nic0:1", "nic1:1"), ("nic0:1", "nic1:1")]


def test_literal_nics_count_on_both_sides() -> None:
    message = "app 1: bind.nic exclusive: NIC nic0:1 is also used by app 0 on the same node"
    with pytest.raises(ValueError, match=re.escape(message)):
        place([{"nics": ["nic0:1"]}, {"bind": EXCL}])
    with pytest.raises(ValueError, match=held_by_app_0("nic1:1")):
        place([{"bind": EXCL}, {"nics": ["nic1:1"]}])


def test_an_app_that_names_no_nic_is_not_checked() -> None:
    place([{"bind": EXCL}, {"bind": {"gpu": "shared"}}])


def test_two_ranks_of_one_exclusive_app_still_refuse_each_other() -> None:
    bind = {"nic": {"pick": "single", "exclusive": True}, "gpu": "per-rank"}
    with pytest.raises(ValueError, match=re.escape("bind.nic exclusive: 2 ranks would share")):
        place([{"bind": bind}])
