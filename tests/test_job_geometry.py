"""The job's `--ntasks-per-node` is the largest per-node load of any experiment (ADR-036)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from crab.core.execution.job_geometry import job_tasks_per_node, needs_job_geometry
from crab.core.experiment.runner import ExperimentRunner
from settings_fixtures import slurm_settings


def _config(
    experiments: dict[str, dict[str, Any]], numnodes: Any = "4", **global_opts: Any
) -> dict[str, Any]:
    global_options: dict[str, Any] = dict(global_opts)
    if numnodes is not None:
        global_options["numnodes"] = numnodes
    return {"global_options": global_options, "experiments": experiments}


def _apps(*options: dict[str, Any]) -> dict[str, Any]:
    return {str(i): {"path": "w.py", **o} for i, o in enumerate(options)}


def test_only_a_global_ppn() -> None:
    config = _config({"e": {"apps": _apps({}, {})}}, ppn=4)
    # Two apps with no partition and the default allocation split the four nodes: one per node.
    assert job_tasks_per_node(config) == 4


def test_two_disjoint_apps_take_the_larger_ppn() -> None:
    allocation = {"partitions": {"a": {"share": 50}, "b": {"share": 50}}}
    apps = _apps({"partition": "a", "ppn": 1}, {"partition": "b", "ppn": 4})
    config = _config({"e": {"apps": apps, "local_options": {"allocation": allocation}}})
    assert job_tasks_per_node(config) == 4


def test_two_apps_split_over_the_default_allocation_do_not_add_up() -> None:
    config = _config({"e": {"apps": _apps({"ppn": 1}, {"ppn": 4})}})
    assert job_tasks_per_node(config) == 4


def test_shared_apps_add_up() -> None:
    config = _config(
        {"e": {"apps": _apps({"ppn": 1}, {"ppn": 4}), "local_options": {"allocation": SHARED}}}
    )
    assert job_tasks_per_node(config) == 5


def test_shared_apps_with_shared_cores_add_up_too() -> None:
    allocation = {"mode": "shared", "cores": "shared"}
    config = _config(
        {"e": {"apps": _apps({"ppn": 1}, {"ppn": 4}), "local_options": {"allocation": allocation}}}
    )
    assert job_tasks_per_node(config) == 5


def test_a_chain_counts_once_at_its_largest_ppn() -> None:
    config = _config(
        {
            "e": {
                "apps": _apps({"ppn": 1}, {"ppn": 4, "start": "s0"}),
                "local_options": {"allocation": SHARED},
            }
        }
    )
    assert job_tasks_per_node(config) == 4


def test_a_chain_adds_to_a_concurrent_head() -> None:
    config = _config(
        {
            "e": {
                "apps": _apps({"ppn": 1}, {"ppn": 4, "start": "s0"}, {"ppn": 2}),
                "local_options": {"allocation": SHARED},
            }
        }
    )
    assert job_tasks_per_node(config) == 6


def test_local_options_ppn_overrides_the_global_one() -> None:
    config = _config(
        {
            "a": {"apps": _apps({}), "local_options": {"ppn": 2}},
            "b": {"apps": _apps({})},
        },
        ppn=1,
    )
    assert job_tasks_per_node(config) == 2


def test_the_maximum_is_taken_across_experiments() -> None:
    config = _config(
        {
            "a": {"apps": _apps({"ppn": 3})},
            "b": {"apps": _apps({"ppn": 7})},
            "c": {"apps": _apps({"ppn": 2})},
        }
    )
    assert job_tasks_per_node(config) == 7


def test_ppn_defaults_to_one() -> None:
    assert job_tasks_per_node(_config({"e": {"apps": _apps({})}})) == 1


def test_a_token_gives_none() -> None:
    config = _config({"e": {"apps": _apps({})}}, ppn="{p}")
    assert job_tasks_per_node(config) is None


def test_a_token_in_numnodes_gives_none() -> None:
    assert job_tasks_per_node(_config({"e": {"apps": _apps({})}}, numnodes="{n}")) is None


def test_missing_numnodes_gives_none() -> None:
    assert job_tasks_per_node(_config({"e": {"apps": _apps({})}}, numnodes=None)) is None


def test_no_experiments_or_no_apps_gives_none() -> None:
    assert job_tasks_per_node(_config({})) is None
    assert job_tasks_per_node(_config({"e": {"apps": {}}})) is None


def test_an_invalid_allocation_gives_none() -> None:
    config = _config({"e": {"apps": _apps({"start": "s9"})}})
    assert job_tasks_per_node(config) is None


SHARED = {"mode": "shared"}


def test_null_local_options_or_apps_count_as_empty() -> None:
    config = _config(
        {
            "a": {"apps": _apps({"ppn": 3}), "local_options": None},
            "b": {"apps": None},
            "c": {"local_options": None},
        }
    )
    assert job_tasks_per_node(config) == 3


@pytest.mark.parametrize(
    "experiment",
    [
        {"apps": _apps({"ppn": 2})},
        {"apps": _apps({}), "local_options": {"ppn": 2}},
        {"apps": _apps({}, {}), "local_options": {"allocation": SHARED}},
        {"apps": _apps({}, {}), "local_options": {"allocation": {"partitions": {"a": SHARED}}}},
    ],
    ids=["app ppn", "experiment ppn", "shared allocation", "shared partition"],
)
def test_the_global_ppn_does_not_size_the_job_when(experiment: dict[str, Any]) -> None:
    assert needs_job_geometry(_config({"e": experiment}))


@pytest.mark.parametrize(
    "experiment",
    [{"apps": _apps({}, {})}, {"apps": None, "local_options": None}, {"apps": {}}],
    ids=["plain apps", "null apps and options", "no apps"],
)
def test_the_global_ppn_sizes_the_job_otherwise(experiment: dict[str, Any]) -> None:
    assert not needs_job_geometry(_config({"e": experiment}, ppn=4))


def test_a_shared_global_allocation_needs_the_geometry_too() -> None:
    config = _config({"e": {"apps": _apps({}, {})}}, allocation=SHARED)
    assert needs_job_geometry(config)


_WRAPPER = (
    "from crab.wrappers.base import base\n\n"
    "class app(base):\n"
    "    metadata = [{'name': 'v', 'unit': 'x', 'conv': True}]\n\n"
    "    def run_app(self):\n"
    "        return 'echo 1'\n"
)


def test_the_job_load_matches_what_the_runner_places_on_each_node(tmp_path: Path) -> None:
    """Shared apps with ppn 1, 4 (after app 0) and 2: app 0 and 1 count once, at ppn 4."""
    wrapper = tmp_path / "w.py"
    wrapper.write_text(_WRAPPER)
    apps = {
        str(i): {"path": str(wrapper), "collect": False, **options}
        for i, options in enumerate([{"ppn": 1}, {"ppn": 4, "start": "s0"}, {"ppn": 2}])
    }
    experiment = {"apps": apps, "local_options": {"allocation": SHARED}}
    config = _config({"e": experiment}, numnodes="2", ppn=1)
    runner = ExperimentRunner(
        exp_name="e",
        config=experiment,
        global_options=config["global_options"],
        node_list=["n0", "n1"],
        output_dir=str(tmp_path / "system" / "job_2026-01-01_00-00-00"),
        logger=MagicMock(),
        settings=slurm_settings(),
    )
    runner.setup()
    chains = [[0, 1], [2]]  # app 1 runs after app 0, on its nodes
    assert runner.apps[1].node_list == runner.apps[0].node_list
    load: dict[str, int] = {}
    for chain in chains:
        ppn = max(runner.apps[i].ppn for i in chain)
        for node in runner.apps[chain[0]].node_list:
            load[node] = load.get(node, 0) + ppn
    assert load == {"n0": 6, "n1": 6}
    assert job_tasks_per_node(config) == max(load.values())
