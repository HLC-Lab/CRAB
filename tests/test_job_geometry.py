"""The job's `--ntasks-per-node` is the largest per-node load of any experiment (ADR-036)."""

from __future__ import annotations

from typing import Any

from crab.core.execution.job_geometry import job_tasks_per_node


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
