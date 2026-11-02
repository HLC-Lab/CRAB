"""`bind` applied while an experiment is placed (ADR-043): what reaches the placement, what is refused.

The same `place_experiment` runs before submit and in the worker, so these tests cover both. Shapes come
from the shape matrix; nothing here is tied to one machine.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from crab.core.config_checks import check_config
from crab.core.execution.placement import parse_cpus, place_experiment
from crab.core.execution.placement_checks import check_experiment
from crab.core.execution.settings import ExecutionSettings
from settings_fixtures import slurm_settings
from topology_fixtures import SHAPES

EXCLUSIVE = {"sbatch_directives": ["--exclusive"]}


def settings_for(shape: str = "four-gpus-four-nics") -> ExecutionSettings:
    return dataclasses.replace(slurm_settings(), topology=SHAPES[shape]())


def place(
    configs: list[dict[str, Any]],
    nodes: list[list[str]] | None = None,
    settings: ExecutionSettings | None = None,
    ppn: int = 4,
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


def test_bind_gives_each_rank_a_nic_and_a_core_next_to_it_in_rank_order() -> None:
    placed = place([{"bind": {"gpu": "none", "cores": "nearest", "nic": "spread"}}])
    assert placed.cpu_lists[0] == (0, 1, 2, 3)
    assert placed.nics[0] == ("nic0:1", "nic1:1", "nic2:1", "nic3:1")


def test_without_bind_the_placement_is_what_it_was() -> None:
    placed = place([{"cpus": "8,0-1", "nics": ["a:1"]}], ppn=3)
    assert placed.cpu_lists[0] == (0, 1, 8)
    assert placed.nics[0] == ("a:1",)


def test_a_bind_that_asks_for_nothing_needs_no_topology() -> None:
    placed = place(
        [{"bind": {}}, {"bind": {"gpu": "none", "nic": "default"}}], settings=slurm_settings()
    )
    assert placed.cpu_lists == [None, None] and placed.nics == [None, None]


def test_bind_without_a_topology_in_the_preset_is_refused() -> None:
    with pytest.raises(ValueError) as error:
        place([{"bind": {"cores": "nearest"}}], settings=slurm_settings())
    assert str(error.value) == (
        "experiment e1: app 0: bind needs a topology in the preset, and this preset has none "
        "(crab probe prints one for a node)"
    )


def test_apps_on_the_same_nodes_take_cores_in_order_without_overlap() -> None:
    configs = [{"bind": {"cores": "nearest"}}, {"bind": {"cores": "nearest"}}]
    placed = place(configs, ppn=2)
    assert placed.cpu_lists == [(0, 1), (2, 3)]


def test_apps_on_different_nodes_do_not_take_each_others_cores() -> None:
    configs = [{"bind": {"cores": "nearest"}}, {"bind": {"cores": "nearest"}}]
    placed = place(configs, nodes=[["h0"], ["h1"]], ppn=2)
    assert placed.cpu_lists == [(0, 1), (0, 1)]


def test_a_bind_app_takes_the_cores_a_literal_app_before_it_holds() -> None:
    configs = [{"cpus": "0-1"}, {"bind": {"cores": "nearest"}}]
    assert place(configs, ppn=2).cpu_lists == [(0, 1), (2, 3)]


def test_a_chained_app_keeps_its_heads_cores_and_may_bind_a_nic() -> None:
    configs = [{"bind": {"cores": "nearest"}}, {"start": "s0", "bind": {"nic": "spread"}}]
    placed = place(configs, ppn=2)
    assert placed.cpu_lists == [(0, 1), (0, 1)]
    assert placed.nics == [None, ("nic0:1", "nic1:1")]
    with pytest.raises(
        ValueError, match="app 1: bind.cores cannot be set on an app that starts after another"
    ):
        place([{}, {"start": "s0", "bind": {"cores": "nearest"}}], ppn=2)


def test_bind_and_a_literal_key_on_the_same_axis_are_refused() -> None:
    with pytest.raises(
        ValueError, match="app 0: bind.cores and cpus both place the cores; keep one"
    ):
        place([{"cpus": "0-3", "bind": {"cores": "nearest"}}])
    for literal in ({"nics": ["a:1"]}, {"env": {"UCX_NET_DEVICES": "a:1"}}):
        with pytest.raises(
            ValueError, match=r"app 0: bind.nic and nics \(or env UCX_NET_DEVICES\)"
        ):
            place([{**literal, "bind": {"nic": "spread"}}])


def test_a_literal_key_on_another_axis_is_fine() -> None:
    placed = place([{"nics": ["a:1"], "bind": {"cores": "nearest"}}], ppn=1)
    assert placed.cpu_lists[0] == (0,) and placed.nics[0] == ("a:1",)


def test_gpu_per_rank_gives_each_rank_its_gpu_a_nic_and_a_core_in_one_domain() -> None:
    placed = place([{"bind": {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"}}])
    assert placed.gpus[0] == (0, 1, 2, 3)
    assert placed.nics[0] == ("nic0:1", "nic1:1", "nic2:1", "nic3:1")
    assert placed.cpu_lists[0] == (0, 1, 8, 9)


def test_the_rank_order_of_the_resolver_is_kept_where_a_literal_list_is_sorted() -> None:
    placed = place([{"bind": {"gpu": "per-rank", "cores": "nearest", "order": "spread"}}])
    assert placed.gpus[0] == (0, 2, 1, 3)
    assert placed.cpu_lists[0] == (0, 8, 1, 9)
    assert parse_cpus("0,8,1,9", "t") == (0, 1, 8, 9)


def test_gpu_all_sets_no_gpu_and_shared_wraps() -> None:
    assert place([{"bind": {"gpu": "all"}}]).gpus == [None]
    assert place([{"bind": {"gpu": "shared"}}], ppn=6).gpus[0] == (0, 1, 2, 3, 0, 1)


def test_several_ports_of_a_card_become_one_entry_per_rank() -> None:
    settings = settings_for("two-gpus-two-port-cards")
    body = {"bind": {"gpu": "per-rank", "nic": {"pick": "nearest", "ports": 2}}}
    placed = place([body], settings=settings, ppn=2)
    assert placed.nics[0] == ("rdma0:1,rdma1:1", "rdma2:1,rdma3:1")


def test_a_gpu_app_with_a_literal_gpus_or_the_vendor_variable_is_refused() -> None:
    with pytest.raises(
        ValueError, match=r"app 0: bind.gpu and gpus \(or env CUDA_VISIBLE_DEVICES\)"
    ):
        place([{"gpus": [0], "bind": {"gpu": "per-rank"}}])
    with pytest.raises(
        ValueError, match=r"app 0: bind.gpu and gpus \(or env CUDA_VISIBLE_DEVICES\)"
    ):
        place([{"env": {"CUDA_VISIBLE_DEVICES": "0"}, "bind": {"gpu": "per-rank"}}])
    assert place([{"gpus": [0, 1], "bind": {"cores": "nearest"}}], ppn=2).gpus[0] == (0, 1)


def test_slurm_gpu_binding_options_are_refused_with_a_gpu_bind() -> None:
    for option in ("--gpus-per-task=1", "--gpu-bind=closest"):
        with pytest.raises(ValueError) as error:
            place_experiment(
                "e1",
                ["0"],
                [{"bind": {"gpu": "per-rank"}}],
                [["h0"]],
                {},
                {"ppn": 2},
                settings_for(),
                [],
                {"sbatch_directives": ["--exclusive", option]},
            )
        assert "sets the GPUs itself, so --gpus-per-task and --gpu-bind cannot be among" in str(
            error.value
        )
    placed = place_experiment(
        "e1",
        ["0"],
        [{"bind": {"gpu": "all"}}],
        [["h0"]],
        {},
        {"ppn": 2},
        settings_for(),
        [],
        {"sbatch_directives": ["--gpus-per-task=1"]},
    )
    assert placed.gpus == [None]


def test_the_vendor_variable_follows_the_topology() -> None:
    settings = settings_for("amd-single-socket")
    with pytest.raises(ValueError, match=r"bind.gpu and gpus \(or env ROCR_VISIBLE_DEVICES\)"):
        place(
            [{"env": {"ROCR_VISIBLE_DEVICES": "0"}, "bind": {"gpu": "per-rank"}}],
            settings=settings,
            ppn=2,
        )


def test_what_the_topology_cannot_give_fails_with_its_numbers() -> None:
    with pytest.raises(ValueError) as error:
        place([{"bind": {"cores": "nearest"}}], ppn=17)
    assert str(error.value).startswith(
        "experiment e1: app 0: bind.cores nearest: local rank 16 has no free core"
    )
    with pytest.raises(ValueError, match="experiment e1: app 0: bind: unknown key 'gpus'"):
        place([{"bind": {"gpus": "per-rank"}}])


def test_the_resolvers_warnings_reach_the_placement() -> None:
    placed = place([{"bind": {"cores": "nearest"}}], ppn=2)
    assert any("no physical core list" in w for w in placed.warnings)


def test_cores_from_bind_still_need_exclusive_under_slurm() -> None:
    keys = ["0"]
    with pytest.raises(ValueError, match="--exclusive"):
        place_experiment(
            "e1",
            keys,
            [{"bind": {"cores": "nearest"}}],
            [["h0"]],
            {},
            {"ppn": 2},
            settings_for(),
            [],
            {},
        )


CHECK_OPTS = {"numnodes": 1, "ppn": 2, **EXCLUSIVE}


def test_the_check_before_submit_reports_a_bind_error_without_running_anything() -> None:
    with pytest.raises(ValueError, match="nothing places the rank"):
        check_experiment("e1", {"0": {"bind": {"nic": "nearest"}}}, {}, CHECK_OPTS, settings_for())
    ok = check_experiment(
        "e1", {"0": {"bind": {"cores": "nearest"}}}, {}, CHECK_OPTS, settings_for()
    )
    assert any("no physical core list" in w for w in ok)


def test_bind_cores_is_warned_about_under_the_direct_launcher_like_cpus() -> None:
    apps = {"0": {"bind": {"cores": "nearest"}}}
    direct = dataclasses.replace(settings_for(), launcher="direct")
    warnings = check_experiment("e1", apps, {}, CHECK_OPTS, direct)
    assert (
        "experiment e1: app 0: bind.cores has no effect with the direct launcher; "
        "only mpirun binds to an explicit core list"
    ) in warnings
    srun = dataclasses.replace(
        settings_for(), srun=dataclasses.replace(settings_for().srun, flags=("--cpu-bind=socket",))
    )
    assert any("--cpu-bind" in w for w in check_experiment("e1", apps, {}, CHECK_OPTS, srun))
    quiet = check_experiment("e1", {"0": {"bind": {"nic": "spread"}}}, {}, CHECK_OPTS, direct)
    assert not any("has no effect" in w for w in quiet)


def test_check_config_runs_the_bind_check_with_the_preset_topology() -> None:
    config = {
        "schema_version": 2,
        "global_options": {"numnodes": 1, "ppn": 2, "sbatch_directives": ["--exclusive"]},
        "experiments": {"e1": {"apps": {"0": {"path": "x.py", "bind": {"cores": "nearest"}}}}},
    }
    assert any("no physical core list" in w for w in check_config(config, settings_for()))
    with pytest.raises(ValueError, match="bind needs a topology in the preset"):
        check_config(config, slurm_settings())
